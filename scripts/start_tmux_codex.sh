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
CODEX_MODEL_INSTRUCTIONS_SOURCE_FILE="${CODEX_MODEL_INSTRUCTIONS_FILE:-/Users/david/.codex/AGENTS.md}"
CODEX_MODEL_INSTRUCTIONS_FILE="$CODEX_MODEL_INSTRUCTIONS_SOURCE_FILE"
CODEX_MODEL_INSTRUCTIONS_FILE_ESCAPED=""
CODEX_PROJECT_DOC_MAX_BYTES="${CODEX_PROJECT_DOC_MAX_BYTES:-131072}"
CODEX_GOALS_ENABLED="${CODEX_GOALS_ENABLED:-1}"
MODEL_KEY="$(printf '%s' "${MODEL_NAME:-codex}" | tr '[:upper:]' '[:lower:]')"
MODEL_RESUME_SESSION_ID="${MODEL_RESUME_SESSION_ID:-}"
MODEL_WORKDIR="${MODEL_WORKDIR:-$ROOT_DIR}"
MODEL_SESSION_ROOT="${MODEL_SESSION_ROOT:-${CODEX_SESSION_ROOT:-$HOME/.codex/sessions}}"
MODEL_SESSION_GLOB="${MODEL_SESSION_GLOB:-rollout-*.jsonl}"
SESSION_POINTER_FILE="${SESSION_POINTER_FILE:-$LOG_ROOT/${MODEL_NAME:-codex}/${PROJECT_NAME:-project}/current_session.txt}"
SESSION_ACTIVE_ID_FILE="${SESSION_ACTIVE_ID_FILE:-$(dirname "${SESSION_POINTER_FILE}")/active_session_id.txt}"
SESSION_BINDER="${SESSION_BINDER:-$ROOT_DIR/scripts/session_binder.py}"
SESSION_BINDER_POLL_INTERVAL="${SESSION_BINDER_POLL_INTERVAL:-0.5}"
SESSION_BINDER_LOG="${SESSION_BINDER_LOG:-$(dirname "${SESSION_POINTER_FILE}")/session_binder.log}"
# session_binder 默认常驻等待首个会话（0 表示一直等待），避免启动较久后首次交互导致指针长期为空。
SESSION_BINDER_TIMEOUT="${SESSION_BINDER_TIMEOUT:-0}"
# 用于管理后台 binder 生命周期，避免 stop/restart 后残留常驻进程。
SESSION_BINDER_PID_FILE="${SESSION_BINDER_PID_FILE:-$(dirname "${SESSION_POINTER_FILE}")/session_binder.pid}"
SESSION_BINDER_TOKEN_FILE="${SESSION_BINDER_TOKEN_FILE:-$(dirname "${SESSION_POINTER_FILE}")/session_binder_token.txt}"
RECENT_SESSIONS_FILE="${RECENT_SESSIONS_FILE:-$(dirname "${SESSION_POINTER_FILE}")/recent_sessions.json}"
RECENT_SESSIONS_LIMIT="${RECENT_SESSIONS_LIMIT:-3}"
CODEX_MODEL_INSTRUCTIONS_GENERATED_FILE="${CODEX_MODEL_INSTRUCTIONS_GENERATED_FILE:-$(dirname "${SESSION_POINTER_FILE}")/codex_model_instructions.md}"
SESSION_READY_FILE="${SESSION_READY_FILE:-}"
SESSION_READY_TIMEOUT_SECONDS="${SESSION_READY_TIMEOUT_SECONDS:-6}"
SESSION_READY_POLL_INTERVAL_SECONDS="${SESSION_READY_POLL_INTERVAL_SECONDS:-0.2}"
SESSION_READY_PROBE_LINES="${SESSION_READY_PROBE_LINES:-80}"
SESSION_READY_MARKERS="${SESSION_READY_MARKERS:-OpenAI Codex||model:||/model to change}"

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
SESSION_ACTIVE_ID_FILE=$(expand_path "$SESSION_ACTIVE_ID_FILE")
SESSION_BINDER_LOG=$(expand_path "$SESSION_BINDER_LOG")
SESSION_BINDER_PID_FILE=$(expand_path "$SESSION_BINDER_PID_FILE")
SESSION_BINDER_TOKEN_FILE=$(expand_path "$SESSION_BINDER_TOKEN_FILE")
CODEX_MODEL_INSTRUCTIONS_SOURCE_FILE=$(expand_path "$CODEX_MODEL_INSTRUCTIONS_SOURCE_FILE")
CODEX_MODEL_INSTRUCTIONS_GENERATED_FILE=$(expand_path "$CODEX_MODEL_INSTRUCTIONS_GENERATED_FILE")
SESSION_READY_FILE=$(expand_path "$SESSION_READY_FILE")
ensure_dir "$(dirname "$LOG_PATH")"
ensure_dir "$(dirname "$SESSION_POINTER_FILE")"
ensure_dir "$(dirname "$SESSION_ACTIVE_ID_FILE")"
ensure_dir "$(dirname "$SESSION_BINDER_LOG")"
ensure_dir "$(dirname "$SESSION_BINDER_PID_FILE")"
ensure_dir "$(dirname "$SESSION_BINDER_TOKEN_FILE")"
ensure_dir "$(dirname "$CODEX_MODEL_INSTRUCTIONS_GENERATED_FILE")"
if [[ -n "$SESSION_READY_FILE" ]]; then
  ensure_dir "$(dirname "$SESSION_READY_FILE")"
