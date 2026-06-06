#!/usr/bin/env bash
set -eo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MASTER_CONFIG_ROOT="${MASTER_CONFIG_ROOT:-$HOME/.config/vibego}"
RUNTIME_DIR="${VIBEGO_RUNTIME_ROOT:-$MASTER_CONFIG_ROOT/runtime}"
VENV_DIR="$RUNTIME_DIR/.venv"
LEGACY_VENV_DIR="$ROOT_DIR/.venv"
STATE_DIR="$MASTER_CONFIG_ROOT/state"
LOG_DIR="$MASTER_CONFIG_ROOT/logs"
LOCK_FILE="$STATE_DIR/master_restart.lock"
START_LOG="$LOG_DIR/start.log"
CODEX_STAMP_FILE="$STATE_DIR/npm_codex_install.stamp"
CODEX_INSTALL_TTL="${CODEX_INSTALL_TTL:-86400}"

# 统一重启信号文件路径：使用配置目录而非代码目录
# 这样 pipx 安装的 master 和源码运行的 master 可以共享同一个信号文件
export MASTER_RESTART_SIGNAL_PATH="$STATE_DIR/restart_signal.json"
export LOG_ROOT="${LOG_ROOT:-$LOG_DIR}"
if [[ -z "${LOG_FILE:-}" ]]; then
  export LOG_FILE="$LOG_DIR/vibe.log"
fi

log_line() {
  local ts
  ts=$(date '+%Y-%m-%d %H:%M:%S%z')
  printf '[%s] %s\n' "$ts" "$*"
}

log_info() {
  log_line "$@"
}

log_error() {
  log_line "$@" >&2
}

cleanup() {
  rm -f "$LOCK_FILE"
}

trap cleanup EXIT

cd "$ROOT_DIR"

mkdir -p "$(dirname "$LOCK_FILE")"
mkdir -p "$(dirname "$START_LOG")"
mkdir -p "$RUNTIME_DIR"
touch "$START_LOG"
exec >>"$START_LOG"
exec 2>&1

log_info "start.sh 启动，pid=$$"

if [[ -f "$LOCK_FILE" ]]; then
  log_error "已有 start.sh 在执行，跳过本次启动。"
  exit 1
fi

printf '%d\n' $$ > "$LOCK_FILE"

log_info "锁文件已创建：$LOCK_FILE"

ensure_codex_installed() {
  local need_install=1
  local now
  local codex_bin
  if ! command -v npm >/dev/null 2>&1; then
    log_error "未检测到 npm，可执行文件缺失，跳过 @openai/codex 全局安装"
    return
  fi

  log_info "检测到 npm 版本：$(npm --version)"

  if [[ ! "$CODEX_INSTALL_TTL" =~ ^[0-9]+$ ]]; then
    log_error "CODEX_INSTALL_TTL 非法值：$CODEX_INSTALL_TTL，回退为 86400 秒"
    CODEX_INSTALL_TTL=86400
  fi

  if (( need_install )); then
    codex_bin=$(command -v codex 2>/dev/null || true)
    if [[ -n "$codex_bin" ]]; then
      log_info "Detected existing codex binary at ${codex_bin}; skipping install (upgrade manually if needed)"
      need_install=0
    elif [[ -x "/opt/homebrew/bin/codex" ]]; then
      log_info "Detected existing codex binary at /opt/homebrew/bin/codex; skipping install (upgrade manually if needed)"
      need_install=0
    fi
  fi

  if (( need_install )) && [[ -f "$CODEX_STAMP_FILE" ]]; then
    local last_ts
    last_ts=$(cat "$CODEX_STAMP_FILE" 2>/dev/null || printf '0')
    if [[ "$last_ts" =~ ^[0-9]+$ ]]; then
      now=$(date +%s)
      local elapsed=$(( now - last_ts ))
      if (( elapsed < CODEX_INSTALL_TTL )); then
        local remaining=$(( CODEX_INSTALL_TTL - elapsed ))
        log_info "Previous install happened ${elapsed}s ago (cooldown ${CODEX_INSTALL_TTL}s); skipping install with ${remaining}s remaining"
        need_install=0
      fi
    fi
  fi

  if (( need_install )); then
    log_info "开始执行 npm install -g @openai/codex@latest"
    if npm install -g @openai/codex@latest; then
      now=$(date +%s)
      printf '%s\n' "$now" > "$CODEX_STAMP_FILE"
      log_info "npm install -g @openai/codex@latest 成功"
    else
      local status=$?
      log_error "npm install -g @openai/codex@latest failed (exit code ${status}); continuing startup"
    fi
  fi
}

ensure_codex_installed

