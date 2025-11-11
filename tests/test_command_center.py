import os
os.environ.setdefault("BOT_TOKEN", "dummy-token")

import asyncio
from pathlib import Path

import bot
from command_center.service import CommandPresetService


def _run(coro):
    return asyncio.run(coro)


async def _make_service(tmp_path: Path, slug: str = "demo") -> CommandPresetService:
    svc = CommandPresetService(tmp_path / f"{slug}.db", slug)
    await svc.initialize()
    return svc


def test_command_service_create_and_retrieve(tmp_path):
    async def scenario():
        svc = await _make_service(tmp_path)
        created = await svc.create_preset(
            title="Git Push",
            command="git push",
            workdir="/repo",
            require_confirmation=True,
        )
        fetched = await svc.get_preset(created.id)
        assert fetched is not None
        assert fetched.title == "Git Push"
        assert fetched.require_confirmation is True
    _run(scenario())


def test_command_service_list_pagination_order(tmp_path):
    async def scenario():
        svc = await _make_service(tmp_path)
        for idx in range(6):
            await svc.create_preset(
                title=f"Cmd {idx}",
                command=f"echo {idx}",
                workdir=None,
                require_confirmation=bool(idx % 2),
            )
        first_page = await svc.list_presets(page=1, page_size=5)
        second_page = await svc.list_presets(page=2, page_size=5)
        assert len(first_page) == 5
        assert len(second_page) == 1
        assert first_page[0].title == "Cmd 5"
        assert second_page[0].title == "Cmd 0"
    _run(scenario())


def test_command_service_toggle_confirmation(tmp_path):
    async def scenario():
        svc = await _make_service(tmp_path)
        preset = await svc.create_preset(
            title="Toggle",
            command="ls",
            workdir=None,
            require_confirmation=True,
        )
        updated = await svc.toggle_confirmation(preset.id)
        assert updated is not None and updated.require_confirmation is False
        reverted = await svc.toggle_confirmation(preset.id)
        assert reverted is not None and reverted.require_confirmation is True
    _run(scenario())


def test_command_service_delete_removes_record(tmp_path):
    async def scenario():
        svc = await _make_service(tmp_path)
        preset = await svc.create_preset(
            title="Cleanup",
            command="rm -rf build",
            workdir=None,
            require_confirmation=True,
        )
        deleted = await svc.delete_preset(preset.id)
        assert deleted is True
        missing = await svc.get_preset(preset.id)
        assert missing is None
    _run(scenario())


def test_command_service_update_missing_returns_none(tmp_path):
    async def scenario():
        svc = await _make_service(tmp_path)
        result = await svc.update_preset(
            999,
            title="Missing",
            command="echo oops",
            workdir=None,
            require_confirmation=False,
        )
        assert result is None
    _run(scenario())


def test_command_service_count_reflects_entries(tmp_path):
    async def scenario():
        svc = await _make_service(tmp_path)
        assert await svc.count_presets() == 0
        await svc.create_preset(title="A", command="echo a", workdir=None, require_confirmation=True)
        await svc.create_preset(title="B", command="echo b", workdir=None, require_confirmation=False)
        assert await svc.count_presets() == 2
    _run(scenario())


def test_build_command_list_view_handles_empty_state(monkeypatch, tmp_path):
    async def scenario():
        svc = await _make_service(tmp_path)
        monkeypatch.setattr(bot, "COMMAND_PRESET_SERVICE", svc)
        monkeypatch.setattr(bot, "GLOBAL_COMMAND_PRESET_SERVICE", svc)
        text, markup = await bot._build_command_list_view(page=1)
        assert "尚未配置命令" in text
        assert markup.inline_keyboard[-1][0].callback_data.startswith("cmd:create")
    _run(scenario())


def test_build_command_list_view_includes_commands(monkeypatch, tmp_path):
    async def scenario():
        project_svc = await _make_service(tmp_path / Path("project_db"), slug="project")
        global_svc = await _make_service(tmp_path / Path("global_db"), slug="__global__")
        await project_svc.create_preset(title="Deploy", command="make deploy", workdir=None, require_confirmation=True)
        monkeypatch.setattr(bot, "COMMAND_PRESET_SERVICE", project_svc)
        monkeypatch.setattr(bot, "GLOBAL_COMMAND_PRESET_SERVICE", global_svc)
        text, markup = await bot._build_command_list_view(page=1)
        assert "Deploy" in text
        buttons = [button.text for row in markup.inline_keyboard for button in row]
        assert any("Deploy" in label for label in buttons)
    _run(scenario())


def test_build_command_detail_view_displays_flags(tmp_path):
    async def scenario():
        svc = await _make_service(tmp_path)
        preset = await svc.create_preset(
            title="Sync",
            command="./sync-all.sh",
            workdir="/repo",
            require_confirmation=False,
        )
        text, markup = bot._build_command_detail_view(
            preset,
            origin_page=1,
            scope=bot.COMMAND_SCOPE_PROJECT,
        )
        assert "Sync" in text
        assert "执行前确认" in text
        assert markup.inline_keyboard[0][0].callback_data.startswith("cmd:run")
    _run(scenario())


def test_worker_command_list_includes_global_presets(monkeypatch, tmp_path):
    async def scenario():
        project_svc = await _make_service(tmp_path / Path("project"), slug="project")
        global_svc = await _make_service(tmp_path / Path("global"), slug="__global__")
        await global_svc.create_preset(title="Master Cmd", command="echo master", workdir=None, require_confirmation=True)
        await project_svc.create_preset(title="Local Cmd", command="echo local", workdir=None, require_confirmation=False)
        monkeypatch.setattr(bot, "COMMAND_PRESET_SERVICE", project_svc)
        monkeypatch.setattr(bot, "GLOBAL_COMMAND_PRESET_SERVICE", global_svc)
        text, markup = await bot._build_command_list_view(page=1)
        assert "Master Cmd" in text and "Local Cmd" in text
        detail_button = markup.inline_keyboard[0][0]
        assert detail_button.callback_data.startswith("cmd:detail:g:")
    _run(scenario())


def test_combine_command_prompt_with_workdir_and_multiline():
    command = "git status\nnpm test"
    prompt = bot._combine_command_prompt(command_text=command, workdir="/tmp/repo path")
    lines = prompt.splitlines()
    assert lines[0].startswith("cd ")
    assert "repo path" in lines[0]
    assert "git status" in lines[1]