fi


select_agents_template_file() {
  local fallback_template="$1"
  local prefix="$2"
  local override_root="${VIBEGO_AGENTS_OVERRIDE_ROOT:-$CONFIG_ROOT/agents/current}"
  local override_manifest="$override_root/manifest.json"
  local override_template="$override_root/AGENTS-template.md"
  local override_skills_dir="$override_root/vibego_cli/data/skills"
  if [[ -f "$override_manifest" ]]; then
    if [[ ! -f "$override_template" || ! -d "$override_skills_dir" ]]; then
      echo "[$prefix] AGENTS override 损坏: $override_root" >&2
      exit 1
    fi
    local skill_probe=""
    skill_probe="$(find "$override_skills_dir" -mindepth 2 -maxdepth 2 -name SKILL.md -print -quit 2>/dev/null || true)"
    if [[ -z "$skill_probe" ]]; then
      echo "[$prefix] AGENTS override 损坏: $override_skills_dir 缺少 SKILL.md" >&2
      exit 1
    fi
    export VIBEGO_BUILTIN_SKILLS_DIR="$override_skills_dir"
    printf '%s' "$override_template"
    return 0
  fi
  printf '%s' "$fallback_template"
}

if [[ "${VIBEGO_AGENTS_SYNCED:-0}" != "1" ]]; then
  DEFAULT_AGENTS_TEMPLATE="$(select_agents_template_file "$ROOT_DIR/AGENTS-template.md" "start-tmux")"
  AGENTS_TEMPLATE_FILE="${VIBEGO_AGENTS_TEMPLATE:-$DEFAULT_AGENTS_TEMPLATE}"
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

generate_session_bind_marker() {
  "$PYTHON_EXEC" - <<'PY'
import secrets

print("vibego-session-bind-token:" + secrets.token_hex(16))
PY
}

render_recent_sessions_context() {
  local recent_file="$1"
  if [[ -z "$recent_file" || ! -s "$recent_file" ]]; then
    return 0
  fi
  "$PYTHON_EXEC" - "$recent_file" "${RECENT_SESSIONS_LIMIT:-3}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1]).expanduser()
try:
    limit = max(int(sys.argv[2]), 1)
except (IndexError, ValueError):
    limit = 3
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    data = []
if not isinstance(data, list):
    data = []

entries = []
for raw in data:
    if not isinstance(raw, dict):
        continue
    session_id = str(raw.get("session_id") or "").strip()
    jsonl_path = str(raw.get("jsonl_path") or "").strip()
    if not session_id or not jsonl_path:
        continue
    entries.append(
        {
            "session_id": session_id,
            "jsonl_path": jsonl_path,
            "cwd": str(raw.get("cwd") or "").strip(),
            "bound_at": str(raw.get("bound_at") or "").strip(),
        }
    )
    if len(entries) >= limit:
        break

if not entries:
    raise SystemExit(0)

def inline(value: str) -> str:
    return value.replace("`", "\\`")

print("\n\n## 近期会话 JSONL 路径索引")
print("")
print("当前项目最近绑定的会话路径如下，仅作近期上下文线索；不得替代 /docs 主任务文档。")
print("只有当用户明确提到“刚才、继续、上次、没反应、你刚修的”等依赖近期上下文的表达时，才按需读取对应 JSONL 的尾部或关键 turn。")
print("不要复制或默认展开完整 JSONL 内容；若从 JSONL 得到有效结论，进入设计/实现阶段后必须同步沉淀到 docs。")
print("")
for index, item in enumerate(entries, 1):
    print(f"{index}. session_id: `{inline(item['session_id'])}`")
    print(f"   - jsonl_path: `{inline(item['jsonl_path'])}`")
    if item["cwd"]:
        print(f"   - cwd: `{inline(item['cwd'])}`")
    if item["bound_at"]:
        print(f"   - bound_at: `{inline(item['bound_at'])}`")
PY
}

