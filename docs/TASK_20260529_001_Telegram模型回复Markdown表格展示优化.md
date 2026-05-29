# TASK_20260529_001 Telegram 模型回复 Markdown 表格展示优化

## 阶段

PLAN（只读排查 + 方案评审；本阶段未修改源码实现）

## 需求原文

用户反馈：Markdown 表格在终端显示友好，但在 Telegram 显示很丑，并提供截图。

附件证据：`/Users/david/.config/vibego/data/telegram/vibegobot/2026-05-29/20260529_055617771-e97901c0a67e.jpg`。截图中 Telegram 直接展示了 Markdown 管道表格的原始 `|` 和分隔线；表格较宽，中文内容在手机端换行后非常难读。

## 前置规约读取情况

- 已读取 `$HOME/.config/vibego/AGENTS.md`。
  - 锚点：`# 全局统一约束`、`## plan 阶段`、`## develop 阶段`。
- 已读取当前根目录 `AGENTS.md`。
  - 锚点：`# AGENTS.md（Strict Evidence Mode）`、`## 0) Non-negotiables`、`## 7) Testing & Quality Gates`、`## 9) Vibe Coding Workflow`。
- 当前仓库扫描仅发现根 `./AGENTS.md`；未发现 `PROJECT-STYLE.md`、`CODE-GUIDELINES.md`、`DESIGN.md` 或受影响子目录内更近规约文件。
  - 证据命令：`find .. -name AGENTS.md -o -name PROJECT-STYLE.md -o -name CODE-GUIDELINES.md -o -name DESIGN.md | sort`。
- CodeGraph 当前未初始化。
  - 证据：`codegraph_status(projectPath=/Users/david/hypha/tools/vibego)` 返回 `CodeGraph not initialized`。

## 仓库现状证据

1. 模型回复发送默认使用 Telegram parse mode。
   - 证据：`bot.py`（锚点：`_parse_mode_env = (os.environ.get("TELEGRAM_PARSE_MODE") or "Markdown").strip()`、`MODEL_OUTPUT_PARSE_MODE`、`_parse_mode_value`）。

2. 模型回复投递链路会把模型文本交给 `reply_large_text`。
   - 证据：`bot.py`（锚点：`async def _deliver_pending_messages`、`formatted_text = _prepend_completion_header(text_to_send)`、`delivered_payload = await reply_large_text(... overflow_mode="split")`）。

3. `reply_large_text` 当前关注长度、Markdown 解析降级和超长分片/附件，不包含“表格转 Telegram 友好结构”的展示转换。
   - 证据：`bot.py`（锚点：`async def reply_large_text`、`_prepare_model_payload_variants`、`_split_text_for_telegram_messages`、`bot.send_message`、`bot.send_document`）。

4. 当前仓库已有 `markdown-it-py` 依赖，可用于后续解析 Markdown 表格；尚未在生产代码中用于表格渲染。
   - 证据：`pyproject.toml`（锚点：`dependencies = [`、`markdown-it-py>=3.0.0,<4.0.0`）；代码扫描未发现 `MarkdownIt` / `markdown_it` 生产使用。

5. 历史任务已经把“模型超长回复”从 `.md` 附件改为 Telegram 正文分片。
   - 证据：`docs/TASK_0104_telegram消息.md`（锚点：`范围：仅模型回复`、`格式策略：优先保留 Markdown`、`_deliver_pending_messages(...) 仅在模型回复链路调用 reply_large_text(..., overflow_mode="split")`）。

6. 现有测试已经覆盖 `reply_large_text` 的短消息、附件、split 和 fenced code block 分片契约。
   - 证据：`tests/test_plan_progress.py`（锚点：`test_reply_large_text_short_message`、`test_reply_large_text_attachment`、`test_reply_large_text_split_mode_sends_multiple_messages_and_only_last_chunk_has_markup`、`test_reply_large_text_split_mode_preserves_fenced_code_blocks`）。

## 外部事实

