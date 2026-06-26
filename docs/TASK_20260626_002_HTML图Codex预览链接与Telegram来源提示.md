# TASK_20260626_002 HTML 图 Codex 预览链接与 Telegram 来源提示

## 1. 背景与现象

用户反馈：Telegram 侧现在已经能在点击下载 HTML 文件后预览；但 Codex App 侧仍只看到类似 `docs/TASK_xxx.html` 的文本路径，不能直接点击预览。

仓库证据：

- `vibego_cli/data/skills/vibe-diagram/SKILL.md`（锚点：`交付铁律`）目前强调 Telegram 场景必须显式引用项目内 `.html` 文件以触发 worker `send_document`，但没有单独定义 Codex App 的可点击预览链接口径。
- `bot.py`（锚点：`ENFORCED_AGENTS_NOTICE`）当前 Telegram 入模前缀只有 `以下是用户需求描述：`，没有告诉模型“本次来源是 Telegram / 移动端”。
- `bot.py`（锚点：`_collect_model_response_local_documents`、`_send_model_response_local_documents`、`_deliver_pending_messages_locked`）已经支持 Telegram 展示层在模型最终回复引用项目内 HTML 后补发 `send_document`。
- `tests/test_plan_progress.py`（锚点：`test_deliver_pending_messages_sends_project_local_html_as_document_after_text`）已覆盖 Telegram HTML 文件附件补发。

## 2. 根因判断

当前缺口不是 Telegram 附件链路，而是“同一个 HTML 图交付在不同来源/端上的最终回复契约不同”：

- Telegram 需要看到文件附件；worker 可以通过最终文本中的项目内 HTML 路径补发附件。
- Codex App 没有 worker 补发层；如果最终回复只写裸路径，UI 只能显示裸路径。
- skill 目前把“附件发送”作为强约束，但缺少“Codex 默认用 `file://` Markdown 链接”的交付格式。
- Telegram 入模提示也没有显式来源上下文，模型无法判断当前最终回复应该偏向移动端文件卡片，还是 Codex App 可点击链接。

## 3. 推荐方案

### 3.1 方案 A（推荐）：单 skill，按来源输出不同交付块，Codex 为默认

核心规则：

1. `vibe-diagram` 增加 `交付目标识别` 小节：
   - 未检测到来源上下文时，默认按 Codex App 交付。
   - Codex App：最终回复必须给 Markdown `file://` 链接，例如 `[打开 HTML 预览](file:///Users/.../docs/xxx.html)`，并保留绝对路径作为 fallback。
   - Telegram/vibego：最终回复必须显式引用项目内 `.html` 路径，触发 worker `send_document`；可以同时给 Codex `file://` 链接，但不能只给裸路径。
2. `bot.py` 在普通 Telegram 业务提示的 `以下是用户需求描述：` 后追加轻量来源上下文：
   - `请求来源：vibego Telegram worker / 移动端。`
   - `HTML 图交付：优先保证 Telegram 文件附件；最终回复仍应引用项目内 .html 文件。`
3. 不拆成两个 skill，避免规则漂移。

优点：

- Codex App 直接使用时默认可点击预览，符合用户当前诉求。
- Telegram 仍保留已有附件发送链路，不倒退。
- 一个 skill 维护一套图形质量规则，只在“交付块”分端。
- 不需要新增依赖、后台服务或本地 HTTP 预览端口。

缺点：

- `file://` 链接能否被 Codex App 以“内置浏览器”打开，需要用真实 Codex App 点击验证；若客户端安全策略限制，仍会退回为普通链接或系统浏览器。
- Telegram 最终文本中可能同时出现 Codex 链接与项目路径，需通过文案控制降低噪音。

### 3.2 方案 B：最终回复始终同时发 Codex 链接 + Telegram 路径

优点：实现最简单，source context 依赖少。

缺点：所有端都看到两套入口，Telegram 移动端噪音增加；长期容易变成“路径清单”，违背当前 HTML 图交付铁律。

