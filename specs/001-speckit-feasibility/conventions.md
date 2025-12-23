# Conventions（通用约定）

**Date**: 2025-12-22  
**Feature**: `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/`

本文件定义本特性在“评估/演示/路线图”工作流中的通用约定，用于保证可追踪、可审阅、可恢复与安全边界一致。

## 1) 产物命名与 run_id

目的：避免重复执行时覆盖文件、便于并发与追踪。

- 每次自动化运行（assessment/demo）MUST 生成 `run_id`（UUID）。
- 自动化生成的可变产物 SHOULD 写入：
  - `/Users/david/hypha/tools/vibego/specs/<feature-slug>/runs/<run_id>/`
  - 或以 `<name>.<run_id>.<ext>` 的方式命名（例如 `assessment-report.<run_id>.md`）
- 人工审阅的“最终交付入口文件”建议保持稳定命名（例如 `assessment-report.md`、`demo-flow.md`、`roadmap.md`），
  但 MUST 明确写出生成来源与更新时间，避免误用旧结论。

## 2) 脱敏与敏感信息处理

目的：遵守项目宪章“敏感数据不出终端”，避免 token/用户标识泄露。

- MUST 将以下信息视为敏感信息并禁止明文出现在仓库文档、日志与报错中：
  - Telegram bot token（形如 `123456:ABC...`）
  - `MASTER_BOT_TOKEN`、任意 API key/secret
  - chat_id/user_id（可视为用户标识）
  - 本地绝对路径以外的个人隐私信息（如手机号、邮箱等）
- MUST 使用占位符替代真实值，例如：
  - `<TOKEN_REDACTED>`、`<CHAT_ID_REDACTED>`、`<USER_ID_REDACTED>`
- SHOULD 在输出/日志中采用“最小必要披露”：
  - 仅输出配置项是否存在、是否合法（例如“token 已设置/缺失/格式不合法”），不要输出值本身

## 3) 幂等与并发策略

目的：远程触发存在重复消息与并发，必须保证“可重复执行”。

- Feature 初始化（创建 spec 目录/分支）MUST 只允许执行一次；重复执行应返回清晰错误并提示恢复步骤。
- 评估/演示流程 SHOULD 允许重复执行，但 MUST：
  - 默认不覆盖已有产物
  - 在检测到冲突时拒绝并提示（或要求显式 `--force`）
  - 输出包含 `run_id` 与产物路径列表，便于追踪
- 并发触发 MUST 有明确策略（拒绝/队列/锁），并在输出中说明当前策略。

## 4) 配置目录边界

目的：避免运行期数据污染仓库、遵守宪章。

- 运行期日志/状态/数据库 MUST 写入配置根目录（默认 `~/.config/vibego/`）。
- 配置根目录解析优先级 MUST 遵循：
  `MASTER_CONFIG_ROOT` → `VIBEGO_CONFIG_DIR` → `$XDG_CONFIG_HOME/vibego` → `~/.config/vibego`。

## 5) 参考链接（官方/可核验）

- 项目宪章：`/Users/david/hypha/tools/vibego/.specify/memory/constitution.md`
- Spec Kit（GitHub）：https://github.com/github/spec-kit
- Spec-Driven Development（上游文档）：https://raw.githubusercontent.com/github/spec-kit/main/spec-driven.md
- uv（Spec Kit 推荐的包管理工具）：https://docs.astral.sh/uv/
- RFC 2119（MUST/SHOULD/MAY）：https://www.rfc-editor.org/rfc/rfc2119
- SemVer 2.0.0：https://semver.org/
- XDG Base Directory：https://specifications.freedesktop.org/basedir-spec/basedir-spec-latest.html
- Telegram Bot API：https://core.telegram.org/bots/api
