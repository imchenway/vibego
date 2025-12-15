import asyncio
import json
from datetime import datetime

import bot
from tasks.models import TaskHistoryRecord, TaskRecord


def _make_task() -> TaskRecord:
    """构造简易任务对象。"""

    return TaskRecord(
        id="TASK_0001",
        project_slug="demo",
        title="测试任务",
        status="research",
        priority=3,
        task_type="task",
        tags=(),
        due_date=None,
        description="",
        parent_id=None,
        root_id="TASK_0001",
        depth=0,
        lineage="0001",
        archived=False,
    )


def _make_history(event_type: str, field: str, *, new_value: str = "-", payload: dict | None = None) -> TaskHistoryRecord:
    """构造历史记录，便于测试过滤逻辑。"""

    payload_text = json.dumps(payload, ensure_ascii=False) if payload is not None else None
    return TaskHistoryRecord(
        id=1,
        task_id="TASK_0001",
        field=field,
        old_value=None,
        new_value=new_value,
        actor="tester",
        event_type=event_type,
        payload=payload_text,
        created_at=datetime.now(bot.UTC).isoformat(),
    )


def test_history_context_filters_attachment_events(monkeypatch):
    attach_event = _make_history("attachment_added", "attachment", payload={"files": ["./data/a.txt"]})
    kept_event = _make_history("task_action", "description", new_value="保持的事件")

    async def fake_list_history(task_id: str):
        return [attach_event, kept_event]

    monkeypatch.setattr(bot.TASK_SERVICE, "list_history", fake_list_history)

    history_text, count = asyncio.run(bot._build_history_context_for_model("TASK_0001"))

    assert count == 1
    assert "attachment" not in history_text
    assert "保持的事件" in history_text


def test_render_history_skips_attachment_events(monkeypatch):
    attach_event = _make_history("attachment_added", "attachment", payload={"files": ["./data/a.txt"]})
    kept_event = _make_history("task_action", "description", new_value="历史正文")
    task = _make_task()

    async def fake_get_task(task_id: str):
        return task

    async def fake_list_notes(task_id: str):
        return []

    async def fake_list_history(task_id: str):
        return [attach_event, kept_event]

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)
    monkeypatch.setattr(bot.TASK_SERVICE, "list_notes", fake_list_notes)
    monkeypatch.setattr(bot.TASK_SERVICE, "list_history", fake_list_history)

    text, _, _, _ = asyncio.run(bot._render_task_history("TASK_0001", page=0))

    assert "attachment" not in text
    assert "历史正文" in text