### 3.3 方案 C：为 Codex 启动本地 HTTP 预览服务

优点：`http://127.0.0.1:<port>/...` 链接最像浏览器链接，也更容易被客户端打开。

缺点：需要服务生命周期、端口管理、安全边界和清理；对一个 HTML 文件预览来说过重，不建议作为默认。

## 4. 受影响目录

| 路径 | 影响 | 是否必须 |
| --- | --- | --- |
| `vibego_cli/data/skills/vibe-diagram/SKILL.md` | 增加 Codex 默认预览链接、Telegram 来源交付块、双端 fallback 规则。 | 是 |
| `bot.py` | 在 Telegram 普通业务 prompt 注入来源上下文；保持 slash/control 命令不注入。 | 是 |
| `tests/test_builtin_skills_injection.py` | 增加 skill 新规则断言，防止回退成只发路径。 | 是 |
| `tests/test_task_description.py` 或 `tests/test_agents_template_migration.py` | 覆盖 Telegram prompt source context 拼接与跳过命令/idempotence。 | 是 |
| `tests/test_plan_progress.py` | 增补 `file://...html` Markdown 链接仍可被 Telegram document collector 识别的回归。 | 建议 |
| `AGENTS.md` | 实现后补充事实表：Codex/Telegram HTML 交付分端契约。 | 是 |
| `docs/` | 记录设计、实施顺序、测试矩阵、风险与回滚。 | 是 |

## 5. 契约变更

### 5.1 Codex App 默认契约

- HTML 图最终回复必须包含可点击 Markdown 链接：`[打开 HTML 预览](file:///绝对路径/xxx.html)`。
- 同时保留绝对路径 fallback，避免客户端不支持 `file://` 时完全不可达。
- 不要求发送附件；Codex 没有 Telegram worker 的 `send_document` 展示层。

### 5.2 Telegram/vibego 契约

- Telegram 入模 prompt 显式标明来源是移动端/Telegram。
- HTML 图最终回复必须引用项目内 `.html` 路径，worker 继续补发 `send_document`。
- 可同时携带 Codex `file://` 链接，但文案必须短，避免移动端噪音。

### 5.3 不变边界

- 不新增依赖。
- 不启动本地 HTTP 服务。
- 不放宽 HTML 文件白名单：Telegram 自动补发仍只允许项目目录内 HTML。
- 图片直发、近期 `/tmp` 图片白名单不变。

## 6. 测试矩阵

| 场景 | 期望 | 建议测试 |
| --- | --- | --- |
| Codex 默认 skill 规则 | skill 明确要求 `file://` Markdown 链接 + fallback 绝对路径 | `tests/test_builtin_skills_injection.py` |
| Telegram 来源提示 | 普通业务 prompt 在用户正文前包含来源上下文 | `tests/test_task_description.py` 聚焦 `_prepend_enforced_agents_notice` 或新 helper |
| slash/control 命令 | `/compact`、`/goal` 等不被来源上下文破坏 | 复用现有跳过用例 |
| idempotence | 已注入来源上下文的 prompt 不重复注入 | 新增参数化用例 |
| Telegram HTML 附件 | 项目内 HTML 仍触发 `send_document` | 现有 `test_deliver_pending_messages_sends_project_local_html_as_document_after_text` |
| `file://` HTML 链接 | `[打开](file:///.../docs/a.html)` 仍能被 collector 解析为项目内文件 | 新增 `test_collect_model_response_local_documents_accepts_file_uri_link` |
| 项目外 HTML | 不自动发送 | 保留/新增负向用例 |

## 7. 实施顺序

1. 先补测试：skill 断言、Telegram 来源上下文、`file://` HTML collector。
2. 更新 skill 交付铁律，加入 `Codex 默认 / Telegram 来源 / 双端 fallback`。
3. 在 `bot.py` 抽一个小 helper 构造来源上下文，接到普通业务 prompt 注入点。
4. 跑聚焦测试。
5. 更新 `AGENTS.md` 事实表与本任务文档完成状态。
6. 提醒重启对应 worker 后 Telegram prompt 注入与 skill 同步才生效。

