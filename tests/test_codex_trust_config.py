from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_TOKEN", "TEST_TOKEN")

import bot  # noqa: E402
from parallel_runtime import ParallelSessionStore  # noqa: E402


def test_parallel_session_store_persists_codex_trusted_path_records(tmp_path: Path) -> None:
    """Codex trusted 路径注册表应可持久化增删查。"""

    db_path = tmp_path / "parallel.db"
    store = ParallelSessionStore(db_path, "demo")

    async def scenario() -> None:
        await store.upsert_trusted_path(
            path="/tmp/demo-workspace",
            scope="parallel_workspace",
            owner_key="TASK_9001",
            previous_trust_level=None,
            managed_by_vibego=True,
        )
        record = await store.get_trusted_path("/tmp/demo-workspace")
        assert record is not None
        assert record.path == "/tmp/demo-workspace"
        assert record.scope == "parallel_workspace"
        assert record.owner_key == "TASK_9001"
        assert record.managed_by_vibego is True

        items = await store.list_trusted_paths(scope="parallel_workspace")
        assert len(items) == 1

        await store.delete_trusted_path("/tmp/demo-workspace")
        missing = await store.get_trusted_path("/tmp/demo-workspace")
        assert missing is None

    asyncio.run(scenario())


def test_ensure_codex_trusted_project_path_creates_missing_section_and_records_vibego_managed_entry(
    monkeypatch, tmp_path: Path
) -> None:
    """缺失 trusted 配置时，应自动补写并登记为 vibego 自管。"""

    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")
    target_path = tmp_path / "workspace"
    target_path.mkdir()
    captured: dict[str, object] = {}

    async def fake_get_trusted_path(_path: str):
        return None

    async def fake_upsert_trusted_path(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(bot, "CODEX_CONFIG_PATH", config_path)
    monkeypatch.setattr(bot.PARALLEL_SESSION_STORE, "get_trusted_path", fake_get_trusted_path)
    monkeypatch.setattr(bot.PARALLEL_SESSION_STORE, "upsert_trusted_path", fake_upsert_trusted_path)

    asyncio.run(
        bot._ensure_codex_trusted_project_path(
            target_path,
            scope="parallel_workspace",
            owner_key="TASK_9002",
        )
    )

    content = config_path.read_text(encoding="utf-8")
    expected_header = f'[projects."{target_path}"]'
    assert expected_header in content
    assert 'trust_level = "trusted"' in content
    assert captured["path"] == str(target_path)
    assert captured["scope"] == "parallel_workspace"
    assert captured["owner_key"] == "TASK_9002"
    assert captured["previous_trust_level"] is None
    assert captured["managed_by_vibego"] is True


def test_cleanup_codex_trusted_project_path_restores_previous_untrusted_value(monkeypatch, tmp_path: Path) -> None:
    """清理自管 trusted 路径时，应恢复旧值而不是一律删除。"""

    config_path = tmp_path / "config.toml"
    target_path = tmp_path / "workspace"
    config_path.write_text(
        f'[projects."{target_path}"]\ntrust_level = "trusted"\n',
        encoding="utf-8",
    )
    record = SimpleNamespace(
        path=str(target_path),
        scope="parallel_workspace",
        owner_key="TASK_9003",
        previous_trust_level="untrusted",
        managed_by_vibego=True,
    )
    deleted: list[str] = []

    async def fake_get_trusted_path(_path: str):
        return record

    async def fake_delete_trusted_path(path: str):
        deleted.append(path)

    monkeypatch.setattr(bot, "CODEX_CONFIG_PATH", config_path)
    monkeypatch.setattr(bot.PARALLEL_SESSION_STORE, "get_trusted_path", fake_get_trusted_path)
    monkeypatch.setattr(bot.PARALLEL_SESSION_STORE, "delete_trusted_path", fake_delete_trusted_path)

    asyncio.run(
        bot._cleanup_codex_trusted_project_path(
            target_path,
            scope="parallel_workspace",
            owner_key="TASK_9003",
        )
    )

    content = config_path.read_text(encoding="utf-8")
    assert 'trust_level = "untrusted"' in content
    assert 'trust_level = "trusted"' not in content
    assert deleted == [str(target_path)]


def test_reconcile_codex_trusted_paths_removes_stale_managed_parallel_entries(monkeypatch, tmp_path: Path) -> None:
    """启动对账时，应清除目录已不存在的并行 trusted 条目。"""

    config_path = tmp_path / "config.toml"
    stale_path = tmp_path / "missing-workspace"
    config_path.write_text(
        '[projects."/tmp/keep"]\ntrust_level = "trusted"\n\n'
        f'[projects."{stale_path}"]\ntrust_level = "trusted"\n',
        encoding="utf-8",
    )
    record = SimpleNamespace(
        path=str(stale_path),
        scope="parallel_workspace",
        owner_key="TASK_9004",
        previous_trust_level=None,
        managed_by_vibego=True,
    )
    deleted: list[str] = []

    async def fake_list_trusted_paths(scope: str | None = None):
        assert scope == "parallel_workspace"
        return [record]

    async def fake_get_session(_task_id: str):
        return None

    async def fake_delete_trusted_path(path: str):
        deleted.append(path)

    monkeypatch.setattr(bot, "CODEX_CONFIG_PATH", config_path)
    monkeypatch.setattr(bot.PARALLEL_SESSION_STORE, "list_trusted_paths", fake_list_trusted_paths)
    monkeypatch.setattr(bot.PARALLEL_SESSION_STORE, "get_session", fake_get_session)
    monkeypatch.setattr(bot.PARALLEL_SESSION_STORE, "delete_trusted_path", fake_delete_trusted_path)

    asyncio.run(bot._reconcile_codex_trusted_paths())

    content = config_path.read_text(encoding="utf-8")
    assert f'[projects."{stale_path}"]' not in content
    assert '[projects."/tmp/keep"]' in content
    assert deleted == [str(stale_path)]


def test_ensure_primary_workdir_codex_trust_uses_primary_workdir(monkeypatch, tmp_path: Path) -> None:
    """worker 启动阶段应优先确保 PRIMARY_WORKDIR 被 trusted。"""

    calls: list[tuple[str, str, str]] = []

    async def fake_ensure(path: Path, *, scope: str, owner_key: str):
        calls.append((str(path), scope, owner_key))

    monkeypatch.setattr(bot, "PRIMARY_WORKDIR", tmp_path / "project-root")
    monkeypatch.setattr(bot, "PROJECT_SLUG", "demo-project")
    monkeypatch.setattr(bot, "_ensure_codex_trusted_project_path", fake_ensure)

    asyncio.run(bot._ensure_primary_workdir_codex_trust())

    assert calls == [(str(tmp_path / "project-root"), "project_workdir", "demo-project")]