select_python_binary() {
  # 选择满足 CPython <=3.12 的解释器，默认禁用 3.13（pydantic-core 在 pipx 基础环境下无兼容轮子）
  local allow_py313="${VIBEGO_ALLOW_PY313:-}"
  local candidates=()
  local chosen=""
  local name
  if [[ -n "${VIBEGO_PYTHON:-}" ]]; then
    candidates+=("$VIBEGO_PYTHON")
  fi
  for name in python3.13 python3.12 python3.11 python3.10 python3.9 python3; do
    if [[ "${VIBEGO_PYTHON:-}" == "$name" ]]; then
      continue
    fi
    candidates+=("$name")
  done

  for name in "${candidates[@]}"; do
    if [[ -z "$name" ]]; then
      continue
    fi
    if ! command -v "$name" >/dev/null 2>&1; then
      continue
    fi
    local version_raw
    version_raw=$("$name" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null) || continue
    local major="${version_raw%%.*}"
    local minor="${version_raw#*.}"
    if [[ "$major" != "3" ]]; then
      log_line "跳过 ${name} (版本 ${version_raw})：非 CPython 3.x" >&2
      continue
    fi
    local explicit_override=0
    if [[ -n "${VIBEGO_PYTHON:-}" && "$name" == "$VIBEGO_PYTHON" ]]; then
      explicit_override=1
    fi
    if [[ "$minor" =~ ^[0-9]+$ ]] && (( minor == 13 )) && [[ "$allow_py313" != "1" ]] && (( explicit_override == 0 )); then
      log_line "跳过 ${name} (版本 ${version_raw})：默认禁用 Python 3.13，可设置 VIBEGO_ALLOW_PY313=1 覆盖" >&2
      continue
    fi
    if [[ "$minor" =~ ^[0-9]+$ ]] && (( minor > 13 )); then
      log_line "跳过 ${name} (版本 ${version_raw})：高于 3.13" >&2
      continue
    fi
    if [[ "$minor" =~ ^[0-9]+$ ]] && (( minor < 9 )); then
      log_line "跳过 ${name} (版本 ${version_raw})：低于 3.9，可能缺少官方轮子" >&2
      continue
    fi
    chosen="$name"
    log_line "使用 Python 解释器：${chosen} (版本 ${version_raw})" >&2
    break
  done

  if [[ -z "$chosen" ]]; then
    log_error "未找到满足 <=3.13 的 Python 解释器，可通过设置 VIBEGO_PYTHON 指定路径"
    exit 1
  fi

  printf '%s' "$chosen"
}

# 检查Python依赖是否已安装完整
check_deps_installed() {
  # 检查虚拟环境是否存在
  if [[ ! -d "$VENV_DIR" ]]; then
    log_info "虚拟环境不存在，需要初始化"
    return 1
  fi

  # 检查虚拟环境的Python解释器
  if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    log_info "虚拟环境Python解释器缺失"
    return 1
  fi

  # 激活虚拟环境并检查关键依赖包
  # aiogram: Telegram Bot框架
  # aiohttp: 异步HTTP客户端
  # aiosqlite: 异步SQLite数据库
  if ! "$VENV_DIR/bin/python" -c "import aiogram, aiohttp, aiosqlite" 2>/dev/null; then
    log_info "关键依赖包缺失或损坏"
    return 1
  fi

  log_info "依赖检查通过，虚拟环境完整"
  return 0
}

