#!/usr/bin/env bash
# Copilot 模型配置

model_configure() {
  MODEL_NAME="copilot"
  MODEL_WORKDIR="${COPILOT_WORKDIR:-${MODEL_WORKDIR:-$ROOT_DIR}}"
  MODEL_CMD="${COPILOT_CMD:-copilot}"
  MODEL_SESSION_ROOT="${COPILOT_SESSION_ROOT:-$HOME/.copilot/session-state}"
  MODEL_SESSION_GLOB="${COPILOT_SESSION_GLOB:-events.jsonl}"
  MODEL_POINTER_BASENAME="${MODEL_POINTER_BASENAME:-current_session.txt}"
}
