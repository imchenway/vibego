#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MASTER_CONFIG_ROOT="${MASTER_CONFIG_ROOT:-$HOME/.config/vibego}"
STATE_DIR="$MASTER_CONFIG_ROOT/state"
LOCK_FILE="$STATE_DIR/master_restart.lock"
RESTART_SIGNAL="$STATE_DIR/restart_signal.json"
LEGACY_STATE_DIR="$ROOT_DIR/state"
LEGACY_LOCK_FILE="$LEGACY_STATE_DIR/master_restart.lock"
LEGACY_RESTART_SIGNAL="$LEGACY_STATE_DIR/restart_signal.json"
DEFAULT_TMUX_PREFIX="${TMUX_SESSION_PREFIX:-vibe}"
STOP_BOT_SCRIPT="$ROOT_DIR/scripts/stop_bot.sh"

DRY_RUN=0
VERBOSE=0

usage() {
  cat <<USAGE
用法：${0##*/} [--dry-run] [--verbose]
  --dry-run    仅打印将要执行的操作，不真正终止进程
  --verbose    输出更详细的诊断信息
  -h, --help   显示本帮助
USAGE
}

log_line() {
  local level="$1"; shift
  local ts
  ts=$(date '+%Y-%m-%d %H:%M:%S%z')
  printf '[%s] [%s] %s\n' "$ts" "$level" "$*"
}

log_info() { log_line INFO "$@"; }
log_warn() { log_line WARN "$@" >&2; }
log_error() { log_line ERROR "$@" >&2; }

run_or_echo() {
  if (( DRY_RUN )); then
    log_info "[dry-run] $*"
  else
    "$@"
  fi
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run)
        DRY_RUN=1; shift ;;
      --verbose)
        VERBOSE=1; shift ;;
      -h|--help)
        usage; exit 0 ;;
      *)
        log_error "未知参数: $1"
        usage
        exit 1 ;;
    esac
  done
}

list_pids() {
  local pattern="$1"
  pgrep -f "$pattern" 2>/dev/null || true
}

terminate_pattern() {
  local pattern="$1" desc="$2"
  local pids
  pids=$(list_pids "$pattern")
  if [[ -z "$pids" ]]; then
    (( VERBOSE )) && log_info "未发现 $desc 进程"
    return 0
  fi
  log_info "停止 $desc: $pids"
  if (( DRY_RUN )); then
    return 0
  fi
  kill $pids >/dev/null 2>&1 || true
  sleep 1
  local leftover
  leftover=$(list_pids "$pattern")
  if [[ -n "$leftover" ]]; then
    log_warn "$desc 未完全退出，执行 kill -9 $leftover"
    kill -9 $leftover >/dev/null 2>&1 || true
  fi
}

stop_workers() {
  if [[ ! -x "$STOP_BOT_SCRIPT" ]]; then
    log_warn "未找到 stop_bot.sh，跳过 worker 清理"
    return 0
  fi
  if (( DRY_RUN )); then
    log_info "[dry-run] 调用 stop_bot.sh 清理所有 worker"
    return 0
  fi
  if "$STOP_BOT_SCRIPT" >/dev/null 2>&1; then
    log_info "worker 已停止"
  else
    log_warn "stop_bot.sh 返回非零状态，请检查日志"
  fi
  return 0
}

stop_tmux_sessions() {
  if ! command -v tmux >/dev/null 2>&1; then
    (( VERBOSE )) && log_info "未检测到 tmux，跳过会话清理"
    return 0
  fi
  local prefix="$DEFAULT_TMUX_PREFIX"
  [[ -z "$prefix" ]] && prefix="vibe"
  local full_prefix
  if [[ "$prefix" == *- ]]; then
    full_prefix="$prefix"
  else
    full_prefix="${prefix}-"
  fi
  local tmux_output sessions
  tmux_output=$(tmux -u list-sessions 2>/dev/null || true)
  if [[ -z "$tmux_output" ]]; then
    (( VERBOSE )) && log_info "未发现匹配 tmux 会话"
    return 0
  fi
  sessions=$(printf '%s\n' "$tmux_output" | awk -F: -v prefix="$full_prefix" '$1 ~ "^" prefix {print $1}')
  if [[ -z "$sessions" ]]; then
    (( VERBOSE )) && log_info "未发现匹配 tmux 会话"
    return 0
  fi
  while IFS= read -r sess; do
    [[ -z "$sess" ]] && continue
    if (( DRY_RUN )); then
      log_info "[dry-run] tmux -u kill-session -t $sess"
    else
      tmux -u kill-session -t "$sess" >/dev/null 2>&1 || true
      log_info "已终止 tmux 会话: $sess"
    fi
  done <<<"$sessions"
  return 0
}

cleanup_state_files() {
  local removed=0
  for file in "$LOCK_FILE" "$RESTART_SIGNAL" "$LEGACY_LOCK_FILE" "$LEGACY_RESTART_SIGNAL"; do
    if [[ -e "$file" ]]; then
      if (( DRY_RUN )); then
        log_info "[dry-run] rm -f $file"
      else
        rm -f "$file"
      fi
      removed=1
      log_info "已清理状态文件: $file"
    fi
  done
  if (( ! removed )) && (( VERBOSE )); then
    log_info "无状态文件需要清理"
  fi
}

main() {
  parse_args "$@"
  (( DRY_RUN )) && log_info "当前为 dry-run 模式，不会真正结束进程"

  stop_workers
  stop_tmux_sessions
  terminate_pattern "[Pp]ython.*master.py" "master"
  terminate_pattern "$ROOT_DIR/start.sh" "start.sh"
  terminate_pattern "$ROOT_DIR/bot.py" "残留 worker"
  cleanup_state_files

  log_info "所有相关进程与状态已处理完成"
}

main "$@"
