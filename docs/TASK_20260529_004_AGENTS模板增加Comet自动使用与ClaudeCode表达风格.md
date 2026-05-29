# TASK_20260529_004 AGENTS 模板增加 Comet 自动使用与 Claude Code 表达风格

## 1. 任务背景

用户希望把两类长期偏好写入 AGENTS 模板，避免后续每次手动提醒：

1. 当 Comet 可用时，研发类任务自动优先使用 Comet 工作流。
2. Codex/其他 agent 默认采用接近 Claude Code 的清晰工程沟通风格。

## 2. 受影响目录与文件

| 范围 | 文件 | 变更说明 |
|---|---|---|
| 仓库模板 | `AGENTS-template.md` | 新增 `Comet 自动调用规则` 与 `Claude Code 风格表达规则`。 |
| 测试资产 | `tests/test_agents_template_migration.py` | 新增模板内容回归测试，防止后续模板回退。 |
| pipx 安装副本 | `/Users/david/.local/pipx/venvs/vibego/AGENTS-template.md` | 与仓库模板同步，避免当前 pipx 运行路径继续使用旧模板。 |
| 当前全局规约副本 | `/Users/david/.config/vibego/AGENTS.md` | 已刷新 vibego 注入区块。 |
| Codex 当前规约副本 | `/Users/david/.codex/AGENTS.md`、`/Users/david/.codex/agents.md` | 已刷新 vibego 注入区块，供 Codex 新会话读取。 |
| Claude/Gemini 当前规约副本 | `/Users/david/.claude/CLAUDE.md`、`/Users/david/.gemini/GEMINI.md` | 已刷新 vibego 注入区块，保持跨模型一致。 |

## 3. 契约变更

### 3.1 Comet 自动调用契约

- 当环境已安装并可用 `comet` skill、`/comet` 命令或同等 Comet 工作流时，研发类任务不需要用户手动输入 `/comet`，agent 必须优先启用。
- 适用场景：新需求、功能迭代、Bug 修复、复杂重构、跨文件变更、需要设计/实现/验证/归档闭环的任务。
- 不适用场景：用户明确要求不用 Comet、纯问答/翻译/简单解释/只读查询/一次性命令提示等轻量任务。
- Comet 不可绕过用户决策点；方案确认、构建方式、验证失败处理、分支处理等仍必须暂停等待用户确认。
- 创建新变更必须走 `/comet` 或 `comet-open`，不得直接用 `/opsx:new` 替代。

### 3.2 Claude Code 风格表达契约

- 默认简体中文。
- 先给结论，再给原因、修法和验证方式。
- Bug/故障类回复收敛为“现象 -> 影响 -> 根因 -> 修法 -> 验证”。
- 少用术语、少贴过程日志、少堆文件清单；必要术语必须解释影响。
- 未实际验证不得宣称已完成、已修复或已验证。
- Telegram/移动端场景优先短段落、清单和明确标题。

## 4. 测试矩阵

| 测试项 | 命令 | 结果 | 说明 |
|---|---|---|---|
| Baseline（修改前） | `python3.11 -m pytest -q tests/test_agents_template_migration.py` | 失败：2 failed, 3 passed | 既有 `ENFORCED_AGENTS_NOTICE` 相关测试在本任务前已失败，未纳入本次修复范围。 |
| 新增测试红灯 | `python3.11 -m pytest -q tests/test_agents_template_migration.py -k 'comet_for_complex_workflows or claudecode_like_communication'` | 失败：2 failed | 证明旧模板未包含新增规则。 |
| 新增测试绿灯 | `python3.11 -m pytest -q tests/test_agents_template_migration.py -k 'comet_for_complex_workflows or claudecode_like_communication'` | 通过：2 passed, 5 deselected | 新模板规则已被测试固化。 |
| 同文件完整回归 | `python3.11 -m pytest -q tests/test_agents_template_migration.py` | 失败：2 failed, 5 passed | 剩余 2 个失败为修改前已存在的 baseline 失败，原因是 `bot.ENFORCED_AGENTS_NOTICE` 当前仅为 `以下是用户需求描述：`。 |
| 同步副本验证 | `rg -n 'Comet 自动调用规则|Claude Code 风格表达规则|必须优先启用 `comet` skill|先给结论，再给原因' ...` | 通过 | 仓库模板、pipx 副本、vibego/Codex/Claude/Gemini 当前规约副本均包含新增规则。 |

## 5. 实施顺序

1. 读取当前仓库规约与模板同步路径。
2. 新增模板内容回归测试，并先确认失败。
3. 更新仓库 `AGENTS-template.md`。
4. 同步 pipx 模板副本与当前全局模型规约副本。
5. 运行新增聚焦测试并记录完整同文件回归的既有失败。
6. 写入本文档，沉淀变更、验证和风险。