Telegram Bot API 的格式能力是“基础格式”：粗体、斜体、下划线、删除线、spoiler、blockquote、inline link、pre-formatted code 等；官方列出的 MarkdownV2 / HTML 支持语法没有 Markdown 表格。

- 官方文档：`https://core.telegram.org/bots/api#formatting-options`
- 关键锚点：`Formatting options`、`MarkdownV2 style`、`HTML style`、`Only the tags mentioned above are currently supported.`

结论：这不是 vibego 单纯没有“开启表格渲染”，而是 Telegram Bot API 本身不支持 Markdown 表格实体。继续把 Markdown 表格原样发给 Telegram，只会显示原始管道符和分隔线。

## 问题本质

终端适合 Markdown 表格，是因为终端/CLI 环境通常使用等宽字体、横向空间大，且模型输出的管道表格可以按列对齐。

Telegram 私聊/手机端不适合宽表格，原因有三点：

1. Telegram 不解析 Markdown 表格。
2. 手机屏幕窄，宽表格必然折行。
3. 中文字符、英文、数字、emoji 混排时，肉眼列宽更难稳定。

因此，“让 Telegram 支持 Markdown 表格”不是好目标；更合理的目标是：同一份模型输出在 Telegram 展示层自动转成移动端友好的结构，同时保留原始 Markdown 表格的可审计/可复制入口。

## 方案对比

### A. 最小改造：把表格整段包成 fenced code block / `<pre>`

做法：检测 Markdown 管道表格后，把表格原文包成代码块或 HTML `<pre>` 发送。

优点：

- 改动最小。
- 不改变模型输出内容。
- 保留原始表格。

缺点：

- Telegram 仍不会真正渲染表格。
- 手机端仍然需要横向阅读或折行；中文宽表格仍丑。
- 只比截图略好，不解决根本体验。

适用：只想快速止血，不追求移动端阅读体验。

### B. 推荐改造：Telegram 展示层把 Markdown 表格转为“卡片/清单”

做法：仅在模型回复投递到 Telegram 前，识别 Markdown 管道表格，并把每一行转成移动端可读卡片。例如：

```text
📊 表格：库存扣减策略

1. 非空真实批次，库存充足
   - WRITE 阶段：Redis 原子预占
   - BATCH 阶段：正常扣减
   - 是否允许负库存：不产生负数

2. 非空真实批次，BATCH 时库存不足
   - WRITE 阶段：当时可能足够
   - BATCH 阶段：先 CAS，失败后强制负扣
   - 是否允许负库存：允许，但必须告警/记录
```

优点：

- 真正适配 Telegram 手机端。
- 文本可复制、可搜索、可转发。
- 不新增依赖时可先用轻量解析；若用既有 `markdown-it-py`，也不需要改构建依赖。
- 可只作用于 Telegram 模型回复展示层，不污染 Codex JSONL 和终端输出。

缺点：

- 不再保持表格原始视觉形态。
- 需要定义表格识别、列数上限、空单元格和转义管道符等规则。
- 需要较完整回归测试，避免误把普通 `|` 文本转坏。

适用：vibego 当前最主要场景，即用户在 Telegram 读模型总结、方案对比、决策矩阵。

### C. 高保真改造：把表格渲染成图片，同时附文本摘要

做法：将 Markdown 表格渲染为 PNG/JPG，以图片发送给 Telegram；正文给摘要或说明。

优点：

- 视觉最好，适合复杂矩阵、报告、宽表。
- 可做颜色、边框、字号和自动换行。

缺点：

- 图片不可搜索、复制困难。
- 需要图片渲染能力；若引入 Pillow/Playwright/HTML 渲染，会涉及新增依赖或运行时复杂度，属于高风险变更。
- 对长表、多表、深色模式、字体缺失、文件大小都有额外处理成本。

适用：正式报告、截图式交付，不适合作为所有模型回复的默认策略。

### D. 只靠提示词要求模型“不要输出表格”

做法：修改注入给模型的系统/任务提示，要求面向 Telegram 时不要使用 Markdown 表格。

