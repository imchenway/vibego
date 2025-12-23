# Assessment Report: vibego × speckit 互补集成可行性

**Date**: 2025-12-22  
**Feature**: `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/spec.md`  
**Decision Criteria**: `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/decision-criteria.md`  
**Conventions**: `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/conventions.md`

## 0) 一句话结论

推荐以“工作流互补”为主：在 vibego 内复用现有 `.specify/` 脚本与模板，提供可远程触发的 speckit 工作流编排体验；
将上游 Spec Kit 的 `specify` CLI 作为可选依赖用于初始化/同步模板，而不是强绑定到 vibego 运行时。

## 1) 评估范围（Scope）

本报告聚焦以下问题：

1. vibego 与 Spec Kit（speckit）在“从需求到可执行计划/任务”的工作流层面是否互补？
2. 互补集成有哪些方案？各自的收益/成本/风险如何？
3. 在不破坏 vibego 宪章约束（安全/隐私/边界/幂等/可诊断性）的前提下，推荐落地路径是什么？

不在本范围（Out of Scope）：

- 直接承诺一次性覆盖 Spec Kit 的全部能力与所有工作流变体
- 立即实现一整套新的 HTTP 服务或新的运行期存储（仅做概念合同）

## 2) 能力映射（Capability Map）与证据

说明：每一项都给出可核验证据（官方链接或仓库文件路径）。

### 2.1 speckit（Spec Kit）强调的能力

- SDD 方法论与 speckit 工作流（/speckit.specify → /speckit.plan → /speckit.tasks）
  - 证据（官方）：https://raw.githubusercontent.com/github/spec-kit/main/spec-driven.md
- 提供 `specify` CLI 用于初始化项目模板、检查 agent 支持等
  - 证据（官方）：https://github.com/github/spec-kit （README 的 “Specify CLI Reference”）

### 2.2 vibego 现状能力（与互补点相关）

- 通过 Telegram 远程驱动本地终端 AI CLI，且强调敏感数据不出终端、运行期数据写入 `~/.config/vibego/`
  - 证据（仓库）：`/Users/david/hypha/tools/vibego/README.md`
- 已包含 speckit 工作流骨架（feature 目录/分支初始化、plan 初始化、prerequisites 检查）
  - 证据（仓库）：
    - `/Users/david/hypha/tools/vibego/.specify/scripts/bash/create-new-feature.sh`
    - `/Users/david/hypha/tools/vibego/.specify/scripts/bash/setup-plan.sh`
    - `/Users/david/hypha/tools/vibego/.specify/scripts/bash/check-prerequisites.sh`
    - `/Users/david/hypha/tools/vibego/.specify/templates/*`
- 存在可扩展的“命令中心/任务管理”能力，可用于将工作流步骤产品化成可触发命令
  - 证据（仓库）：`/Users/david/hypha/tools/vibego/command_center/`、`/Users/david/hypha/tools/vibego/tasks/`

### 2.3 差距（Gap）与互补机会

- 差距：vibego 当前更多是“远程驱动与运行期编排”，缺少统一的“规格/计划/任务”审阅产物入口与质量闸门。
- 互补：引入 speckit/Spec Kit 的产物结构与阶段化工作流，让“vibe coding”变为“spec 驱动的可预测交付”。

## 3) 可选互补方案（至少 2 个）

以下方案均需满足 `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/decision-criteria.md` 的 Gate。

### 方案 A（推荐）：工作流编排优先（vibego 内提供 speckit 工作流封装）

描述：
- 复用仓库内 `.specify/` 脚本与模板作为“speckit 工作流执行器”
- 将其包装为 vibego 可远程触发的命令/流程（Telegram → 本地执行 → 回推产物路径）
- `specify` CLI 仅作为可选工具，用于模板初始化/同步（不强依赖）

收益：
- 环境门槛低（不要求所有用户具备 Python 3.11+ / uv）
- 最大化复用 vibego 的远程触发、权限控制、日志与状态能力
- 与宪章一致：运行期边界清晰，产物可审阅、可 diff、可追踪

成本/风险：
- 需要设计“命令入口 + 产物路径约定 + 幂等/并发策略”
- 需要维护 `.specify` 脚本与模板与上游理念的一致性（但可控）

### 方案 B：外部依赖优先（引导用户直接使用上游 `specify` CLI）

描述：
- vibego 仅提供远程驱动 agent 的能力
- 用户自行安装 `uv` + `specify` CLI 来初始化/执行 speckit 工作流
- vibego 不对工作流做任何封装，仅提供文档与最佳实践

收益：
- 几乎不增加 vibego 内部复杂度
- 与上游 Spec Kit 保持一致（减少分叉）

成本/风险：
- 环境门槛高（上游要求 Python 3.11+ 与 uv；不同用户环境差异大）
- 体验割裂（远程触发与产物组织不统一）
- 难以利用 vibego 的状态/权限/日志能力形成闭环

## 4) 推荐方案与理由

推荐：方案 A（工作流编排优先）。

理由（对齐决策标准）：
- Gate：更容易满足宪章要求（脱敏、数据边界、HTTPS、幂等/可恢复）。
- 用户价值：把“规格化产物生成与审阅”变成可远程触发的标准流程，降低沟通成本。
- 维护成本：复用现有 `.specify`，在 vibego 内只做薄封装，避免强绑定外部工具链。
- 兼容性：允许在没有上游 CLI 的环境下继续工作流（可选增强，而非强依赖）。

## 5) 主要风险与缓解

- 风险 1：引入工作流后，用户误把敏感信息粘贴进 spec/plan/报告  
  - 缓解：默认脱敏规则、预提交/扫描提示、在 quickstart 与命令输出中反复强调（见 conventions）。
- 风险 2：远程并发触发导致产物覆盖或状态错乱  
  - 缓解：强制 run_id、默认不覆盖、冲突错误码与恢复提示、必要时加锁/队列。
- 风险 3：与上游 Spec Kit 演进不一致导致概念偏离  
  - 缓解：把上游作为参考而非强耦合；保留同步入口（可选使用 `specify` CLI），并定期审阅上游变更。

## 6) 路线图（以 roadmap.md 为准）

详见：`/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/roadmap.md`

摘要：
- Phase 0：评估产物与最小证据链（本分支主要交付）
- Phase 1：vibego 内封装 speckit 工作流入口（最小落地）
- Phase 2：强化与扩展（可选上游同步 + 自动化闸门）

## 7) 参考资料（官方/可核验）

- Spec Kit：https://github.com/github/spec-kit
- Spec Kit Docs：https://github.github.io/spec-kit/
- SDD / speckit 工作流文档：https://raw.githubusercontent.com/github/spec-kit/main/spec-driven.md
- uv：https://docs.astral.sh/uv/
