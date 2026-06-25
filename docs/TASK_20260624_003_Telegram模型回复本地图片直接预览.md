# TASK_20260624_003 Telegram 模型回复本地图片直接预览

## 1. 背景与目标

用户反馈：模型已经生成了本地 PNG 流程图，并在 Telegram 回复中列出路径，但 Telegram 只能看到文本路径，不能直接预览图片。

目标：当模型最终回复引用当前项目目录内的本地图片产物时，vibego 自动在文本回复后补发 Telegram 可预览图片；后续 `TASK_20260625_002` 已补充近期 `/tmp`/`TMPDIR` 图片安全白名单；若 Telegram 图片直发失败，则降级为文件发送，避免产物丢失。

## 2. 现状与根因

现状证据：

- 命令执行路径已有 `TG_PHOTO_FILE` 协议，`bot.py::_execute_command_definition` 会解析 stdout 并调用 `send_photo`，失败时降级 `send_document`。
- 模型回复路径 `bot.py::_deliver_pending_messages_locked` 只把最终文本交给 `reply_large_text`，不会识别回复里的本地图片路径。

根因：图片回传能力只绑定在“命令 stdout 协议”，没有绑定到“模型 final answer 展示层”。模型生成图片后只写出路径，Telegram 端缺少本地图片产物识别与发送步骤。

## 3. 受影响范围

| 范围 | 是否影响 | 说明 |
| --- | --- | --- |
| `bot.py` | 是 | 新增模型回复本地图片路径识别、安全边界校验、图片发送与文件降级。 |
| `tests/test_plan_progress.py` | 是 | 新增模型 final answer 引用本地图片后的发送顺序、安全边界与降级测试。 |
| `tests/test_command_execution_flow.py` | 否 | 既有命令 `TG_PHOTO_FILE` 回传路径保持不变，仅作为回归集合。 |
| SQLite / 任务表 / 命令表 | 否 | 不新增字段、不改变迁移。 |
| Telegram 命令协议 | 否 | 不新增命令，不改变 `TG_PHOTO_FILE` stdout 协议。 |
| 前端 / 小程序 / Web | 否 | 本次只改 Telegram worker 展示层。 |

## 4. 契约变更

- 模型最终回复中出现当前项目目录内的本地图片路径时，vibego 自动补发图片。
- 支持图片格式：`.png`、`.jpg`、`.jpeg`、`.webp`。
- 支持路径形态：
  - Markdown 图片/链接：`![图](docs/a.png)`、`[图](docs/a.png)`；
  - 普通文本路径：`docs/a.png`、`/项目根/docs/a.png`；
  - 反引号包裹路径。
- 安全边界：
  - 只允许当前 session cwd、`PRIMARY_WORKDIR`、`MODEL_WORKDIR/CODEX_WORKDIR` 内的图片；
  - 项目目录外绝对路径默认不自动发送；`TASK_20260625_002` 起，模型显式引用且通过扩展名、MIME、大小、mtime 与事件时间窗口校验的近期 `/tmp`/`TMPDIR` 图片例外允许直发；
  - 缺失文件或非图片扩展不发送；
  - 每条回复最多发送 `TELEGRAM_MODEL_LOCAL_IMAGE_MAX_COUNT` 张，默认 4 张；
  - 同一路径去重。
- 发送顺序：先发送模型文本，再发送图片；`send_photo` 失败时降级 `send_document`。
- 图片发送失败不回滚 session offset，避免下一轮重复发送同一模型文本。

## 5. 实现摘要

1. 新增 `MODEL_RESPONSE_LOCAL_IMAGE_*` 常量与正则，提取模型回复中的本地图片 token。
2. 新增 `_model_response_local_image_roots()`，从 session cwd、worker 主工作目录和环境工作目录收敛允许根目录。
3. 新增 `_resolve_model_response_local_image_path()` 与 `_path_is_within_directory()`，确保候选文件真实存在且在允许目录内。
4. 新增 `_send_model_response_local_images()`，复用 Telegram `send_photo`，失败时 `send_document` 降级。
5. 在 `_deliver_pending_messages_locked()` 文本投递成功后调用图片发送函数，保持 JSONL 原文不变。

## 6. 测试矩阵

