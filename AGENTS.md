# AGENTS.md（Strict Evidence Mode）

> 适用范围：`/Users/david/hypha/tools/vibego`  
> 生成时间：2026-03-03

## 0) Non-negotiables

1. **严格证据模式**：所有“仓库事实”必须附带 `文件路径 + 锚点`；找不到证据只允许写 `TODO`。
2. **Fail-Closed**：证据冲突无法裁决时，不下结论，进入 `TODO & Known Issues` 并阻断后续实现决策。
3. **写入范围限制**：当前任务仅允许写入 `./AGENTS.md`（本文件）。
4. **冲突裁决优先级**：`CI/脚本 > README > 构建配置 > 代码`。
5. **现实优先**：若本文件与仓库现实冲突，先更新本文件再继续开发。
6. **验证资产保护**：单元测试/测试用例/覆盖率规则属于长期验证资产，后续需求与修复均不得削弱。
7. **前置规约来源已读取**：`$HOME/.config/vibego/AGENTS.md`（锚点：`# PLAN 模式门禁`）。

---

## 1) Facts Table

| Fact        | Value                                                                                                                                                                     | Evidence                                                                                                                                                                                                                                  |
|-------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 项目主语言/构建系统  | Python + setuptools（非 Maven/Gradle 证据）                                                                                                                                    | `pyproject.toml`（锚点：`[build-system]`, `build-backend = "setuptools.build_meta"`, `[project]`）                                                                                                                                             |
| 包名与 CLI 入口  | `vibego`，控制台命令 `vibego = vibego_cli.main:main`                                                                                                                            | `pyproject.toml`（锚点：`[project.scripts]`）；`vibego.egg-info/entry_points.txt`（锚点：`[console_scripts]`）                                                                                                                                       |
| Python 版本口径 | 打包声明 `>=3.9`；CLI 运行检查 `>=3.11`                                                                                                                                            | `pyproject.toml`（锚点：`requires-python = ">=3.9"`）；`vibego_cli/deps.py`（锚点：`python_version_ok`）                                                                                                                                             |
| 主控进程职责      | `master.py` 管理多项目 worker，调用 run/stop 脚本                                                                                                                                   | `master.py`（文件头注释，锚点：`Master bot controller.`、`调用 scripts/run_bot.sh / scripts/stop_bot.sh`）                                                                                                                                              |
| worker 职责   | `bot.py` 负责 Telegram 消息收发、任务流程与模型会话桥接                                                                                                                                     | `bot.py`（文件头注释，锚点：`Telegram 提示词 → Mac 执行 → 回推`）                                                                                                                                                                                           |
| 运行期目录规范     | 运行日志/状态默认写入 `~/.config/vibego/`                                                                                                                                           | `README.md`（锚点：`运行期日志和状态文件统一写入本机 ~/.config/vibego/`）                                                                                                                                                                                      |
| 配置根目录解析     | 优先 `VIBEGO_CONFIG_DIR`，否则 `XDG_CONFIG_HOME/vibego` 或 `~/.config/vibego`                                                                                                   | `vibego_cli/config.py`（锚点：`_default_config_root`）                                                                                                                                                                                         |
| 关键依赖        | `aiogram`, `aiohttp-socks`, `aiosqlite`, `markdown-it-py`                                                                                                                 | `pyproject.toml`（锚点：`dependencies = [`）                                                                                                                                                                                                   |
| 模型运行形态      | 支持 `codex / claudecode / gemini`                                                                                                                                          | `scripts/run_bot.sh`（锚点：`--model 启动指定模型 (codex                                                                                                                                                                                            |claudecode|gemini)`）；`README.md`（锚点：`模型切换`） |
| 数据存储        | SQLite（项目配置 + 任务/命令数据）                                                                                                                                                    | `project_repository.py`（锚点：`sqlite3.connect`, `CREATE TABLE IF NOT EXISTS projects`）；`tasks/service.py`（锚点：`aiosqlite.connect`, `CREATE TABLE IF NOT EXISTS tasks`）；`command_center/service.py`（锚点：`CREATE TABLE IF NOT EXISTS commands`） |
| 迁移方式        | 应用内代码迁移（`CREATE TABLE IF NOT EXISTS` + `ALTER TABLE`）                                                                                                                     | `tasks/service.py`（锚点：`_create_tables`, `ALTER TABLE ...`）；`command_center/service.py`（锚点：`_ensure_scope_column`）                                                                                                                         |
| 日志格式        | 结构化上下文：`agent/project/model/session`                                                                                                                                      | `logging_setup.py`（锚点：`LOG_FORMAT = "%(asctime)s ... [%(agent)] [%(project)] [%(model)] [%(session)]"`）                                                                                                                                   |
| CLI 子命令     | `init/start/stop/status/doctor/commands-seed`                                                                                                                             | `vibego_cli/main.py`（锚点：`subparsers.add_parser("...")`）                                                                                                                                                                                   |
| 已执行最小校验（本次） | `python3.11 -m vibego_cli --help`、`python3.11 -m vibego_cli doctor`、`bash scripts/test_deps_check.sh`、`python3.11 -m pytest -q tests/test_vibego_cli_runtime_venv.py` 均成功 | 本次命令执行记录（2026-03-03）；命令来源见 `vibego_cli/main.py`、`scripts/test_deps_check.sh`、`tests/test_vibego_cli_runtime_venv.py`                                                                                                                      |