# 清理旧 master 进程的健壮函数（改进版：支持 PID 文件 + pgrep 双保险）
cleanup_old_master() {
  local max_wait=10  # 最多等待10秒优雅退出
  local waited=0
  local old_pids=""
  local master_pid_file="$STATE_DIR/master.pid"

  # 方案1：优先从 PID 文件读取
  if [[ -f "$master_pid_file" ]]; then
    local pid_from_file
    pid_from_file=$(cat "$master_pid_file" 2>/dev/null || true)
    if [[ "$pid_from_file" =~ ^[0-9]+$ ]]; then
      if kill -0 "$pid_from_file" 2>/dev/null; then
        old_pids="$pid_from_file"
        log_info "从 PID 文件检测到旧 master 实例（PID: $old_pids）"
      else
        log_info "PID 文件存在但进程已不在，清理过期 PID 文件"
        rm -f "$master_pid_file"
      fi
    fi
  fi

  # 方案2：使用 pgrep 查找（支持多种运行方式）
  if [[ -z "$old_pids" ]]; then
    # 匹配模式：支持源码运行和 pipx 安装的方式
    # - python.*master.py（源码运行）
    # - Python.*master.py（macOS 上的 Python.app）
    # - bot.py（pipx 安装的 master 别名）
    local pgrep_pids
    pgrep_pids=$(pgrep -f "master\.py$" 2>/dev/null || true)
    if [[ -n "$pgrep_pids" ]]; then
      old_pids="$pgrep_pids"
      log_info "通过 pgrep 检测到旧 master 实例（PID: $old_pids）"
    fi
  fi

  # 如果两种方式都没找到，说明没有旧进程
  if [[ -z "$old_pids" ]]; then
    log_info "未检测到旧 master 实例"
    return 0
  fi

  # 开始清理旧进程
  log_info "正在优雅终止旧 master 实例（PID: $old_pids）..."

  # 发送 SIGTERM 信号优雅终止
  for pid in $old_pids; do
    kill -15 "$pid" 2>/dev/null || true
  done

  # 循环等待进程退出
  while (( waited < max_wait )); do
    sleep 1
    ((waited++))

    # 检查所有 PID 是否都已退出
    local all_exited=1
    for pid in $old_pids; do
      if kill -0 "$pid" 2>/dev/null; then
        all_exited=0
        break
      fi
    done

    if (( all_exited )); then
      log_info "✅ 旧 master 已优雅退出（耗时 ${waited}秒）"
      rm -f "$master_pid_file"
      return 0
    fi
  done

  # 优雅终止超时，执行强制结束
  log_info "优雅终止超时（${max_wait}秒），执行强制结束..."
  for pid in $old_pids; do
    kill -9 "$pid" 2>/dev/null || true
  done
  sleep 2

  # 最后检查
  local remaining_pids=""
  for pid in $old_pids; do
    if kill -0 "$pid" 2>/dev/null; then
      remaining_pids="$remaining_pids $pid"
    fi
  done

  if [[ -n "$remaining_pids" ]]; then
    log_error "❌ 无法清理旧 master 进程（残留 PID:$remaining_pids）"
    log_error "请手动执行: kill -9$remaining_pids"
    exit 1
  fi

  log_info "✅ 旧 master 实例已强制清理"
  rm -f "$master_pid_file"
  return 0
}

# 调用清理函数
cleanup_old_master

# 智能依赖管理：仅在必要时安装
REQUIREMENTS_FILE="${VIBEGO_REQUIREMENTS_PATH:-$ROOT_DIR/scripts/requirements.txt}"
if [[ ! -f "$REQUIREMENTS_FILE" ]]; then
  log_error "依赖文件缺失: $REQUIREMENTS_FILE"
  exit 1
fi

PYTHON_BIN="$(select_python_binary)"

# 兼容旧版本：如检测到仓库内的 .venv，则迁移至运行期目录
migrate_legacy_venv() {
  if [[ -d "$LEGACY_VENV_DIR" && ! -e "$VENV_DIR" ]]; then
    log_info "检测到旧虚拟环境目录：$LEGACY_VENV_DIR，准备迁移至 $VENV_DIR"
    if mv "$LEGACY_VENV_DIR" "$VENV_DIR"; then
      log_info "虚拟环境已迁移至：$VENV_DIR"
    else
      log_error "迁移旧虚拟环境失败，请手动检查后重试"
    fi
  fi
}

migrate_legacy_venv

# 检查是否需要安装依赖
if check_deps_installed; then
  log_info "依赖已安装且完整，跳过pip install（加速重启）"
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
else
  log_info "首次启动或依赖缺失，正在安装依赖..."

  # 创建或重建虚拟环境
  "$PYTHON_BIN" -m venv "$VENV_DIR"
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"

  # 安装依赖
  # 将 pip 输出重定向到日志文件，避免 BrokenPipe 错误
  log_info "开始执行 pip install -r $REQUIREMENTS_FILE"
  PIP_LOG_FILE="$LOG_DIR/pip_install_$(date +%Y%m%d_%H%M%S).log"
  if pip install -r "$REQUIREMENTS_FILE" > "$PIP_LOG_FILE" 2>&1; then
    log_info "依赖安装完成"
  else
    PIP_EXIT_CODE=$?
    log_error "pip install 失败，退出码=$PIP_EXIT_CODE，详见 $PIP_LOG_FILE"
    # 如果是 BrokenPipe (退出码 141)，验证依赖是否实际已安装
    if [[ $PIP_EXIT_CODE -eq 141 ]]; then
      log_info "检测到 BrokenPipe 错误，验证依赖完整性..."
      # 验证关键依赖包是否可导入
      if "$VENV_DIR/bin/python" -c "import aiogram, aiohttp, aiosqlite" 2>/dev/null; then
        log_info "依赖验证通过，BrokenPipe 可忽略，继续执行"
      else
        log_error "依赖验证失败，虽然是 BrokenPipe 但依赖未完整安装"
        exit 1
      fi
    else
      # 其他错误直接退出
      exit $PIP_EXIT_CODE
    fi
  fi