## 8. 风险与回滚

| 风险 | 影响 | 缓解 | 回滚 |
| --- | --- | --- | --- |
| Codex App 不支持 `file://` 在内置浏览器打开 | 仍可能只打开系统浏览器或不打开 | 保留绝对路径 fallback；可后续再评估 localhost 预览服务 | 移除 Codex `file://` 规则 |
| Telegram 文本噪音增加 | 移动端看到 Codex 链接 | 仅在交付块短句展示，附件仍是主交付 | 保留 Telegram 路径规则，删除 Codex link 输出 |
| 来源上下文误伤控制命令 | slash 命令语义破坏 | 测试覆盖 slash/control prompt 不注入 | 回滚 `bot.py` source context helper |
| 双端规则漂移 | skill 变难维护 | 不拆 skill，只分交付块 | 回滚到单一附件规则 |

## 9. 当前状态

- [x] 已完成现状取证。
- [x] 已给出推荐方案。
- [ ] 待用户确认方案。
- [ ] 待确认后进入 TDD 实现。

## 10. 用户确认与 Telegram 交付口径（2026-06-26）

用户确认：必须保持 **单 skill**；Codex 侧采用 `file://` Markdown 链接方式展示，因为在 Codex App 中可以直接点击打开预览。

Telegram 侧推荐口径如下：

1. **Telegram 不使用 `file://` 作为主入口**：移动端无法访问本机 `/Users/...` 路径，`file://` 对 Telegram 用户没有稳定意义。
2. **Telegram 主交付仍是 HTML 文件附件卡片**：模型最终回复必须引用项目内 `.html/.htm` 文件路径，让 vibego worker 的 `_collect_model_response_local_documents` 与 `_send_model_response_local_documents` 自动补发 `send_document`。
3. **Telegram 可选 PNG fallback**：如果 HTML 图适合首屏预览或用户明确要求图片预览，可同时引用项目内 PNG，让现有图片直发链路补发 `send_photo`；但 PNG 不能替代 HTML。
4. **最终回复文案应按来源变短**：Telegram 来源下只需要类似“已生成 HTML 图，文件已作为附件发送；如未看到附件，可打开/下载下方 HTML 文件。”，避免把 Codex 的 `file://` 链接作为移动端主文案。
5. **skill 仍统一维护**：同一个 `vibe-diagram` skill 增加“交付目标”规则：默认 Codex，可点击 `file://`；来源为 vibego/Telegram 时，主交付为 Telegram 文件附件，文本中保留项目内 HTML 路径触发 worker。

因此，落地实现时应同时改两处：

- `vibego_cli/data/skills/vibe-diagram/SKILL.md`：新增“Codex 默认 / Telegram 来源”交付规则。
- `bot.py`：在普通 Telegram 业务 prompt 的规约前缀中注入来源上下文，让模型知道本轮来自 Telegram 移动端，应优先触发附件卡片交付。

## 11. 实施计划（2026-06-26，用户确认后）

**目标**：保持单一 `vibe-diagram` skill；Codex 默认用可点击 `file://` HTML 链接；Telegram 来源不需要 PNG，主交付为 HTML 文件附件卡片。

**实现边界**：

- 不新增依赖。
- 不新增本地 HTTP 服务。
- 不生成 PNG，不要求 PNG fallback。
- 不改变 Telegram 自动发送文件的安全白名单：仍只允许项目目录内 HTML。
- 不改变 slash/control 命令语义。

**文件计划**：

