#!/usr/bin/env bash
# Gemini 模型占位配置（待实现）

model_configure() {
  MODEL_NAME="gemini"
  MODEL_WORKDIR="${GEMINI_WORKDIR:-${MODEL_WORKDIR:-$ROOT_DIR}}"
  MODEL_CMD="${GEMINI_CMD:-echo 'gemini CLI 尚未配置'; sleep 1}" 
  MODEL_SESSION_ROOT="${GEMINI_SESSION_ROOT:-$LOG_ROOT/gemini-placeholder}"
  MODEL_SESSION_GLOB="${GEMINI_SESSION_GLOB:-*.jsonl}"
  MODEL_POINTER_BASENAME="${MODEL_POINTER_BASENAME:-current_session.txt}"
}

