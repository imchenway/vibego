from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

os.environ.setdefault("BOT_TOKEN", "TEST_TOKEN")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from parallel_runtime import ParallelSessionRecord  # noqa: E402


def test_build_session_live_list_view_includes_main_and_parallel(monkeypatch):
    entries = [
        bot.SessionLiveEntry(
            key="main",
            label="💻 主会话（vibe）",
            tmux_session="vibe",
            kind="main",
            task_id=None,
        ),
        bot.SessionLiveEntry(
            key="parallel:TASK_0115",
            label="/TASK_0115 并行会话",
            tmux_session="vibe-par-demo",
            kind="parallel",
            task_id="TASK_0115",
        ),
    ]

    async def fake_list_project_live_sessions():
        return entries

    monkeypatch.setattr(bot, "_list_project_live_sessions", fake_list_project_live_sessions, raising=False)

    text, markup = asyncio.run(bot._build_session_live_list_view())

    assert "会话实况" in text
    callback_data = [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    ]
    assert bot.SESSION_LIVE_MAIN_CALLBACK in callback_data
    assert f"{bot.SESSION_LIVE_PARALLEL_PREFIX}TASK_0115" in callback_data


def test_build_session_live_snapshot_view_uses_parallel_tmux_session(monkeypatch):
    entry = bot.SessionLiveEntry(
        key="parallel:TASK_0115",
        label="/TASK_0115 并行会话",
        tmux_session="vibe-par-demo",
        kind="parallel",
        task_id="TASK_0115",
    )
    captured: dict[str, object] = {}

    async def fake_resolve_session_live_entry(entry_key: str):
        assert entry_key == "parallel:TASK_0115"
        return entry

    def fake_capture(lines: int, tmux_session: str | None = None):
        captured["lines"] = lines
        captured["tmux_session"] = tmux_session
        return "line-1\nline-2"

    monkeypatch.setattr(bot, "_resolve_session_live_entry", fake_resolve_session_live_entry, raising=False)
    monkeypatch.setattr(bot, "_capture_tmux_recent_lines", fake_capture)

    text, _markup = asyncio.run(bot._build_session_live_snapshot_view("parallel:TASK_0115"))

    assert "line-1" in text
    assert captured["lines"] == bot.TMUX_SNAPSHOT_LINES
    assert captured["tmux_session"] == "vibe-par-demo"


def test_list_active_parallel_sessions_skips_stale_records(monkeypatch):
    session = ParallelSessionRecord(
        id=1,
        task_id="TASK_0115",
        project_slug="demo",
        title_snapshot="并行任务",
        workspace_root="/tmp/workspace",
        tmux_session="vibe-par-demo",
        pointer_file="/tmp/pointer.txt",
        task_branch="vibego/TASK_0115",
        status="running",
        created_at="2025-01-01T00:00:00+08:00",
        updated_at="2025-01-01T00:00:00+08:00",
    )
    updates: list[tuple[str, str, str | None]] = []
    drops: list[str] = []

    async def fake_list_sessions():
        return [session]

    async def fake_update_status(task_id: str, *, status: str, last_error=None, **_kwargs):
        updates.append((task_id, status, last_error))

    async def fake_drop_parallel_session_bindings(task_id: str, *, session_key=None):
        drops.append(task_id)

    monkeypatch.setattr(bot.PARALLEL_SESSION_STORE, "list_sessions", fake_list_sessions)
    monkeypatch.setattr(bot.PARALLEL_SESSION_STORE, "update_status", fake_update_status)
    monkeypatch.setattr(bot, "_drop_parallel_session_bindings", fake_drop_parallel_session_bindings)
    monkeypatch.setattr(bot, "_parallel_session_runtime_issue", lambda _session: "tmux 会话不存在")

    result = asyncio.run(bot._list_active_parallel_sessions())

    assert result == []
    assert updates == [("TASK_0115", "closed", "tmux 会话不存在")]
    assert drops == ["TASK_0115"]
