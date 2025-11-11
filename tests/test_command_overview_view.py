import os

import pytest

os.environ.setdefault("BOT_TOKEN", "TEST_TOKEN")

import bot
from command_center.models import CommandDefinition, CommandHistoryRecord


class _StubCommandService:
    """简单桩对象，用于替代真实的 CommandService。"""

    def __init__(self, commands, history=None):
        self._commands = commands
        self._history = history or []

    async def list_commands(self):
        return self._commands

    async def list_history(self, limit=bot.COMMAND_HISTORY_LIMIT):
        return self._history[:limit]


def _patch_command_services(
    monkeypatch,
    *,
    project_commands=None,
    global_commands=None,
    project_history=None,
    global_history=None,
):
    """统一打桩项目命令与通用命令的服务。"""

    monkeypatch.setattr(
        bot,
        "COMMAND_SERVICE",
        _StubCommandService(project_commands or [], project_history or []),
    )
    monkeypatch.setattr(
        bot,
        "GLOBAL_COMMAND_SERVICE",
        _StubCommandService(global_commands or [], global_history or []),
    )


@pytest.mark.asyncio
async def test_global_commands_listed_before_project(monkeypatch):
    global_commands = [
        CommandDefinition(
            id=30,
            project_slug="__global__",
            scope="global",
            name="alpha",
            title="Alpha",
            command="echo alpha",
            description="",
            aliases=(),
        ),
        CommandDefinition(
            id=31,
            project_slug="__global__",
            scope="global",
            name="zulu",
            title="Zulu",
            command="echo zulu",
            description="",
            aliases=(),
        ),
    ]
    project_commands = [
        CommandDefinition(
            id=1,
            project_slug="demo",
            scope="project",
            name="beta",
            title="Beta",
            command="./beta.sh",
            description="",
            aliases=(),
        )
    ]
    _patch_command_services(
        monkeypatch,
        project_commands=project_commands,
        global_commands=global_commands,
    )
    combined = await bot._list_combined_commands()
    assert [cmd.name for cmd in combined] == ["alpha", "zulu", "beta"]


@pytest.mark.asyncio
async def test_build_command_overview_view_hides_detailed_list(monkeypatch):
    project_commands = [
        CommandDefinition(
            id=1,
            project_slug="demo",
            scope="project",
            name="deploy_api",
            title="部署 API",
            command="./deploy.sh api",
            description="",
            aliases=("deploy",),
        ),
        CommandDefinition(
            id=2,
            project_slug="demo",
            scope="project",
            name="cleanup",
            title="清理",
            command="./cleanup.sh",
            description="",
            aliases=(),
        ),
    ]
    global_commands = [
        CommandDefinition(
            id=30,
            project_slug="__global__",
            scope="global",
            name="git_status",
            title="查看 git 状态",
            command="git status",
            description="",
            aliases=("gstatus",),
        )
    ]
    _patch_command_services(
        monkeypatch,
        project_commands=project_commands,
        global_commands=global_commands,
    )
    text, markup = await bot._build_command_overview_view()
    assert "命令数量：3（项目 2 / 通用 1）" in text
    assert "deploy_api" not in text
    assert "cleanup" not in text
    button_labels = [btn.text for row in markup.inline_keyboard for btn in row]
    assert any(label.endswith("deploy_api") for label in button_labels)
    assert any(label.endswith("cleanup") for label in button_labels)
    assert any("仅 master 可改" in label for label in button_labels)


@pytest.mark.asyncio
async def test_build_command_overview_view_when_empty(monkeypatch):
    _patch_command_services(monkeypatch, project_commands=[], global_commands=[])
    text, markup = await bot._build_command_overview_view()
    assert "命令数量：0（项目 0 / 通用 0）" in text
    assert "暂无命令" in text
    # 仅保留基础按钮，inline keyboard 至少包含新增命令入口
    button_texts = [btn.text for row in markup.inline_keyboard for btn in row]
    assert "🆕 新增命令" in button_texts


@pytest.mark.asyncio
async def test_build_command_history_view_with_records(monkeypatch):
    project_records = [
        CommandHistoryRecord(
            id=1,
            command_id=10,
            project_slug="demo",
            command_name="deploy_api",
            command_title="部署 API",
            trigger="/deploy",
            actor_id=1,
            actor_username="tester",
            actor_name="Tester",
            exit_code=0,
            status="success",
            output="ok",
            error="",
            started_at="2025-11-11T16:00:00+08:00",
            finished_at="2025-11-11T16:01:00+08:00",
        )
    ]
    _patch_command_services(
        monkeypatch,
        project_commands=[],
        project_history=project_records,
        global_commands=[],
        global_history=[],
    )
    text, markup = await bot._build_command_history_view()
    assert "部署 API" in text
    assert "触发" not in text
    assert "Tester" not in text
    assert markup is not None
    button_texts = [btn.text for row in markup.inline_keyboard for btn in row]
    assert any("部署 API" in label for label in button_texts)
    detail_button = markup.inline_keyboard[0][0]
    assert detail_button.callback_data.startswith(bot.COMMAND_HISTORY_DETAIL_PREFIX)


@pytest.mark.asyncio
async def test_build_command_history_view_merges_global_records(monkeypatch):
    global_records = [
        CommandHistoryRecord(
            id=99,
            command_id=500,
            project_slug="demo",
            command_name="git_status",
            command_title="查看 git 状态",
            trigger="按钮",
            actor_id=2,
            actor_username="bot",
            actor_name="Bot",
            exit_code=0,
            status="success",
            output="clean",
            error="",
            started_at="2025-11-11T17:00:00+08:00",
            finished_at="2025-11-11T17:01:00+08:00",
        )
    ]
    _patch_command_services(
        monkeypatch,
        project_commands=[],
        global_commands=[],
        project_history=[],
        global_history=global_records,
    )
    text, markup = await bot._build_command_history_view()
    assert "（通用）" in text
    assert markup is not None
    button = markup.inline_keyboard[0][0]
    assert button.callback_data.startswith(bot.COMMAND_HISTORY_DETAIL_GLOBAL_PREFIX)


@pytest.mark.asyncio
async def test_build_command_history_view_when_empty(monkeypatch):
    _patch_command_services(monkeypatch, project_commands=[], global_commands=[], project_history=[], global_history=[])
    text, markup = await bot._build_command_history_view()
    assert "暂无历史记录" in text
    assert markup is None
