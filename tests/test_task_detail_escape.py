import os

# 测试前设置 token，避免 bot 模块因缺少配置直接退出
os.environ.setdefault("BOT_TOKEN", "dummy-token")

import bot  # noqa: E402
from tasks.models import TaskAttachmentRecord, TaskRecord  # noqa: E402


def test_task_detail_preescaped_brackets_clean(monkeypatch):
    """任务详情中的描述与附件应清理多余反斜杠，保持与标题一致。"""

    # 模拟 MarkdownV2 渲染路径，避免 legacy 转义产生额外反斜杠
    monkeypatch.setattr(bot, "_IS_MARKDOWN_V2", True)
    monkeypatch.setattr(bot, "_IS_MARKDOWN", False)

    task = TaskRecord(
        id="TASK_9999",
        project_slug="demo",
        title="示例标题",
        status="todo",
        description="含有\\)括号和\\(示例",
    )
    attachment = TaskAttachmentRecord(
        id=1,
        task_id=task.id,
        display_name="file\\(1\\).txt",
        mime_type="text/plain",
        path="/tmp/path\\(1\\).txt",
    )

    rendered = bot._format_task_detail(task, notes=(), attachments=(attachment,))

    # 描述不应再出现多余的反斜杠
    assert "\\)" not in rendered
    assert "\\(" not in rendered
    assert "含有)括号和(示例" in rendered
    # 附件行中的文件名与路径同样清理反斜杠
    assert "file(1).txt" in rendered
    assert "/tmp/path(1).txt" in rendered