| 文件 | 操作 | 说明 |
| --- | --- | --- |
| `tests/test_builtin_skills_injection.py` | 修改 | 新增 skill 规则断言：Codex 默认 `file://`；Telegram 来源主交付为 HTML 文件附件；不强制 PNG。 |
| `tests/test_task_description.py` | 修改 | 新增/调整 `_prepend_enforced_agents_notice` 用例，覆盖 Telegram 来源上下文、幂等、slash 跳过。 |
| `tests/test_plan_progress.py` | 修改 | 新增 `file://...html` Markdown 链接仍可被 HTML document collector 解析的用例。 |
| `vibego_cli/data/skills/vibe-diagram/SKILL.md` | 修改 | 增加“交付目标识别”：Codex 默认、Telegram 来源、无 PNG 要求。 |
| `bot.py` | 修改 | 将 Telegram 来源上下文追加到普通业务 prompt 前缀中，避免模型在 Telegram 端输出 Codex 主入口。 |
| `AGENTS.md` | 修改 | 更新 Facts Table，记录 HTML 图双端交付契约。 |
| `docs/TASK_20260626_002_HTML图Codex预览链接与Telegram来源提示.md` | 修改 | 记录 TDD、验证结果、风险与回滚。 |

**TDD 顺序**：

1. 运行受影响基线：`tests/test_builtin_skills_injection.py`、`tests/test_task_description.py::test_prepend_enforced_agents_notice_cases`、`tests/test_plan_progress.py::test_deliver_pending_messages_sends_project_local_html_as_document_after_text`。
2. 先补红灯测试：skill 断言、Telegram 来源上下文、`file://` document collector。
3. 确认红灯失败原因是功能缺失，不是测试错误。
4. 最小修改 skill 与 `bot.py`。
5. 跑聚焦绿灯测试。
6. 跑相关组合回归：内置 skill 注入、HTML 附件、普通 prompt 注入。
7. 更新 AGENTS 与任务文档。

**风险与回滚**：

| 风险 | 缓解 | 回滚 |
| --- | --- | --- |
| Telegram prompt 前缀变长 | 仅追加短来源上下文；不触碰附件/命令正文 | 恢复 `ENFORCED_AGENTS_NOTICE` 旧值或删除来源上下文 helper |
| 控制命令被误注入 | 保持 `_prepend_enforced_agents_notice` 对 `/` 开头跳过，并补测试 | 回滚 helper |
| Codex `file://` 链接客户端兼容性 | 已由用户截图确认可点击；仍保留绝对路径 fallback | skill 恢复裸路径+附件规则 |
| HTML document collector 误发项目外文件 | 不放宽白名单；仅接受 resolve 后仍在项目根内的文件 | 保持现有 collector 或回滚新增 file URI 测试相关修改 |


## 12. 开发记录与验证（2026-06-26）

### 12.1 TDD 红灯

| 测试 | 红灯结果 | 含义 |
| --- | --- | --- |
| `tests/test_builtin_skills_injection.py::test_html_visual_skill_pack_exists_and_is_packaged` | 失败：skill 缺少 `Codex 默认`、`file://`、`Telegram 来源`、`不需要 PNG` 等规则 | 证明旧 skill 没有双端交付契约。 |
| `tests/test_task_description.py::test_prepend_enforced_agents_notice_cases` | 失败：实际 prompt 只有 `以下是用户需求描述：`，缺少来源上下文 | 证明 Telegram 入模前缀未告知来源。 |
| `tests/test_task_description.py::test_prepend_enforced_agents_notice_describes_telegram_html_delivery` | 失败：缺少 `请求来源：vibego Telegram worker / 移动端` | 证明 Telegram 来源口径未落地。 |

### 12.2 实现内容

| 文件 | 修改 |
| --- | --- |
| `bot.py` | 新增 `TELEGRAM_SOURCE_CONTEXT_NOTICE`；普通非 slash prompt 注入“vibego Telegram worker / 移动端”来源上下文，并明确 Telegram HTML 图主交付是项目内 `.html/.htm` 文件附件卡片、不需要 PNG、不要把 `file://` 作为 Telegram 主入口。 |
| `vibego_cli/data/skills/vibe-diagram/SKILL.md` | 交付铁律改为单 skill 分来源：Codex 默认输出可点击 Markdown `file://` 链接 + 绝对路径兜底；Telegram 来源触发 HTML 文件附件卡片；不需要 PNG，除非用户明确要求图片预览。 |
| `tests/test_builtin_skills_injection.py` | 增加 Codex/Telegram 双端交付规则断言。 |
| `tests/test_task_description.py` | 更新 prompt 前缀用例并新增 Telegram HTML 交付来源上下文断言。 |
| `tests/test_agents_template_migration.py` | 将旧的 ENFORCED 文案断言更新为当前真实契约。 |
| `tests/test_plan_progress.py` | 新增 `file://` Markdown 链接仍能被 HTML document collector 识别的回归测试。 |
| `AGENTS.md` | 增加 HTML 图 Codex/Telegram 双端交付事实表。 |

