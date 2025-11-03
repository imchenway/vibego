#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC2155
SOURCE_ROOT="${VIBEGO_PACKAGE_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

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

ensure_dir "$LOG_DIR"

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

if [[ ! -d "$VENV_DIR" ]]; then
  python3 -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
REQUIREMENTS_PATH="${VIBEGO_REQUIREMENTS_PATH:-$SOURCE_ROOT/scripts/requirements.txt}"
if [[ ! -f "$REQUIREMENTS_PATH" ]]; then
  echo "[run-bot] 未找到依赖清单: $REQUIREMENTS_PATH" >&2
  exit 1
fi
if [[ ! -f "$VENV_DIR/.requirements.installed" ]] || [[ "$REQUIREMENTS_PATH" -nt "$VENV_DIR/.requirements.installed" ]]; then
  pip install -r "$REQUIREMENTS_PATH" >/dev/null
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
export TMUX_SESSION="$TMUX_SESSION"
export TMUX_LOG="$MODEL_LOG"
export PROJECT_NAME="$PROJECT_NAME"
export LOG_DIR="$LOG_DIR"
export ROOT_DIR="$SOURCE_ROOT"
if [[ -n "${CLAUDE_CODE_DISABLE_FILE_CHECKPOINTING:-}" ]]; then
  export CLAUDE_CODE_DISABLE_FILE_CHECKPOINTING
fi

"$SOURCE_ROOT/scripts/start_tmux_codex.sh" --kill >/dev/null

echo $$ > "$LOG_DIR/bot.pid"
exec python "$SOURCE_ROOT/bot.py"
