---

description: "Task list template for feature implementation"
---

# Tasks: 探索 vibego 与 speckit 互补集成可行性

**Input**: Design documents from `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/`  
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/, quickstart.md

**Tests**: 本特性未显式要求 TDD/自动化测试；仅在后续进入“实现自动化命令/脚本”阶段时再补充测试任务。

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- 每条任务描述必须包含绝对路径，确保可直接执行

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 确保 speckit 工作流骨架可重复执行，并固化本特性运行前置检查

- [x] T001 运行 `/Users/david/hypha/tools/vibego/.specify/scripts/bash/check-prerequisites.sh --json` 并将输出写入 `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/prerequisites.json`
- [x] T002 [P] 确认并创建目录 `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/contracts/` 与 `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/checklists/`（如缺失则创建）
- [x] T003 [P] 确认 `/Users/david/hypha/tools/vibego/.specify/scripts/bash/*.sh` 可执行；如不可执行，对其执行 `chmod +x`，并在 `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/setup-notes.md` 记录变更原因与清单
- [x] T004 在 `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/plan.md` 更新 “Documentation (this feature)” 树，补充本特性新增交付文件（assessment-report/demo-flow/roadmap/conventions/decision-criteria）

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 固化通用规则（脱敏/幂等/命名/决策标准），为后续每个用户故事提供统一基线

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T005 创建 `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/conventions.md`，包含：产物命名规则（含 run_id）、脱敏规则、幂等/并发处理规则、配置目录边界、参考链接（宪章/Spec Kit/uv/RFC2119/SemVer）
- [x] T006 [P] 创建 `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/decision-criteria.md`，定义可复核的决策维度与判定阈值（安全风险/维护成本/用户价值/可迭代性等），并引用 `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/conventions.md`
- [x] T007 [P] 更新 `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/contracts/openapi.yaml`：补充错误语义与约束（必须脱敏、幂等冲突/覆盖策略、不可写路径等），并保持 schema 与数据模型一致
- [x] T008 [P] 更新 `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/data-model.md`：补充 run_id 与产物命名约束（引用 `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/conventions.md`），确保与 `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/contracts/openapi.yaml` 一致

**Checkpoint**: Foundation ready - user story implementation can now begin in parallel

---

## Phase 3: User Story 1 - 获取可决策的可行性评估结论 (Priority: P1) 🎯 MVP

**Goal**: 输出一份可审阅、可复用、可决策的评估结论（能力映射 + 方案对比 + 推荐结论 + 风险/缓解 + 决策标准）

**Independent Test**: 维护者仅阅读报告即可在 30 分钟内做出“继续/暂停/调整方向”决策，且报告中无任何敏感信息明文

### Implementation for User Story 1

- [x] T009 [US1] 创建 `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/assessment-report.md`，必须包含：范围、能力映射（vibego vs Spec Kit，逐项给证据链接或文件路径）、至少 2 个互补方案对比、推荐方案与理由（引用 `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/decision-criteria.md`）、风险与缓解、以及最小路线图草案
- [x] T010 [P] [US1] 更新 `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/research.md`：补充与 `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/assessment-report.md` 的交叉链接，并确保 Decision/Rationale/Alternatives 与报告结论一致
- [x] T011 [P] [US1] 更新 `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/spec.md`：移除遗留占位提示（例如 “Add more user stories...”），并在文末添加“交付产物入口”链接到 `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/assessment-report.md`
- [x] T012 [US1] 更新 `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/contracts/openapi.yaml`：校对 `/speckit/assessments` 与 `/speckit/assessments/{runId}` 的输入/输出字段与 `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/assessment-report.md` 一致，并明确幂等/冲突错误 code

**Checkpoint**: User Story 1 完成后，应具备可决策结论与清晰推荐项（MVP 交付）

---

## Phase 4: User Story 2 - 验证最小互补工作流可演示 (Priority: P2)

**Goal**: 提供“最小可演示流程”，让维护者可重复执行并产出预期产物，验证结论可落地

**Independent Test**: 维护者按 demo-flow.md 步骤执行，能产出预期产物清单，并通过成功检查；失败时有恢复步骤且不污染仓库

### Implementation for User Story 2