fi

# 后台启动 master，日志落在 vibe.log
# 显式传递重启标记环境变量（如果存在）
if [[ -n "${MASTER_RESTART_EXPECTED:-}" ]]; then
  log_info "检测到重启标记环境变量 MASTER_RESTART_EXPECTED=$MASTER_RESTART_EXPECTED"
  export MASTER_RESTART_EXPECTED
fi

log_info "准备启动 master 进程..."

# 清理旧的错误日志（保留最近 10 次）
cleanup_old_error_logs() {
  local error_log_pattern="$LOG_DIR/master_error_*.log"
  local error_logs
  error_logs=$(ls -t $error_log_pattern 2>/dev/null || true)
  if [[ -n "$error_logs" ]]; then
    local count=0
    while IFS= read -r logfile; do
      ((count++))
      if (( count > 10 )); then
        rm -f "$logfile"
        log_info "已清理旧错误日志: $logfile"
      fi
    done <<< "$error_logs"
  fi
}

cleanup_old_error_logs

# 创建带时间戳的错误日志文件
MASTER_ERROR_LOG="$LOG_DIR/master_error_$(date +%Y%m%d_%H%M%S).log"
MASTER_STDOUT_LOG="$LOG_DIR/master_stdout.log"

# 显式传递环境变量给 nohup 进程，确保重启信号文件路径正确
# 使用虚拟环境的 Python 解释器，避免版本不匹配导致依赖加载失败
# 重要：将 stderr 保存到日志文件，方便排查启动失败问题
MASTER_RESTART_SIGNAL_PATH="$MASTER_RESTART_SIGNAL_PATH" nohup "$VENV_DIR/bin/python" master.py > "$MASTER_STDOUT_LOG" 2> "$MASTER_ERROR_LOG" &
MASTER_PID=$!

# 健壮性检查：确保进程成功启动
if [[ -z "${MASTER_PID:-}" ]]; then
  log_error "❌ 无法获取 master 进程 PID，启动失败"
  log_error "可能原因：python 命令不可用或 master.py 有语法错误"
  exit 1
fi

# 短暂等待后检查进程是否仍在运行
sleep 0.5
if ! kill -0 "$MASTER_PID" 2>/dev/null; then
  log_error "❌ master 进程启动后立即退出（PID=$MASTER_PID）"
  log_error "请检查："
  log_error "  - master.py 是否有语法错误: python master.py"
  log_error "  - 依赖是否完整: pip list | grep aiogram"
  log_error "  - 错误日志: $MASTER_ERROR_LOG"

  # 输出错误日志的最后 20 行，帮助快速定位问题
  if [[ -s "$MASTER_ERROR_LOG" ]]; then
    log_error ""
    log_error "=== 错误日志最后 20 行 ==="
    tail -20 "$MASTER_ERROR_LOG" | while IFS= read -r line; do
      log_error "  $line"
    done
    log_error "=========================="
  else
    log_error "错误日志文件为空，可能是环境变量或路径问题"
  fi

  exit 1
fi

log_info "master 已后台启动，PID=$MASTER_PID，日志写入 ${LOG_FILE}"

# 健康检查：等待 master 上线并验证关键 worker
log_info "开始执行健康检查..."
HEALTHCHECK_START=$(date +%s)

if python scripts/master_healthcheck.py --project hyphavibebotbackend; then
  HEALTHCHECK_END=$(date +%s)
  HEALTHCHECK_DURATION=$((HEALTHCHECK_END - HEALTHCHECK_START))
  log_info "✅ master 健康检查通过，耗时 ${HEALTHCHECK_DURATION}s"
else
  HEALTHCHECK_END=$(date +%s)
  HEALTHCHECK_DURATION=$((HEALTHCHECK_END - HEALTHCHECK_START))
  log_error "⚠️ master 健康检查失败，耗时 ${HEALTHCHECK_DURATION}s"
  log_error "建议检查："
  log_error "  - 进程状态: ps aux | grep 'python.*master.py'"
  log_error "  - 启动日志: tail -100 $LOG_DIR/start.log"
  log_error "  - 运行日志: tail -100 $LOG_FILE"
  log_error "  - 进程 PID: $MASTER_PID"

  # 检查进程是否仍在运行
  if kill -0 "$MASTER_PID" 2>/dev/null; then
    log_info "master 进程仍在运行（PID=$MASTER_PID），允许继续启动"
    log_info "⚠️ 请手动验证服务是否正常工作"
  else
    log_error "❌ master 进程已退出，启动失败"
    exit 1
  fi
fi
