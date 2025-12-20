#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC2155
SOURCE_ROOT="${VIBEGO_PACKAGE_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

pick_python_bin() {
  if [[ -n "${VIBEGO_PYTHON_BIN:-}" ]]; then
    printf '%s' "$VIBEGO_PYTHON_BIN"
    return 0
  fi
  local candidate
  for candidate in python3.11 python3.10 python3.9 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  return 1
}

PYTHON_BIN="$(pick_python_bin)" || {
  echo "[run-bot] 未检测到可用的 python3，请先安装 Python>=3.9" >&2
  exit 1
}

PY_VERSION_OUTPUT="$("$PYTHON_BIN" - <<'PY'
import sys
print(sys.version_info.major, sys.version_info.minor, sys.version_info.micro)
PY
)"
read -r PY_MAJOR PY_MINOR PY_MICRO <<<"$PY_VERSION_OUTPUT"
PY_VERSION_STR="${PY_MAJOR}.${PY_MINOR}.${PY_MICRO}"
if (( PY_MAJOR < 3 || (PY_MAJOR == 3 && PY_MINOR < 9) )); then
  echo "[run-bot] 当前 Python 版本 $PY_VERSION_STR 不满足 >=3.9 要求" >&2
  exit 1
fi
echo "[run-bot] 使用 Python: $PY_VERSION_STR ($PYTHON_BIN)"

resolve_config_root() {
  local raw=""
  if [[ -n "${MASTER_CONFIG_ROOT:-}" ]]; then
    raw="$MASTER_CONFIG_ROOT"
  elif [[ -n "${VIBEGO_CONFIG_DIR:-}" ]]; then
    raw="$VIBEGO_CONFIG_DIR"
  elif [[ -n "${XDG_CONFIG_HOME:-}" ]]; then
    raw="${XDG_CONFIG_HOME%/}/vibego"
  else
    raw="$HOME/.config/vibego"
  fi
  if [[ "$raw" == ~* ]]; then
    printf '%s' "${raw/#\~/$HOME}"
  else
    printf '%s' "$raw"
  fi
}

CONFIG_ROOT="$(resolve_config_root)"
RUNTIME_ROOT="${VIBEGO_RUNTIME_ROOT:-$CONFIG_ROOT/runtime}"
VENV_DIR="$RUNTIME_ROOT/.venv"
MODELS_DIR="$SOURCE_ROOT/scripts/models"
LOG_ROOT="${LOG_ROOT:-$CONFIG_ROOT/logs}"
DEFAULT_LOG_FILE="$LOG_ROOT/run_bot.log"
LOG_FILE="${LOG_FILE:-$DEFAULT_LOG_FILE}"
MODEL_DEFAULT="${MODEL_DEFAULT:-codex}"
PROJECT_DEFAULT="${PROJECT_NAME:-}"

usage() {
  cat <<USAGE
用法：${0##*/} [--model 名称] [--project 名称] [--foreground] [--no-stop]
  --model        启动指定模型 (codex|claudecode|gemini)，默认: $MODEL_DEFAULT
  --project      指定项目别名，用于日志/会话区分
  --foreground   在前台运行（调试用），默认后台
  --no-stop      启动前不执行 stop_bot.sh（默认会先停旧实例）
USAGE
}

MODEL="$MODEL_DEFAULT"
PROJECT_OVERRIDE="$PROJECT_DEFAULT"
FOREGROUND=0
NO_STOP=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --model)
      MODEL="$2"; shift 2 ;;
    --project)
      PROJECT_OVERRIDE="$2"; shift 2 ;;
    --foreground)
      FOREGROUND=1; shift ;;
    --no-stop)
      NO_STOP=1; shift ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "未知参数: $1" >&2
      usage
      exit 1 ;;
  esac
done

MODEL_SCRIPT="$MODELS_DIR/$MODEL.sh"
if [[ ! -f "$MODEL_SCRIPT" ]]; then
  echo "[run-bot] 不支持的模型: $MODEL" >&2
  exit 1
fi

