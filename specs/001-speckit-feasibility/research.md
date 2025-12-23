# Research: vibego × Spec Kit（speckit）互补集成可行性

**Date**: 2025-12-22  
**Feature**: `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/spec.md`  
**Plan**: `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/plan.md`  
**Assessment Report**: `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/assessment-report.md`

## 背景与现状（可验证事实）

- vibego：通过 Telegram 驱动本地终端 AI CLI（Codex/ClaudeCode），并提供任务管理/缺陷报告等能力。
  - 证据：`/Users/david/hypha/tools/vibego/README.md`
- Spec Kit：GitHub 开源的 Spec-Driven Development 工具包，包含 `specify` CLI 与 speckit 命令工作流（如
  `/speckit.specify`、`/speckit.plan`）。
  - 证据（官方）：https://github.com/github/spec-kit 与其文档 https://github.github.io/spec-kit/
- 当前 vibego 仓库已包含 `.specify/` 脚本与模板（用于 feature 编号、分支创建、spec/plan 模板初始化等），
  与 Spec Kit 描述的 speckit 工作流高度一致。
  - 证据：`/Users/david/hypha/tools/vibego/.specify/scripts/bash/create-new-feature.sh`、
    `/Users/david/hypha/tools/vibego/.specify/scripts/bash/setup-plan.sh`

## 关键问题

1. vibego 与 Spec Kit 在价值上是否互补？互补点在哪里？
2. 结合方式有哪些可选方案？成本/风险/收益如何？
3. 如何在不破坏 vibego 安全边界与工程约束的前提下落地？

## 结论与决策（Decision / Rationale / Alternatives）

### Decision 1：以“工作流互补”为主，避免在 vibego 内强绑定 `specify` CLI

**Decision**: 将 Spec Kit 的核心价值定位为“可复制的 SDD 工作流与产物结构”，优先复用仓库内已有
`.specify/scripts` 与模板来实现工作流；把 `specify` CLI（上游）作为可选依赖，用于初始化/同步模板。

**Rationale**:
- 上游 Spec Kit 的 `specify` CLI 明确要求 Python 3.11+ 与 uv（工具链门槛更高）；而 vibego 项目自身支持
Python >=3.9（降低安装门槛，面向更广泛环境）。
- 当前仓库已有 `.specify/`，可直接支撑 speckit 的“spec → plan → tasks”产物结构，减少耦合与引入成本。

**Alternatives considered**:
- A) 直接把 `specify-cli` 作为 vibego 的强依赖：升级成本与环境约束增加，且需处理 Python 版本差异。
- B) vendoring（把上游模板/脚本整包复制进 vibego 并长期维护）：会引入同步成本与分叉风险。

### Decision 2：把 speckit 工作流“暴露为 vibego 可调用的命令/流程”

**Decision**: 将 speckit 的关键环节抽象为可重复执行的流程（入口、产物、退出条件），并通过 vibego 的命令系统
或脚本能力对外提供“可触发、可观察、可恢复”的体验（尤其适配 Telegram 远程场景）。

**Rationale**:
- vibego 强项：远程交互、状态/任务管理、多模型切换、脚本编排与日志。
- Spec Kit 强项：前置规格化（spec/plan/tasks）、降低“纯 vibe coding”不确定性、形成可审阅产物。
- 互补结果：把“结构化产物生成/审阅”变成可远程触发的标准流程，并让每一步可追踪、可回滚、可复用。

**Alternatives considered**:
- A) 仅提供文档说明，不做任何集成：学习成本与执行一致性差，无法复用 vibego 的远程/状态能力。
- B) 做成完全独立的外部工具：会弱化 vibego 现有生态（命令/任务/日志/权限）协同。

### Decision 3：产物存储边界采用“仓库内 specs + 配置目录运行态”

**Decision**: 规格/计划/研究/合同等“可审阅产物”存放在仓库 `specs/<feature>/`；任何运行期状态、日志、数据库都
必须留在配置根目录（XDG / `~/.config/vibego`）范围内，且禁止把敏感信息写入仓库。