| 场景 | 用例 | 预期 |
| --- | --- | --- |
| 模型回复列出项目内 PNG | `test_deliver_pending_messages_sends_project_local_image_after_text` | 文本先发，随后 `send_photo` 发送图片。 |
| 模型回复列出项目外图片 | `test_deliver_pending_messages_ignores_local_image_outside_project` | 不发送图片/文件，避免误发本机文件。 |
| 模型回复列出近期 `/tmp` 图片 | `test_deliver_pending_messages_sends_recent_tmp_image_after_text` | 文本先发，随后 `send_photo` 发送图片。 |
| 模型回复列出过旧 `/tmp` 图片 | `test_deliver_pending_messages_ignores_stale_tmp_image` | 不发送图片/文件，避免误发历史临时文件。 |
| Telegram 图片直发失败 | `test_deliver_pending_messages_falls_back_to_document_when_local_image_photo_fails` | 先尝试 `send_photo`，失败后降级 `send_document`。 |
| 命令二维码回传回归 | `tests/test_command_execution_flow.py` | 既有 `TG_PHOTO_FILE` 行为不变。 |

## 7. 执行记录

- [x] Baseline：`python3.11 -m pytest -q tests/test_plan_progress.py tests/test_command_execution_flow.py` -> `43 passed`。
- [x] RED：新增 3 个模型回复图片测试后执行聚焦命令 -> `2 failed, 1 passed`；失败点为旧链路只发送文本、不发送图片，符合预期。
- [x] GREEN：实现图片识别、安全边界与发送降级后执行同一聚焦命令 -> `3 passed, 2 warnings`。
- [x] 聚焦回归：`python3.11 -m pytest -q tests/test_plan_progress.py tests/test_command_execution_flow.py` -> `46 passed`。

## 8. 风险与回滚

| 风险 | 缓解 |
| --- | --- |
| 模型回复误包含本机敏感图片路径 | 默认只允许当前项目目录内图片，项目外绝对路径跳过。 |
| 同一回复列出大量图片刷屏 | 默认最多 4 张，可通过 `TELEGRAM_MODEL_LOCAL_IMAGE_MAX_COUNT` 调整或设 0 关闭。 |
| Telegram `send_photo` 因格式/网络失败 | 自动降级文件发送；降级也失败时只记录 warning，不重复模型文本。 |
| 路径识别误伤普通文本 | 只识别明确图片扩展，且必须存在真实文件。 |

回滚方式：移除 `bot.py` 中 `MODEL_RESPONSE_LOCAL_IMAGE_*` 常量、图片收集/发送函数与 `_deliver_pending_messages_locked()` 的调用；删除新增测试；移除 AGENTS 事实行与本文档。

## 9. 后续验证补充

实现后仍需完成最终基础验证：

```bash
python3.11 -m py_compile bot.py
python3.11 -m vibego_cli doctor
bash scripts/test_deps_check.sh
git diff --check
```

上线后需要重启对应 vibego worker，才能加载新的 `bot.py` 展示层逻辑。

## 10. 最终验证记录

- [x] Python 编译：`python3.11 -m py_compile bot.py` -> 通过。
- [x] 聚焦回归：`python3.11 -m pytest -q tests/test_plan_progress.py tests/test_command_execution_flow.py` -> `46 passed`。
- [x] 运行诊断：`python3.11 -m vibego_cli doctor` -> `python_ok=true`，依赖缺失为空。
- [x] 依赖自检：`bash scripts/test_deps_check.sh` -> 依赖完整，runtime venv 存在，关键依赖已安装。
- [x] Diff 格式检查：`git diff --check` -> 通过。

## 11. 全量回归记录

- [ ] 全量回归：`python3.11 -m pytest -q` -> `3 failed, 1000 passed, 6 warnings`。
- 失败项均为既有规约模板断言：
  - `tests/test_agents_template_migration.py::test_enforced_notice_points_to_agents_md`
  - `tests/test_agents_template_migration.py::test_enforced_notice_adds_user_requirement_header_before_prompt`
  - `tests/test_agents_template_migration.py::test_agents_template_requires_comet_for_complex_workflows`
- 判定：失败点集中在 `ENFORCED_AGENTS_NOTICE` / `AGENTS-template.md` 的 Comet/AGENTS 规约口径，与本次模型回复本地图片回传链路无关；本任务不扩大范围修复，避免把展示层能力改动混入规约模板迁移。
