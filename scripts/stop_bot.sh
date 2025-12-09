#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC2155
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

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
LOG_ROOT="${LOG_ROOT:-$CONFIG_ROOT/logs}"
MODELS_DIR="$ROOT_DIR/scripts/models"
MODEL_DEFAULT="${MODEL_DEFAULT:-codex}"
PROJECT_DEFAULT="${PROJECT_NAME:-}"

usage() {
  cat <<USAGE
用法：${0##*/} [--model 名称] [--project 名称]
  --model    目标模型，默认 $MODEL_DEFAULT
  --project  项目别名；未指定时尝试使用当前目录配置
  --all      显式全局清理（包含 kill_all_vibego_processes）
USAGE
}

MODEL="$MODEL_DEFAULT"
PROJECT_OVERRIDE="$PROJECT_DEFAULT"
FORCE_KILL_ALL=0
# 允许通过环境变量强制全局清理（兼容旧版兜底行为）
if [[ "${VIBEGO_FORCE_KILL_ALL:-0}" == "1" ]]; then
  FORCE_KILL_ALL=1
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --model)
      MODEL="$2"; shift 2 ;;
    --project)
      PROJECT_OVERRIDE="$2"; shift 2 ;;
    --all)
      FORCE_KILL_ALL=1; shift ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "未知参数: $1" >&2
      usage
      exit 1 ;;
  esac
done

# shellcheck disable=SC1090
source "$MODELS_DIR/common.sh"

MODEL_SCRIPT="$MODELS_DIR/$MODEL.sh"
if [[ -f "$MODEL_SCRIPT" ]]; then
  # shellcheck disable=SC1090
  source "$MODEL_SCRIPT"
  if declare -f model_configure >/dev/null 2>&1; then
    model_configure
  fi
fi

POINTER_BASENAME="${MODEL_POINTER_BASENAME:-current_session.txt}"
ACTIVE_SESSION_BASENAME="${MODEL_ACTIVE_SESSION_BASENAME:-active_session_id.txt}"
LOCK_BASENAME="${MODEL_LOCK_BASENAME:-worker.lock}"

kill_all_vibego_processes() {
  if ! command -v ps >/dev/null 2>&1; then
    return
  fi
  local targets=()
  while IFS= read -r entry; do
    [[ -z "$entry" ]] && continue
    local pid="${entry%% *}"
    local cmd="${entry#* }"
    [[ "$cmd" == *"vibego"* ]] || continue
    if [[ "$cmd" == *"bot.py"* || "$cmd" == *"master.py"* || "$cmd" == *"session_binder.py"* || "$cmd" == *"log_writer.py"* ]]; then
      targets+=("$pid")
    fi
  done < <(ps -Ao pid=,args= 2>/dev/null || true)
  for pid in "${targets[@]}"; do
    kill "$pid" >/dev/null 2>&1 || true
  done
  for pid in "${targets[@]}"; do
    if ps -p "$pid" >/dev/null 2>&1; then
      sleep 0.5
      kill -9 "$pid" >/dev/null 2>&1 || true
    fi
  done
}