优点：

- 代码改动很少甚至无需改代码。
- 源头输出更贴合 Telegram。

缺点：

- 不可靠：模型仍可能输出表格。
- 会影响终端可读性；终端场景本来适合表格。
- 无法处理已有表格、引用表格、用户要求表格等情况。

适用：作为补充约束，不适合作为唯一方案。

## 推荐结论

推荐采用 B，并保留 A 作为兜底：

1. 模型原始输出不改，仍保留 Markdown 表格给终端和 JSONL 审计。
2. Telegram 展示层新增“表格友好化渲染”：把 Markdown 管道表格转成卡片/清单。
3. 如果表格列数过多、单元格过长或解析不确定，则不要硬转卡片，改为：
   - 发送简短摘要；
   - 表格原文包成代码块；
   - 必要时附 `.md` 原文文件。
4. 后续可通过环境变量提供开关，例如 `TELEGRAM_TABLE_RENDER_MODE=cards|pre|off`，默认 `cards`。

## 契约变更

### 用户可见契约

- Telegram 中模型回复里的 Markdown 表格不再原样显示为 `| --- |` 管道表格。
- 简单表格会转成移动端友好的“编号卡片/字段清单”。
- 表格原始信息不得丢失；表头和每个单元格都必须在卡片中体现。
- 解析失败或表格过宽时必须 fail-safe：保留原文，不得生成误导性内容。

### 内部链路契约

- 只修改 Telegram 模型回复展示层；不改 Codex/Claude/Gemini 原始会话文件。
- 不改变任务详情、推送预览、request_user_input 等非模型回复场景，除非后续明确扩展。
- 表格转换应发生在 `_strip_internal_oai_memory_citation_block` 之后、`_prepend_completion_header` 之前或之后均可，但 hash 去重必须基于最终实际投递文本，避免重复投递。
- Markdown 转义/parse_mode 仍由现有 `_prepare_model_payload` / `_send_with_markdown_guard` 负责。

## 受影响目录

- `bot.py`
  - 影响点：模型回复投递前的展示转换、Markdown payload 生成前的原文重排。
- `tests/test_plan_progress.py` 或新增专门测试文件 `tests/test_telegram_table_renderer.py`
  - 影响点：表格识别、卡片转换、投递链路回归。
- `docs/`
  - 影响点：本任务文档与后续 develop 记录。
- 不受影响：`tasks/`、`command_center/`、SQLite 表结构、`scripts/`、CLI 启停逻辑。
  - 原因：本需求是 Telegram 展示层优化，不涉及任务/命令持久化、数据库迁移或 worker 启停。

## 设计规则草案

### 表格识别

建议先支持 GitHub 风格管道表格：

```text
| A | B |
|---|---|
| 1 | 2 |
```

识别要求：

- 至少三行：表头、分隔行、至少一行数据。
- 分隔行每列只允许 `-`、`:`、空格。
- 表头列数与数据列数不一致时 fail-safe，不转换。
- fenced code block 内的表格不转换，避免破坏代码示例。
- 转义管道符 `\|` 不应被当作分隔符。

### 卡片渲染

默认输出：

```text
📊 表格

1. <第一列值>
   - <第二列表头>：<第二列值>
   - <第三列表头>：<第三列值>

2. <第一列值>
   - <第二列表头>：<第二列值>
```

规则：

- 第一列默认作为卡片标题；若第一列为空，则使用 `第 N 行`。
- 第二列及之后按 `表头：值` 展示。
- 空值展示为 `（空）`。
- 单元格内换行统一压缩为空格。
- 多个表格逐个转换，非表格段落保持原样。

### 兜底策略

- 列数超过 5 或转换后长度超过原文 2 倍：使用 `<pre>`/代码块保留原表，避免刷屏。
- 单个表格超过 Telegram 单条限制：沿用现有 `reply_large_text(... overflow_mode="split")`。
- Markdown 解析失败：沿用现有 `_send_with_markdown_guard` 降级纯文本。

