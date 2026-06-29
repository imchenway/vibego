# AGENTS Sync Command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 vibego 增加 `agents-sync` 本机同步能力，并确保后续 worker 启动优先使用同步后的 override，避免被旧 pipx 包内容覆盖。

**Architecture:** 新增 `vibego_cli/agents_sync.py` 作为单一同步服务；CLI 和 Master Bot 都调用同一服务。启动脚本只做 override 选择与 fail-closed 校验：存在有效 `~/.config/vibego/agents/current/manifest.json` 时使用 override 模板和 skills；override 损坏时阻断启动；override 不存在时才回退安装包。

**Tech Stack:** Python 3.11、pytest、aiogram handler、bash 启动脚本。

---

## Files

- Create: `vibego_cli/agents_sync.py`：同步服务，负责源目录解析、override 写入、目标 AGENTS 文件更新、manifest 输出。
- Modify: `vibego_cli/main.py`：新增 `agents-sync` CLI 子命令。
- Modify: `master.py`：新增 `/agents_sync` 命令与系统设置按钮。
- Modify: `scripts/run_bot.sh`：worker 启动时优先使用 override，损坏则 fail-closed。
- Modify: `scripts/start_tmux_codex.sh`：兜底 tmux 启动入口同样优先使用 override。
- Create: `tests/test_agents_sync.py`：服务与 CLI 行为测试。
- Modify: `tests/test_master_update_notifications.py`：Master `/agents_sync` handler 测试。
- Modify: `tests/test_chat_menu_buttons.py`：系统设置按钮测试。
- Modify: `tests/test_start_tmux_model_cmd.py`：启动脚本 override 保护测试。
- Modify: `docs/TASK_20260629_005_AGENTS和skills本机同步命令.md`：记录实现与验证。

## Task 1: 同步服务红绿闭环

**Files:**
- Create: `tests/test_agents_sync.py`
- Create: `vibego_cli/agents_sync.py`

- [ ] **Step 1: Write failing service tests**

```python
def test_sync_agents_writes_override_and_targets(tmp_path, monkeypatch):
    source = make_source_root(tmp_path)
    config_root = tmp_path / "config"
    targets = make_targets(tmp_path)
    result = sync_agents(source_root=source, config_root=config_root, targets=targets)
    assert (config_root / "agents/current/AGENTS-template.md").exists()
    assert (config_root / "agents/current/vibego_cli/data/skills/vibe-diagram/SKILL.md").exists()
    assert result.override_root == config_root / "agents/current"
    assert all(path.exists() for path in targets.values())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `BOT_TOKEN=123456:ABC python3.11 -m pytest -q tests/test_agents_sync.py`
Expected: FAIL because `vibego_cli.agents_sync` does not exist.

- [ ] **Step 3: Implement minimal service**

Implement `AgentsSyncError`, `AgentsSyncResult`, `sync_agents`, `render_builtin_skills`, `update_managed_block`, `validate_agents_override_root`.

- [ ] **Step 4: Run test to verify it passes**

Run: `BOT_TOKEN=123456:ABC python3.11 -m pytest -q tests/test_agents_sync.py`
Expected: PASS.

## Task 2: CLI command red-green

**Files:**
- Modify: `tests/test_agents_sync.py`
- Modify: `vibego_cli/main.py`

- [ ] **Step 1: Write failing CLI test**

```python
def test_agents_sync_cli_json_uses_env_targets(tmp_path):
    result = subprocess.run([... "agents-sync", "--source-root", str(source), "--json"], env=isolated_env, check=True)
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `BOT_TOKEN=123456:ABC python3.11 -m pytest -q tests/test_agents_sync.py::test_agents_sync_cli_json_uses_env_targets`
Expected: FAIL because subcommand is missing.

- [ ] **Step 3: Add CLI parser and handler**

Add `agents-sync` parser with `--source-root` and `--json`, return non-zero on `AgentsSyncError`.

- [ ] **Step 4: Run test to verify it passes**

Run: `BOT_TOKEN=123456:ABC python3.11 -m pytest -q tests/test_agents_sync.py::test_agents_sync_cli_json_uses_env_targets`
Expected: PASS.

## Task 3: Master button and command red-green

**Files:**
- Modify: `tests/test_master_update_notifications.py`
- Modify: `tests/test_chat_menu_buttons.py`
- Modify: `master.py`

- [ ] **Step 1: Write failing Master tests**

Add tests for authorized `/agents_sync`, unauthorized request, parallel request rejection, and system settings button callback.

- [ ] **Step 2: Run tests to verify they fail**

Run: `BOT_TOKEN=123456:ABC python3.11 -m pytest -q tests/test_master_update_notifications.py::test_cmd_agents_sync_authorized tests/test_chat_menu_buttons.py::test_master_system_settings_menu_includes_agents_sync`
Expected: FAIL because handler and button are missing.

- [ ] **Step 3: Add Master handler and menu button**

Add `/agents_sync`, `system:agents_sync`, background task lock, and concise status messages.

- [ ] **Step 4: Run tests to verify they pass**

Run the same pytest command. Expected: PASS.

## Task 4: 启动脚本 override 保护红绿闭环

**Files:**
- Modify: `tests/test_start_tmux_model_cmd.py`
- Modify: `scripts/run_bot.sh`
- Modify: `scripts/start_tmux_codex.sh`

- [ ] **Step 1: Write failing script tests**

Static tests assert script text contains `agents/current`, `manifest.json`, `VIBEGO_BUILTIN_SKILLS_DIR`, and fail-closed branch.

- [ ] **Step 2: Run tests to verify they fail**

Run: `BOT_TOKEN=123456:ABC python3.11 -m pytest -q tests/test_start_tmux_model_cmd.py::test_start_tmux_prefers_agents_override_when_manifest_exists`
Expected: FAIL because override logic is missing.

- [ ] **Step 3: Add override selector to scripts**

If `manifest.json` exists and template/skills are valid, set `AGENTS_TEMPLATE_FILE` and `VIBEGO_BUILTIN_SKILLS_DIR`; if manifest exists but required files are missing, exit 1; otherwise use package fallback.

- [ ] **Step 4: Run tests to verify they pass**

Run start_tmux script tests. Expected: PASS.

## Task 5: 文档与最终验证

**Files:**
- Modify: `docs/TASK_20260629_005_AGENTS和skills本机同步命令.md`

- [ ] **Step 1: Update task doc with implemented behavior and validation commands**
- [ ] **Step 2: Run affected tests**

Run: `BOT_TOKEN=123456:ABC python3.11 -m pytest -q tests/test_agents_sync.py tests/test_master_update_notifications.py tests/test_chat_menu_buttons.py tests/test_start_tmux_model_cmd.py`

- [ ] **Step 3: Run syntax checks**

Run: `python3.11 -m py_compile master.py bot.py vibego_cli/main.py vibego_cli/agents_sync.py`

- [ ] **Step 4: Run isolated CLI smoke**

Run `agents-sync --json` with `VIBEGO_CONFIG_DIR`, `CODEX_AGENTS_FILE`, `CLAUDE_AGENTS_FILE`, `GEMINI_AGENTS_FILE` pointing to a temporary directory.

