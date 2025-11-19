import pytest
import pytest_asyncio

from command_center import (
    CommandService,
    CommandAlreadyExistsError,
    CommandAliasConflictError,
    CommandHistoryNotFoundError,
    CommandNotFoundError,
)


@pytest_asyncio.fixture
async def command_service(tmp_path):
    service = CommandService(tmp_path / "commands.db", "demo")
    await service.initialize()
    return service


@pytest.mark.asyncio
async def test_create_command_defaults_title_when_missing(command_service):
    created = await command_service.create_command(
        name="quick_sync",
        title="",
        command="echo sync",
    )
    assert created.title == "quick_sync"


@pytest.mark.asyncio
async def test_create_command_lists_aliases(command_service):
    await command_service.create_command(
        name="deploy_api",
        title="部署 API",
        command="./deploy.sh api",
        description="发布最新镜像",
        aliases=["deploy", "dp"],
    )
    commands = await command_service.list_commands()
    assert len(commands) == 1
    definition = commands[0]
    assert definition.title == "部署 API"
    assert definition.aliases == ("deploy", "dp")
    assert definition.scope == "project"


@pytest.mark.asyncio
async def test_duplicate_name_raises(command_service):
    await command_service.create_command(
        name="sync_logs",
        title="同步日志",
        command="tail -n 20 logs/app.log",
    )
    with pytest.raises(CommandAlreadyExistsError):
        await command_service.create_command(
            name="SYNC_LOGS",
            title="同步日志 2",
            command="tail -n 50 logs/app.log",
        )


@pytest.mark.asyncio
async def test_alias_conflict_detected(command_service):
    await command_service.create_command(
        name="reboot_worker",
        title="重启 worker",
        command="systemctl restart worker",
        aliases=["reboot"],
    )
    with pytest.raises(CommandAliasConflictError):
        await command_service.create_command(
            name="reboot_master",
            title="重启 master",
            command="systemctl restart master",
            aliases=["reboot"],
        )


@pytest.mark.asyncio
async def test_replace_aliases_overwrites_previous(command_service):
    created = await command_service.create_command(
        name="collect_metrics",
        title="采集指标",
        command="python metrics.py",
        aliases=["metrics", "mt"],
    )
    new_aliases = await command_service.replace_aliases(created.id, ["stats", "collect"])
    assert new_aliases == ("stats", "collect")
    refreshed = await command_service.get_command(created.id)
    assert set(refreshed.aliases) == {"stats", "collect"}


@pytest.mark.asyncio
async def test_resolve_by_trigger_matches_alias(command_service):
    created = await command_service.create_command(
        name="cleanup",
        title="清理目录",
        command="rm -rf /tmp/demo",
        aliases=["clean"],
    )
    resolved = await command_service.resolve_by_trigger("CLEAN")
    assert resolved
    assert resolved.id == created.id


@pytest.mark.asyncio
async def test_update_command_fields(command_service):
    created = await command_service.create_command(
        name="migrate_db",
        title="执行迁移",
        command="alembic upgrade head",
    )
    updated = await command_service.update_command(
        created.id,
        description="应用最新 schema",
        enabled=False,
        timeout=9999,
    )
    assert updated.description == "应用最新 schema"
    assert updated.enabled is False
    assert updated.timeout == CommandService.MAX_TIMEOUT


@pytest.mark.asyncio
async def test_record_history_and_list(command_service):
    created = await command_service.create_command(
        name="show_version",
        title="查看版本",
        command="cat VERSION",
    )
    recorded = await command_service.record_history(
        created.id,
        trigger="/show_version",
        actor_id=100,
        actor_username="tester",
        actor_name="Tester",
        exit_code=0,
        status="success",
        output="1.0.0",
        error=None,
    )
    history = await command_service.list_history()
    assert history
    latest = history[0]
    assert latest.command_id == created.id
    assert latest.output == "1.0.0"
    assert latest.command_title == "查看版本"
    assert recorded.command_title == "查看版本"


@pytest.mark.asyncio
async def test_get_history_record_returns_detail_and_missing_raises(command_service):
    created = await command_service.create_command(
        name="publish",
        title="发布版本",
        command="echo ok",
    )
    recorded = await command_service.record_history(
        created.id,
        trigger="按钮",
        actor_id=None,
        actor_username=None,
        actor_name=None,
        exit_code=0,
        status="success",
        output="done",
        error="",
    )
    fetched = await command_service.get_history_record(recorded.id)
    assert fetched.command_title == "发布版本"
    assert fetched.output == "done"
    assert fetched.command_id == created.id
    with pytest.raises(CommandHistoryNotFoundError):
        await command_service.get_history_record(recorded.id + 100)


@pytest.mark.asyncio
async def test_record_history_uses_custom_history_project_slug(tmp_path):
    service = CommandService(
        tmp_path / "global.db",
        "global",
        scope="global",
        history_project_slug="demo",
    )
    created = await service.create_command(
        name="shared_cmd",
        title="通用命令",
        command="echo shared",
    )
    record = await service.record_history(
        created.id,
        trigger="按钮",
        actor_id=None,
        actor_username=None,
        actor_name=None,
        exit_code=0,
        status="success",
        output="done",
        error="",
    )
    assert record.project_slug == "demo"
    history = await service.list_history()
    assert history[0].project_slug == "demo"


@pytest.mark.asyncio
async def test_delete_command_removes_definition_and_history(command_service):
    created = await command_service.create_command(
        name="remove_me",
        title="待删除命令",
        command="echo doomed",
        aliases=["rmme"],
    )
    await command_service.record_history(
        created.id,
        trigger="/remove_me",
        actor_id=1,
        actor_username="tester",
        actor_name="Tester",
        exit_code=0,
        status="success",
        output="ok",
        error="",
    )
    await command_service.delete_command(created.id)
    with pytest.raises(CommandNotFoundError):
        await command_service.get_command(created.id)
    remaining_history = await command_service.list_history()
    assert remaining_history == []