**Rationale**: 与项目宪章完全一致，避免敏感信息泄露、避免污染仓库、支持离线审阅与代码评审流。

**Alternatives considered**:
- A) 将产物写入 SQLite：更难审阅与 diff；对非工程协作不友好。
- B) 将产物写入远端存储：引入安全/合规与可用性风险，不符合“敏感数据不出终端”的方向。

### Decision 4：对外“合同”以概念 OpenAPI 表达，实施阶段再决定落地形态

**Decision**: 在 Phase 1 先用 OpenAPI 形式描述评估/演示流程的输入输出合同（概念性），作为跨团队讨论与后续实现
的共同语言；实施阶段再决定最终落地为 CLI/Telegram 命令/本地 HTTP（或组合）。

**Rationale**:
- 合同能强约束输入/输出与错误语义，降低“讨论停留在口头”的不确定性。
- OpenAPI 是通用表达方式，即使最终不实现 HTTP，也能复用其中的 schema 与错误码约定。

**Alternatives considered**:
- A) 只写自然语言说明：容易歧义，后续实现难以验证。
- B) GraphQL：不符合 vibego 当前形态与需求复杂度，且学习成本更高。

### Decision 5：并发与幂等策略必须显式化

**Decision**: 对关键动作（创建 feature、生成 plan、生成评估/演示产物）定义幂等策略与并发冲突处理：
- feature 只允许“创建一次”（编号与分支名不可重复）；重复触发必须返回明确错误与恢复路径；
- 报告/产物生成允许重复执行，但必须带 `run_id`（或时间戳）避免覆盖，并能明确输出路径；
- 若存在写入冲突，必须拒绝并给出解决建议（或提供显式 `--force`）。

**Rationale**: 远程触发天然存在重复消息/网络抖动；幂等是可恢复与可维护的基础。

**Alternatives considered**:
- A) 允许覆盖：会破坏可审阅性与追踪性，风险高。
- B) 只靠人工约束：无法规模化，也不适合 Telegram 场景。

### Decision 6：安全控制以“默认脱敏 + 最小权限”为基线

**Decision**: 所有输出（日志/报告/错误信息）默认脱敏；只允许管理员触发 speckit 流程；对外通信保持 HTTPS。

**Rationale**: vibego 与 Telegram token/用户标识高度敏感，且本项目以“敏感数据不出终端”为核心承诺。

**Alternatives considered**:
- A) 允许调试时输出明文：容易被复制/转发，几乎不可控。

## 推荐互补方案（摘要）

- 方案 A（推荐）：vibego 内提供“speckit 工作流编排”命令，复用 `.specify/scripts` 初始化产物结构，然后驱动
AI agent 填充模板与产出研究/合同/quickstart。
- 方案 B：保持 vibego 不变，仅在 README/文档中引导用户安装 Spec Kit 的 `specify` CLI 并手工执行工作流，
vibego 只负责远程驱动 agent。

与结论一致性说明：
- 本文件记录可复用的 Decision/Rationale/Alternatives；最终推荐结论与方案对比见
  `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/assessment-report.md`

## 参考资料（官方/可核验）

- Spec Kit（GitHub）：https://github.com/github/spec-kit
- Spec Kit 文档（GitHub Pages）：https://github.github.io/spec-kit/
- Spec-Driven Development 方法论（上游文档）：https://raw.githubusercontent.com/github/spec-kit/main/spec-driven.md
- Specify CLI 安装依赖（uv）：https://docs.astral.sh/uv/
- Telegram Bot API：https://core.telegram.org/bots/api
- RFC 2119（MUST/SHOULD/MAY）：https://www.rfc-editor.org/rfc/rfc2119
- SemVer 2.0.0：https://semver.org/
- XDG Base Directory：https://specifications.freedesktop.org/basedir-spec/basedir-spec-latest.html
