# TASK_20260512_001 Telegram 模型回复记忆引用标签泄露分析

## 阶段

PLAN（只读排查 + 方案确认；未修改源码实现）

## 需求原文

用户截图反馈：Telegram 里模型回复末尾出现一堆类似 `<oai-mem-citation> / <citation_entries> / <rollout_ids>` 的“标签”，询问“消息后面跟着的这一堆标签是什么”。

附件证据：`/Users/david/.config/vibego/data/telegram/vibegobot/2026-05-12/20260512_000819234-5c185b327309.jpg`，截图末尾可见完整 `<oai-mem-citation>` 块。

## 前置规约读取情况

- 已读取 `$HOME/.config/vibego/AGENTS.md`（锚点：`# 全局统一约束`、`## plan 阶段`、`## develop 阶段`）。
- 已读取当前根目录 `AGENTS.md`（锚点：`# AGENTS.md（Strict Evidence Mode）`、`## 0) Non-negotiables`、`## 7) Testing & Quality Gates`、`## 9) Vibe Coding Workflow`）。
- 当前仓库扫描仅发现根 `./AGENTS.md`；未发现 `PROJECT-STYLE.md`、`CODE-GUIDELINES.md`、`DESIGN.md`、`AGENTS.evidence.json` 或受影响子目录内更近规约文件。
  - Evidence：命令 `find . -name AGENTS.md -o -name AGENTS.evidence.json -o -name PROJECT-STYLE.md -o -name CODE-GUIDELINES.md -o -name DESIGN.md` 输出仅有 `./AGENTS.md`。

## 现象 -> 影响 -> 根因 -> 修法 -> 验证

### 现象

Telegram 模型回复正文末尾出现机器可读的引用块：

```text
<oai-mem-citation>
<citation_entries>
MEMORY.md:549-590|note=[...]
</citation_entries>
<rollout_ids>
</rollout_ids>
</oai-mem-citation>
```

### 影响

- 普通 Telegram 用户会把这些 XML-like 标记误认为业务标签、HTML 标签或异常日志。
- 回复可读性下降；尤其在手机端末尾占据大段空间。
- 该块本意是 Codex/平台侧用于“记忆引用追踪”的元数据，不应该作为 Telegram 面向用户正文展示。

### 根因判断

高置信根因：`bot.py` 对 Codex assistant final_answer 的 `output_text` 做了原样投递，没有在 Telegram 输出层剥离平台内部的记忆引用块。

证据链：

1. Codex 会话 JSONL 中，assistant 的 final_answer 本身包含 `<oai-mem-citation>`。
   - Evidence：`/Users/david/.codex/sessions/2026/05/12/rollout-2026-05-12T01-09-39-019e1804-0ed6-7923-898e-53a312ebd79e.jsonl`（锚点：line 424，`phase=final_answer`，正文包含 `<oai-mem-citation>`）。
2. vibego 解析 Codex `response_item` 时，从 `content[].text` 拼接为可投递消息。
   - Evidence：`bot.py`（锚点：`def _extract_codex_payload`，`payload_type in {"message", "assistant_message"}`，`fragments.append(text)`，`return DELIVERABLE_KIND_MESSAGE, "\n".join(fragments), metadata`）。
3. vibego 投递模型消息前仅添加完成前缀、计算去重 hash，然后调用 `reply_large_text`。
   - Evidence：`bot.py`（锚点：`async def _deliver_pending_messages`，`formatted_text = _prepend_completion_header(text_to_send)`，`await reply_large_text(... overflow_mode="split")`）。
4. `reply_large_text` 只负责 Telegram 长度、Markdown 与附件/拆分策略，不做平台内部元数据剥离。
   - Evidence：`bot.py`（锚点：`async def reply_large_text`，`_prepare_model_payload_variants`、`_split_text_for_telegram_messages`、`send_message` / `send_document`）。

### 排除项

