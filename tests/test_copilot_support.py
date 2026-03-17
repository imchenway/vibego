from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_TOKEN", "test-token")

import bot  # noqa: E402
from scripts import session_binder  # noqa: E402


def _build_copilot_assistant_message(
    *,
    content: str = "",
    phase: str | None = None,
    tool_requests: list[dict] | None = None,
) -> dict:
    data: dict = {
        "content": content,
        "toolRequests": tool_requests or [],
    }
    if phase is not None:
        data["phase"] = phase
    return {
        "type": "assistant.message",
        "timestamp": "2026-03-17T10:00:00.000Z",
        "data": data,
    }


@pytest.fixture(autouse=True)
def _reset_runtime(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(bot, "ACTIVE_MODEL", "copilot")
    monkeypatch.setattr(bot, "MODEL_CANONICAL_NAME", "copilot")
    monkeypatch.setattr(bot, "MODEL_DISPLAY_LABEL", "Copilot")
    bot.SESSION_OFFSETS.clear()
    yield
    bot.SESSION_OFFSETS.clear()


def test_extract_copilot_final_answer_message() -> None:
    event = _build_copilot_assistant_message(content="Copilot 已完成。", phase="final_answer")

    result = bot._extract_deliverable_payload(event, event_timestamp=event["timestamp"])

    assert result == (bot.DELIVERABLE_KIND_MESSAGE, "Copilot 已完成。", None)


def test_extract_copilot_commentary_message_is_ignored() -> None:
    event = _build_copilot_assistant_message(content="正在搜索文件……", phase="commentary")

    result = bot._extract_deliverable_payload(event, event_timestamp=event["timestamp"])

    assert result is None


def test_extract_copilot_update_plan_from_tool_requests() -> None:
    event = _build_copilot_assistant_message(
        phase="commentary",
        tool_requests=[
            {
                "name": "update_plan",
                "toolCallId": "tool_plan_1",
                "type": "tool_request",
                "arguments": {
                    "explanation": "正在同步进度",
                    "plan": [
                        {"step": "补 Copilot 解析", "status": "completed"},
                        {"step": "跑回归测试", "status": "in_progress"},
                    ],
                },
            }
        ],
    )

    result = bot._extract_deliverable_payload(event, event_timestamp=event["timestamp"])

    assert result is not None
    kind, text, metadata = result
    assert kind == bot.DELIVERABLE_KIND_PLAN
    assert "当前任务执行计划：" in text
    assert "正在同步进度" in text
    assert "补 Copilot 解析" in text
    assert metadata == {"plan_completed": False, "call_id": "tool_plan_1"}


def test_extract_copilot_ask_user_as_request_input() -> None:
    event = _build_copilot_assistant_message(
        phase="commentary",
        tool_requests=[
            {
                "name": "ask_user",
                "toolCallId": "tool_ask_1",
                "type": "tool_request",
                "arguments": {
                    "message": "请选择本轮修改范围",
                    "requestedSchema": {
                        "properties": {
                            "scope": {
                                "type": "string",
                                "title": "修改范围",
                                "description": "请选择本轮修改范围",
                                "enum": ["仅库存页", "两页都改"],
                            }
                        },
                        "required": ["scope"],
                    },
                },
            }
        ],
    )

    result = bot._extract_deliverable_payload(event, event_timestamp=event["timestamp"])

    assert result is not None
    kind, text, metadata = result
    assert kind == bot.DELIVERABLE_KIND_REQUEST_INPUT
    assert "模型请求你补充决策" in text
    assert metadata is not None
    assert metadata["tool_name"] == "ask_user"
    assert metadata["call_id"] == "tool_ask_1"
    assert metadata["questions"][0]["id"] == "scope"
    assert metadata["questions"][0]["options"][0]["label"] == "仅库存页"
    assert metadata["questions"][0]["options"][1]["label"] == "两页都改"


def test_read_session_meta_cwd_supports_copilot_session_start(tmp_path: Path) -> None:
    session_file = tmp_path / "events.jsonl"
    session_file.write_text(
        json.dumps(
            {
                "type": "session.start",
                "data": {"context": {"cwd": str(tmp_path / "workspace")}},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    assert bot._read_session_meta_cwd(session_file) == str(tmp_path / "workspace")


def test_session_binder_selects_latest_copilot_session_by_context_cwd(tmp_path: Path) -> None:
    workdir = tmp_path / "repo"
    workdir.mkdir()
    other_workdir = tmp_path / "other"
    other_workdir.mkdir()

    root = tmp_path / "copilot"
    good_dir = root / "session-a"
    newer_dir = root / "session-b"
    bad_dir = root / "session-c"
    good_dir.mkdir(parents=True)
    newer_dir.mkdir(parents=True)
    bad_dir.mkdir(parents=True)

    older = good_dir / "events.jsonl"
    newer = newer_dir / "events.jsonl"
    bad = bad_dir / "events.jsonl"

    for path, cwd in (
        (older, workdir),
        (newer, workdir),
        (bad, other_workdir),
    ):
        path.write_text(
            json.dumps(
                {
                    "type": "session.start",
                    "data": {"context": {"cwd": str(cwd)}},
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

    os.utime(older, (1, 1))
    os.utime(newer, (3, 3))
    os.utime(bad, (5, 5))

    selected = session_binder._select_latest_session(  # noqa: SLF001
        roots=[root],
        pattern="events.jsonl",
        target_cwd=str(workdir),
        boot_ts_ms=0.0,
    )

    assert selected == newer
