import pytest
import pytest_asyncio

from tasks import TaskService


@pytest_asyncio.fixture
async def task_service(tmp_path):
    service = TaskService(tmp_path / "tasks.db", "demo")
    await service.initialize()
    return service


@pytest.mark.asyncio
async def test_add_and_list_attachments(task_service: TaskService):
    task = await task_service.create_root_task(
        title="附件测试",
        status="todo",
        priority=3,
        task_type="需求",
        tags=(),
        due_date=None,
        description="", 
        actor="tester",
    )

    first = await task_service.add_attachment(
        task.id,
        display_name="log.txt",
        mime_type="text/plain",
        path="rel/log.txt",
        kind="document",
    )
    second = await task_service.add_attachment(
        task.id.lower(),
        display_name="img.png",
        mime_type="image/png",
        path="rel/img.png",
        kind="photo",
    )

    items = await task_service.list_attachments(task.id)
    assert len(items) == 2
    # 倒序返回，最新记录在前
    assert items[0].path == second.path
    assert items[1].path == first.path
    assert items[0].kind == "photo"


@pytest.mark.asyncio
async def test_add_attachment_requires_path(task_service: TaskService):
    task = await task_service.create_root_task(
        title="附件校验",
        status="todo",
        priority=3,
        task_type="需求",
        tags=(),
        due_date=None,
        description="",
        actor="tester",
    )
    with pytest.raises(ValueError):
        await task_service.add_attachment(
            task.id,
            display_name="no-path",
            mime_type="text/plain",
            path="",
        )
