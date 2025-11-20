#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODELS_DIR="$ROOT_DIR/scripts/models"

# shellcheck disable=SC1090
source "$MODELS_DIR/common.sh"

SESSION_NAME="${TMUX_SESSION:-vibe}"
LOG_PATH="${TMUX_LOG:-$ROOT_DIR/logs/${MODEL_NAME:-codex}/${PROJECT_NAME:-project}/model.log}"
LOG_WRITER="${LOG_WRITER:-$ROOT_DIR/scripts/log_writer.py}"
PYTHON_EXEC="${PYTHON_EXEC:-python3}"
MODEL_LOG_MAX_BYTES="${MODEL_LOG_MAX_BYTES:-20971520}"
MODEL_LOG_RETENTION_SECONDS="${MODEL_LOG_RETENTION_SECONDS:-86400}"
MODEL_CMD="${MODEL_CMD:-${CODEX_CMD:-codex --dangerously-bypass-approvals-and-sandbox -c trusted_workspace=true}}"
MODEL_WORKDIR="${MODEL_WORKDIR:-$ROOT_DIR}"
MODEL_SESSION_ROOT="${MODEL_SESSION_ROOT:-${CODEX_SESSION_ROOT:-$HOME/.codex/sessions}}"
MODEL_SESSION_GLOB="${MODEL_SESSION_GLOB:-rollout-*.jsonl}"
SESSION_POINTER_FILE="${SESSION_POINTER_FILE:-$LOG_ROOT/${MODEL_NAME:-codex}/${PROJECT_NAME:-project}/current_session.txt}"
SESSION_ACTIVE_ID_FILE="${SESSION_ACTIVE_ID_FILE:-$(dirname "${SESSION_POINTER_FILE}")/active_session_id.txt}"
SESSION_BINDER="${SESSION_BINDER:-$ROOT_DIR/scripts/session_binder.py}"
SESSION_BINDER_POLL_INTERVAL="${SESSION_BINDER_POLL_INTERVAL:-0.5}"
SESSION_BINDER_TIMEOUT="${SESSION_BINDER_TIMEOUT:-600}"
SESSION_BINDER_LOG="${SESSION_BINDER_LOG:-$(dirname "${SESSION_POINTER_FILE}")/session_binder.log}"

# 避免 oh-my-zsh 在非交互环境弹出更新提示
export DISABLE_UPDATE_PROMPT="${DISABLE_UPDATE_PROMPT:-true}"

expand_path() {
  local path="$1"
  if [[ -z "$path" ]]; then
    return
  fi
  if [[ "$path" == ~* ]]; then
    path="${path/#\~/$HOME}"
  fi
  printf '%s' "$path"
}

DRY_RUN=0
RESTART=0
FORCE_START=0
KILL_SESSION=0

usage() {
  cat <<USAGE
用法：${0##*/} [--dry-run] [--force] [--restart] [--kill]
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --force) FORCE_START=1 ;;
    --restart) RESTART=1; FORCE_START=1 ;;
    --kill) KILL_SESSION=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数: $1" >&2; usage; exit 1 ;;
  esac
  shift
done

if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux 未安装" >&2
  exit 1
fi

LOG_PATH=$(expand_path "$LOG_PATH")
MODEL_WORKDIR=$(expand_path "$MODEL_WORKDIR")
MODEL_SESSION_ROOT=$(expand_path "$MODEL_SESSION_ROOT")
SESSION_POINTER_FILE=$(expand_path "$SESSION_POINTER_FILE")
ensure_dir "$(dirname "$LOG_PATH")"
ensure_dir "$(dirname "$SESSION_POINTER_FILE")"
ensure_dir "$(dirname "$SESSION_ACTIVE_ID_FILE")"
ensure_dir "$(dirname "$SESSION_BINDER_LOG")"

if [[ "${VIBEGO_AGENTS_SYNCED:-0}" != "1" ]]; then
  AGENTS_TEMPLATE_FILE="${VIBEGO_AGENTS_TEMPLATE:-$ROOT_DIR/AGENTS.md}"
  if [[ ! -f "$AGENTS_TEMPLATE_FILE" ]]; then
    echo "[start-tmux] 未找到 AGENTS 模板文件: $AGENTS_TEMPLATE_FILE" >&2
    exit 1
  fi
  if ! sync_vibego_agents_for_model "${MODEL_NAME:-codex}" "$AGENTS_TEMPLATE_FILE"; then
    echo "[start-tmux] 同步 AGENTS 模板失败。" >&2
    exit 1
  fi
  export VIBEGO_AGENTS_SYNCED=1
  export VIBEGO_AGENTS_TEMPLATE="$AGENTS_TEMPLATE_FILE"
fi

run_tmux() {
  if (( DRY_RUN )); then
    printf '[dry-run] tmux -u %s\n' "$*"
  else
    tmux -u "$@"
  fi
}

SESSION_CREATED=0
if (( KILL_SESSION )); then
  if (( DRY_RUN )); then
    printf '[dry-run] tmux -u kill-session -t %s\n' "$SESSION_NAME"
  else
    tmux -u kill-session -t "$SESSION_NAME" >/dev/null 2>&1 || true
  fi
fi

