import os
import pytest
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["BOT_TOKEN"] = "test-token"
os.environ["MODE"] = "B"
os.environ["ACTIVE_MODEL"] = "claudecode"

import bot


@pytest.fixture(autouse=True)
def _force_claudecode(monkeypatch):
    """Make sure your tests always go to the ClaudeCode branch."""

    monkeypatch.setattr(bot, "ACTIVE_MODEL", "claudecode")
    monkeypatch.setattr(bot, "MODEL_CANONICAL_NAME", "claudecode")
    return


def test_extract_claudecode_assistant_message():
    event = {
        "isSidechain": False,
        "type": "assistant",
        "message": {
            "id": "msg_test",
            "content": [
                {"type": "thinking", "thinking": "hidden thinking"},
                {"type": "text", "text": "First paragraph of output"},
                {"type": "tool_use", "name": "Bash", "input": {"command": "pwd"}},
                {"type": "text", "text": "Second section output"},
                {
                    "type": "tool_result",
                    "output": "command result",
                    "content": "command result",
                },
            ],
        },
    }
    result = bot._extract_deliverable_payload(event, event_timestamp=None)
    assert result is not None
    kind, text, metadata = result
    assert kind == bot.DELIVERABLE_KIND_MESSAGE
    assert "First paragraph of output" in text and "Second section output" in text
    assert metadata == {"message_id": "msg_test"}


def test_extract_claudecode_assistant_tool_result():
    event = {
        "isSidechain": False,
        "type": "assistant",
        "message": {
            "id": "msg_tool",
            "content": [
                {
                    "type": "tool_result",
                    "output": "/Users/david/project",
                    "content": "/Users/david/project",
                    "is_error": False,
                }
            ],
        },
    }
    result = bot._extract_deliverable_payload(event, event_timestamp=None)
    assert result is None


def test_extract_claudecode_sidechain_message_ignored():
    event = {
        "isSidechain": True,
        "type": "assistant",
        "message": {
            "id": "msg_side",
            "content": [
                {"type": "text", "text": "Welcome"},
            ],
        },
    }
    result = bot._extract_deliverable_payload(event, event_timestamp=None)
    assert result is None


def test_extract_claudecode_fallback_text():
    event = {
        "isSidechain": False,
        "type": "assistant",
        "message": {
            "id": "msg_fallback",
            "text": "direct text",
        },
    }
    result = bot._extract_deliverable_payload(event, event_timestamp=None)
    assert result is not None
    kind, text, metadata = result
    assert kind == bot.DELIVERABLE_KIND_MESSAGE
    assert text == "direct text"
    assert metadata == {"message_id": "msg_fallback"}


def test_claudecode_fallback_selects_latest(tmp_path):
    pointer = tmp_path / "current_session.txt"
    old_file = tmp_path / "rollout-old.jsonl"
    new_dir = tmp_path / "sessions"
    new_dir.mkdir()
    new_file = new_dir / "rollout-20250101.jsonl"

    old_file.write_text("{}", encoding="utf-8")
    pointer.write_text(str(old_file), encoding="utf-8")
    new_file.write_text("{}", encoding="utf-8")
    os.utime(old_file, (time.time() - 60, time.time() - 60))
    os.utime(new_file, (time.time(), time.time()))

    previous_root = bot.MODEL_SESSION_ROOT
    try:
        bot.MODEL_SESSION_ROOT = str(tmp_path)
        result = bot._find_latest_claudecode_rollout(pointer)
    finally:
        bot.MODEL_SESSION_ROOT = previous_root

    assert result == new_file