prepare_codex_model_instructions_file() {
  local source_file="$1" target_file="$2" required_marker="$3" recent_file="${4:-}"
  if [[ -z "$required_marker" ]]; then
    printf '%s' "$source_file"
    return 0
  fi
  if [[ ! -f "$source_file" ]]; then
    echo "[start-tmux] Codex 指令文件不存在: $source_file" >&2
    return 1
  fi
  ensure_dir "$(dirname "$target_file")"
  {
    cat "$source_file"
    render_recent_sessions_context "$recent_file"
    printf '\n\n<!-- %s -->\n' "$required_marker"
  } >"$target_file"
  printf '%s' "$target_file"
}

SESSION_BINDER_REQUIRED_MARKER=""
if [[ "$MODEL_KEY" == "codex" ]]; then
  if [[ -n "$MODEL_RESUME_SESSION_ID" ]]; then
    # resume 历史线程可能没有本次启动 marker；清空 token，避免 bot 误判历史会话。
    if (( ! DRY_RUN )); then
      : >"$SESSION_BINDER_TOKEN_FILE"
    fi
  else
    if (( DRY_RUN )); then
      SESSION_BINDER_REQUIRED_MARKER="vibego-session-bind-token:dry-run"
    else
      SESSION_BINDER_REQUIRED_MARKER="$(generate_session_bind_marker)"
      printf '%s\n' "$SESSION_BINDER_REQUIRED_MARKER" >"$SESSION_BINDER_TOKEN_FILE"
    fi
    CODEX_MODEL_INSTRUCTIONS_FILE="$(
      prepare_codex_model_instructions_file \
        "$CODEX_MODEL_INSTRUCTIONS_SOURCE_FILE" \
        "$CODEX_MODEL_INSTRUCTIONS_GENERATED_FILE" \
        "$SESSION_BINDER_REQUIRED_MARKER" \
        "$RECENT_SESSIONS_FILE"
    )"
  fi
else
  if (( ! DRY_RUN )); then
    : >"$SESSION_BINDER_TOKEN_FILE"
  fi
fi

if [[ "$MODEL_KEY" == "codex" ]]; then
  CODEX_BASE_CMD="${MODEL_CMD:-${CODEX_CMD:-codex --dangerously-bypass-approvals-and-sandbox -c trusted_workspace=true}}"
  printf -v CODEX_MODEL_INSTRUCTIONS_FILE_ESCAPED '%q' "$CODEX_MODEL_INSTRUCTIONS_FILE"
  MODEL_CMD="${CODEX_BASE_CMD} -c model_instructions_file=${CODEX_MODEL_INSTRUCTIONS_FILE_ESCAPED} -c project_doc_max_bytes=${CODEX_PROJECT_DOC_MAX_BYTES}"
  CODEX_GOALS_ENABLED_NORMALIZED="$(printf '%s' "$CODEX_GOALS_ENABLED" | tr '[:upper:]' '[:lower:]')"
  case "$CODEX_GOALS_ENABLED_NORMALIZED" in
    0|false|no|off)
      ;;
    *)
      if [[ "$MODEL_CMD" != *"features.goals"* ]]; then
        MODEL_CMD="${MODEL_CMD} -c features.goals=true"
      fi
      ;;
  esac
  if [[ -n "$MODEL_RESUME_SESSION_ID" ]]; then
    MODEL_RESUME_SESSION_ID_ESCAPED=""
    printf -v MODEL_RESUME_SESSION_ID_ESCAPED '%q' "$MODEL_RESUME_SESSION_ID"
    # 恢复历史主会话时必须启动 Codex resume，避免只改 pointer 造成“监听旧会话、终端仍在新会话”的假绑定。
    MODEL_CMD="${MODEL_CMD} resume ${MODEL_RESUME_SESSION_ID_ESCAPED}"
  fi
elif [[ -n "$MODEL_RESUME_SESSION_ID" ]]; then
  echo "[start-tmux] 当前仅 Codex 支持按 sessionId 恢复会话。" >&2
  exit 1
fi

run_tmux() {
  if (( DRY_RUN )); then
    printf '[dry-run] tmux -u %s\n' "$*"
  else
    tmux -u "$@"
  fi
}

is_shell_command() {
  case "${1##*/}" in
    sh|bash|zsh|fish|dash|ksh|csh|tcsh) return 0 ;;
    *) return 1 ;;
  esac
}

tmux_output_contains_ready_marker() {
  local output="$1"
  local marker
  IFS='||' read -r -a READY_MARKERS <<<"$SESSION_READY_MARKERS"
  for marker in "${READY_MARKERS[@]}"; do
    [[ -n "$marker" && "$output" == *"$marker"* ]] && return 0
  done
  return 1
}