---

## 2) Repo Map

| Path                      | Responsibility                              | Evidence                                                                        |
|---------------------------|---------------------------------------------|---------------------------------------------------------------------------------|
| `master.py`               | Master 控制面：项目编排、worker 生命周期管理、管理员命令         | `master.py`（文件头注释，锚点：`统一管理多个项目 worker`）                                         |
| `bot.py`                  | Worker 执行面：Telegram 消息处理、任务流、模型会话读写         | `bot.py`（文件头注释，锚点：`Telegram 提示词 → Mac 执行 → 回推`）                                 |
| `vibego_cli/`             | 本地 CLI：`init/start/stop/status/doctor` 管理入口 | `vibego_cli/main.py`（锚点：`build_parser` + `subparsers.add_parser`）               |
| `tasks/`                  | 任务领域模型与持久化服务（SQLite）                        | `tasks/models.py`（锚点：`TaskRecord`）；`tasks/service.py`（锚点：`class TaskService`）   |
| `command_center/`         | 通用命令中心（命令定义、别名、历史）                          | `command_center/service.py`（锚点：`class CommandService`）                          |
| `scripts/`                | 运维脚本：启动/停止 worker、发布、依赖检查                   | `README.md`（锚点：`目录结构`）；`scripts/run_bot.sh`/`scripts/stop_bot.sh`               |
| `tests/`                  | pytest 自动化测试资产                              | `tests/conftest.py`（锚点：`import pytest`）；`tests/test_vibego_cli_runtime_venv.py` |
| `docs/`                   | 历史任务文档与回溯记录                                 | 目录扫描（锚点：`./docs/TASK_*.md`）                                                     |
| `pom.xml / build.gradle*` | TODO（未在本仓库形成可引用证据）                          | TODO：若为 Java 后端，请补充 Maven/Gradle 文件                                             |

---

## 3) Commands Table

> 状态定义：✅ Verified（本次执行成功）/ ⚠️ Unverified（有来源未执行）/ ❌ Unknown（无可靠来源）/ 🔴 Failed（本次执行失败）

