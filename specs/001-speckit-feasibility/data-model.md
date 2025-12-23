# Data Model: vibego × speckit 互补可行性工作流

**Date**: 2025-12-22  
**Feature**: `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/spec.md`  
**Research**: `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/research.md`

## 设计目标

本数据模型用于描述“评估/演示/路线图产出”在流程层面的核心对象与约束，优先服务于：

- 可追踪：每次评估/演示都有明确 run_id 与产物路径
- 可审阅：产物存放在仓库 `specs/<feature>/`，易于 diff 与评审
- 可恢复：失败不污染仓库；重复执行不会覆盖关键产物
- 安全：任何对象字段都不得包含敏感信息明文（token、用户标识等）

通用约定见：
- `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/conventions.md`

## 实体与字段

### 1) Feature（特性）

表示一个 speckit 特性目录（例如 `001-speckit-feasibility`）及其规格/计划文件。

字段：
- `feature_id`：字符串，格式 `NNN`（例如 `001`）
- `feature_slug`：字符串，格式 `NNN-xxx`（例如 `001-speckit-feasibility`）
- `title`：字符串（面向人类的标题）
- `created_at`：ISO 日期（YYYY-MM-DD）
- `status`：枚举（`draft` | `planned` | `implemented` | `deferred`）
- `paths`：
  - `spec_path`：绝对路径
  - `plan_path`：绝对路径
  - `feature_dir`：绝对路径

校验规则：
- `feature_slug` MUST 与目录名一致
- `spec_path`/`plan_path` MUST 位于 `feature_dir` 内

### 2) AssessmentRun（评估运行）

一次“互补可行性评估”的执行记录，用于解决并发、可追踪与复跑。

字段：
- `run_id`：UUID
- `idempotency_key`：可选字符串（用于幂等；与请求头 `Idempotency-Key` 语义一致）
- `feature_slug`：外键 → Feature.feature_slug
- `initiator`：可选（Telegram 管理员标识或本地操作者标识；必须可脱敏）
- `scope`：字符串（例如：`workflow-only`、`command-orchestration`、`end-to-end`）
- `started_at` / `finished_at`：时间戳
- `status`：枚举（`queued` | `running` | `completed` | `failed`）
- `outputs`：
  - `report_path`：评估报告路径（仓库内）
  - `artifacts`：其他产物路径列表（例如 demo 产物）
- `errors`：失败时的错误摘要（不得包含敏感信息明文）

校验规则：
- `report_path` MUST 位于 `specs/<feature>/` 下
- `errors` MUST 经过脱敏处理
- 自动化可变产物 SHOULD 写入 `specs/<feature>/runs/<run_id>/`（避免覆盖），或采用 `<name>.<run_id>.<ext>` 命名
  （详见 `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/conventions.md`）

状态流转：
```text
queued -> running -> completed
queued -> running -> failed
```

### 3) AssessmentReport（评估报告）

评估结论本体（以文件形式存在），包含能力映射、方案对比与推荐结论。

字段（文件结构建议）：
- `summary`：一段话结论
- `capability_map`：CapabilityMap
- `options`：IntegrationOption[]（至少 2 个）
- `recommendation`：IntegrationOption（推荐项）
- `decision_criteria`：字符串列表（用于复核理由）
- `risks`：RiskItem[]
- `roadmap`：RoadmapPhase[]

校验规则：
- `options.length >= 2`
- `summary` MUST 可独立读懂（不依赖实现细节）

### 4) CapabilityMap（能力映射）

用于对照 vibego 与 speckit 的能力与差距。

字段：
- `categories`：CategoryMap[]，每个 CategoryMap 包含：
  - `category`：字符串（例如：spec 初始化、plan 生成、任务拆分、质量闸门、产物结构、agent 协作、审阅流程）
  - `vibego_capability`：字符串（现状说明）
  - `speckit_capability`：字符串（上游能力说明）
  - `gap`：字符串（差距/阻塞项）
  - `notes`：字符串（补充说明）

### 5) IntegrationOption（互补方案）

一个可选的集成/互补路径，面向决策对比。

字段：
- `name`：字符串（例如：`workflow-wrapper`）
- `description`：字符串（方案概述）
- `user_value`：字符串（用户价值）
- `cost_level`：枚举（`low` | `medium` | `high`）
- `risk_level`：枚举（`low` | `medium` | `high`）
- `prerequisites`：字符串列表（前置条件）
- `deliverables`：字符串列表（产物/能力）
- `constraints`：字符串列表（边界条件）

校验规则：
- `name` MUST 唯一
- `constraints` MUST 覆盖安全边界与幂等要求

### 6) DemoFlow（最小可演示流程）

用于把“纸面结论”变成可验证的证据链。

字段：
- `demo_id`：UUID
- `idempotency_key`：可选字符串（用于幂等；与请求头 `Idempotency-Key` 语义一致）
- `feature_slug`：外键 → Feature.feature_slug
- `steps`：字符串列表（可重复执行）
- `expected_artifacts`：路径/文件名列表（相对 `specs/<feature>/`）
- `success_check`：字符串列表（如何验证成功）

校验规则：
- `steps` MUST 可由维护者独立执行（不依赖隐式前提）
- `expected_artifacts` MUST 不包含敏感信息

### 7) RiskItem（风险项）

字段：
- `risk`：字符串
- `impact`：枚举（`low` | `medium` | `high`）
- `mitigation`：字符串（缓解方案）
- `owner`：可选字符串（责任人/角色）

### 8) RoadmapPhase（路线图阶段）

字段：
- `phase`：字符串（例如：Phase 0/1/2）
- `goal`：字符串
- `deliverables`：字符串列表
- `exit_criteria`：字符串列表（退出条件）
- `quality_gates`：字符串列表（必须通过的闸门；对齐宪章）

## 关系总览（纯文本）

```text
Feature 1 --- n AssessmentRun
AssessmentRun 1 --- 1 AssessmentReport (file)
AssessmentReport 1 --- n IntegrationOption
AssessmentReport 1 --- 1 CapabilityMap
AssessmentReport 1 --- n RiskItem
AssessmentReport 1 --- n RoadmapPhase
Feature 1 --- n DemoFlow (optional)
```

## 与宪章对齐要点（摘要）

- 所有字段与输出 MUST 避免敏感信息明文（token、用户标识）
- 所有产物文件 MUST 位于 `specs/<feature>/`（审阅友好）
- 运行期状态与日志 MUST 留在配置目录边界内（不污染仓库）
- 所有流程 MUST 明确定义幂等与并发冲突处理策略
