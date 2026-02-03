import pytest

import bot
from tasks.models import TaskRecord


@pytest.mark.parametrize(
    ("status", "archived", "expected_push"),
    [
        ("research", False, True),
        ("test", False, True),
        ("done", False, True),
        ("research", True, True),
        ("test", True, True),
        ("done", True, True),
        ("unknown", False, False),
        ("", False, False),
        ("RESEARCH", False, False),
        (" research ", False, False),
    ],
)
def test_task_detail_actions_replace_archive_with_attach(status: str, archived: bool, expected_push: bool) -> None:
    """任务详情按钮：移除归档按钮，添加附件按钮占位（覆盖多状态/边界输入）。"""

    task = TaskRecord(
        id=f"TASK_{status or 'EMPTY'}_{'A' if archived else 'N'}",
        project_slug="proj",
        title="测试任务",
        status=status,
        description="描述",
        archived=archived,
    )
    markup = bot._build_task_actions(task)

    buttons = [button for row in markup.inline_keyboard for button in row]

    # 不再展示归档按钮（旧回调仍可能出现在历史消息中，但不应在新渲染中出现）
    assert not any(
        (button.callback_data or "").startswith("task:toggle_archive:")
        for button in buttons
    )

    # “添加附件”只出现一次，且在“编辑字段”同一行的右侧
    edit_row = None
    for row in markup.inline_keyboard:
        if len(row) != 2:
            continue
        if row[0].callback_data == f"task:edit:{task.id}" and row[1].callback_data == f"task:attach:{task.id}":
            edit_row = row
            break
    assert edit_row is not None
    assert edit_row[0].text == "✏️ 编辑字段"
    assert edit_row[1].text == "📎 添加附件"

    attach_buttons = [button for button in buttons if button.callback_data == f"task:attach:{task.id}"]
    assert len(attach_buttons) == 1

    has_push = any(button.callback_data == f"task:push_model:{task.id}" for button in buttons)
    assert has_push is expected_push


def test_task_detail_actions_move_delete_to_history_position() -> None:
    """任务详情按钮：删除（归档）移动到原查看历史位置，且移除查看历史入口（TASK_0060）。"""

    task = TaskRecord(
        id="TASK_0060",
        project_slug="proj",
        title="测试任务",
        status="research",
        description="描述",
        archived=False,
    )
    markup = bot._build_task_actions(task)

    rows = markup.inline_keyboard
    buttons = [button for row in rows for button in row]

    # “查看历史”入口不再出现在任务详情页（历史能力仍可能通过其他入口触发）
    assert not any(button.callback_data == f"task:history:{task.id}" for button in buttons)

    # “删除（归档）”仅出现一次
    delete_callback = f"{bot.TASK_DETAIL_DELETE_PROMPT_CALLBACK}:{task.id}"
    delete_buttons = [button for button in buttons if button.callback_data == delete_callback]
    assert len(delete_buttons) == 1
    assert delete_buttons[0].text == "🗑️ 删除（归档）"

    # 删除按钮位于“报告缺陷”同行右侧（原“查看历史”位置）
    expected_row = None
    for row in rows:
        if len(row) != 2:
            continue
        if row[0].callback_data == f"task:bug_report:{task.id}" and row[1].callback_data == delete_callback:
            expected_row = row
            break
    assert expected_row is not None
    assert expected_row[0].text == "🚨 报告缺陷"
    assert expected_row[1].text == "🗑️ 删除（归档）"
