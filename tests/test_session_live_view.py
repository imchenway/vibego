from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("BOT_TOKEN", "TEST_TOKEN")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from parallel_runtime import ParallelSessionRecord  # noqa: E402


def _write_jsonl(path: Path, *events: dict) -> None:
    """写入测试用 Codex JSONL 事件，保持一行一个 JSON 对象。"""

    path.write_text(
        "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n",
        encoding="utf-8",
    )


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
    assert bot.SESSION_LIVE_RESUME_CALLBACK in callback_data


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


def test_build_codex_session_status_view_reads_model_context_and_limits(monkeypatch, tmp_path):
    """主会话 JSONL 有 turn_context 与 token_count 时，应展示模型、推理等级、窗口和限额。"""

    session_file = tmp_path / "rollout-status.jsonl"
    pointer_file = tmp_path / "codex-session.pointer"
    pointer_file.write_text(str(session_file), encoding="utf-8")
    _write_jsonl(
        session_file,
        {
            "type": "event_msg",
            "payload": {
                "type": "task_started",
                "model_context_window": 128000,
            },
        },
        {
            "type": "turn_context",
            "payload": {
                "model": "fallback-model",
                "collaboration_mode": {
                    "settings": {
                        "model": "gpt-5.5",
                        "reasoning_effort": "xhigh",
                    }
                },
            },
        },
        {
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "model_context_window": 243200,
                },
                "rate_limits": {
                    "primary": {
                        "used_percent": 6.0,
                        "window_minutes": 300,
                        "resets_at": 1780490580,
                    },
                    "secondary": {
                        "used_percent": 65.0,
                        "window_minutes": 10080,
                        "resets_at": 1780875660,
                    },
                },
            },
        },
    )

    monkeypatch.setattr(bot, "CODEX_SESSION_FILE_PATH", str(pointer_file))

    text = bot._build_codex_session_status_view()

    assert "*会话状态*" in text
    assert "模型：gpt-5.5" in text
    assert "推理等级：xhigh" in text
    assert "Context Window：243,200 tokens" in text
    assert "限额：5h 6%（20:43 重置） · 周 65%（06-08 07:41 重置）" in text


def test_build_codex_session_status_view_uses_latest_token_count(monkeypatch, tmp_path):
    """存在多条 token_count 时，应以最新一条限额数据为准。"""

    session_file = tmp_path / "rollout-status-latest.jsonl"
    pointer_file = tmp_path / "codex-session.pointer"
    pointer_file.write_text(str(session_file), encoding="utf-8")
    _write_jsonl(
        session_file,
        {
            "type": "event_msg",
            "payload": {"type": "task_started", "model_context_window": 128000},
        },
        {
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {"model_context_window": 128000},
                "rate_limits": {
                    "primary": {"used_percent": 10.0, "window_minutes": 300, "resets_at": 1780484580},
                    "secondary": {"used_percent": 11.0, "window_minutes": 10080, "resets_at": 1780875660},
                },
            },
        },
        {
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {"model_context_window": 243200},
                "rate_limits": {
                    "primary": {"used_percent": 42.0, "window_minutes": 300, "resets_at": 1780490580},
                    "secondary": {"used_percent": 64.0, "window_minutes": 10080, "resets_at": 1780875660},
                },
            },
        },
    )

    monkeypatch.setattr(bot, "CODEX_SESSION_FILE_PATH", str(pointer_file))
    monkeypatch.setattr(bot, "ACTIVE_MODEL", "codex-fallback")

    text = bot._build_codex_session_status_view()

    assert "模型：codex-fallback" in text
    assert "Context Window：243,200 tokens" in text
    assert "限额：5h 42%（20:43 重置） · 周 64%（06-08 07:41 重置）" in text
    assert "5h 10%" not in text


def test_build_codex_session_status_view_reports_missing_token_count(monkeypatch, tmp_path):
    """缺少 token_count 时不报错，展示等待模型事件的可见提示。"""

    session_file = tmp_path / "rollout-no-token-count.jsonl"
    pointer_file = tmp_path / "codex-session.pointer"
    pointer_file.write_text(str(session_file), encoding="utf-8")
    _write_jsonl(
        session_file,
        {
            "type": "event_msg",
            "payload": {
                "type": "task_started",
                "model_context_window": 128000,
            },
        },
        {
            "type": "turn_context",
            "payload": {
                "collaboration_mode": {
                    "settings": {
                        "model": "gpt-5.5",
                    }
                },
            },
        },
    )

    monkeypatch.setattr(bot, "CODEX_SESSION_FILE_PATH", str(pointer_file))

    text = bot._build_codex_session_status_view()

    assert "模型：gpt-5.5" in text
    assert "Context Window：128,000 tokens" in text
    assert "暂无限额数据，等待下一次模型事件" in text


def test_build_codex_session_status_view_reports_unbound_session(monkeypatch, tmp_path):
    """pointer 缺失或指向不存在文件时，应 fail-soft 显示未绑定当前会话。"""

    pointer_file = tmp_path / "missing.pointer"
    monkeypatch.setattr(bot, "CODEX_SESSION_FILE_PATH", str(pointer_file))

    text = bot._build_codex_session_status_view()

    assert "未绑定当前会话" in text