- 不是 Telegram 自己生成的标签：截图中的标签块与 Telegram Bot API 或 Markdown/HTML parse_mode 无直接关系。
- 不是业务任务标签：内容指向 `MEMORY.md`、`rollout_ids`，属于 Codex 记忆系统引用元数据。
- 不是 AGENTS.md 被误解析成 HTML：Codex final_answer 里已经包含该块，vibego 只是原样转发。

## 受影响目录与文件

| 范围 | 是否受影响 | 说明 |
|---|---:|---|
| `bot.py` | 是 | Codex/Claude/Gemini/Copilot 会话输出解析与 Telegram 投递主链路。推荐在投递前统一清理平台内部 citation 块。 |
| `tests/test_plan_progress.py` | 是 | 已覆盖 `_deliver_pending_messages`、`reply_large_text` 等投递链路，适合补 TDD 回归。 |
| `docs/` | 是 | 本任务文档沉淀分析、方案、测试矩阵与风险。 |
| `master.py` | 否 | 截图为 worker bot 的模型回复投递，不是 master 菜单/项目管理消息。 |
| `tasks/` / `command_center/` / SQLite 表结构 | 否 | 仅影响消息展示层，不改变任务/命令持久化契约。 |
| `scripts/` | 否 | 不涉及 worker 启停脚本与模型启动参数。 |

## 契约变更建议

### 对用户可见契约

- Telegram 中模型最终回复不再展示 `<oai-mem-citation>`、`<citation_entries>`、`<rollout_ids>` 块。
- 回复正文其余内容保持不变，包括任务编码、影响点、待执行脚本等业务收尾字段。
- 若正文中出现普通 HTML/XML-like 内容但不是完整 memory citation 块，应保留，避免误删用户真实内容。

### 对内部链路契约

- Codex JSONL 原始会话文件不修改；只在 Telegram 投递前做展示层清理。
- 去重 hash 建议基于清理后的最终投递文本计算，避免同一条带/不带 citation 的内容被视为两条不同消息。
- 对 `reply_large_text` 的通用发送能力不建议侵入；更推荐在“模型输出投递链路”清理，避免影响普通业务消息/预览消息。

## 方案对比

### A. 仅解释，不修代码

- 优点：零风险、最快。
- 缺点：后续每次使用 memory 的 Codex final_answer 仍会泄露 citation 块，手机端体验继续变差。
- 适用：只想了解“这是什么”。

### B. 在 Telegram 模型输出投递前剥离 memory citation 块（推荐）

- 优点：改动最小；只影响模型最终回复展示；不改 Codex 原始会话，不破坏审计。
- 缺点：需要新增一个小型清理函数与回归测试。
- 适用：当前问题的高置信修复。

### C. 在更底层 `reply_large_text` 清理

- 优点：所有发送入口都会被保护。
- 缺点：过宽，可能误伤用户主动发送/预览的 XML-like 文本；不符合最小影响原则。

### D. 改 Codex/memory 侧提示词，要求不要生成 citation

- 优点：源头减少输出。
- 缺点：这是平台/记忆系统的外层要求，vibego 无法可靠控制；且当前会话系统要求在部分场景必须追加 citation，不能作为本仓库稳定修复。

## 推荐开发设计（待确认后进入 develop）

### 核心设计

新增纯函数：

```python
def _strip_internal_oai_memory_citation_block(text: str) -> str:
    """移除 Codex 平台内部记忆引用块，避免泄露到 Telegram 用户消息。"""
```

规则：

1. 只删除完整块：从行首可带空白的 `<oai-mem-citation>` 到行首可带空白的 `</oai-mem-citation>`。
2. 支持块前后空行收口：删除后不留下 3 个以上连续空行，末尾 `rstrip("\n")`。
3. 不删除不完整标签，避免误删用户真实正文并暴露模型异常；可记录 warning（可选）。
4. 只在 `DELIVERABLE_KIND_MESSAGE` 的 Telegram 投递路径处理，`DELIVERABLE_KIND_PLAN` / `DELIVERABLE_KIND_REQUEST_INPUT` 不动。

