# TASK_20260619_001 项目显示名与 Telegram Bot 名解耦

## 1. 背景与结论

- 现象：新增项目时输入 `Zeus`、`AI` 等短名称会被提示 `bot 名仅允许 5-64 位字母、数字、下划线或点`。
- 根因：Master 项目向导把用户理解的“项目名称”直接当作 `bot_name` 校验；`bot_name` 实际是 Telegram bot 用户名，应保留 5-64 位校验。
- 修法：启用已有 JSON `name` / DB `legacy_name` 作为项目显示名，新增/编辑向导先录入项目显示名，再录入 Telegram `bot_name`。

## 2. 受影响目录

| 范围 | 是否影响 | 说明 |
| --- | --- | --- |
| `master.py` | 是 | 项目向导字段、显示名优先级、项目名称校验、提交摘要。 |
| `project_repository.py` | 是 | 复用既有 `legacy_name` / JSON `name` 字段，无新增 schema。 |
| `tests/test_master_project_management.py` | 是 | 增加短项目名、显示名优先、无效名称、编辑显示名回归测试。 |
| `README.md` / `config/projects.json.example` | 是 | 文档化 `name` 字段。 |
| `AGENTS.md` | 是 | 增加项目显示名与 `bot_name` 边界事实。 |
| Worker / `bot.py` | 否 | Worker 仍使用 `PROJECT_NAME` / `project_slug` 作为运行期标识，展示名不进入 worker。 |
| SQLite 表结构 | 否 | 复用 `projects.legacy_name`，不新增字段、不需要迁移。 |

## 3. 契约变更

- `name` / `legacy_name`：项目显示名，1-64 字符，允许短名称；禁止空值、换行与控制字符。
- `bot_name`：Telegram bot 用户名，仍为 5-64 位字母、数字、下划线或点。
- `ProjectConfig.display_name`：优先返回 `legacy_name`，为空时回退 `bot_name`。
- `project_slug`：仍由 `bot_name` 默认生成，继续用于日志目录、worker 路径与 tmux session；项目显示名不参与运行目录命名。
- 展示名唯一性：Master 向导层校验展示名大小写不敏感唯一，避免用显示名查找项目时歧义。

## 4. 测试矩阵

| 场景 | 用例 | 预期 |
| --- | --- | --- |
| 短项目名创建 | `test_project_display_name_accepts_short_name_and_controls_button_label` | `Zeus` 可作为显示名，列表显示 `Zeus`，记录中 `bot_name` 仍为 `ZeusProjectBot`。 |
| 显示名优先 | `test_project_config_display_name_prefers_project_name` | `ProjectConfig.display_name == name`。 |
| 项目名校验 | `test_validate_project_name_rejects_invalid_and_duplicate_names` | 空值、换行、超过 64 字符、重复显示名被拒绝；`AI` 通过。 |
| bot_name 校验不放松 | `test_validate_bot_name_still_requires_telegram_length` | `AI` 作为 `bot_name` 仍失败。 |
| 编辑显示名 | `test_edit_flow_updates_short_project_display_name` | 已有项目可改为短显示名 `AI`。 |
| 既有项目管理回归 | `tests/test_master_project_management.py` | 项目管理测试整体通过。 |
| slug 运行路径回归 | `tests/test_worker_health_boot_id.py` | `project_slug` 与 worker 健康检查不受显示名影响。 |

## 5. 实施顺序

1. 跑 `tests/test_master_project_management.py` 建立基线。
2. 先写失败测试，覆盖短显示名、显示名优先、非法显示名、`bot_name` 不放松、编辑显示名。
3. 修改 `master.py`：新增 `project_name` 向导字段、显示名校验、`display_name` 优先级、提交时写入 `legacy_name`。
4. 更新 README、配置示例、AGENTS 事实与本任务文档。
5. 跑聚焦测试与回归测试，记录结果。

## 6. 风险与回滚

- 风险：直接编辑 JSON/DB 仍可能绕过向导写入重复 `name`；当前保持与既有配置方式一致，不新增数据库唯一约束。
- 风险：旧配置的 `name` 若原本只是历史兼容字段，现在会成为优先展示名；这是本次复用字段的预期行为。
- 回滚：回退 `master.py` 中 `project_name` 向导字段、`display_name` 优先级和 `_session_to_record` 的 `legacy_name` 写入；同时回退测试、README、配置示例、AGENTS 与本任务文档。

## 7. 完成状态

- [x] 基线测试：`python3.11 -m pytest -q tests/test_master_project_management.py` -> `32 passed`。
- [x] RED：新增 5 个项目显示名测试，运行聚焦用例 -> `4 failed, 1 passed`，失败原因符合预期。
- [x] GREEN：实现后运行聚焦用例 -> `5 passed`。
- [x] 项目管理回归：`python3.11 -m pytest -q tests/test_master_project_management.py` -> `37 passed`。
- [x] 运行路径回归：`python3.11 -m pytest -q tests/test_worker_health_boot_id.py` -> `11 passed`。
- [x] 受影响范围合并验证：`python3.11 -m pytest -q tests/test_master_project_management.py tests/test_worker_health_boot_id.py` -> `48 passed`。
- [x] 运行诊断：`python3.11 -m vibego_cli doctor` -> 成功，`python_ok=true` 且项目配置存在。
- [x] 全量 pytest：`python3.11 -m pytest -q` -> `979 passed, 3 failed, 6 warnings`；失败集中在 `tests/test_agents_template_migration.py` 的 AGENTS/Comet 历史基线断言，与本次项目显示名改造无直接关系。
