#!/usr/bin/env bash
# ClaudeCode 模型配置

# 将工作目录转换为 Claude CLI 默认的 project key。
# Claude 官方实现是简单地把绝对路径中的斜杠替换成连字符，并保留大小写。
claude_project_key_from_workdir() {
  local path="$1"
  if [[ -z "$path" ]]; then
    printf 'project'
    return
  fi
  if [[ "$path" == ~* ]]; then
    path="${path/#\~/$HOME}"
  fi
  path="${path%/}"
  local replaced="${path//\//-}"
  printf '%s' "${replaced:-project}"
}

model_configure() {
  MODEL_NAME="ClaudeCode"
  MODEL_WORKDIR="${CLAUDE_WORKDIR:-${MODEL_WORKDIR:-$ROOT_DIR}}"
  # 默认关闭文件快照，避免孤儿 CLI 持续写入 jsonl
  CLAUDE_DISABLE_FILE_CHECKPOINTING="${CLAUDE_DISABLE_FILE_CHECKPOINTING:-1}"
  CLAUDE_CODE_DISABLE_FILE_CHECKPOINTING="${CLAUDE_CODE_DISABLE_FILE_CHECKPOINTING:-$CLAUDE_DISABLE_FILE_CHECKPOINTING}"
  export CLAUDE_CODE_DISABLE_FILE_CHECKPOINTING
  local project_key
  if [[ -n "${CLAUDE_PROJECT_KEY:-}" ]]; then
    project_key="$CLAUDE_PROJECT_KEY"
  else
    project_key="$(claude_project_key_from_workdir "$MODEL_WORKDIR")"
    if [[ -z "$project_key" ]]; then
      project_key="$(project_slug_from_workdir "$MODEL_WORKDIR")"
    fi
  fi
  local claude_root="${CLAUDE_PROJECT_ROOT:-$HOME/.claude/projects}"
  MODEL_SESSION_ROOT="$claude_root/$project_key"
  MODEL_SESSION_GLOB="${CLAUDE_SESSION_GLOB:-*.jsonl}"
  MODEL_CMD="${CLAUDE_CMD:-claude --dangerously-skip-permissions}"
  MODEL_POINTER_BASENAME="${MODEL_POINTER_BASENAME:-current_session.txt}"
}