伪代码：

```python
text_to_send = _strip_internal_oai_memory_citation_block((deliverable.text or "").rstrip("\n"))
if not text_to_send:
    mark offset delivered and continue
formatted_text = _prepend_completion_header(text_to_send)
payload_for_hash = _prepare_model_payload(formatted_text)
```

### 测试矩阵

| 用例 | 入口 | 预期 |
|---|---|---|
| final_answer 末尾含完整 citation 块 | `_deliver_pending_messages` | Telegram 收到内容不含 `<oai-mem-citation>`，保留业务收尾字段。 |
| final_answer 中间含完整 citation 块 | 清理函数单测 | 块被删除，前后段落正常收口。 |
| 不完整 `<oai-mem-citation>` | 清理函数单测 | 不删除，避免误伤。 |
| 普通 XML/HTML-like 文本 | 清理函数单测 | 保留。 |
| 清理后内容为空 | `_deliver_pending_messages` | 不发送空消息，offset 正常推进。 |
| 长文本 split | `reply_large_text` 经模型链路 | 清理先于拆分，不把 citation 块拆到后续消息。 |

### 验证命令

进入 develop 后建议执行：

1. baseline：`python3.11 -m pytest -q tests/test_plan_progress.py`
2. 红灯：新增测试后先运行同一命令，确认因 citation 未剥离失败。
3. 绿灯：实现后运行 `python3.11 -m pytest -q tests/test_plan_progress.py`。
4. 仓库基础门禁：`python3.11 -m vibego_cli doctor`、`bash scripts/test_deps_check.sh`。
5. 双轮回归：重复执行受影响 pytest + 基础门禁。

## 风险与回滚

- 风险 1：正则过宽误删用户正文。
  - 控制：只匹配完整、独占行的 `<oai-mem-citation>` 块。
- 风险 2：去重 hash 因清理前后不一致导致重复投递。
  - 控制：清理后再计算 hash，并以最终投递 payload 记录。
- 风险 3：未来平台新增其他隐藏元数据块。
  - 控制：本次只处理已复现的 `<oai-mem-citation>`；其它块另案评估，避免过度泛化。
- 回滚：删除清理函数调用与对应测试即可恢复原样；不涉及数据库/配置/构建脚本迁移。

## 当前结论

截图里的“标签”是 Codex 记忆系统的引用元数据，原本用于当前平台记录“这次回答用了哪些 memory 文件/rollout”，不应出现在 Telegram 面向用户消息里。当前 vibego 原样转发 final_answer，导致它泄露到了 Telegram。

推荐进入 develop 后按方案 B 做最小源码修复，并用 `tests/test_plan_progress.py` 做 TDD 回归。

---

## DEVELOP 实施记录（2026-05-12）

### 用户确认

用户已确认“待决策项全部按模型推荐”，即采用方案 B：在 Telegram 模型输出投递前剥离完整 `<oai-mem-citation>` 内部记忆引用块。

### 实施范围

| 文件 | 类型 | 说明 |
|---|---|---|
| `bot.py` | 源码实现 | 新增 `_strip_internal_oai_memory_citation_block`；在 `DELIVERABLE_KIND_MESSAGE` 投递路径中剥离内部引用块；清理后为空则跳过 Telegram 投递并推进 offset。 |
| `tests/test_plan_progress.py` | 回归测试 | 新增 5 个用例，覆盖完整块剥离、不完整块保留、普通 XML-like 文本保留、模型消息投递剥离、清理后为空时不发送。 |
| `AGENTS.md` | 证据更新 | Facts Table 新增 Telegram 模型回复展示约束，记录该行为变化与证据锚点。 |
| `docs/TASK_20260512_001_Telegram模型回复记忆引用标签泄露分析.md` | 任务文档 | 补充 develop 实施、验证结果、风险与剩余问题。 |

### TDD 红灯记录

baseline 通过后先补测试，未实现前执行：

