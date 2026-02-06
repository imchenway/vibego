import os
import sys
from pathlib import Path
from typing import Optional

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["BOT_TOKEN"] = "test-token"
os.environ["MODE"] = "B"
os.environ["ACTIVE_MODEL"] = "codex"

import bot


@pytest.fixture(autouse=True)
def _force_codex(monkeypatch):
    """确保测试始终走 Codex 分支。"""

    monkeypatch.setattr(bot, "ACTIVE_MODEL", "codex")
    monkeypatch.setattr(bot, "MODEL_CANONICAL_NAME", "codex")
    return


def _build_codex_message_event(text: str, *, phase: Optional[str]) -> dict:
    payload = {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text}],
    }
    if phase is not None:
        payload["phase"] = phase
    return {
        "type": "response_item",
        "payload": payload,
    }


def _build_codex_event_msg_agent_message_event(text: str, *, phase: Optional[str]) -> dict:
    payload = {
        "type": "agent_message",
        "message": text,
    }
    if phase is not None:
        payload["phase"] = phase
    return {
        "type": "event_msg",
        "payload": payload,
    }


def test_extract_codex_commentary_phase_ignored():
    event = _build_codex_message_event("中间进度", phase="commentary")
    assert bot._extract_deliverable_payload(event, event_timestamp=None) is None


def test_extract_codex_final_answer_phase_delivered():
    event = _build_codex_message_event("最终答案", phase="final_answer")
    result = bot._extract_deliverable_payload(event, event_timestamp=None)
    assert result is not None
    kind, text, metadata = result
    assert kind == bot.DELIVERABLE_KIND_MESSAGE
    assert text == "最终答案"
    assert metadata is None


def test_extract_codex_legacy_message_without_phase_kept():
    event = _build_codex_message_event("旧格式输出", phase=None)
    result = bot._extract_deliverable_payload(event, event_timestamp=None)
    assert result is not None
    kind, text, metadata = result
    assert kind == bot.DELIVERABLE_KIND_MESSAGE
    assert text == "旧格式输出"
    assert metadata is None


def test_extract_codex_unknown_phase_ignored():
    event = _build_codex_message_event("未知阶段输出", phase="analysis")
    assert bot._extract_deliverable_payload(event, event_timestamp=None) is None


def test_extract_codex_event_msg_without_phase_ignored():
    event = _build_codex_event_msg_agent_message_event("中间进度", phase=None)
    assert bot._extract_deliverable_payload(event, event_timestamp=None) is None


def test_extract_codex_event_msg_commentary_phase_ignored():
    event = _build_codex_event_msg_agent_message_event("中间进度", phase="commentary")
    assert bot._extract_deliverable_payload(event, event_timestamp=None) is None


def test_extract_codex_event_msg_final_answer_phase_delivered():
    event = _build_codex_event_msg_agent_message_event("最终答案", phase="final_answer")
    result = bot._extract_deliverable_payload(event, event_timestamp=None)
    assert result is not None
    kind, text, metadata = result
    assert kind == bot.DELIVERABLE_KIND_MESSAGE
    assert text == "最终答案"
    assert metadata is None
