# TASK_20260616_001 Master项目slug归一化导致Zeus启动状态误判

## 1. 问题与根因

- 现象：Zeus worker 已在 tmux 中启动，且 `logs/codex/zeus/bot.pid` 存在并指向存活进程，但 Master 项目列表仍提示 `Zeus. 启动失败 - 未检测到 bot.pid 或内容为空`。
- 根因：Python 侧 `master.py::_sanitize_slug` / `project_repository.py::_sanitize_slug` 保留 `.`，而 shell 侧 `scripts/models/common.sh::sanitize_slug` 会删除 `.`。因此配置 slug `Zeus.` 在 Master 中是 `zeus.`，在 `run_bot.sh` 中是 `zeus`，健康检查读取了错误目录。
- 证据锚点：
  - `master.py`：`_sanitize_slug`、`_worker_runtime_paths`、`_health_check_worker`。
  - `project_repository.py`：`_sanitize_slug`、`_repair_records`。
  - `scripts/models/common.sh`：`sanitize_slug`。
  - 运行证据：`/Users/david/.config/vibego/logs/codex/zeus/bot.pid` 存在，`/Users/david/.config/vibego/logs/codex/zeus.` 不存在。

## 2. 影响范围

- 受影响目录/文件：
  - `master.py`：项目配置 slug 归一化、状态文件旧 key 兼容读取、worker 健康检查路径。
  - `project_repository.py`：项目仓库读写与启动修复时的 slug 归一化。
  - `tests/test_worker_health_boot_id.py`：Master 健康检查路径与旧 state key 迁移回归测试。
  - `tests/test_master_project_management.py`：仓库层 slug 归一化回归测试。
- 不受影响边界：
  - Telegram Bot API 契约不变。
  - worker 启停脚本参数不变。
  - SQLite 表结构不变，不需要 DB schema migration。
  - tmux session 命名规则仍由 shell `sanitize_slug` 决定，本次只是让 Python 侧对齐。

## 3. 契约变更

- `project_slug` 运行期合法字符收敛为 `[a-z0-9_-]`，空值仍回退为 `project`。
- 空格、`/`、`:`、`\\`、`@` 统一转为 `-`；其他非法字符（包括 `.`）删除。
- 旧 state 文件中的 `zeus.` 等 key 会在读取时按归一化 key `zeus` 继承运行态，避免丢失 `chat_id`、`actual_username`、`telegram_user_id` 等信息。
- 启动时 `ProjectRepository._repair_records()` 会把 DB/JSON 中的旧 slug 修复为新 slug。

## 4. 测试矩阵

| 场景 | 覆盖文件 | 期望 |
| --- | --- | --- |
| `Zeus.` 项目运行路径 | `tests/test_worker_health_boot_id.py::test_project_slug_drops_shell_unsafe_punctuation_for_runtime_paths` | Master 查 `logs/codex/zeus/bot.pid` |
| 旧 state key 迁移 | `tests/test_worker_health_boot_id.py::test_state_store_migrates_legacy_punctuated_slug_from_disk` | `zeus.` 运行态迁移到 `zeus` |
| 仓库层归一化 | `tests/test_master_project_management.py::test_repository_removes_shell_unsafe_punctuation_from_slug` | DB/JSON 保存 `zeus` |
| 既有健康检查与管理流程 | `tests/test_worker_health_boot_id.py`、`tests/test_master_project_management.py`、`tests/test_master_network_resilience.py` | 既有 46 个用例不回退 |

## 5. 实施顺序

1. 先运行受影响 baseline：`python3.11 -m pytest -q tests/test_worker_health_boot_id.py tests/test_master_project_management.py tests/test_master_network_resilience.py`。
2. 增加 3 个回归测试，并确认它们在旧实现下失败。
3. 最小修改 Python 侧 slug 归一化与 StateStore 兼容读取逻辑。
4. 运行新增测试与受影响测试集。
5. 人工复核 Zeus 当前运行证据与路径一致性。

## 6. 风险与回滚

- 风险：如果历史项目 slug 依赖 `.` 等非法字符作为唯一标识，归一化后可能与既有项目冲突。
- 现有保护：`project_repository.py::_repair_records` 已在归一化冲突时抛错 fail-closed，避免静默覆盖。
- 回滚方式：回退 `master.py`、`project_repository.py` 和对应测试文件；运行时若已经导出新 `projects.json`，需从自动备份或手工将 slug 恢复，但不建议回滚到 Python/shell 规则不一致状态。

## 7. 当前执行记录

- Baseline 已执行：`python3.11 -m pytest -q tests/test_worker_health_boot_id.py tests/test_master_project_management.py tests/test_master_network_resilience.py` -> `46 passed`。
- 红灯已确认：新增 3 个回归测试在旧实现下失败，失败原因分别是 `zeus.` 未归一到 `zeus`、旧 state key 未迁移、仓库查不到 `zeus`。
- 绿灯待最终验证：实现已完成，等待最终聚焦测试与复核。

## 8. 最终验证记录

- 新增回归测试单独验证：`python3.11 -m pytest -q tests/test_worker_health_boot_id.py::test_project_slug_drops_shell_unsafe_punctuation_for_runtime_paths tests/test_worker_health_boot_id.py::test_state_store_migrates_legacy_punctuated_slug_from_disk tests/test_master_project_management.py::test_repository_removes_shell_unsafe_punctuation_from_slug` -> `3 passed`。
- 受影响测试双轮验证：
  - 第 1 轮：`python3.11 -m pytest -q tests/test_worker_health_boot_id.py tests/test_master_project_management.py tests/test_master_network_resilience.py` -> `49 passed`。
  - 第 2 轮：同命令 -> `49 passed`。
- Doctor 验证：`python3.11 -m vibego_cli doctor` -> `python_ok=true`，关键依赖缺失列表为空。
- 原始问题复核：`ProjectConfig.from_dict(project_slug="zeus.")` 当前归一为 `zeus`，期望 pid 路径为 `/Users/david/.config/vibego/logs/codex/zeus/bot.pid`，该文件存在且 PID `10745` 存活。
- 全量测试补充执行：`python3.11 -m pytest -q` -> `974 passed, 3 failed, 6 warnings`。失败集中在 `tests/test_agents_template_migration.py`，断言旧 Comet/AGENTS notice 文案，与本次 slug 修复无直接关系；本次未扩大范围修复该历史基线问题。