| Purpose                  | Command                                                         | Status        | Evidence                                                                                                 | Notes                        |
|--------------------------|-----------------------------------------------------------------|---------------|----------------------------------------------------------------------------------------------------------|------------------------------|
| 查看 CLI 帮助                | `python3.11 -m vibego_cli --help`                               | ✅ Verified    | `vibego_cli/__main__.py`（锚点：`python -m vibego_cli`）；本次输出展示 `init/start/stop/status/doctor/commands-seed` | 零副作用                         |
| 本地运行诊断                   | `python3.11 -m vibego_cli doctor`                               | ✅ Verified    | `vibego_cli/main.py`（锚点：`command_doctor`）                                                                | 输出 config_root/env/db 状态     |
| Worker 启动（参数说明）          | `bash scripts/run_bot.sh --help`                                | ✅ Verified    | `scripts/run_bot.sh`（锚点：`usage()`）                                                                       | 仅帮助信息，无启动副作用                 |
| Worker 停止（参数说明）          | `bash scripts/stop_bot.sh --help`                               | ✅ Verified    | `scripts/stop_bot.sh`（锚点：`usage()`）                                                                      | 仅帮助信息，无停止动作                  |
| 依赖自检脚本                   | `bash scripts/test_deps_check.sh`                               | ✅ Verified    | `scripts/test_deps_check.sh`（脚本全文）                                                                       | 该脚本检查 venv 与关键依赖；**非全量单测**   |
| 最小 pytest 验证             | `python3.11 -m pytest -q tests/test_vibego_cli_runtime_venv.py` | ✅ Verified    | `tests/test_vibego_cli_runtime_venv.py`；本次输出 `2 passed`                                                  | 仅样例测试，不代表全量                  |
| 打包构建（正式）                 | `python -m build`                                               | ⚠️ Unverified | `scripts/publish.sh`（锚点：`步骤 4/7：构建分发包` + `python -m build`）                                              | 未执行：会写 `dist/`，当前任务限制不触发完整构建 |
| 构建工具探测                   | `python3.11 -m build --version`                                 | ✅ Verified    | 本次输出 `build 1.3.0`                                                                                       | 只读校验                         |
| Master 启动（正式）            | `vibego start`                                                  | ⚠️ Unverified | `README.md`（锚点：`vibego start`）；`vibego_cli/main.py`（锚点：`command_start`）                                  | 未执行：会创建/启动进程                 |
| Master 停止（正式）            | `vibego stop`                                                   | ⚠️ Unverified | `vibego_cli/main.py`（锚点：`command_stop`）                                                                  | 未执行：会终止进程                    |
| Lint / Format            | TODO                                                            | ❌ Unknown     | `pyproject.toml` 与 `scripts/` 未发现 ruff/black/flake8/mypy 命令                                              | TODO：补充统一门禁命令                |
| DB 迁移触发命令                | TODO（应用启动内隐执行）                                                  | ❌ Unknown     | `tasks/service.py`（锚点：`initialize -> _create_tables/_migrate_*`）                                         | 无独立 migration CLI            |
| Docker Compose           | TODO                                                            | ❌ Unknown     | 仓库扫描未定位 `docker-compose*.yml`                                                                            | TODO：若有容器化请补充                |
| （失败记录）CLI 帮助（python3）    | `python3 -m vibego_cli --help`                                  | 🔴 Failed     | 本次错误：`ModuleNotFoundError: No module named 'aiosqlite'`                                                  | Python 3.14 环境缺依赖            |
| （失败记录）pytest 版本（python3） | `python3 -m pytest --version`                                   | 🔴 Failed     | 本次错误：`No module named pytest`                                                                            | Python 3.14 环境缺 pytest       |
| （失败记录）build 版本（python3）  | `python3 -m build --version`                                    | 🔴 Failed     | 本次错误：`No module named build`                                                                             | Python 3.14 环境缺 build        |
| （失败记录）空测试文件              | `python3.11 -m pytest -q tests/test_version_display.py`         | 🔴 Failed     | 本次输出：`no tests ran`（exit code 5）                                                                         | 文件中未收集到测试                    |

---

## 4) Config & Environments

1. **配置文件与目录**
    - 配置根：`VIBEGO_CONFIG_DIR` > `XDG_CONFIG_HOME/vibego` > `~/.config/vibego`。
        - Evidence: `vibego_cli/config.py`（锚点：`_default_config_root`）
    - 关键路径：`.env / config/projects.json / config/master.db / state/master_state.json / logs/vibe.log`。
        - Evidence: `vibego_cli/config.py`（锚点：`ENV_FILE`, `PROJECTS_JSON`, `MASTER_DB`, `MASTER_STATE`, `LOG_FILE`）

2. **运行模式/“profile”口径（仓库证据）**
    - 模型维度：`codex` / `claudecode` / `gemini`。
        - Evidence: `scripts/run_bot.sh`（锚点：`--model ... (codex|claudecode|gemini)`）

3. **环境变量（仅列出有证据项）**
    - 目录与路径：`VIBEGO_CONFIG_DIR`, `MASTER_CONFIG_ROOT`, `XDG_CONFIG_HOME`, `LOG_FILE`, `LOG_LEVEL`, `LOG_TIMEZONE`。
        - Evidence: `vibego_cli/config.py`, `logging_setup.py`, `scripts/models/common.sh`
    - Master 鉴权：`MASTER_BOT_TOKEN`, `MASTER_CHAT_ID`, `MASTER_USER_ID`, `MASTER_WHITELIST`。
        - Evidence: `README.md`（锚点：`配置要点`）

