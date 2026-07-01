#!/usr/bin/env bash
# 公共工具：模型脚本/运行脚本/停止脚本共享

# 避免重复定义时覆盖
if [[ -n "${_MODEL_COMMON_LOADED:-}" ]]; then
  return
fi
_MODEL_COMMON_LOADED=1

COMMON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# 公共模型脚本位于 scripts/models；默认根目录应回到包/仓库根，而不是 scripts 目录。
ROOT_DIR="${ROOT_DIR:-$(cd "$COMMON_DIR/../.." && pwd)}"
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
VIBEGO_AGENTS_MARKER_START="<!-- vibego-agents:start -->"
VIBEGO_AGENTS_MARKER_END="<!-- vibego-agents:end -->"

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

_vibego_python_bin() {
  if [[ -n "${PYTHON_EXEC:-}" ]] && command -v "$PYTHON_EXEC" >/dev/null 2>&1; then
    printf '%s' "$PYTHON_EXEC"
    return 0
  fi
  if [[ -n "${PYTHON_BIN:-}" ]] && command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    printf '%s' "$PYTHON_BIN"
    return 0
  fi
  printf '%s' "python3"
}

sync_agents_block() {
  local target_file="$1" template_file="$2"
  local marker_start="${3:-$VIBEGO_AGENTS_MARKER_START}"
  local marker_end="${4:-$VIBEGO_AGENTS_MARKER_END}"
  local builtin_skills_dir="${VIBEGO_BUILTIN_SKILLS_DIR:-$ROOT_DIR/vibego_cli/data/skills}"
  if [[ -z "$target_file" || -z "$template_file" ]]; then
    return 0
  fi
  if [[ ! -f "$template_file" ]]; then
    echo "[agents-sync] 模板不存在: $template_file" >&2
    return 1
  fi
  ensure_dir "$(dirname "$target_file")"
  local py_bin
  py_bin="$(_vibego_python_bin)"
  "$py_bin" - "$target_file" "$template_file" "$marker_start" "$marker_end" "$builtin_skills_dir" <<'PY'
import sys, shutil, datetime
from pathlib import Path

target = Path(sys.argv[1]).expanduser()
template = Path(sys.argv[2]).expanduser()
marker_start = sys.argv[3]
marker_end = sys.argv[4]
builtin_skills_dir = Path(sys.argv[5]).expanduser()
timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def extract_skill_index(skill_file: Path) -> tuple[str, str]:
    """从 SKILL.md frontmatter 提取索引信息，不把正文常驻注入 AGENTS。"""

    text = skill_file.read_text(encoding="utf-8")
    skill_name = skill_file.parent.name
    description = ""
    if text.startswith("---\n"):
        _, frontmatter, _ = text.split("---", 2)
        for raw_line in frontmatter.splitlines():
            line = raw_line.strip()
            if line.startswith("name:"):
                skill_name = line.removeprefix("name:").strip().strip('"').strip("'") or skill_name
            elif line.startswith("description:"):
                description = line.removeprefix("description:").strip().strip('"').strip("'")
    return skill_name, description


def render_builtin_skills(skills_dir: Path) -> str:
    """把 vibego 内置 skill 索引合并进 AGENTS，确保全局 Codex 也能发现。"""

    if not skills_dir.is_dir():
        return ""
    skill_files = sorted(skills_dir.glob("*/SKILL.md"))
    if not skill_files:
        return ""

    lines = [
        "# Vibego 内置 Skills",
        "",
        "以下技能包由 vibego 在同步 AGENTS 时自动注入索引，与本文件其他全局规约同级生效。",
        "当用户需求命中某个 skill 的 name/description 时，必须先读取对应 SKILL.md 全文，再执行该 skill 的规则。",
        "AGENTS 只保留索引，不常驻注入完整 skill 正文，避免提示词膨胀。",
        "",
    ]
    for skill_file in skill_files:
        skill_name, description = extract_skill_index(skill_file)
        lines.extend(
            [
                f"## Skill: {skill_name}",
                "",
                f"<!-- vibego-skill-source: {skill_file} -->",
                "",
                "```yaml",
                f"name: {skill_name}",
                f"description: {description}",
                "```",
                "",
                "- 命中该 skill 时，先读取上方 vibego-skill-source 指向的 SKILL.md 全文。",
                "- 若读取失败，必须 fail-closed，不得凭索引内容继续执行。",
                "",
            ]
        )
    return "\n".join(lines).rstrip()


body = template.read_text(encoding="utf-8").rstrip()
skills_block = render_builtin_skills(builtin_skills_dir)
if skills_block:
    body = body + "\n\n" + skills_block
block_lines = [
    marker_start,
    f"<!-- vibego-source: {template} -->",
    f"<!-- vibego-synced-at-utc: {timestamp} -->",
    "",
    body,
    marker_end,
    "",
]
block = "\n".join(block_lines)
target.parent.mkdir(parents=True, exist_ok=True)
status = ""
backup_path = None
if target.exists():
    text = target.read_text(encoding="utf-8")
    import re
    pattern = re.compile(
        re.escape(marker_start) + r".*?" + re.escape(marker_end),
        re.DOTALL,
    )
    match = pattern.search(text)
    if match:
        new_text = text[:match.start()] + block + text[match.end():]
        if not new_text.endswith("\n"):
            new_text += "\n"
        target.write_text(new_text, encoding="utf-8")
        status = "updated"
    else:
        backup_path = Path(str(target) + ".vibego.bak")
        if not backup_path.exists():
            shutil.copy2(target, backup_path)
        append_text = text.rstrip() + "\n\n" + block if text.strip() else block
        target.write_text(append_text, encoding="utf-8")
        status = "appended"
else:
    target.write_text(block, encoding="utf-8")
    status = "created"

msg = f"[agents-sync] {status} {target}"
if backup_path:
    msg += f" (backup={backup_path})"
print(msg)
PY
}

sync_vibego_agents_for_model() {
  local model_key="${1:-}"
  local template="${2:-$ROOT_DIR/AGENTS-template.md}"
  if [[ -z "$model_key" ]]; then
    return 0
  fi
  local lower_model
  lower_model="$(printf '%s' "$model_key" | tr '[:upper:]' '[:lower:]')"
  local target=""
  case "$lower_model" in
    codex)
      target="${CODEX_AGENTS_FILE:-$HOME/.codex/AGENTS.md}"
      ;;
    claudecode|claude|claude-code|claudecodex)
      target="${CLAUDE_AGENTS_FILE:-$HOME/.claude/CLAUDE.md}"
      ;;
    gemini)
      # Gemini CLI 默认读取 ~/.gemini/GEMINI.md 作为项目上下文文件
      target="${GEMINI_AGENTS_FILE:-$HOME/.gemini/GEMINI.md}"
      ;;
    *)
      return 0
      ;;
  esac
  sync_agents_block "$target" "$template"
}
