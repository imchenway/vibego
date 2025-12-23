# Roadmap: vibego × speckit 互补集成落地路线图

**Date**: 2025-12-22  
**Feature**: `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/spec.md`  
**Constitution**: `/Users/david/hypha/tools/vibego/.specify/memory/constitution.md`  
**Decision Criteria**: `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/decision-criteria.md`

## 总体目标与边界

目标：
- 把 speckit/Spec Kit 的“spec → plan → tasks”产物节奏引入 vibego 的工作方式，减少纯 vibe coding 的不确定性。
- 以“可审阅产物 + 可重复执行流程”为核心交付：任何结论都必须可被第三方复核。

不做（Phase 2 之前）：
- 不承诺实现一个新的对外 HTTP 服务（合同仅用于表达语义）。
- 不把上游 `specify` CLI 作为 vibego 的强依赖（仅作为可选能力）。

## Phase 0（当前）：评估产物与最小证据链

**目标**：在不修改核心运行逻辑的前提下，产出“可决策结论 + 可复现演示 + 可执行路线图”。

**里程碑**：
- M0.1 形成可复核的决策标准与通用约定（脱敏/幂等/边界/命名）
- M0.2 形成可决策评估报告（能力映射 + 方案对比 + 推荐结论）
- M0.3 形成可重复执行的演示流程（两条路径：无上游 CLI / 使用上游 CLI）
- M0.4 形成路线图与退出条件（本文件）

**验收标准**：
- 评估报告可在 30 分钟内支持维护者做出“继续/暂停/调整方向”的决策（对齐 SC-001/SC-002）。
- demo-flow 可在隔离工作区复现，且演示产物位于配置目录边界内，不污染主工作区。
- 所有文档与示例不包含敏感信息明文（对齐宪章 Principle I）。

**退出条件（停止推进的信号）**：
- 发现任何无法规避的敏感信息泄露风险（例如必须回显 token 才能工作）。
- 需要破坏性调整 vibego 的 Python 版本底线（>=3.9）才能引入上游能力。
- 无法在不显著增加维护成本的情况下保持脚本/契约稳定（对齐宪章 Principle II）。

**质量闸门（对齐宪章）**：
- 安全与隐私：不得把 token/chat_id/user_id 写入仓库；演示目录必须在配置根目录边界内。
- 数据边界：运行期/临时产物必须在 `$XDG_CONFIG_HOME`/`~/.config/vibego`，不得写入仓库非 specs 区域。
- 可诊断性：失败必须给出可执行下一步（缺少依赖/路径不可写/冲突等）。
- 幂等与可恢复：演示可重复执行，不覆盖已有产物；冲突时有明确恢复路径。

## Phase 1：最小落地（vibego 内封装 speckit 工作流入口）

**目标**：在 vibego 内提供“可触发、可观察、可恢复”的 speckit 工作流封装（优先 CLI/脚本形态）。

**里程碑**（建议）：
- M1.1 定义并实现 1 个稳定入口（例如 `vibego speckit ...` 或命令中心命令），支持：
  - 创建 feature（或指向已有 feature）
  - 初始化/更新 plan（从模板复制）
  - 生成 tasks（基于现有模板与文档）
- M1.2 固化 `--json` 机器可读输出字段（对齐宪章 Principle II），并保持向后兼容。
- M1.3 形成最小可观测性：run_id、日志路径、失败恢复提示（对齐宪章 Principle III）。
- M1.4 并发与幂等策略落地：重复触发行为稳定可预期（对齐宪章 Principle IV）。

**验收标准**：
- 在干净环境下，维护者可在 60 秒内完成一次“spec → plan → tasks”产物生成并得到清晰产物路径。
- 任意步骤失败时，不污染仓库（除 specs 产物外），且提供可执行恢复建议。
- 默认脱敏：任何日志/错误输出不包含敏感信息明文。

**退出条件**：
- 需要引入大量新依赖或破坏现有命令/脚本契约才能达成（与 Gate 冲突）。
- 无法在多次重复触发下保持幂等与一致输出路径约定。

**质量闸门（对齐宪章）**：
- CLI/脚本契约：帮助文档、退出码、stdout/stderr 语义、`--json` schema 稳定。
- 幂等与可恢复：run_id/锁/拒绝策略明确；重复消息不会造成状态错乱。
- 测试优先：对新增入口与关键流程增加回归测试（至少覆盖参数解析/路径生成/脱敏）。

## Phase 2：强化与扩展（可选上游同步 + CI 闸门）

**目标**：在保持“上游可选依赖”的前提下，增强一致性、可维护性与自动化质量闸门。

**里程碑**（建议）：
- M2.1 引入“可选上游模板同步”机制（例如检测到 `specify` CLI 时提供同步命令），并提供回滚策略。
- M2.2 把敏感信息扫描、断链检查、契约校验纳入自动化（本地脚本或 CI）。
- M2.3 扩展到多项目/多 worker 场景：明确权限、隔离、并发策略与资源清理机制。

**验收标准**：
- 同步上游模板不会破坏现有工作流；若发生变化，有清晰迁移与版本升级理由（SemVer）。
- 自动化检查可稳定发现敏感信息与断链问题，并在失败时给出修复建议。

**退出条件**：
- 上游演进导致同步成本不可控（频繁破坏性变化），且收益不足以覆盖维护成本。
- 引入外部工具链后无法满足 Python >=3.9 兼容底线（对齐宪章 Additional Constraints）。

**质量闸门（对齐宪章）**：
- 兼容性与迁移：破坏性变化必须提供迁移/回滚策略，并升级版本号。
- 测试与回归：合入前测试必须通过；关键流程有集成级覆盖。

## 本路线图的可核验证据入口

- 宪章：`/Users/david/hypha/tools/vibego/.specify/memory/constitution.md`
- 评估报告：`/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/assessment-report.md`
- 演示流程：`/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/demo-flow.md`