## 测试矩阵

1. 简单三列表格。
   - 入口：表格转换纯函数。
   - 预期：转成 2 个编号卡片，所有表头和值保留。

2. 用户截图中的四列表格。
   - 入口：表格转换纯函数。
   - 预期：第一列作为场景标题，WRITE/BATCH/是否允许负库存作为字段。

3. 表格前后有普通段落。
   - 入口：表格转换纯函数。
   - 预期：普通段落顺序不变，只转换表格块。

4. fenced code block 内含管道表格。
   - 入口：表格转换纯函数。
   - 预期：代码块保持原样，不转换。

5. 普通文本包含 `A | B` 但没有分隔行。
   - 入口：表格转换纯函数。
   - 预期：保持原样。

6. 列数不一致。
   - 入口：表格转换纯函数。
   - 预期：保持原样或走 pre 兜底，不生成错误卡片。

7. 超宽表格列数超过阈值。
   - 入口：表格转换纯函数。
   - 预期：走 pre/原文兜底。

8. `_deliver_pending_messages` 模型回复包含表格。
   - 入口：投递链路测试。
   - 预期：Telegram 收到卡片化文本，不包含 Markdown 分隔行 `|---|`。

9. reply_markup 快捷按钮。
   - 入口：投递链路测试。
   - 预期：按钮仍只挂最后一条消息，行为不回退。

10. memory citation 与表格同时存在。
    - 入口：投递链路测试。
    - 预期：先剥离 `<oai-mem-citation>`，再转换表格，最终不泄露内部引用块。

## 实施顺序

1. Baseline：运行现有受影响测试，确认当前基线。
   - 建议命令：`python3.11 -m pytest -q tests/test_plan_progress.py -k 'reply_large_text or deliver_pending_messages_uses_split_mode_for_model_output'`。

2. TDD 红灯：新增表格转换纯函数测试和投递链路测试，先确认失败。

3. 实现纯函数：例如 `_render_markdown_tables_for_telegram(text: str) -> str`。

4. 接入模型回复链路：在 `_deliver_pending_messages` 中对模型正文做 Telegram 展示层转换。

5. 绿灯验证：运行新增测试和既有 `reply_large_text` 测试。

6. 基础门禁：运行 `python3.11 -m vibego_cli doctor`、`bash scripts/test_deps_check.sh`。

7. 双轮回归：重复执行受影响 pytest 与基础门禁，结果写回本文档。

## 风险与回滚

### 风险

1. 误识别普通文本为表格。
   - 控制：必须有合法分隔行；代码块内不转换。

2. 表格内容丢失或字段错位。
   - 控制：列数不一致 fail-safe；测试覆盖截图样例。

3. 转换后文本变长，导致 Telegram 分片增多。
   - 控制：设置列数/长度阈值，过宽走 pre/附件兜底。

4. Markdown parse_mode 与转义冲突。
   - 控制：转换函数输出普通 Markdown 文本，继续复用现有 `_prepare_model_payload` 和 `_send_with_markdown_guard`。

### 回滚

- 删除或关闭 `_render_markdown_tables_for_telegram` 接入点即可恢复原样。
- 若提供环境变量开关，可临时设置 `TELEGRAM_TABLE_RENDER_MODE=off` 回退。
- 不涉及数据库、迁移、配置文件结构或依赖升级。

## 当前结论

Telegram 官方不支持 Markdown 表格，截图里的丑不是单纯渲染 bug，而是“终端友好格式直接投递到移动 IM”的媒介不匹配。

推荐下一步：若用户确认进入 develop，按“B. 卡片/清单化渲染 + A. pre 兜底”一次性完成实现与测试，不做新增依赖，不改模型原始输出。

---

## DEVELOP 实施记录（2026-05-29）

### 用户确认

用户已确认方案边界：终端回复、模型原始 JSONL 与审计内容保持 Markdown 表格不变；仅 vibego 在发送 Telegram 前，将模型回复中的 Markdown 管道表格转换为卡片/清单。