4. **依赖服务**
    - Telegram Bot API（HTTPS）。
        - Evidence: `README.md`（锚点：`Telegram Bot API 的 HTTPS 请求通道`）
    - 本地依赖命令：`tmux`、模型 CLI（codex/claude/gemini）。
        - Evidence: `README.md`（锚点：`环境依赖`、`模型切换`）；`scripts/run_bot.sh`（锚点：`command -v tmux`）

5. **敏感信息规则**
    - `~/.config/vibego/.env` 含 token/管理员信息，不得提交版本库。
        - Evidence: `README.md`（锚点：`~/.config/vibego/.env 内包含敏感 Token...请勿提交`）

6. **配置中心（nacos/apollo/consul）**
    - TODO：未定位到仓库内对应配置与接入代码。

---

## 5) Coding Standards

> 仅保留“有证据”的仓库现状；无证据项标 TODO。

1. **模块分层**：CLI（`vibego_cli`）/ 控制层（`master.py`, `bot.py`）/ 领域服务（`tasks`, `command_center`）/ 脚本层（
   `scripts`）。
    - Evidence: `vibego_cli/main.py`、`master.py`、`bot.py`、`tasks/service.py`、`command_center/service.py`

2. **数据模型风格**：广泛使用 `dataclass` 建模 DTO（任务、命令、历史记录）。
    - Evidence: `tasks/models.py`（锚点：`@dataclass` + `TaskRecord` 等）；`command_center/models.py`

3. **异常策略（局部可证）**：
    - 领域异常使用自定义异常（如 `CommandAlreadyExistsError`）。
    - 数据不存在时使用 `ValueError`（如项目不存在）。
    - Evidence: `command_center/service.py`（锚点：`class CommandAlreadyExistsError`）；`project_repository.py`（锚点：
      `raise ValueError(f"未找到项目`）

4. **日志字段规范（可证）**：日志必须携带 `agent/project/model/session` 上下文。
    - Evidence: `logging_setup.py`（锚点：`LOG_FORMAT`、`create_logger`）

5. **事务边界（可证）**：写操作显式事务（`BEGIN IMMEDIATE`/`commit`/`rollback`）。
    - Evidence: `project_repository.py`（锚点：`BEGIN IMMEDIATE`, `conn.rollback()`）；`tasks/service.py`（锚点：
      `await db.execute("BEGIN IMMEDIATE")`）

6. **DTO/HTTP 返回体规范**：TODO（未找到 REST Controller/统一 API 返回体证据）。

---

## 6) Database & Migration

1. **数据库类型与访问层**
    - SQLite（同步 `sqlite3` + 异步 `aiosqlite`）。
    - Evidence: `project_repository.py`（锚点：`import sqlite3`, `sqlite3.connect`）；`tasks/service.py`、
      `command_center/service.py`（锚点：`import aiosqlite`）

2. **核心表（可证）**
    - `projects`（项目配置）
    - `tasks/task_notes/task_attachments/task_history/task_sequences`（任务域）
    - `commands/command_aliases/command_history`（命令中心）
    - Evidence: `project_repository.py`、`tasks/service.py`、`command_center/service.py` 对应 `CREATE TABLE IF NOT EXISTS`

3. **迁移机制**
    - 代码内迁移：`CREATE TABLE IF NOT EXISTS` + `ALTER TABLE` + 启动时调用 `_migrate_*`。
    - Evidence: `tasks/service.py`（锚点：`_create_tables`, `_migrate_timezones`, `_migrate_task_ids_to_underscore`）；
      `command_center/service.py`（锚点：`_ensure_scope_column`）

4. **索引与约束（可证）**
    - 多处 `CREATE INDEX IF NOT EXISTS`；启用 `PRAGMA foreign_keys = ON`。
    - Evidence: `tasks/service.py`、`command_center/service.py`

5. **回滚约束（可证）**
    - 事务内异常回滚（`rollback`）在关键写路径存在。
    - Evidence: `project_repository.py`（锚点：`conn.rollback()`）

6. **外部迁移工具（Flyway/Liquibase）**
    - TODO：未定位到仓库接入证据。

---

