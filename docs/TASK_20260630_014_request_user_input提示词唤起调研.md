# TASK_20260630_014 request_user_input 提示词唤起调研

## 结论

不能只靠 AGENTS.md 或普通提示词稳定唤起 Codex 原生 `request_user_input` 点击提问器。

原因：当前会话工具契约与实测都表明，`request_user_input` 在 Default mode 不可用；官方文档只说明 Plan mode
会先收集上下文、提澄清问题并形成计划，也说明 App Server 存在实验性的 `tool/requestUserInput`，但没有说明普通 prompt
可以把该工具注入当前 turn。

## 推荐设计

AGENTS 可采用“能力感知强门禁”：

1. 如果当前工具列表可用 `request_user_input`，必须用 Codex 提问器让用户点击确认。
2. 如果不可用，必须退化为单个阻塞问题，不得继续实现、改文件或进入后续流程。
3. 用户明确确认“进入下一步”后，才进入方案、计划、实现或验证。

## 来源

- 当前会话实测：Default mode 调用 `request_user_input` 返回不可用。
- 当前工具契约：`request_user_input` 仅 Plan mode 可用。
- 官方 Codex Best Practices：Plan mode 会收集上下文、提澄清问题并制定计划。
- 官方 Codex App Server：列出 `tool/requestUserInput` 与 `collaborationMode`。
- 官方 Codex Configuration Reference：仅发现 `plan_mode_reasoning_effort`，未发现 Default mode 自动开启提问器的配置。
