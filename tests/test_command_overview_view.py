import os

import pytest

os.environ.setdefault("BOT_TOKEN", "TEST_TOKEN")

import bot
from command_center.models import CommandDefinition


class _StubCommandService:
    """简单桩对象，用于替代真实的 CommandService。"""

    def __init__(self, commands):
        self._commands = commands

    async def list_commands(self):
        return self._commands


@pytest.mark.asyncio
async def test_build_command_overview_view_hides_detailed_list(monkeypatch):
    commands = [
        CommandDefinition(
            id=1,
            project_slug="demo",
            name="deploy_api",
            title="部署 API",
            command="./deploy.sh api",
            description="",
            aliases=("deploy",),
        ),
        CommandDefinition(
            id=2,
            project_slug="demo",
            name="cleanup",
            title="清理",
            command="./cleanup.sh",
            description="",
            aliases=(),
        ),
    ]
    monkeypatch.setattr(bot, "COMMAND_SERVICE", _StubCommandService(commands))
    text, markup = await bot._build_command_overview_view()
    assert "命令数量：2" in text
    assert "deploy_api" not in text
    assert "cleanup" not in text
    button_labels = [btn.text for row in markup.inline_keyboard for btn in row]
    assert any(label.endswith("deploy_api") for label in button_labels)
    assert any(label.endswith("cleanup") for label in button_labels)


@pytest.mark.asyncio
async def test_build_command_overview_view_when_empty(monkeypatch):
    monkeypatch.setattr(bot, "COMMAND_SERVICE", _StubCommandService([]))
    text, markup = await bot._build_command_overview_view()
    assert "命令数量：0" in text
    assert "暂无命令" in text
    # 仅保留基础按钮，inline keyboard 至少包含新增命令入口
    button_texts = [btn.text for row in markup.inline_keyboard for btn in row]
    assert "🆕 新增命令" in button_texts