```bash
python3.11 -m pytest -q tests/test_plan_progress.py
```

结果：`5 failed, 24 passed`。

失败点符合预期：

- `_strip_internal_oai_memory_citation_block` 尚不存在；
- `_deliver_pending_messages` 仍原样发送 `<oai-mem-citation>`；
- 仅包含内部引用块的输出仍被当作模型正文发送。

### 生产代码实现

核心规则：

1. 仅匹配独占行形式的完整 `<oai-mem-citation> ... </oai-mem-citation>` 块。
2. 不完整块 fail-open 保留原文，避免误删用户真实正文或模型截断内容。
3. 普通 XML-like 文本不处理。
4. 只在模型消息 `DELIVERABLE_KIND_MESSAGE` 的 Telegram 展示层清理，不修改 JSONL 原始会话。
5. 清理后为空则不发送空的“模型执行完成”消息，但正常推进 session offset，避免重复处理。

### 验证结果

已通过：

```bash
python3.11 -m pytest -q tests/test_plan_progress.py
```

- baseline：`24 passed, 2 warnings`
- TDD 绿灯首轮：`29 passed, 2 warnings`
- 回归第 2 轮：`29 passed`
- 回归第 3 轮：`29 passed`

已通过基础门禁：

```bash
python3.11 -m vibego_cli doctor
bash scripts/test_deps_check.sh
```

- 两轮均通过。
- `doctor` 第二轮输出中 `master_chat_id` 被程序脱敏显示为 `1`，不记录真实敏感值。

全量 pytest 结果：

```bash
python3.11 -m pytest -q
```

- 结果：`24 failed, 883 passed, 6 warnings`
- 失败全部集中在 `tests/test_parallel_flow.py`，错误为既有测试夹具未传 `send_mode`：
  - `ParallelLaunchSession.__init__() missing 1 required positional argument: 'send_mode'`
  - `_begin_parallel_launch() missing 1 required keyword-only argument: 'send_mode'`
- 本次未修改并行分支/并行启动链路；该失败与本次 Telegram memory citation 展示修复无直接调用关系，按 fail-closed 记录为既有旁路问题，不在本任务内扩大修复。

### Checklist 完成状态

- [x] 读取 `$HOME/.config/vibego/AGENTS.md`、根 `AGENTS.md`。
- [x] 扫描受影响子目录规约：当前仅发现根 `./AGENTS.md`，无更近 `AGENTS.evidence.json`。
- [x] 基于当前任务文档进入 develop。
- [x] 先跑受影响 baseline：`tests/test_plan_progress.py` 通过。
- [x] 先补失败测试并确认红灯。
- [x] 最小源码修复：只改 `bot.py` 展示层过滤。
- [x] 补充回归测试：5 个用例。
- [x] 更新 AGENTS 证据与 docs 任务文档。
- [x] 受影响测试双轮以上通过。
- [x] 基础门禁 `doctor` 与依赖检查双轮通过。
- [x] 尝试全量 pytest，并如实记录既有旁路失败。

### 影响点

- Codex/ClaudeCode/Gemini/Copilot 走 `DELIVERABLE_KIND_MESSAGE` 的最终模型消息展示层。
- Telegram 用户不再看到完整 `<oai-mem-citation>` 内部引用块。
- 仅包含内部引用块的事件不会发送空消息，但 offset 会推进。
- 原始 JSONL 会话文件、memory 文件、模型输出源数据不变。

### 风险与回滚

- 风险：未来平台新增其它内部元数据块仍可能透出。
  - 本次按最小影响原则只处理已复现的 `<oai-mem-citation>`。
- 风险：如果模型正文故意展示独占行的完整 `<oai-mem-citation>` 示例，会被过滤。
  - 当前判断：该格式是平台内部保留块，Telegram 用户侧展示价值低；如未来需要展示示例，可另行设计转义策略。
- 回滚：移除 `_strip_internal_oai_memory_citation_block` 调用、函数和对应测试即可；无数据库/配置/依赖回滚。