## 7) Testing & Quality Gates

### 7.1 Current Evidence

1. **当前测试框架**
    - `pytest`（测试文件显式导入与标记）。
    - Evidence: `tests/conftest.py`（`import pytest`）；`tests/test_wx_preview_port_flow.py`（`@pytest.mark.skipif`）

2. **当前覆盖率工具**
    - TODO：仓库内未定位 `.coveragerc`/`pytest-cov`/coverage 命令配置证据。

3. **当前 coverage 阈值**
    - TODO：未定位阈值配置证据。

4. **当前 test/typecheck/build 证据命令**
    - test（最小验证）：`bash scripts/test_deps_check.sh` ✅；`python3.11 -m pytest -q tests/test_vibego_cli_runtime_venv.py`
      ✅
    - build（工具可用）：`python3.11 -m build --version` ✅；正式构建 `python -m build` ⚠️（来源 `scripts/publish.sh`，未执行）
    - typecheck：TODO（未定位统一命令）

5. **当前 CI / quality gate 现状**
    - TODO：未定位 `.github/workflows` / `Jenkinsfile` / `.gitlab-ci.yml` 证据。

### 7.2 Required Engineering Gate（工程强制门禁，非“仓库现状事实”）

1. baseline test 全绿后，才能开始新需求实现。
2. baseline coverage 目标默认 **100%**（优先 line+branch；若无 branch 工具，line=100% 且测试策略覆盖分支/异常路径）。
3. 新需求必须执行 TDD：
    1) 先写测试；2) 先运行并确认“功能未实现”失败；3) 再写生产代码；4) 再跑全量 test+coverage；5) 连续两次通过且 coverage 恢复到
       100%。
4. 不得通过新增/扩大 exclusions 达标。
5. 测试覆盖场景必须尽可能多而全：正常/边界/异常/状态/幂等/权限/交互/并发（适用时）。

---

## 8) Definition of Done

1. **命令通过要求**（按仓库现有证据）
    - 至少通过：`python3.11 -m vibego_cli doctor`、受影响 pytest 集合、（若涉及发布）`python -m build`。
    - 若缺统一命令（如 coverage/typecheck），必须在 PR/任务文档中记录 TODO 与补齐计划。

2. **补测试/迁移/文档触发条件**
    - 新增/修改逻辑：必须补充对应测试。
    - 涉及表结构/字段变化：必须补迁移与回滚说明（本仓库当前为代码内迁移）。
    - 行为变化：必须更新文档与 AGENTS 证据。

3. **强制门禁（显式）**
    - 新需求实现前：baseline 必须全绿。
    - 新需求必须先补测试并确认失败。
    - 实现后必须双轮通过（连续两次一致）。
    - 无法达到 Required Engineering Gate：**fail-closed**。

---

## 9) Vibe Coding Workflow（适配 PLAN / YOLO）

工作流固定：`vibe -> design -> develop`。

develop 阶段必须强制执行：

1. 影响面分析（子模块/目录/配置/数据）
2. baseline gate（先验证现有 test/build/质量门）
3. TDD gate（先测后码，先红后绿）
4. implementation gate（最小改动，禁止无关重构）
5. self-test gate（受影响 + 全量 + 双轮一致）
6. bounded auto-repair loop（默认最多 5 轮，超限 fail-closed）

---

## 10) Guardrails

### 10.1 Repo-specific Guardrails（有证据）

1. 运行期日志/状态文件应落在 `~/.config/vibego/`（或配置根覆盖），不要落仓库。
    - Evidence: `README.md`（锚点：`运行期日志和状态文件统一写入本机 ~/.config/vibego/`）

2. 敏感 Token 不得提交版本库。
    - Evidence: `README.md`（锚点：`~/.config/vibego/.env 内包含敏感 Token...请勿提交`）

3. worker 生命周期通过 `scripts/run_bot.sh` / `scripts/stop_bot.sh` 管理。
    - Evidence: `README.md`（锚点：`目录结构`）；`master.py`（锚点：`调用 scripts/run_bot.sh / scripts/stop_bot.sh`）

4. SQLite 写路径应保持事务语义（`BEGIN IMMEDIATE` + commit/rollback）。
    - Evidence: `project_repository.py`, `tasks/service.py`

