import asyncio
from pathlib import Path

import bot
from tasks import TaskService


def test_related_task_select_view_sorts_by_updated_at_desc_and_paginates(monkeypatch, tmp_path: Path) -> None:
    """关联任务选择视图：按更新时间倒序，且每页固定 5 条并支持翻页。"""

    service = TaskService(tmp_path / "tasks.db", "demo")
    asyncio.run(service.initialize())
    monkeypatch.setattr(bot, "TASK_SERVICE", service)

    import tasks.service as task_service_module

    times = iter(
        [
            "2025-01-01T00:00:00+08:00",
            "2025-01-02T00:00:00+08:00",
            "2025-01-03T00:00:00+08:00",
            "2025-01-04T00:00:00+08:00",
            "2025-01-05T00:00:00+08:00",
            "2025-01-06T00:00:00+08:00",
        ]
    )
    monkeypatch.setattr(task_service_module, "shanghai_now_iso", lambda: next(times))

    created_ids: list[str] = []
    for idx in range(6):
        task = asyncio.run(
            service.create_root_task(
                title=f"任务{idx + 1}",
                status="research",
                priority=3,
                task_type="task",
                tags=(),
                due_date=None,
                description="",
                actor="tester",
            )
        )
        created_ids.append(task.id)

    text_1, markup_1 = asyncio.run(bot._build_related_task_select_view(page=1))
    assert "页码 1/2" in text_1
    assert "每页 5 条" in text_1

    selected_rows_1 = [
        row[0].callback_data.split(":")[2]
        for row in markup_1.inline_keyboard
        if row
        and row[0].callback_data
        and row[0].callback_data.startswith(f"{bot.TASK_RELATED_SELECT_PREFIX}:")
    ]
    assert selected_rows_1 == [
        "TASK_0006",
        "TASK_0005",
        "TASK_0004",
        "TASK_0003",
        "TASK_0002",
    ]

    nav_buttons_1 = [
        button.callback_data
        for row in markup_1.inline_keyboard
        for button in row
        if (button.callback_data or "").startswith(f"{bot.TASK_RELATED_PAGE_PREFIX}:")
    ]
    assert f"{bot.TASK_RELATED_PAGE_PREFIX}:2" in nav_buttons_1
    assert f"{bot.TASK_RELATED_PAGE_PREFIX}:1" not in nav_buttons_1

    # “跳过/取消”已迁移到菜单栏（ReplyKeyboard），不再出现在列表 InlineKeyboard 中
    callbacks_1 = [
        button.callback_data
        for row in markup_1.inline_keyboard
        for button in row
        if button.callback_data
    ]
    assert bot.TASK_RELATED_SKIP_CALLBACK not in callbacks_1
    assert bot.TASK_RELATED_CANCEL_CALLBACK not in callbacks_1

    text_2, markup_2 = asyncio.run(bot._build_related_task_select_view(page=2))
    assert "页码 2/2" in text_2
    selected_rows_2 = [
        row[0].callback_data.split(":")[2]
        for row in markup_2.inline_keyboard
        if row
        and row[0].callback_data
        and row[0].callback_data.startswith(f"{bot.TASK_RELATED_SELECT_PREFIX}:")
    ]
    assert selected_rows_2 == ["TASK_0001"]

    nav_buttons_2 = [
        button.callback_data
        for row in markup_2.inline_keyboard
        for button in row
        if (button.callback_data or "").startswith(f"{bot.TASK_RELATED_PAGE_PREFIX}:")
    ]
    assert f"{bot.TASK_RELATED_PAGE_PREFIX}:1" in nav_buttons_2
    assert f"{bot.TASK_RELATED_PAGE_PREFIX}:2" not in nav_buttons_2

    callbacks_2 = [
        button.callback_data
        for row in markup_2.inline_keyboard
        for button in row
        if button.callback_data
    ]
    assert bot.TASK_RELATED_SKIP_CALLBACK not in callbacks_2
    assert bot.TASK_RELATED_CANCEL_CALLBACK not in callbacks_2