## 6. 风险与回滚

| 风险 | 影响 | 缓解 |
|---|---|---|
| Comet 自动触发过度 | 纯问答也可能被误判为研发任务 | 模板已明确“不适用场景”，轻量任务不启用 Comet。 |
| 与现有 plan/develop 规则冲突 | agent 可能不知道优先级 | 模板已明确 Comet 不可绕过本 AGENTS 的 TDD、文档沉淀和最终回复字段。 |
| 当前 Codex 会话不立即生效 | 当前会话不会重新加载全局 AGENTS | 需要重启 Codex/新开会话。 |
| 既有测试失败未修 | `tests/test_agents_template_migration.py` 不能全绿 | 已记录为修改前 baseline 问题；本任务仅验证新增规则通过。 |

回滚方式：

1. 还原 `AGENTS-template.md` 中新增的两个章节。
2. 还原 `tests/test_agents_template_migration.py` 中新增的两个测试。
3. 重新执行 `sync_agents_block` 刷新 `/Users/david/.config/vibego/AGENTS.md`、`/Users/david/.codex/AGENTS.md`、`/Users/david/.codex/agents.md`、`/Users/david/.claude/CLAUDE.md`、`/Users/david/.gemini/GEMINI.md`。
4. 如需回滚 pipx 当前副本，同步还原 `/Users/david/.local/pipx/venvs/vibego/AGENTS-template.md`。

## 7. 完成状态

- [x] 新增 Comet 自动调用规则。
- [x] 新增 Claude Code 风格表达规则。
- [x] 新增模板内容回归测试。
- [x] 同步 pipx 模板副本。
- [x] 同步当前 vibego/Codex/Claude/Gemini 规约副本。
- [x] 记录新增测试通过与既有 baseline 失败。

## 8. 追补变更：所有任务默认走 Comet（2026-05-29）

### 8.1 用户追补要求

用户明确指出：“所有任务都给我走 comet 流程，不只是复杂任务”。因此本轮将原来的“复杂研发任务自动优先使用 Comet”收紧为“所有用户任务默认必须走 Comet 工作流”。

### 8.2 契约追补

- 所有用户任务默认先进入 Comet 的任务识别与阶段判定。
- 覆盖范围从研发任务扩展到：文档/配置调整、只读调研、解释说明、简单问答、一次性命令建议等。
- 不因为任务看起来简单而跳过 Comet。
- 轻量任务优先走 `comet-tweak`；Bug/热修复优先走 `comet-hotfix`；触发升级条件时回到完整 Comet 流程。
- 只保留两个例外：
  1. 用户明确要求不要使用 Comet；
  2. 当前环境找不到 Comet skill/命令，或当前会话尚未加载 Comet skill 且无法等效执行。

### 8.3 受影响文件

| 文件 | 变更 |
|---|---|
| `AGENTS-template.md` | 将 Comet 自动调用规则改为“所有用户任务默认必须走 Comet 工作流”。 |
| `/Users/david/.local/pipx/venvs/vibego/AGENTS-template.md` | 同步 pipx 当前模板副本。 |
| `/Users/david/.config/vibego/AGENTS.md` | 刷新当前 vibego 全局规约副本。 |
| `/Users/david/.codex/AGENTS.md`、`/Users/david/.codex/agents.md` | 刷新 Codex 规约副本。 |
| `/Users/david/.claude/CLAUDE.md`、`/Users/david/.gemini/GEMINI.md` | 刷新 Claude/Gemini 规约副本。 |
| `tests/test_agents_template_migration.py` | 测试新增“所有用户任务默认必须走 Comet 工作流”和“不因为任务看起来简单而跳过 Comet”的断言。 |

### 8.4 验证记录

| 测试项 | 命令 | 结果 |
|---|---|---|
| 红灯确认 | `python3.11 -m pytest -q tests/test_agents_template_migration.py -k 'comet_for_complex_workflows'` | 失败：缺少“所有用户任务默认必须走 Comet 工作流”。 |
| 聚焦绿灯 | `python3.11 -m pytest -q tests/test_agents_template_migration.py -k 'comet_for_complex_workflows or claudecode_like_communication'` | 通过：`2 passed, 5 deselected`。 |
| 同文件完整回归 | `python3.11 -m pytest -q tests/test_agents_template_migration.py` | 失败：`2 failed, 5 passed`；失败项仍为修改前已存在的 `ENFORCED_AGENTS_NOTICE` baseline 问题。 |

### 8.5 风险与边界

- 风险：纯问答也走 Comet 会增加流程感和 token 消耗，但这是用户明确偏好。
- 边界：如果当前会话尚未加载 Comet skill，agent 必须说明降级原因，不能假装已走 Comet。
- 回滚：还原本节相关模板段落与测试断言，并重新同步各全局规约副本。
