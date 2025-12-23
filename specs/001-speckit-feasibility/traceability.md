# Traceability: 001-speckit-feasibility

**Date**: 2025-12-22  
**Spec**: `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/spec.md`

## 目的

把 `spec.md` 中的 Functional Requirements（FR）与 Success Criteria（SC）逐条映射到本特性的交付产物与章节位置，
便于审阅、复核与后续实施阶段扩展。

## Functional Requirements（FR）映射

- **FR-001**（发起可行性评估入口 + 范围）：  
  - 合同：`/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/contracts/openapi.yaml`（`/speckit/assessments`，`AssessmentRequest.scope`）  
  - 结论入口：`/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/assessment-report.md`（0/1 节）
- **FR-002**（能力映射）：  
  - 结论：`/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/assessment-report.md`（2 节）  
  - 研究事实：`/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/research.md`（背景与现状）
- **FR-003**（至少 2 个方案对比）：  
  - 结论：`/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/assessment-report.md`（3 节）
- **FR-004**（推荐方案 + 基于决策标准解释）：  
  - 决策标准：`/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/decision-criteria.md`  
  - 推荐结论：`/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/assessment-report.md`（4 节）
- **FR-005**（最小可演示流程 + 预期产物清单）：  
  - 演示流程：`/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/demo-flow.md`  
  - Quickstart：`/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/quickstart.md`（步骤 3）
- **FR-006**（分阶段路线图 + 边界）：  
  - 路线图：`/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/roadmap.md`
- **FR-007**（输出与产物不含敏感信息明文）：  
  - 约定：`/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/conventions.md`（脱敏规则）  
  - 合同：`/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/contracts/openapi.yaml`（安全约束/错误脱敏）  
  - 自检：`/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/security-scan.md`
- **FR-008**（重复执行不冲突/可恢复）：  
  - 约定：`/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/conventions.md`（幂等/并发策略）  
  - 数据模型：`/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/data-model.md`（AssessmentRun.run_id/idempotency_key）  
  - 合同：`/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/contracts/openapi.yaml`（Idempotency-Key、409 错误语义）
- **FR-009**（失败场景有可执行修复建议，避免半成品）：  
  - 演示恢复：`/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/demo-flow.md`（失败恢复/清理）  
  - 合同错误提示：`/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/contracts/openapi.yaml`（Error.hint）
- **FR-010**（治理与质量闸门 + 宪章对齐）：  
  - 宪章：`/Users/david/hypha/tools/vibego/.specify/memory/constitution.md`（Core Principles / Governance）  
  - Plan 复核：`/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/plan.md`（Constitution Check）  
  - 路线图闸门：`/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/roadmap.md`（各 Phase 质量闸门）

## Success Criteria（SC）映射

- **SC-001**（30 分钟可决策）：  
  - 一句话结论与范围：`/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/assessment-report.md`（0/1 节）
- **SC-002**（至少 2 方案 + 可复核理由）：  
  - 方案对比与推荐：`/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/assessment-report.md`（3/4 节）  
  - 决策标准：`/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/decision-criteria.md`
- **SC-003**（演示可复现且首次即可产出预期产物）：  
  - 演示步骤与成功检查：`/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/demo-flow.md`  
  - Quickstart 演示命令：`/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/quickstart.md`
- **SC-004**（无敏感信息明文 + 有修复建议）：  
  - 脱敏规则：`/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/conventions.md`  
  - 自检结果：`/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/security-scan.md`
- **SC-005**（路线图含里程碑/验收/退出/风险缓解）：  
  - 路线图：`/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/roadmap.md`

## 备注

- 本映射覆盖 Phase 0 的“可行性评估与证据链”交付；若进入路线图 Phase 1/2（实施阶段），需要补充实现级任务、
  测试与 CI 闸门，并扩展本映射到具体源码与测试用例。
