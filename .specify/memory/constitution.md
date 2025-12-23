<!--
Sync Impact Report
- Version change: template → 1.0.0
- Modified principles:
  - Principle 1 placeholder → I. 安全与隐私优先（不可妥协）
  - Principle 2 placeholder → II. CLI/脚本接口稳定、可脚本化
  - Principle 3 placeholder → III. 可观测性与可诊断性
  - Principle 4 placeholder → IV. 幂等与可恢复（可靠性）
  - Principle 5 placeholder → V. 测试优先与回归保护
- Added sections:
  - Additional Constraints（配置与安全边界）
  - Development Workflow（Spec 驱动与质量闸门）
- Removed sections: None
- Templates requiring updates:
  - ✅ .specify/templates/plan-template.md
  - ✅ .specify/templates/spec-template.md
  - ✅ .specify/templates/tasks-template.md
  - ✅ .specify/templates/commands/*.md（目录不存在，无需更新）
- Follow-up TODOs: None
-->

# vibego Constitution

本宪章定义 vibego 的非协商开发原则与治理规则；当与其他文档冲突时，以本宪章为准。

## Core Principles

### I. 安全与隐私优先（不可妥协）
- MUST 将所有 Token/密钥/用户标识视为机密信息（例如 Telegram bot token、`MASTER_BOT_TOKEN`、`chat_id`）。
  - 不得写入仓库，不得在日志/报错/回显中明文输出。
- MUST 将运行期日志/数据库/状态文件写入配置根目录下（默认 `~/.config/vibego/`，可由
  `VIBEGO_CONFIG_DIR`/`MASTER_CONFIG_ROOT` 覆盖）。
  - 仓库内不得生成包含敏感信息的运行期文件。
- MUST 仅通过 HTTPS（TLS）与 Telegram Bot API 等必要服务通信；不得引入明文传输通道。
理由：项目承诺“敏感数据不出终端”，且 Bot API 访问通道基于 HTTPS。

### II. CLI/脚本接口稳定、可脚本化
- MUST 为所有用户可见的 CLI/脚本接口提供稳定契约：参数含义、默认值、退出码、stdout/stderr 语义。
- MUST 在破坏性变更发生时提供迁移方案（兼容期或迁移脚本），并按语义化版本进行主版本升级。
- SHOULD 为自动化流程提供机器可读输出（例如 `--json`），并保持字段名稳定。
理由：vibego 的核心使用场景是远程/自动化驱动，接口不稳定会直接放大升级与运维成本。

### III. 可观测性与可诊断性
- MUST 记录可定位问题的日志（至少包含时间、级别、项目/模型上下文、关键事件 ID），并写入配置根目录下的日志目录。
- MUST 在错误场景给出可执行的下一步（例如缺少依赖、鉴权失败、路径不可用），不得静默失败。
- SHOULD 避免在日志中输出敏感信息；如需诊断，必须脱敏或以摘要形式输出。
理由：项目包含长驻进程与脚本编排，故障定位必须依赖一致日志与清晰错误信息。

### IV. 幂等与可恢复（可靠性）
- MUST 保证 `start/stop/switch/upgrade` 等控制链路幂等：重复执行不会造成资源泄漏、重复启动或状态错乱。
- MUST 在异常退出/重启后能够基于状态文件恢复到一致状态，或给出清晰的人工恢复步骤。
- SHOULD 将并发风险显式化（例如并行 upgrade/run 请求），并提供锁/队列/拒绝策略。
理由：远程操作不可避免存在重复触发与网络抖动，可靠性必须以“可重复执行”为基础。

### V. 测试优先与回归保护
- MUST 为可测试逻辑提供自动化测试；任何缺陷修复 MUST 增加回归测试覆盖该缺陷。
- MUST 将测试纳入合并/发布质量闸门：合入前本地或 CI 必须通过。
- SHOULD 为关键链路（配置解析、授权、启动/停止编排、状态恢复）提供集成级覆盖。
理由：该项目涉及流程编排与状态机，回归风险高；测试是最可持续的稳定手段。

## Additional Constraints（配置与安全边界）
- 配置根目录解析 MUST 遵循优先级：`MASTER_CONFIG_ROOT` → `VIBEGO_CONFIG_DIR` →
  `$XDG_CONFIG_HOME/vibego` → `~/.config/vibego`。
- Telegram API Root 默认为 `https://api.telegram.org`，允许通过 `MASTER_TELEGRAM_API_ROOT` 覆盖
  （仍必须为 HTTPS）。
- Python 运行环境 MUST 支持 `>=3.9`；对 3.11+ 的能力可优先使用，但必须提供兼容降级路径。

## Development Workflow（Spec 驱动与质量闸门）
- 重大需求/破坏性变更 MUST 走 Spec-Driven Development：spec → plan → tasks，并在 plan 中完成
  Constitution Check。
- 所有变更 MUST 明确影响面：配置项、日志路径、对 Telegram API 的调用、向后兼容性与迁移方案。
- 合并前 MUST 完成：测试通过、文档更新（README/脚本帮助/迁移说明）、以及必要的版本升级与变更摘要。

## Governance
- 本宪章对仓库内所有开发活动生效，并高于模板、脚本注释、README 等其他约定。
- 术语：MUST/SHOULD/MAY 采用 RFC 2119 定义。
- 修订流程：
  - 提交修订说明（动机、影响面、迁移/落地方式）。
  - 更新本文件顶部的 Sync Impact Report，并同步更新 `.specify/templates/*` 等依赖产物。
  - 版本号按语义化规则递增（MAJOR/MINOR/PATCH），并更新 `Last Amended` 日期。
- 合规检查：
  - 每个特性 `plan.md` 必须包含 “Constitution Check” 并逐条说明符合/例外原因。
  - 若出现例外，必须写明风险与回滚方案，并由维护者批准。

**Version**: 1.0.0 | **Ratified**: 2025-12-22 | **Last Amended**: 2025-12-22