- [x] T013 [US2] 创建 `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/demo-flow.md`：给出可重复执行的演示步骤、预期产物清单、成功检查、失败恢复（强调不覆盖/不污染仓库与脱敏要求）
- [x] T014 [P] [US2] 更新 `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/quickstart.md`：引用 `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/demo-flow.md`，并补充两条路径（无上游 CLI 与使用上游 `specify` CLI）对应的演示命令与预期输出
- [x] T015 [US2] 更新 `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/contracts/openapi.yaml`：校对 `/speckit/demos` 的输入/输出字段与 `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/demo-flow.md` 一致，并补充 expected artifacts 语义说明

**Checkpoint**: User Story 2 完成后，演示流程可复现并可被第三方复核

---

## Phase 5: User Story 3 - 明确边界、风险与落地路线图 (Priority: P3)

**Goal**: 明确“做什么/不做什么”，并给出分阶段路线图与质量闸门，控制范围与风险

**Independent Test**: 路线图文件包含里程碑、验收标准、退出条件与风险缓解策略，并与项目宪章一致

### Implementation for User Story 3

- [x] T016 [US3] 创建 `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/roadmap.md`：分 Phase 0/1/2 写明目标、里程碑、验收标准、退出条件、质量闸门（对齐 `/Users/david/hypha/tools/vibego/.specify/memory/constitution.md`）
- [x] T017 [P] [US3] 更新 `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/assessment-report.md`：将路线图草案替换为链接到 `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/roadmap.md`，并确保推荐方案与路线图一致
- [x] T018 [P] [US3] 更新 `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/plan.md`：在 Phase 0/1 描述中指向最终交付文件（assessment-report/demo-flow/roadmap/conventions/decision-criteria），并确保 Summary 与产物清单一致

**Checkpoint**: User Story 3 完成后，应具备可执行的下一阶段落地路线图与退出条件

---

## Phase N: Polish & Cross-Cutting Concerns

**Purpose**: 跨文档一致性、安全与可维护性收尾

- [x] T019 [P] 在 `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/` 执行敏感信息自检（例如 `rg -n \"token|chat_id|MASTER_BOT_TOKEN\"`），并将结果记录到 `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/security-scan.md`
- [x] T020 [P] 校对 `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/` 下所有文档的内部链接与绝对路径引用，修复断链并记录到 `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/polish-notes.md`
- [x] T021 [P] 更新 `/Users/david/hypha/tools/vibego/README.md`：新增“Spec-Driven Development（speckit）工作流（实验）”小节，链接到 `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/quickstart.md` 与 `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/assessment-report.md`，并强调安全边界（不要粘贴 token）
- [x] T022 最终可追踪性检查：对照 `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/spec.md` 的 FR-001~FR-010 与 SC-001~SC-005，逐条标注对应产物/章节位置，输出到 `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/traceability.md`

---

## Dependencies & Execution Order

### Dependency Graph (User Story Order)

```text
Phase 1 Setup
   ↓
Phase 2 Foundational
   ↓
US1 (P1, MVP)
   ↓
US2 (P2, demo)
   ↓
US3 (P3, roadmap)
   ↓
Polish
```

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3+)**: All depend on Foundational phase completion
- **Polish (Final Phase)**: Depends on all desired user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: 可在 Phase 2 后直接开始（MVP）
- **User Story 2 (P2)**: 依赖 US1 的推荐结论与产物入口（用于演示验证）
- **User Story 3 (P3)**: 依赖 US1/US2 产物，输出完整路线图与边界

### Parallel Opportunities

- [P] 标记的任务可并行执行（不同文件、无未完成依赖）
- 同一用户故事内，文档起草与合同校对可拆分并行（在合并前统一对齐）

---

## Parallel Example: User Story 1

```bash
# US1 的并行起草示例（在 T009 产出初稿后执行）：
Task: "T010 [US1] 更新 /Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/research.md"
Task: "T011 [US1] 更新 /Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/spec.md"
Task: "T012 [US1] 更新 /Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/contracts/openapi.yaml"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational
3. Complete Phase 3: User Story 1
4. **STOP and VALIDATE**: 用独立评审验证“可决策性”（SC-001/SC-002/SC-004）

### Incremental Delivery

1. US1：先交付评估结论（可决策）
2. US2：补齐可演示证据链（可复现）
3. US3：输出路线图与边界（可推进/可退出）
4. 最后做一致性与安全收尾
