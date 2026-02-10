#!/usr/bin/env bash
# Codex 模型配置

model_configure() {
  MODEL_NAME="codex"
  MODEL_WORKDIR="${CODEX_WORKDIR:-${MODEL_WORKDIR:-$ROOT_DIR}}"
  local codex_model_instructions_file="${CODEX_MODEL_INSTRUCTIONS_FILE:-/Users/david/.codex/AGENTS.md}"
  local codex_model_instructions_file_escaped=""
  local codex_project_doc_max_bytes="${CODEX_PROJECT_DOC_MAX_BYTES:-131072}"
  local codex_base_cmd="${MODEL_CMD:-${CODEX_CMD:-codex --dangerously-bypass-approvals-and-sandbox -c trusted_workspace=true}}"
  printf -v codex_model_instructions_file_escaped '%q' "$codex_model_instructions_file"
  MODEL_CMD="${codex_base_cmd} -c model_instructions_file=${codex_model_instructions_file_escaped} -c project_doc_max_bytes=${codex_project_doc_max_bytes}"
  MODEL_SESSION_ROOT="${CODEX_SESSION_ROOT:-$HOME/.codex/sessions}"
  MODEL_SESSION_GLOB="${CODEX_SESSION_GLOB:-rollout-*.jsonl}"
  MODEL_POINTER_BASENAME="${MODEL_POINTER_BASENAME:-current_session.txt}"
}