### 实施范围

| 文件 | 类型 | 说明 |
|---|---|---|
| `bot.py` | 源码实现 | 新增 Telegram 表格展示层转换；模型回复投递前自动卡片化 Markdown 管道表格；提供 `TELEGRAM_TABLE_RENDER_MODE` 与 `TELEGRAM_TABLE_MAX_CARD_COLUMNS` 兜底开关。 |
| `tests/test_plan_progress.py` | 回归测试 | 新增 4 个 TDD 用例，覆盖截图样式表格、普通段落、代码块内表格保护、模型投递链路中 citation 剥离后再表格卡片化。 |
| `AGENTS.md` | 证据更新 | Facts Table 增补 Telegram Markdown 表格展示约束。 |
| `docs/TASK_20260529_001_Telegram模型回复Markdown表格展示优化.md` | 任务文档 | 记录方案、实现、测试矩阵、验证结果、风险与回滚。 |

不受影响范围：`tasks/`、`command_center/`、SQLite 表结构、CLI 启停脚本、模型原始会话文件；原因是本次只改 Telegram 模型回复展示层。

### TDD Gate

#### Baseline

执行：

```bash
python3.11 -m pytest -q tests/test_plan_progress.py -k 'reply_large_text or deliver_pending_messages_uses_split_mode_for_model_output'
```

结果：

- ✅ `5 passed, 24 deselected`
- ⚠️ 既有 warning：`bot.py` 中 MarkdownV2 docstring 的 `invalid escape sequence`，非本次新增失败。

#### 红灯记录

先新增测试后执行：

```bash
python3.11 -m pytest -q tests/test_plan_progress.py -k 'render_markdown_tables_for_telegram or deliver_pending_messages_renders_markdown_table_before_telegram'
```

结果：

- 🔴 `4 failed, 29 deselected`
- 失败符合预期：`bot._render_markdown_tables_for_telegram` 尚不存在；模型投递链路仍包含原始 `|---|---|` 分隔行。

#### 绿灯记录

实现后再次执行同一命令：

```bash
python3.11 -m pytest -q tests/test_plan_progress.py -k 'render_markdown_tables_for_telegram or deliver_pending_messages_renders_markdown_table_before_telegram'
```

结果：

- ✅ `4 passed, 29 deselected`

### 实现细节

新增/调整：

1. `TELEGRAM_TABLE_RENDER_MODE`
   - 默认：`cards`。
   - 可选：`cards`、`pre`、`off`。
   - 非法值回退为 `cards` 并记录 warning。

2. `TELEGRAM_TABLE_MAX_CARD_COLUMNS`
   - 默认：`5`。
   - 表格列数超过阈值时，不强行卡片化，改为代码块保留原表，避免 Telegram 刷屏或误导。

3. `_render_markdown_tables_for_telegram(text)`
   - 只转换合法 Markdown 管道表格。
   - fenced code block 内的表格不转换。
   - 普通 `A | B` 正文没有分隔行时不转换。
   - 列数不一致时 fail-safe，保留原文。

4. `_deliver_pending_messages(...)`
   - 在剥离 `<oai-mem-citation>` 后、添加完成前缀前，执行 Telegram 展示层表格转换。
   - 终端与 JSONL 原文不变。

### 用户可见效果

模型原文：

```text
| 场景 | WRITE 阶段 | BATCH 阶段 |
|---|---|---|
| 空批次 | 不预占 | 直接扣减 |
```

Telegram 展示：

```text
📊 表格

1. 场景：空批次
   - WRITE 阶段：不预占
   - BATCH 阶段：直接扣减
```

### 测试覆盖对应矩阵

1. 四列表格：`test_render_markdown_tables_for_telegram_converts_pipe_table_to_cards`。
2. 普通段落 + 表格 + fenced code block：`test_render_markdown_tables_for_telegram_preserves_surrounding_text_and_code_blocks`。
3. 普通 `A | B` 正文：`test_render_markdown_tables_for_telegram_keeps_non_table_pipe_text`。
4. 模型投递链路 + memory citation：`test_deliver_pending_messages_renders_markdown_table_before_telegram`。
5. 既有长文本/按钮契约：`test_reply_large_text_*` 与 `test_deliver_pending_messages_uses_split_mode_for_model_output`。