### 12.3 绿灯验证

| 验证命令 | 结果 |
| --- | --- |
| `BOT_TOKEN=baseline-token MASTER_CHAT_ID=1 MASTER_USER_ID=1 python3.11 -m pytest -q tests/test_builtin_skills_injection.py tests/test_agents_template_migration.py::test_enforced_notice_keeps_user_requirement_header tests/test_agents_template_migration.py::test_enforced_notice_adds_user_requirement_header_before_prompt tests/test_task_description.py::test_prepend_enforced_agents_notice_cases tests/test_task_description.py::test_prepend_enforced_agents_notice_describes_telegram_html_delivery tests/test_plan_progress.py::test_deliver_pending_messages_sends_project_local_html_as_document_after_text tests/test_plan_progress.py::test_collect_model_response_local_documents_accepts_file_uri_link` | 通过：`21 passed in 0.09s`。 |

### 12.4 剩余待验证

- 真实运行中的 Telegram worker 需要重启后才会同步新的 `AGENTS` skill 内容与 `bot.py` prompt 前缀。
- 未做真实 Telegram 客户端端到端实发验证；本轮通过单元测试锁定 HTML 附件收集与发送契约。
- 未跑全量 pytest；本轮按影响范围跑聚焦测试。

### 12.5 当前完成状态

- [x] 单 skill，不拆分。
- [x] Codex 默认交付：`file://` Markdown 链接 + 绝对路径兜底。
- [x] Telegram 交付：HTML 文件附件卡片为主。
- [x] 不需要 PNG。
- [x] 普通 Telegram prompt 注入来源上下文。
- [x] 聚焦测试通过。

### 12.6 最终聚焦回归补充

| 验证命令 | 结果 |
| --- | --- |
| `BOT_TOKEN=baseline-token MASTER_CHAT_ID=1 MASTER_USER_ID=1 python3.11 -m pytest -q tests/test_builtin_skills_injection.py tests/test_agents_template_migration.py::test_enforced_notice_keeps_user_requirement_header tests/test_agents_template_migration.py::test_enforced_notice_adds_user_requirement_header_before_prompt tests/test_agents_template_migration.py::test_shell_defaults_use_agents_template tests/test_agents_template_migration.py::test_packaging_lists_agents_template tests/test_task_description.py::test_prepend_enforced_agents_notice_cases tests/test_task_description.py::test_prepend_enforced_agents_notice_describes_telegram_html_delivery tests/test_plan_progress.py::test_deliver_pending_messages_sends_project_local_html_as_document_after_text tests/test_plan_progress.py::test_collect_model_response_local_documents_accepts_file_uri_link` | 通过：`23 passed in 0.09s`。 |
| `python3.11 /Users/david/.codex/skills/.system/skill-creator/scripts/quick_validate.py vibego_cli/data/skills/vibe-diagram` | 通过：`Skill is valid!`。 |
| `python3.11 -m py_compile bot.py` | 通过：无输出，exit code 0。 |

### 12.7 已知非本次失败

执行整个 `tests/test_agents_template_migration.py` 时，`test_agents_template_requires_comet_for_complex_workflows` 失败，原因是该测试仍期待旧的 `## Comet 自动调用规则`，而当前 `AGENTS-template.md` 已不包含该旧规则。该失败在本轮修改前已属于已知基线问题，未在本任务中扩大范围修复。
