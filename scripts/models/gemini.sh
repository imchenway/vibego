#!/usr/bin/env bash
# Gemini 模型配置

# 说明：
# - Gemini CLI 的会话文件默认落在 ~/.gemini/tmp/<projectHash>/chats/session-*.json
# - projectHash 可由“工作目录绝对路径字符串”的 sha256 计算得到（见官方仓库与本仓库任务说明）。

model_configure() {
  MODEL_NAME="gemini"
  MODEL_WORKDIR="${GEMINI_WORKDIR:-${MODEL_WORKDIR:-$ROOT_DIR}}"
  # 默认使用 YOLO 模式避免 CLI 阻塞在“等待确认”。
  # 同时开启 Gemini CLI 自带 sandbox，尽量降低误操作风险（仍需注意：YOLO 会自动批准工具调用）。
  MODEL_CMD="${GEMINI_CMD:-gemini --approval-mode yolo --sandbox}"
  # 会话搜索根目录：默认扫描 ~/.gemini/tmp，由 session_binder 依据 projectHash 过滤到当前工作目录。
  MODEL_SESSION_ROOT="${GEMINI_SESSION_ROOT:-$HOME/.gemini/tmp}"
  MODEL_SESSION_GLOB="${GEMINI_SESSION_GLOB:-session-*.json}"
  MODEL_POINTER_BASENAME="${MODEL_POINTER_BASENAME:-current_session.txt}"
}