### 验证结果

已执行：

```bash
python3.11 -m pytest -q tests/test_plan_progress.py -k 'reply_large_text or deliver_pending_messages_uses_split_mode_for_model_output or render_markdown_tables_for_telegram or deliver_pending_messages_renders_markdown_table_before_telegram'
```

结果：

- ✅ `9 passed, 24 deselected`

已执行：

```bash
python3.11 -m pytest -q tests/test_telegram_markdown_renderer.py
```

结果：

- ✅ `9 passed`

已执行：

```bash
python3.11 -m vibego_cli doctor
```

结果：

- ✅ `python_ok=true`
- ✅ `dependencies=[]`

后续最终收口前仍需补充：

- `bash scripts/test_deps_check.sh`
- 受影响 pytest 双轮复跑
- 10 个输入样例的转换冒烟

### 风险与回滚

风险：

1. 表格卡片化后比原文更长。
   - 控制：过宽表格走代码块保留；长消息仍沿用既有 split 机制。
2. 误识别正文。
   - 控制：必须有合法分隔行，代码块内不转换。
3. Markdown parse_mode 冲突。
   - 控制：转换后仍走既有 `_prepare_model_payload` 与 `_send_with_markdown_guard`。

回滚：

- 环境变量设置 `TELEGRAM_TABLE_RENDER_MODE=off` 可关闭转换。
- 源码回滚时移除 `_deliver_pending_messages` 中 `_render_markdown_tables_for_telegram(...)` 调用即可恢复原样。

### 最终验证补充

修正既有 `test_codex_mixed_final_answer_prefers_response_item_once` 的测试替身签名，使其符合当前 `_maybe_send_plan_confirm_prompt(..., plan_text=...)` 契约；该调整不削弱断言，额外断言 `plan_text == final_text`。

最终执行：

```bash
python3.11 -m pytest -q tests/test_plan_progress.py
```

结果：

- ✅ `33 passed`

最终执行：

```bash
python3.11 -m pytest -q tests/test_plan_progress.py -k 'reply_large_text or deliver_pending_messages_uses_split_mode_for_model_output or render_markdown_tables_for_telegram or deliver_pending_messages_renders_markdown_table_before_telegram'
```

结果：

- ✅ `9 passed, 24 deselected`

最终执行：

```bash
python3.11 -m pytest -q tests/test_telegram_markdown_renderer.py
```

结果：

- ✅ `9 passed`

最终执行：

```bash
python3.11 -m vibego_cli doctor
```

结果：

- ✅ `python_ok=true`
- ✅ `dependencies=[]`

最终执行：

```bash
bash scripts/test_deps_check.sh
```

结果：

- ✅ 依赖检查通过
- ✅ `aiogram` / `aiohttp` / `aiosqlite` 已安装

最终执行 10 个输入样例冒烟，覆盖简单表格、段落包围、普通管道正文、代码块内表格、四列表格、转义管道、超宽表格、列数不一致、对齐分隔符、无表格内容。

结果：

- ✅ 需要转换的表格发生转换。
- ✅ 普通管道正文不转换。
- ✅ 代码块内表格不转换。
- ✅ 列数不一致 fail-safe 保留原文。
- ✅ 超宽表格走代码块兜底。

## 完成状态

- [x] 终端与 JSONL 原始 Markdown 表格保持不变。
- [x] Telegram 模型回复发送前自动卡片化合法 Markdown 管道表格。
- [x] 代码块内表格不转换。
- [x] 普通 `A | B` 正文不误转换。
- [x] memory citation 剥离与表格转换可串联工作。
- [x] 过宽表格有代码块兜底。
- [x] 受影响测试与基础门禁通过。