wait_for_tmux_ready() {
  [[ -n "$SESSION_READY_FILE" ]] || return 0
  local max_attempts
  max_attempts="$("$PYTHON_EXEC" - <<PY
import math
timeout = max(float("${SESSION_READY_TIMEOUT_SECONDS}"), 0.0)
poll = max(float("${SESSION_READY_POLL_INTERVAL_SECONDS}"), 0.05)
print(max(1, math.ceil(timeout / poll)))
PY
)"
  local attempt current_command pane_output
  rm -f "$SESSION_READY_FILE"
  for (( attempt=1; attempt<=max_attempts; attempt++ )); do
    current_command="$(tmux -u display-message -p -t "$SESSION_NAME" '#{pane_current_command}' 2>/dev/null || true)"
    if [[ -n "$current_command" ]] && ! is_shell_command "$current_command"; then
      pane_output="$(tmux -u capture-pane -p -t "$SESSION_NAME" -S "-${SESSION_READY_PROBE_LINES}" 2>/dev/null || true)"
      if tmux_output_contains_ready_marker "$pane_output"; then
        {
          printf 'session=%s\n' "$SESSION_NAME"
          printf 'command=%s\n' "$current_command"
        } >"$SESSION_READY_FILE"
        return 0
      fi
    fi
    if (( attempt < max_attempts )); then
      sleep "$SESSION_READY_POLL_INTERVAL_SECONDS"
    fi
  done
  echo "[start-tmux] tmux 会话未在限定时间内进入 Codex ready 状态: session=$SESSION_NAME current_command=${current_command:-unknown}" >&2
  return 1
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

# 清理历史 binder（避免 stop 后残留常驻进程，或升级前未写 pid 的 orphan 进程）
kill_session_binder_for_pointer() {
  local pointer_file="$1" pid_file="$2"
  local pid=""

	  if [[ -f "$pid_file" ]]; then
	    pid="$(cat "$pid_file" 2>/dev/null || true)"
	    if [[ -n "$pid" ]] && ps -p "$pid" >/dev/null 2>&1; then
	      local cmd=""
	      cmd="$(ps -p "$pid" -o args= 2>/dev/null || true)"
	      if [[ "$cmd" == *"session_binder.py"* ]] && [[ "$cmd" == *"$pointer_file"* ]]; then
	        kill "$pid" >/dev/null 2>&1 || true
	        for _ in {1..20}; do
	          sleep 0.1
          ps -p "$pid" >/dev/null 2>&1 || break
        done
        if ps -p "$pid" >/dev/null 2>&1; then
          kill -9 "$pid" >/dev/null 2>&1 || true
        fi
      fi
    fi
    rm -f "$pid_file"
  fi

	  # 升级前版本可能未写 pid_file：按 pointer 路径兜底清理
	  if command -v ps >/dev/null 2>&1; then
	    while read -r orphan_pid _cmd; do
	      [[ -z "$orphan_pid" ]] && continue
	      kill "$orphan_pid" >/dev/null 2>&1 || true
	    done < <(ps -Ao pid=,args= 2>/dev/null | grep -F "session_binder.py" | grep -F "$pointer_file" || true)
	  fi
	}

kill_session_binder_for_pointer "$SESSION_POINTER_FILE" "$SESSION_BINDER_PID_FILE"

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
  if [[ -s "$SESSION_BINDER_TOKEN_FILE" ]]; then
    BINDER_CMD+=(--required-marker "$(cat "$SESSION_BINDER_TOKEN_FILE")")
  fi
  if [[ -n "$RECENT_SESSIONS_FILE" ]]; then
    BINDER_CMD+=(
      --recent-sessions-file "$RECENT_SESSIONS_FILE"
      --recent-limit "$RECENT_SESSIONS_LIMIT"
      --project-slug "${PROJECT_NAME:-}"
    )
  fi
  if [[ -n "$SESSION_ACTIVE_ID_FILE" ]]; then
    BINDER_CMD+=(--session-id-file "$SESSION_ACTIVE_ID_FILE")
  fi
  if [[ -n "$SESSION_BINDER_LOG" ]]; then
    BINDER_CMD+=(--log "$SESSION_BINDER_LOG")
  fi
  nohup "${BINDER_CMD[@]}" >>"$SESSION_BINDER_LOG" 2>&1 &
  echo $! >"$SESSION_BINDER_PID_FILE"
else
  echo "[start-tmux] session binder 未找到：$SESSION_BINDER" >&2
fi

if ! wait_for_tmux_ready; then
  exit 1
fi

exit 0