# 加载公共函数 + 模型配置
# shellcheck disable=SC1090
source "$MODELS_DIR/common.sh"
# 允许模型脚本重用环境变量
MODEL_WORKDIR="${MODEL_WORKDIR:-}" 
# shellcheck disable=SC1090
source "$MODEL_SCRIPT"
model_configure

if [[ -z "$PROJECT_OVERRIDE" ]]; then
  PROJECT_NAME="$(project_slug_from_workdir "$MODEL_WORKDIR")"
else
  PROJECT_NAME="$(sanitize_slug "$PROJECT_OVERRIDE")"
fi
LOG_DIR="$(log_dir_for "$MODEL" "$PROJECT_NAME")"
MODEL_LOG="$LOG_DIR/model.log"
RUN_LOG="$LOG_DIR/run_bot.log"
POINTER_FILE="$LOG_DIR/${MODEL_POINTER_BASENAME:-current_session.txt}"
TMUX_SESSION="$(tmux_session_for "$PROJECT_NAME")"
PID_FILE="$LOG_DIR/bot.pid"
LOCK_FILE="$LOG_DIR/${MODEL_LOCK_BASENAME:-worker.lock}"

expand_model_workdir() {
  local path="$1"
  if [[ "$path" == ~* ]]; then
    printf '%s' "${path/#\~/$HOME}"
  else
    printf '%s' "$path"
  fi
}

MODEL_WORKDIR="$(expand_model_workdir "$MODEL_WORKDIR")"

if [[ -z "$MODEL_WORKDIR" ]]; then
  echo "[run-bot] 配置缺少 MODEL_WORKDIR，请检查 config/projects.json" >&2
  exit 1
fi

if [[ ! -d "$MODEL_WORKDIR" ]]; then
  echo "[run-bot] 工作目录不存在: $MODEL_WORKDIR" >&2
  exit 1
fi

# 优先使用包根目录的 AGENTS.md，若缺失则回退到虚拟环境根目录（pipx 安装时 data_files 会落在此处）
DEFAULT_AGENTS_TEMPLATE="$SOURCE_ROOT/AGENTS.md"
if [[ ! -f "$DEFAULT_AGENTS_TEMPLATE" ]]; then
  VENV_ROOT_FROM_SOURCE="$(cd "$SOURCE_ROOT/../../.." && pwd)"
  CANDIDATE_VENV_TEMPLATE="$VENV_ROOT_FROM_SOURCE/AGENTS.md"
  if [[ -f "$CANDIDATE_VENV_TEMPLATE" ]]; then
    DEFAULT_AGENTS_TEMPLATE="$CANDIDATE_VENV_TEMPLATE"
  elif [[ -n "${VIRTUAL_ENV:-}" && -f "$VIRTUAL_ENV/AGENTS.md" ]]; then
    DEFAULT_AGENTS_TEMPLATE="$VIRTUAL_ENV/AGENTS.md"
  fi
fi
AGENTS_TEMPLATE_FILE="${VIBEGO_AGENTS_TEMPLATE:-$DEFAULT_AGENTS_TEMPLATE}"
if [[ ! -f "$AGENTS_TEMPLATE_FILE" ]]; then
  echo "[run-bot] 未找到 AGENTS 模板文件: $AGENTS_TEMPLATE_FILE" >&2
  exit 1
fi
if ! sync_vibego_agents_for_model "$MODEL" "$AGENTS_TEMPLATE_FILE"; then
  echo "[run-bot] 同步 AGENTS 模板失败，已终止启动。" >&2
  exit 1
fi
# 额外同步一份到 ~/.config/vibego/AGENTS.md，供所有模型/提示语统一引用（避免写死用户名路径）
ENFORCED_AGENTS_FILE="$HOME/.config/vibego/AGENTS.md"
if ! sync_agents_block "$ENFORCED_AGENTS_FILE" "$AGENTS_TEMPLATE_FILE"; then
  echo "[run-bot] 同步统一 AGENTS 文件失败: $ENFORCED_AGENTS_FILE" >&2
  exit 1
fi
export VIBEGO_AGENTS_SYNCED=1
export VIBEGO_AGENTS_TEMPLATE="$AGENTS_TEMPLATE_FILE"