5. 日志输出应保持上下文字段（agent/project/model/session）。
    - Evidence: `logging_setup.py`（锚点：`LOG_FORMAT`）

6. 公共 API 兼容策略：TODO（未找到 REST API 契约证据）。
7. 错误码规范：TODO（未找到统一错误码定义证据）。
8. 配置中心/密钥托管规范：TODO（未定位 nacos/apollo/consul/KMS 接入证据）。
9. 分层依赖约束文档化规则：TODO（未定位显式架构规则文件）。

### 10.2 Universal Safety Guardrails（通用安全护栏，非仓库事实）

1. 未经明确要求，禁止：新增/升级依赖、改构建链/CI、改 public API 合约、改默认配置与生产参数、提交密钥、大范围重命名/重构。
2. 任何 DB schema 变更必须提供迁移与回滚说明；若无迁移机制证据，先补方案再动手。
3. 不得削弱测试资产与回归保护能力。

---

## 11) Common Playbooks

> 每个 playbook 均遵循：**先基线 -> 先补测试（先失败）-> 再实现 -> 再双轮验证**

1. **新增接口（HTTP/开放 API）**
    - TODO：当前仓库未发现 REST Controller/路由层证据，先补“接口契约文档 + 路由入口证据”再实施。

2. **改 DB（SQLite）**
    - Step A：确认影响表/索引与事务点（`tasks/service.py`, `command_center/service.py`, `project_repository.py`）。
    - Step B：先写迁移行为测试（含旧数据兼容/回滚场景）。
    - Step C：最小化修改 `_create_tables/_migrate_*` 或仓储写路径。
    - Step D：双轮执行受影响测试 + 全量测试。

3. **加定时任务或消息流程**
    - Step A：定位入口（`master.py`/`bot.py` 中事件与循环）。
    - Step B：先补异步行为测试（超时/重试/并发）。
    - Step C：实现最小逻辑；保持日志上下文一致。
    - Step D：双轮验证并记录观测点。

4. **排查线上问题**
    - Step A：先取证（`~/.config/vibego/logs/...` + `vibego doctor`）。
    - Step B：补复现测试（先失败）。
    - Step C：最小修复。
    - Step D：双轮回归 + 更新 AGENTS 证据。

---

## 12) TODO & Known Issues

1. **仓库类型期望冲突（高优先）**
    - 现有证据指向 Python 项目（`pyproject.toml`, `vibego_cli/*`）；未找到 Java `pom.xml/build.gradle*`。
    - 缺少的证据来源：`pom.xml`/`build.gradle*`/`src/main/java`（若任务确为 Java 后端请补充）。

2. **CI 门禁证据缺失**
    - 未定位 `.github/workflows`、`Jenkinsfile`、`.gitlab-ci.yml`。
    - 缺少的证据来源：CI 配置文件。

3. **coverage/typecheck 门禁缺失**
    - 未定位 coverage 阈值、typecheck 统一命令。
    - 缺少的证据来源：`.coveragerc`、`pytest-cov` 配置、mypy/pyright/ruff 配置或 CI 步骤。

4. **README 提及 `.env.example` 但仓库未找到文件**
    - Evidence: `README.md`（锚点：`目录结构` 提及 `.env.example`）；目录扫描未发现 `.env*`。
    - 缺少的证据来源：仓库内 `.env.example` 模板文件。

5. **失败命令记录（本次不修代码）**
    - `python3 -m vibego_cli --help` -> `ModuleNotFoundError: aiosqlite`
    - `python3 -m pytest --version` -> `No module named pytest`
    - `python3 -m build --version` -> `No module named build`
    - `python3.11 -m pytest -q tests/test_version_display.py` -> `no tests ran`
    - 缺少的证据来源：统一 Python 版本/虚拟环境使用说明（建议在 README 增补“执行命令需使用 python3.11 或 runtime venv”）。

6. **Java/Spring 入口与配置证据缺失**
    - 未定位 `@SpringBootApplication`、`application*.yml/properties`、Flyway/Liquibase。
    - 缺少的证据来源：Java 代码目录与配置目录（若存在请补充路径）。

7. **Conflicts（按优先级裁决后）**
    - 当前无“同一事实互相矛盾且无法裁决”的硬冲突；仅存在“打包最低版本 3.9 与 CLI 运行建议/检查 3.11+”的口径差异，已在
      Facts 中并列标注。

