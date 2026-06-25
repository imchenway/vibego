# TASK_20260625_005 JSONL 未按 HTML 技能生成原因排查

## 1. 问题口径

用户问：看当前会话 JSONL，为什么没有按 skills 自动生成 HTML 文件。

本次只做只读取证与图形化说明，不修改运行代码。

## 2. 结论

现象需要拆成两层：

1. **自动触发层**：该会话没有在任务开始时按 `html-visual-communication` 自动走 HTML 图沟通。根因成立：当前 JSONL 的 `session_meta.base_instructions` 是旧同步版本，不包含 `HTML 图形沟通默认协议` 与 `html-visual-communication` skill。
2. **产物层**：该会话后半段实际创建过一个 HTML 文件：`docs/TASK_20260625_004_AGENTS与HTML制图skill配合协议.html`。所以“完全没有生成 HTML 文件”不成立；更准确是“没有在任务开始和最终交付中按 skill 协议自动优先使用 HTML 图”。

## 3. 证据链

| 编号 | 证据 | 结论 |
| --- | --- | --- |
| E1 | `/Users/david/.codex/sessions/2026/06/25/rollout-2026-06-25T10-29-25-019efc9c-5ab4-7113-9987-a9bae745b99a.jsonl` line 2237 | 用户明确提出“以后的沟通全部都是通过 HTML 的图来沟通”。 |
| E2 | 同 JSONL 所有 `session_meta` 行均显示：`has_html=False`、`has_skill=False`、`vibego-synced-at-utc: 2026-06-24T23:50:58Z` | 当前会话启动时没有注入新 HTML 默认协议和内置 skill。 |
| E3 | 同 JSONL line 2247 | 模型声明使用的 skill 是 `writing-skills`、`test-driven-development`、`verification-before-completion`，没有 `html-visual-communication`。 |
| E4 | 同 JSONL line 2412 | 模型后续通过 `cat > docs/TASK_20260625_004_AGENTS与HTML制图skill配合协议.html` 创建了 HTML 文件。 |
| E5 | 同 JSONL line 2485 | 最终回复引用了该 HTML 路径，但这属于后置产物引用，不是任务开始即按 HTML 图沟通。 |
| E6 | `/Users/david/.config/vibego/AGENTS.md`、`/Users/david/.codex/AGENTS.md`、`/Users/david/.config/vibego/logs/codex/vibego/codex_model_instructions.md` 当前均包含 `HTML 图形沟通默认协议`、`html-visual-communication`、`AGENTS 配合协议`，mtime 为 `2026-06-25 18:23:46 +0800` | 新协议是在该 JSONL 会话原始 `session_meta` 之后才同步到当前配置；旧会话不会自动回灌更新后的 base instructions。 |

## 4. 根因

已确认根因：**会话级 instructions 快照陈旧**。

Codex JSONL 的 `session_meta.base_instructions` 是会话启动时的快照。该会话创建于 `2026-06-25T02:29:25.836Z`，使用的 AGENTS 同步时间是 `2026-06-24T23:50:58Z`；而 HTML 默认协议与 skill 配合协议是在后续任务中才写入并同步。因此本轮模型没有可执行的“非琐碎任务默认生成 HTML 图”约束，只能把用户需求当成“修改 AGENTS 与 skill 本身”的开发任务执行。

## 5. 修法建议

1. 新开 Codex 会话或重启 vibego worker，让新的 `codex_model_instructions.md` 成为会话启动快照。
2. 对仍在旧 JSONL 上继续的会话，不能指望 session_meta 自动刷新；需要在用户提示或系统注入中显式携带最新 AGENTS，或切新会话。
3. 若用户说的是 Telegram 没收到 HTML 附件，还需要单独追投递层日志；从 JSONL 本身只能确认“最终回复引用了 HTML 路径”，不能单独证明 Telegram 已发送附件。

## 6. 验证

- 已用脚本读取 JSONL：确认旧 `session_meta` 不含 HTML 协议和 skill。
- 已用脚本读取当前 AGENTS / Codex instructions：确认当前配置已经含新协议。
- 已验证当前 `bot._collect_model_response_local_documents(...)` 可以从最终回复中的 Markdown 链接提取项目内 HTML 路径。

