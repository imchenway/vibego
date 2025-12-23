# Implementation Plan: 探索 vibego 与 speckit 互补集成可行性

**Branch**: `001-speckit-feasibility` | **Date**: 2025-12-22 | **Spec**: `specs/001-speckit-feasibility/spec.md` (`/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/spec.md`)
**Input**: Feature specification from `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/spec.md`

**Note**: This template is typically filled in by an automated planning command (e.g., `/speckit.plan`). If you don't
have that command wired up, fill this file in manually.

## Summary

本特性目标不是直接“实现集成”，而是以 Spec-Driven Development（Speckit / Spec Kit）的方法论与工具链为参照，
评估其与 vibego 的互补点，并产出可决策、可复用的结论与最小可演示证据链：

- 产出一份评估报告：能力映射、至少 2 个互补方案、推荐方案与理由、风险与缓解、决策标准
- 产出一个最小可演示流程：可重复的步骤 + 预期产物清单（用于证明结论可落地）
- 产出路线图：明确边界、阶段性里程碑与质量闸门（对齐项目宪章）

交付产物入口：
- `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/assessment-report.md`
- `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/demo-flow.md`
- `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/roadmap.md`

## Technical Context

<!--
  ACTION REQUIRED: Replace the content in this section with the technical details
  for the project. The structure here is presented in advisory capacity to guide
  the iteration process.
-->

**Language/Version**: Python >= 3.9（vibego）；Bash（`.specify/scripts`）；可选外部工具：`specify` CLI（Spec Kit，上游要求 Python 3.11+）  
**Primary Dependencies**: aiogram 3.x、aiosqlite、aiohttp-socks、markdown-it-py；（可选）uv + specify-cli  
**Storage**: SQLite（配置/状态：默认 `~/.config/vibego/`，受 `VIBEGO_CONFIG_DIR`/`MASTER_CONFIG_ROOT` 影响）  
**Testing**: pytest、pytest-asyncio  
**Target Platform**: macOS（主要）；Linux/Windows（尽力兼容，按上游 Spec Kit 支持范围）  
**Project Type**: single（CLI + Telegram bot + bash scripts）  
**Performance Goals**: 典型评估/生成流程在 60 秒内完成；交互类命令保持“可感知的即时反馈”（失败时给出可执行下一步）  
**Constraints**: 不泄露任何敏感信息；运行期文件不落入仓库；重复执行幂等；缺少前置条件时给出明确修复建议  
**Scale/Scope**: 以 vibego 单仓为基准；支持多项目/多 worker 的工作流复用（通过现有 master/worker 体系）

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- [x] Secrets & privacy: no tokens/IDs in repo or logs; avoid printing secrets
- [x] Runtime data boundaries: logs/state/db under config root (XDG / `~/.config/vibego`)
- [x] Transport security: HTTPS/TLS only for Telegram/API calls
- [x] CLI contract: args/defaults/exit codes/stdout-stderr semantics documented; JSON schema stable (if any)
- [x] Observability: actionable error messages; log context (time/level/project/model/event id)
- [x] Reliability: start/stop/switch/upgrade idempotent; restart recovery strategy defined
- [x] Testing gate: tests added/updated for behavior changes; regression tests for bug fixes; suite passes

Re-check（Phase 1 design 后复核）：2025-12-22，PASS。

## Project Structure

### Documentation (this feature)

```text
specs/001-speckit-feasibility/
├── spec.md                     # Feature spec（已生成）
├── plan.md                     # This file
├── tasks.md                    # Phase 2 任务拆解（本次生成）
├── prerequisites.json          # 运行前置检查输出（本次生成）
├── setup-notes.md              # Setup 阶段检查记录（本次生成）
├── research.md                 # Phase 0 output（本次生成）
├── data-model.md               # Phase 1 output（本次生成）
├── conventions.md              # 通用约定（脱敏/幂等/命名/边界）（已生成）
├── decision-criteria.md         # 决策标准（已生成）
├── assessment-report.md         # 可决策评估报告（已生成）
├── demo-flow.md                # 最小可演示流程（已生成）
├── roadmap.md                  # 分阶段路线图（已生成）
├── quickstart.md               # Phase 1 output（本次生成）
├── contracts/                  # Phase 1 output（本次生成）
│   └── openapi.yaml            # 合同：评估/演示流程 API（概念性）
├── security-scan.md            # 敏感信息自检结果（待补齐）
├── polish-notes.md             # 一致性/断链修复记录（待补齐）
├── traceability.md             # FR/SC 可追踪性映射（待补齐）
└── checklists/
    └── requirements.md         # Spec 质量清单（已生成）
```

### Source Code (repository root)

```text
bot.py
master.py
vibego_cli/
command_center/
tasks/
scripts/
.specify/
tests/
README.md
```

**Structure Decision**: 本特性在 Phase 0/1 仅产出 specs 文档与合同，不引入新的源码目录结构；如后续进入实施阶段，
将优先以“新增最小封装命令/脚本 + 文档”为交付形态，尽量复用现有 `scripts/` 与 `command_center/` 能力。

## Complexity Tracking

> 本特性当前无宪章闸门例外，无需复杂度豁免说明。

## Phase 0: Research（输出到 `research.md`）

目标：明确 speckit（Spec Kit）的能力边界、工作流与命令体系，并基于 vibego 现状给出互补方案与推荐路径。

本阶段产物：
- `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/research.md`
- `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/conventions.md`
- `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/decision-criteria.md`
- `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/assessment-report.md`

## Phase 1: Design & Contracts（输出到 `data-model.md`、`contracts/`、`quickstart.md`）

目标：将互补方案抽象为可执行的“合同与产物”，为后续实施提供可追踪的接口与数据结构。

本阶段产物：
- `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/data-model.md`
- `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/contracts/openapi.yaml`
- `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/demo-flow.md`
- `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/quickstart.md`
- `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/roadmap.md`