if ! command -v tmux >/dev/null 2>&1; then
  echo "[run-bot] 未检测到 tmux，可通过 'brew install tmux' 安装" >&2
  exit 1
fi

if [[ -n "$MODEL_CMD" ]]; then
  IFS=' ' read -r MODEL_CMD_BIN _ <<<"$MODEL_CMD"
  if [[ -n "$MODEL_CMD_BIN" ]] && ! command -v "$MODEL_CMD_BIN" >/dev/null 2>&1; then
    echo "[run-bot] 未检测到模型命令: $MODEL_CMD_BIN" >&2
    exit 1
  fi
fi

detect_foreign_vibego_processes() {
  if ! command -v ps >/dev/null 2>&1; then
    return
  fi
  local self_root="$SOURCE_ROOT"
  local matches=()
  while IFS= read -r entry; do
    [[ -z "$entry" ]] && continue
    local pid="${entry%% *}"
    local cmd="${entry#* }"
    [[ "$cmd" == *"bot.py"* || "$cmd" == *"master.py"* || "$cmd" == *"session_binder.py"* ]] || continue
    if [[ "$cmd" == *"$self_root/"* ]]; then
      continue
    fi
    matches+=("$pid|$cmd")
  done < <(ps -Ao pid=,args= 2>/dev/null | grep -i "vibego" || true)
  if (( ${#matches[@]} )); then
    echo "[run-bot] 检测到其他 vibego 进程正在运行，启动已被阻止，请先停止以下进程：" >&2
    for item in "${matches[@]}"; do
      local pid="${item%%|*}"
      local cmd="${item#*|}"
      echo "  - pid=${pid} cmd=${cmd}" >&2
    done
    echo "可使用 'vibego stop' 或 ./scripts/stop_bot.sh 清理所有进程后重试。" >&2
    exit 1
  fi
}

detect_foreign_vibego_processes

ensure_dir "$LOG_DIR"

if command -v flock >/dev/null 2>&1; then
  if ! exec 200>"$LOCK_FILE"; then
    echo "[run-bot] 无法创建锁文件 $LOCK_FILE" >&2
    exit 1
  fi
  if ! flock -n 200; then
    echo "[run-bot] 项目 $PROJECT_NAME 已有 worker 在运行（锁文件：$LOCK_FILE）。如需重启，请先执行 ./scripts/stop_bot.sh --model $MODEL --project $PROJECT_NAME 或 vibego stop。" >&2
    exit 1
  fi
fi

if [[ -f "$PID_FILE" ]]; then
  existing_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "$existing_pid" ]] && ps -p "$existing_pid" >/dev/null 2>&1; then
    echo "[run-bot] 检测到项目 $PROJECT_NAME 现有 worker (pid=$existing_pid)。请先执行 ./scripts/stop_bot.sh --model $MODEL --project $PROJECT_NAME 或 vibego stop 再重试。" >&2
    exit 1
  fi
  rm -f "$PID_FILE"
fi

if (( FOREGROUND == 0 )); then
  mkdir -p "$LOG_DIR"
  CMD=("$0" --model "$MODEL" --project "$PROJECT_NAME" --foreground)
  (( NO_STOP )) && CMD+=(--no-stop)
  nohup "${CMD[@]}" >>"$RUN_LOG" 2>&1 &
  echo "[run-bot] 后台启动 (model=$MODEL project=$PROJECT_NAME) 日志: $RUN_LOG"
  exit 0
fi

if (( NO_STOP == 0 )); then
  "$SOURCE_ROOT/scripts/stop_bot.sh" --model "$MODEL" --project "$PROJECT_NAME" >/dev/null 2>&1 || true
fi

mkdir -p "$RUNTIME_ROOT"

if [[ -n "${VIBEGO_WORKER_BOOT_ID:-}" ]]; then
  # master 会注入 boot_id，用于健康检查在追加日志场景下避免误判旧握手日志。
  echo "[run-bot] boot_id=$VIBEGO_WORKER_BOOT_ID"
fi

ensure_runtime_venv() {
  # 运行时虚拟环境可能因 Homebrew 升级 Python 导致解释器断链（例如指向旧的 Cellar 路径），
  # 此时继续使用会触发 "exec: python: not found"；最稳妥的策略是直接重建 venv。
  if [[ ! -d "$VENV_DIR" ]]; then
    "$PYTHON_BIN" -m venv "$VENV_DIR"
    return 0
  fi

  local venv_python="$VENV_DIR/bin/python"
  local venv_pip="$VENV_DIR/bin/pip"

  if [[ ! -x "$venv_python" ]]; then
    echo "[run-bot] 检测到运行时 venv 异常：python 不可用，将自动重建：$venv_python" >&2
    rm -rf "$VENV_DIR"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
    return 0
  fi

  if ! "$venv_python" -V >/dev/null 2>&1; then
    echo "[run-bot] 检测到运行时 venv 异常：python 无法执行，将自动重建：$venv_python" >&2
    rm -rf "$VENV_DIR"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
    return 0
  fi

  if [[ ! -x "$venv_pip" ]] || ! "$venv_python" -m pip --version >/dev/null 2>&1; then
    echo "[run-bot] 检测到运行时 venv 异常：pip 不可用，将自动重建：$venv_pip" >&2
    rm -rf "$VENV_DIR"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
    return 0
  fi
}

ensure_runtime_venv
VENV_PYTHON="$VENV_DIR/bin/python"
export VIRTUAL_ENV="$VENV_DIR"
REQUIREMENTS_PATH="${VIBEGO_REQUIREMENTS_PATH:-$SOURCE_ROOT/scripts/requirements.txt}"
if [[ ! -f "$REQUIREMENTS_PATH" ]]; then
  echo "[run-bot] 未找到依赖清单: $REQUIREMENTS_PATH" >&2
  exit 1
fi
if [[ ! -f "$VENV_DIR/.requirements.installed" ]] || [[ "$REQUIREMENTS_PATH" -nt "$VENV_DIR/.requirements.installed" ]]; then
  "$VENV_PYTHON" -m pip install -r "$REQUIREMENTS_PATH" >/dev/null
  touch "$VENV_DIR/.requirements.installed"
fi

export LOG_FILE="$LOG_FILE"
export ACTIVE_MODEL="$MODEL"
export MODEL_NAME="$MODEL"
export MODEL_WORKDIR
export MODEL_CMD
export MODEL_SESSION_ROOT
export MODEL_SESSION_GLOB
export SESSION_POINTER_FILE="$POINTER_FILE"
export CODEX_SESSION_FILE_PATH="$POINTER_FILE"
SESSION_ACTIVE_ID_FILE="${SESSION_ACTIVE_ID_FILE:-$LOG_DIR/active_session_id.txt}"
export SESSION_ACTIVE_ID_FILE
SESSION_BINDER_LOG="${SESSION_BINDER_LOG:-$LOG_DIR/session_binder.log}"
export SESSION_BINDER_LOG
export TMUX_SESSION="$TMUX_SESSION"
export TMUX_LOG="$MODEL_LOG"
export PROJECT_NAME="$PROJECT_NAME"
export LOG_DIR="$LOG_DIR"
export ROOT_DIR="$SOURCE_ROOT"
export SESSION_BIND_STRICT="${SESSION_BIND_STRICT:-1}"
export SESSION_BIND_TIMEOUT_SECONDS="${SESSION_BIND_TIMEOUT_SECONDS:-30}"
export SESSION_BIND_POLL_INTERVAL="${SESSION_BIND_POLL_INTERVAL:-0.5}"
export SESSION_BINDER_POLL_INTERVAL="${SESSION_BINDER_POLL_INTERVAL:-0.5}"
export SESSION_BINDER_TIMEOUT="${SESSION_BINDER_TIMEOUT:-600}"
if [[ -n "${CLAUDE_CODE_DISABLE_FILE_CHECKPOINTING:-}" ]]; then
  export CLAUDE_CODE_DISABLE_FILE_CHECKPOINTING
fi

"$SOURCE_ROOT/scripts/start_tmux_codex.sh" --kill >/dev/null

echo $$ > "$PID_FILE"
exec "$VENV_PYTHON" "$SOURCE_ROOT/bot.py"