if ! tmux -u has-session -t "$SESSION_NAME" >/dev/null 2>&1; then
  run_tmux new-session -d -s "$SESSION_NAME" -c "$MODEL_WORKDIR"
  SESSION_CREATED=1
else
  if (( ! DRY_RUN )); then
    CURRENT_PATH=$(tmux -u display-message -p -t "$SESSION_NAME":0 '#{pane_current_path}' 2>/dev/null || echo "")
    if [[ "$CURRENT_PATH" != "$MODEL_WORKDIR" ]]; then
      run_tmux send-keys -t "$SESSION_NAME" "cd" Space "$MODEL_WORKDIR" C-m
      sleep 0.2
    fi
  fi
fi

# 启动前先进行一次清理，避免旧日志超限
if ! env \
  MODEL_LOG_MAX_BYTES="$MODEL_LOG_MAX_BYTES" \
  MODEL_LOG_RETENTION_SECONDS="$MODEL_LOG_RETENTION_SECONDS" \
  "$PYTHON_EXEC" "$LOG_WRITER" "$LOG_PATH" </dev/null; then
  echo "预处理日志文件失败" >&2
  exit 1
fi

printf -v PIPE_CMD 'env MODEL_LOG_MAX_BYTES=%q MODEL_LOG_RETENTION_SECONDS=%q %q %q %q' \
  "$MODEL_LOG_MAX_BYTES" \
  "$MODEL_LOG_RETENTION_SECONDS" \
  "$PYTHON_EXEC" \
  "$LOG_WRITER" \
  "$LOG_PATH"
run_tmux pipe-pane -o -t "$SESSION_NAME" "$PIPE_CMD"

# 同步环境变量到 tmux 服务端，避免复用旧会话时丢失设置
run_tmux set-environment -t "$SESSION_NAME" DISABLE_UPDATE_PROMPT "${DISABLE_UPDATE_PROMPT:-true}"
if [[ -n "${CLAUDE_CODE_DISABLE_FILE_CHECKPOINTING:-}" ]]; then
  run_tmux set-environment -t "$SESSION_NAME" CLAUDE_CODE_DISABLE_FILE_CHECKPOINTING "${CLAUDE_CODE_DISABLE_FILE_CHECKPOINTING}"
fi

if (( RESTART )); then
  run_tmux send-keys -t "$SESSION_NAME" C-c
  sleep 1
fi

env_prefix="env $(printf '%q' "DISABLE_UPDATE_PROMPT=${DISABLE_UPDATE_PROMPT:-true}")"
if [[ -n "${CLAUDE_CODE_DISABLE_FILE_CHECKPOINTING:-}" ]]; then
  env_prefix+=" $(printf '%q' "CLAUDE_CODE_DISABLE_FILE_CHECKPOINTING=${CLAUDE_CODE_DISABLE_FILE_CHECKPOINTING}")"
fi
printf -v FINAL_CMD '%s %s' "$env_prefix" "$MODEL_CMD"

if (( SESSION_CREATED )) || (( FORCE_START )); then
  run_tmux send-keys -t "$SESSION_NAME" "$FINAL_CMD" C-m
fi


if (( DRY_RUN )); then
  printf '[dry-run] 会话日志路径: %s\n' "$SESSION_POINTER_FILE"
  exit 0
fi

: > "$SESSION_POINTER_FILE"
: > "$SESSION_ACTIVE_ID_FILE"

if [[ -n "$SESSION_BINDER" ]] && [[ -f "$SESSION_BINDER" ]]; then
  BIND_BOOT_TS="$("$PYTHON_EXEC" - <<'PY'
import time
print(int(time.time() * 1000))
PY
)"
  BINDER_CMD=(
    "$PYTHON_EXEC" "$SESSION_BINDER"
    --pointer "$SESSION_POINTER_FILE"
    --glob "$MODEL_SESSION_GLOB"
    --boot-ts-ms "$BIND_BOOT_TS"
    --poll-interval "$SESSION_BINDER_POLL_INTERVAL"
    --timeout "$SESSION_BINDER_TIMEOUT"
  )
  if [[ -n "$MODEL_SESSION_ROOT" ]]; then
    BINDER_CMD+=(--session-root "$MODEL_SESSION_ROOT")
  fi
  if [[ -n "${CODEX_SESSION_ROOT:-}" ]]; then
    BINDER_CMD+=(--session-root "$CODEX_SESSION_ROOT")
  fi
  BINDER_CMD+=(--session-root "$(dirname "$SESSION_POINTER_FILE")")
  BINDER_CMD+=(--session-root "$(dirname "$SESSION_POINTER_FILE")/sessions")
  if [[ -n "$MODEL_WORKDIR" ]]; then
    BINDER_CMD+=(--cwd "$MODEL_WORKDIR")
  fi
  if [[ -n "$SESSION_ACTIVE_ID_FILE" ]]; then
    BINDER_CMD+=(--session-id-file "$SESSION_ACTIVE_ID_FILE")
  fi
  if [[ -n "$SESSION_BINDER_LOG" ]]; then
    BINDER_CMD+=(--log "$SESSION_BINDER_LOG")
  fi
  nohup "${BINDER_CMD[@]}" >>"$SESSION_BINDER_LOG" 2>&1 &
else
  echo "[start-tmux] session binder 未找到：$SESSION_BINDER" >&2
fi

exit 0
