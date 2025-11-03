#!/usr/bin/env bash
# Codex 模型配置

model_configure() {
  MODEL_NAME="codex"
  MODEL_WORKDIR="${CODEX_WORKDIR:-${MODEL_WORKDIR:-$ROOT_DIR}}"
  MODEL_CMD="${CODEX_CMD:-codex --dangerously-bypass-approvals-and-sandbox -c trusted_workspace=true}"
  MODEL_SESSION_ROOT="${CODEX_SESSION_ROOT:-$HOME/.codex/sessions}"
  MODEL_SESSION_GLOB="${CODEX_SESSION_GLOB:-rollout-*.jsonl}"
  MODEL_POINTER_BASENAME="${MODEL_POINTER_BASENAME:-current_session.txt}"
}

