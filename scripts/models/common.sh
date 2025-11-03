#!/usr/bin/env bash
# 公共工具：模型脚本/运行脚本/停止脚本共享

# 避免重复定义时覆盖
if [[ -n "${_MODEL_COMMON_LOADED:-}" ]]; then
  return
fi
_MODEL_COMMON_LOADED=1

COMMON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${ROOT_DIR:-$(cd "$COMMON_DIR/.." && pwd)}"
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

CONFIG_ROOT="${CONFIG_ROOT:-$(resolve_config_root)}"
LOG_ROOT="${LOG_ROOT:-$CONFIG_ROOT/logs}"
TMUX_SESSION_PREFIX="${TMUX_SESSION_PREFIX:-vibe}"

# 将任意路径/名称转换为 tmux/session 等安全的 slug
sanitize_slug() {
  local input="$1"
  if [[ -z "$input" ]]; then
    printf 'default'
    return
  fi
  local lower
  lower=$(printf '%s' "$input" | tr '[:upper:]' '[:lower:]')
  lower=$(printf '%s' "$lower" | tr ' /:\\@' '-----')
  lower=$(printf '%s' "$lower" | tr -cd 'a-z0-9_-')
  lower="${lower#-}"
  lower="${lower%-}"
  printf '%s' "${lower:-default}"
}

project_slug_from_workdir() {
  local path="$1"
  if [[ -z "$path" ]]; then
    printf 'project'
    return
  fi
  # 将绝对路径改写为与 Claude 类似的 -Users-... 形式
  local replaced
  replaced=$(printf '%s' "$path" | sed 's#/#-#g')
  replaced="${replaced#-}"
  printf '%s' "$(sanitize_slug "$replaced")"
}

log_dir_for() {
  local model="$1" project="$2"
  printf '%s/%s/%s' "$LOG_ROOT" "$model" "$project"
}

tmux_session_for() {
  local project="$1"
  printf '%s-%s' "$TMUX_SESSION_PREFIX" "$(sanitize_slug "$project")"
}

ensure_dir() {
  local dir="$1"
  mkdir -p "$dir"
}

file_mtime() {
  local file="$1"
  if command -v stat >/dev/null 2>&1; then
    if stat -f "%m" "$file" >/dev/null 2>&1; then
      stat -f "%m" "$file"
    elif stat -c "%Y" "$file" >/dev/null 2>&1; then
      stat -c "%Y" "$file"
    else
      printf '0'
    fi
  else
    printf '0'
  fi
}

find_latest_with_pattern() {
  local root="$1" pattern="$2"
  [[ -d "$root" ]] || return 0
  local latest=""
  local latest_mtime=0
  while IFS= read -r -d '' file; do
    local mtime
    mtime=$(file_mtime "$file")
    mtime=${mtime:-0}
    if (( mtime > latest_mtime )); then
      latest_mtime=$mtime
      latest="$file"
    fi
  done < <(find "$root" -type f -name "$pattern" -print0 2>/dev/null)
  [[ -n "$latest" ]] && printf '%s\n' "$latest"
}