graceful_shutdown_claudecode() {
  local session="$1"
  local timeout="${2:-10}"
  if ! command -v tmux >/dev/null 2>&1; then
    return 0
  fi
  if ! tmux -u has-session -t "$session" >/dev/null 2>&1; then
    return 0
  fi
  local pane_ids=()
  while IFS= read -r pane; do
    [[ -z "$pane" ]] && continue
    pane_ids+=("$pane")
  done < <(tmux -u list-panes -t "$session" -F "#{pane_id}" 2>/dev/null || true)
  (( ${#pane_ids[@]} )) || return 0

  local current_cmd has_claude=0
  for pane in "${pane_ids[@]}"; do
    current_cmd=$(tmux -u display-message -p -t "$pane" '#{pane_current_command}' 2>/dev/null || echo "")
    if [[ "$current_cmd" == claude* ]]; then
      has_claude=1
      break
    fi
  done
  (( has_claude )) || return 0

  printf '[stop-bot] 检测到 ClaudeCode 会话，尝试发送 /exit (session=%s)\n' "$session"
  for pane in "${pane_ids[@]}"; do
    tmux -u send-keys -t "$pane" Escape
    tmux -u send-keys -t "$pane" C-u
    tmux -u send-keys -t "$pane" "/exit" C-m
  done

  local end_time=$(( $(date +%s) + timeout ))
  while tmux -u has-session -t "$session" >/dev/null 2>&1; do
    local still_running=0
    for pane in "${pane_ids[@]}"; do
      current_cmd=$(tmux -u display-message -p -t "$pane" '#{pane_current_command}' 2>/dev/null || echo "")
      if [[ "$current_cmd" == claude* ]]; then
        still_running=1
        break
      fi
    done
    if (( still_running == 0 )); then
      printf '[stop-bot] ClaudeCode 会话已响应 /exit (session=%s)\n' "$session"
      break
    fi
    if (( $(date +%s) >= end_time )); then
      printf '[stop-bot] ClaudeCode /exit 超时，将继续执行强制关闭 (session=%s)\n' "$session" >&2
      break
    fi
    sleep 0.5
  done
  return 0
}

kill_tty_sessions() {
  local session="$1"
  if command -v tmux >/dev/null 2>&1 && tmux -u has-session -t "$session" >/dev/null 2>&1; then
    tmux -u kill-session -t "$session" >/dev/null 2>&1 || true
  fi
}

clear_session_files() {
  local log_dir="$1"
  rm -f "$log_dir/$POINTER_BASENAME" "$log_dir/$ACTIVE_SESSION_BASENAME" "$log_dir/$LOCK_BASENAME"
}

kill_pid_file() {
  local pid_file="$1"
  if [[ ! -f "$pid_file" ]]; then
    local fallback_dir
    fallback_dir="$(dirname "$pid_file")"
    if [[ -d "$fallback_dir" ]]; then
      clear_session_files "$fallback_dir"
    fi
    return 0
  fi
  local bot_pid
  bot_pid=$(cat "$pid_file")
  if [[ -n "$bot_pid" ]] && ps -p "$bot_pid" >/dev/null 2>&1; then
    kill "$bot_pid" >/dev/null 2>&1 || true
    sleep 0.5
    if ps -p "$bot_pid" >/dev/null 2>&1; then
      kill -9 "$bot_pid" >/dev/null 2>&1 || true
    fi
  fi
  rm -f "$pid_file"
  local pid_dir
  pid_dir="$(dirname "$pid_file")"
  if [[ -d "$pid_dir" ]]; then
    clear_session_files "$pid_dir"
  fi
  return 0
}

stop_single_worker() {
  local project_name="$1" model_name="$2"
  local log_dir pid_file tmux_session
  log_dir="$(log_dir_for "$model_name" "$project_name")"
  pid_file="$log_dir/bot.pid"
  tmux_session="$(tmux_session_for "$project_name")"
  graceful_shutdown_claudecode "$tmux_session" 15 || true
  kill_tty_sessions "$tmux_session"
  kill_pid_file "$pid_file"
  clear_session_files "$log_dir"
  return 0
}

stop_all_workers() {
  local stopped=0
  if command -v tmux >/dev/null 2>&1; then
    local prefix="$TMUX_SESSION_PREFIX"
    [[ -z "$prefix" ]] && prefix="vibe"
    local full_prefix
    if [[ "$prefix" == *- ]]; then
      full_prefix="$prefix"
    else
      full_prefix="${prefix}-"
    fi
    local sessions
    sessions=$(tmux -u list-sessions 2>/dev/null | awk -F: -v prefix="$full_prefix" '$1 ~ "^" prefix {print $1}')
    if [[ -n "$sessions" ]]; then
      while IFS= read -r sess; do
        [[ -z "$sess" ]] && continue
        graceful_shutdown_claudecode "$sess" 15 || true
        tmux -u kill-session -t "$sess" >/dev/null 2>&1 || true
        stopped=1
      done <<<"$sessions"
    fi
  fi

  if [[ -d "$LOG_ROOT" ]]; then
    while IFS= read -r pid_file; do
      [[ -z "$pid_file" ]] && continue
      kill_pid_file "$pid_file"
      stopped=1
    done < <(find "$LOG_ROOT" -maxdepth 4 -type f -name "bot.pid" 2>/dev/null)
  fi
  return $stopped
}

if [[ -n "$PROJECT_OVERRIDE" ]]; then
  PROJECT_NAME="$(sanitize_slug "$PROJECT_OVERRIDE")"
  stop_single_worker "$PROJECT_NAME" "$MODEL"
else
  # 未指定项目时，执行逐项目停止；如需彻底清场需显式 --all 或环境变量开启
  if ! stop_all_workers; then
    # fallback:默认 project 名称
    stop_single_worker "project" "$MODEL"
  fi
fi

# 仅在显式请求时执行全局兜底清理，避免误杀其他项目的 worker/master
if (( FORCE_KILL_ALL )); then
  kill_all_vibego_processes
fi

exit 0
