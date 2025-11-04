import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

import master
from project_repository import ProjectRecord, ProjectRepository


@pytest.fixture(autouse=True)
def reset_wizard_state():
    master.PROJECT_WIZARD_SESSIONS.clear()
    master.PROJECT_WIZARD_LOCK = asyncio.Lock()
    yield
    master.PROJECT_WIZARD_SESSIONS.clear()
    master.PROJECT_WIZARD_LOCK = asyncio.Lock()
    master.PROJECT_REPOSITORY = None
    master.MANAGER = None


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> ProjectRepository:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    json_path = config_dir / "projects.json"
    initial = [
        {
            "bot_name": "SampleBot",
            "bot_token": "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
            "project_slug": "sample",
            "default_model": "codex",
            "workdir": str(tmp_path),
            "allowed_chat_id": 100,
        }
    ]
    json_path.write_text(json.dumps(initial, ensure_ascii=False, indent=2), encoding="utf-8")
    db_path = config_dir / "master.db"
    repository = ProjectRepository(db_path, json_path)
    master.PROJECT_REPOSITORY = repository
    monkeypatch.setenv("MASTER_ADMIN_IDS", "1")
    return repository


def _build_manager(repo: ProjectRepository, tmp_path: Path) -> master.MasterManager:
    records = repo.list_projects()
    configs = [master.ProjectConfig.from_dict(record.to_dict()) for record in records]
    state_path = tmp_path / "state.json"
    state_store = master.StateStore(state_path, {cfg.project_slug: cfg for cfg in configs})
    return master.MasterManager(configs, state_store=state_store)


def _build_fsm_state(chat_id: int = 1, user_id: int = 1) -> tuple[MemoryStorage, FSMContext]:
    """Construct an FSM context to facilitate testing state processes."""
    storage = MemoryStorage()
    key = StorageKey(bot_id=0, chat_id=chat_id, user_id=user_id)
    return storage, FSMContext(storage=storage, key=key)


class DummyMessage:
    def __init__(self, chat_id: int = 1) -> None:
        self.text = ""
        self.chat = SimpleNamespace(id=chat_id)
        self.from_user = SimpleNamespace(id=chat_id, username="tester")
        self.message_id = 1
        self.bot = AsyncMock()
        self._answers = []
        self._edits = []

    async def answer(self, text: str, **kwargs):
        self._answers.append((text, kwargs))

    async def edit_text(self, text: str, **kwargs):
        self._edits.append((text, kwargs))

    async def edit_reply_markup(self, **kwargs):
        self._edits.append(("reply_markup", kwargs))


class DummyCallback:
    def __init__(self, data: str, chat_id: int = 1, message: DummyMessage | None = None) -> None:
        self.data = data
        self.from_user = SimpleNamespace(id=chat_id)
        self.message = message or DummyMessage(chat_id)
        self._answers = []

    async def answer(self, text: str | None = None, show_alert: bool = False):
        self._answers.append((text, show_alert))


def test_repository_initial_import_creates_backup(tmp_path: Path):
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    json_path = config_dir / "projects.json"
    data = [
        {
            "bot_name": "InitBot",
            "bot_token": "654321:ABCDEFGHIJKLMNOPQRSTUVWXYZ987654",
            "project_slug": "init",
            "default_model": "codex",
        }
    ]
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    repo = ProjectRepository(config_dir / "master.db", json_path)
    records = repo.list_projects()
    assert len(records) == 1
    backups = list(config_dir.glob("projects.json.*.bak"))
    assert backups, "Initialization should generate a JSON backup"
    exported = json.loads(json_path.read_text(encoding="utf-8"))
    assert exported[0]["project_slug"] == "init"


def test_repository_insert_updates_json(repo: ProjectRepository):
    new_record = ProjectRecord(
        bot_name="NewManageBot",
        bot_token="111111:ABCDEFGHIJKLMNOPQRSTUVWXYZ000000",
        project_slug="manage",
        default_model="codex",
        workdir=None,
        allowed_chat_id=None,
        legacy_name=None,
    )
    repo.insert_project(new_record)
    exported = json.loads(repo.json_path.read_text(encoding="utf-8"))
    slugs = {item["project_slug"] for item in exported}
    assert "manage" in slugs


def test_repository_insert_normalizes_fields(repo: ProjectRepository, tmp_path: Path):
    workdir_dir = tmp_path / "workspace"
    workdir_dir.mkdir()
    messy = ProjectRecord(
        bot_name=" @MixedBot ",
        bot_token="333333:ABCDEFGHIJKLMNOPQRSTUVWXYZ999999",
        project_slug=" Mixed Slug ",
        default_model=" CODEX ",
        workdir=str(workdir_dir) + "  ",
        allowed_chat_id=None,
        legacy_name=" Legacy Name ",
    )
    repo.insert_project(messy)
    stored = repo.get_by_slug("Mixed Slug")
    assert stored is not None
    assert stored.project_slug == "mixed-slug"
    assert stored.bot_name == "MixedBot"
    assert stored.default_model == "codex"
    assert stored.workdir == str(workdir_dir)
    exported = json.loads(repo.json_path.read_text(encoding="utf-8"))
    targets = [item for item in exported if item["bot_name"] == "MixedBot"]
    assert targets, "JSON Normalized records should be written"
    assert targets[0]["project_slug"] == "mixed-slug"


def test_repository_repair_existing_rows(tmp_path: Path):
    json_path = tmp_path / "projects.json"
    json_path.write_text("[]", encoding="utf-8")
    db_path = tmp_path / "master.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_name TEXT NOT NULL UNIQUE,
            bot_token TEXT NOT NULL,
            project_slug TEXT NOT NULL UNIQUE,
            default_model TEXT NOT NULL,
            workdir TEXT,
            allowed_chat_id INTEGER,
            legacy_name TEXT,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );
        """
    )
    conn.execute(
        """
        INSERT INTO projects (
            bot_name, bot_token, project_slug, default_model,
            workdir, allowed_chat_id, legacy_name, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, strftime('%s','now'), strftime('%s','now'));
        """,
        (
            "@LegacyBot ",
            "999999:ABCDEFGHIJKLMNOPQRSTUVWXYZ888888",
            "Legacy Project  ",
            " CODEX ",
            f" {tmp_path} ",
            " 42 ",
            " Legacy Alias ",
        ),
    )
    conn.commit()
    conn.close()
    repo = ProjectRepository(db_path, json_path)
    repaired = repo.get_by_slug("Legacy Project")
    assert repaired is not None
    assert repaired.project_slug == "legacy-project"
    assert repaired.bot_name == "LegacyBot"
    assert repaired.allowed_chat_id == 42
    exported = json.loads(json_path.read_text(encoding="utf-8"))
    assert exported[0]["project_slug"] == "legacy-project"


def test_validate_field_rejects_duplicate_bot(repo: ProjectRepository):
    session = master.ProjectWizardSession(chat_id=1, user_id=1, mode="create")
    value, error = master._validate_field_value(session, "bot_name", "SampleBot")
    assert value is None
    assert error == "The bot name is already occupied by another project"


def test_validate_workdir_requires_existing_path(repo: ProjectRepository, tmp_path: Path):
    session = master.ProjectWizardSession(chat_id=1, user_id=1, mode="create")
    missing_value, missing_error = master._validate_field_value(session, "workdir", str(tmp_path / "missing"))
    assert missing_value is None
    assert "Directory does not exist" in missing_error
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    value, error = master._validate_field_value(session, "workdir", str(workdir))
    assert error is None
    assert value == str(workdir)


def test_start_project_create_registers_session(repo: ProjectRepository, tmp_path: Path):
    manager = _build_manager(repo, tmp_path)
    callback = DummyCallback("project:create:*")

    async def _run():
        await master._start_project_create(callback, manager)

    asyncio.run(_run())
    assert callback.message._answers, "Alert message should be sent"
    assert callback.message.chat.id in master.PROJECT_WIZARD_SESSIONS
    assert master.PROJECT_WIZARD_SESSIONS[callback.message.chat.id].mode == "create"


def test_handle_wizard_cancel_clears_session(repo: ProjectRepository, tmp_path: Path):
    manager = _build_manager(repo, tmp_path)
    callback = DummyCallback("project:create:*")

    async def _prepare():
        await master._start_project_create(callback, manager)

    asyncio.run(_prepare())
    message = callback.message
    message.text = "Cancel"

    async def _cancel():
        await master._handle_wizard_message(message, manager)

    asyncio.run(_cancel())
    assert message.chat.id not in master.PROJECT_WIZARD_SESSIONS


def test_create_flow_writes_repository(repo: ProjectRepository, tmp_path: Path, monkeypatch):
    manager = _build_manager(repo, tmp_path)
    master.MANAGER = manager
    callback = DummyCallback("project:create:*")

    async def _prepare():
        await master._start_project_create(callback, manager)

    asyncio.run(_prepare())
    message = callback.message
    workdir = tmp_path / "new_workdir"
    workdir.mkdir()
    inputs = [
        "NewTesterBot",
        "222222:ABCDEFGHIJKLMNOPQRSTUVWXYZ123456",
        "",  # Let the system automatically generate slug
        "",  # Default model falls back to codex
        str(workdir),
        "-10001",
    ]

    async def _run_flow():
        for text in inputs:
            message.text = text
            await master._handle_wizard_message(message, manager)

    asyncio.run(_run_flow())
    records = repo.list_projects()
    assert any(r.project_slug == "newtesterbot" for r in records)
    assert message.bot.send_message.await_count >= 1


def test_repository_delete_case_insensitive(repo: ProjectRepository):
    """Ensure that the deletion interface can still hit the project even if the case is different."""
    repo.delete_project("SAMPLE")
    slugs = {record.project_slug for record in repo.list_projects()}
    assert "sample" not in slugs


def test_delete_flow_removes_project(repo: ProjectRepository, tmp_path: Path):
    manager = _build_manager(repo, tmp_path)
    master.MANAGER = manager
    callback = DummyCallback("project:delete:sample")
    storage, fsm_state = _build_fsm_state()
    async def _prepare():
        cfg = manager.require_project_by_slug("sample")
        await master._start_project_delete(callback, cfg, manager, fsm_state)

    asyncio.run(_prepare())
    assert callback.message._answers, "A confirmation prompt should be sent"
    confirm = DummyCallback("project:delete_confirm:sample", message=callback.message)

    async def _route_confirm():
        with pytest.raises(SkipHandler):
            await master.on_project_action(confirm, fsm_state)

    asyncio.run(_route_confirm())

    async def _confirm():
        await master.on_project_delete_confirm(confirm, fsm_state)

    asyncio.run(_confirm())
    slugs = {record.project_slug for record in repo.list_projects()}
    assert "sample" not in slugs
    # MemoryStorage No need to explicitly close, but keep the reference for garbage collection


def test_delete_flow_cancel(repo: ProjectRepository, tmp_path: Path):
    manager = _build_manager(repo, tmp_path)
    master.MANAGER = manager
    callback = DummyCallback("project:delete:sample")
    storage, fsm_state = _build_fsm_state()

    async def _prepare():
        cfg = manager.require_project_by_slug("sample")
        await master._start_project_delete(callback, cfg, manager, fsm_state)

    asyncio.run(_prepare())
    cancel = DummyCallback("project:delete_cancel", message=callback.message)

    async def _route_cancel():
        with pytest.raises(SkipHandler):
            await master.on_project_action(cancel, fsm_state)

    asyncio.run(_route_cancel())

    async def _cancel():
        await master.on_project_delete_cancel(cancel, fsm_state)
        return await fsm_state.get_state()

    remaining_state = asyncio.run(_cancel())
    assert remaining_state is None
    slugs = {record.project_slug for record in repo.list_projects()}
    assert "sample" in slugs
    assert cancel.message._answers[-1][0].startswith("Deletion cancelled for project")


def test_delete_flow_text_confirm(repo: ProjectRepository, tmp_path: Path):
    manager = _build_manager(repo, tmp_path)
    master.MANAGER = manager
    callback = DummyCallback("project:delete:sample")
    storage, fsm_state = _build_fsm_state()

    async def _prepare():
        cfg = manager.require_project_by_slug("sample")
        await master._start_project_delete(callback, cfg, manager, fsm_state)

    asyncio.run(_prepare())

    text_message = DummyMessage()
    text_message.text = "Confirm deletion"
    text_message.chat = callback.message.chat
    text_message.from_user = callback.message.from_user
    text_message.bot = callback.message.bot
    text_message.reply_to_message = callback.message

    async def _confirm():
        await master.on_project_delete_text(text_message, fsm_state)

    asyncio.run(_confirm())
    slugs = {record.project_slug for record in repo.list_projects()}
    assert "sample" not in slugs
    assert any("deleted" in answer.lower() for answer, _ in text_message._answers)


def test_delete_flow_text_cancel(repo: ProjectRepository, tmp_path: Path):
    manager = _build_manager(repo, tmp_path)
    master.MANAGER = manager
    callback = DummyCallback("project:delete:sample")
    storage, fsm_state = _build_fsm_state()

    async def _prepare():
        cfg = manager.require_project_by_slug("sample")
        await master._start_project_delete(callback, cfg, manager, fsm_state)

    asyncio.run(_prepare())

    text_message = DummyMessage(chat_id=1)
    text_message.text = "Cancel"
    text_message.chat = callback.message.chat
    text_message.from_user = callback.message.from_user
    text_message.bot = callback.message.bot
    text_message.reply_to_message = callback.message

    async def _cancel():
        await master.on_project_delete_text(text_message, fsm_state)

    asyncio.run(_cancel())
    current_state = asyncio.run(fsm_state.get_state())
    assert current_state is None
    slugs = {record.project_slug for record in repo.list_projects()}
    assert "sample" in slugs
    assert any("Deletion cancelled for project" in answer for answer, _ in text_message._answers)


def test_delete_flow_fallbacks_to_original_slug(repo: ProjectRepository, tmp_path: Path):
    """Verify that the removal process can be executed successfully after slug normalization."""
    record = repo.get_by_slug("sample")
    updated = ProjectRecord(
        bot_name=record.bot_name,
        bot_token=record.bot_token,
        project_slug="SampleCase",
        default_model=record.default_model,
        workdir=record.workdir,
        allowed_chat_id=record.allowed_chat_id,
        legacy_name=record.legacy_name,
    )
    repo.update_project("sample", updated)
    manager = _build_manager(repo, tmp_path)
    master.MANAGER = manager
    master.PROJECT_REPOSITORY = repo
    callback = DummyCallback("project:delete:samplecase")
    storage, fsm_state = _build_fsm_state()

    async def _prepare():
        cfg = manager.require_project_by_slug("samplecase")
        await master._start_project_delete(callback, cfg, manager, fsm_state)

    asyncio.run(_prepare())

    confirm = DummyCallback("project:delete_confirm:samplecase", message=callback.message)

    async def _confirm():
        await master.on_project_delete_confirm(confirm, fsm_state)

    asyncio.run(_confirm())
    assert repo.get_by_bot_name(record.bot_name) is None


def test_get_project_runtime_state_handles_casefold(repo: ProjectRepository, tmp_path: Path):
    """Make sure the helper functions are case-compatible and return the same state instance."""
    manager = _build_manager(repo, tmp_path)
    master.MANAGER = manager
    master.PROJECT_REPOSITORY = repo

    # slug Different case should still hit
    runtime_state_upper = master._get_project_runtime_state(manager, "SAMPLE")
    runtime_state_lower = master._get_project_runtime_state(manager, "sample")
    assert runtime_state_upper is runtime_state_lower
    assert runtime_state_upper.status == "stopped"

    # Return None for non-existent slug
    runtime_state_none = master._get_project_runtime_state(manager, "not-found")
    assert runtime_state_none is None


def test_on_project_action_delete_starts_confirmation_flow(repo: ProjectRepository, tmp_path: Path):
    """Simulate a button click to verify that the deletion process can correctly enter the confirmation state."""
    manager = _build_manager(repo, tmp_path)
    master.MANAGER = manager
    master.PROJECT_REPOSITORY = repo
    storage, fsm_state = _build_fsm_state()
    callback = DummyCallback("project:delete:sample")

    async def _invoke():
        await master.on_project_action(callback, fsm_state)

    asyncio.run(_invoke())
    state_value = asyncio.run(fsm_state.get_state())
    assert state_value == master.ProjectDeleteStates.confirming.state
    assert callback.message._answers, "The user should be prompted to Confirm deletion"
    answer_text, _ = callback.message._answers[-1]
    assert "Confirm deletion of project" in answer_text


def test_on_project_action_delete_blocks_when_running(repo: ProjectRepository, tmp_path: Path):
    """worker Delete requests should be denied while running."""
    manager = _build_manager(repo, tmp_path)
    master.MANAGER = manager
    master.PROJECT_REPOSITORY = repo
    manager.state_store.data["sample"].status = "running"
    storage, fsm_state = _build_fsm_state()
    callback = DummyCallback("project:delete:sample")

    async def _invoke():
        await master.on_project_action(callback, fsm_state)

    asyncio.run(_invoke())
    assert callback._answers[-1] == ("Please stop the worker of this project before deleting it.", True)
    assert not callback.message._answers
    assert asyncio.run(fsm_state.get_state()) is None


def test_on_project_action_delete_unknown_slug(repo: ProjectRepository, tmp_path: Path):
    """The unknown item slug should return the prompt immediately."""
    manager = _build_manager(repo, tmp_path)
    master.MANAGER = manager
    master.PROJECT_REPOSITORY = repo
    storage, fsm_state = _build_fsm_state()
    callback = DummyCallback("project:delete:missing")

    async def _invoke():
        await master.on_project_action(callback, fsm_state)

    asyncio.run(_invoke())
    assert callback._answers[-1] == ("Unknown project", True)
    assert asyncio.run(fsm_state.get_state()) is None


def test_on_project_action_delete_repeated_request(repo: ProjectRepository, tmp_path: Path):
    """When you click the delete button repeatedly, you should be prompted that the process already exists to avoid overwriting the FSM repeatedly."""
    manager = _build_manager(repo, tmp_path)
    master.MANAGER = manager
    master.PROJECT_REPOSITORY = repo
    storage, fsm_state = _build_fsm_state()
    first_callback = DummyCallback("project:delete:sample")

    async def _first():
        await master.on_project_action(first_callback, fsm_state)

    asyncio.run(_first())
    # second click
    second_callback = DummyCallback("project:delete:sample")

    async def _second():
        await master.on_project_action(second_callback, fsm_state)

    asyncio.run(_second())
    assert second_callback._answers[-1] == (
        "The current deletion process is being confirmed, please use the buttons to finish the operation.",
        True,
    )


def test_projects_overview_hides_create_button(repo: ProjectRepository, tmp_path: Path):
    manager = _build_manager(repo, tmp_path)
    text, markup = master._projects_overview(manager)
    assert text == "Please select an action:"
    assert markup is not None
    labels = [btn.text for row in markup.inline_keyboard for btn in row]
    assert "➕ Add new items" not in labels
    assert "🚀 Start all projects" in labels
    run_buttons = [label for label in labels if label.startswith("▶️ start up")]
    stop_buttons = [label for label in labels if label.startswith("⛔️ stop")]
    for label in run_buttons + stop_buttons:
        if label == "⛔️ stopallproject":
            continue
        assert "(" in label and label.endswith(")"), "run/stopThe button should display the current model"


def test_manage_action_sends_inline_menu(repo: ProjectRepository, tmp_path: Path, monkeypatch):
    manager = _build_manager(repo, tmp_path)
    master.MANAGER = manager
    callback = DummyCallback("project:manage:sample")
    _, fsm_state = _build_fsm_state()

    async def _invoke():
        await master.on_project_action(callback, fsm_state)

    asyncio.run(_invoke())
    assert callback.message._answers, "Management options should be sent"
    buttons = callback.message._answers[0][1]["reply_markup"].inline_keyboard
    texts = [btn.text for row in buttons for btn in row]
    callbacks = [btn.callback_data for row in buttons for btn in row]
    assert "📝 edit" in texts
    assert any(text.startswith("🧠 Switch model (current model ") for text in texts)
    assert "🗑 delete" in texts
    assert "project:switch_prompt:sample" in callbacks
    assert all(not cb.startswith("project:switch_to") for cb in callbacks)


def test_manage_button_handler_builds_keyboard(repo: ProjectRepository, tmp_path: Path, monkeypatch):
    manager = _build_manager(repo, tmp_path)

    async def fake_manager():
        return manager

    monkeypatch.setattr(master, "_ensure_manager", fake_manager)
    message = DummyMessage()
    message.text = master.MASTER_MANAGE_BUTTON_TEXT

    async def _invoke():
        await master.on_master_manage_button(message)

    asyncio.run(_invoke())
    assert message._answers
    markup = message._answers[0][1]["reply_markup"]
    labels = [btn.text for row in markup.inline_keyboard for btn in row]
    assert "➕ New project" in labels
    assert any(label.casefold().startswith("⚙️ manage") for label in labels)
    assert any(label.startswith("🧠 Switch model (current model ") for label in labels)
    assert "🔁 Switch all models" in labels
    rows = markup.inline_keyboard
    assert any(
        row[0].text.casefold().startswith("⚙️ manage")
        and row[1].text.startswith("🧠 Switch model (current model ")
        for row in rows
        if len(row) >= 2
    ), "projectmanageLists should be displayed in the same rowmanagebutton with model"


def test_switch_prompt_displays_model_options(repo: ProjectRepository, tmp_path: Path):
    manager = _build_manager(repo, tmp_path)
    master.MANAGER = manager
    master.PROJECT_REPOSITORY = repo
    manager.state_store.update("sample", model="claudecode", status="stopped")
    callback_message = DummyMessage()
    callback = DummyCallback("project:switch_prompt:sample", message=callback_message)
    _, fsm_state = _build_fsm_state()

    async def _invoke():
        await master.on_project_action(callback, fsm_state)

    asyncio.run(_invoke())
    assert callback._answers == [(None, False)]
    assert callback_message._answers, "The model selection menu should pop up"
    reply_markup = callback_message._answers[-1][1]["reply_markup"]
    model_callbacks = [
        btn.callback_data
        for row in reply_markup.inline_keyboard
        for btn in row
        if btn.callback_data
    ]
    model_texts = [
        btn.text
        for row in reply_markup.inline_keyboard
        for btn in row
    ]
    assert any(cb.startswith("project:switch_to:") for cb in model_callbacks), "Should include switch model callbacks"
    assert any(text.startswith("[active] ") for text in model_texts), "The current model should be marked as active"
    assert "project:refresh:*" in model_callbacks, "A return to list button should be provided"


def test_refresh_action_skips_slug_validation(repo: ProjectRepository, tmp_path: Path, monkeypatch):
    manager = _build_manager(repo, tmp_path)
    master.MANAGER = manager
    master.PROJECT_REPOSITORY = repo
    callback_message = DummyMessage()
    callback = DummyCallback("project:refresh:*", message=callback_message)
    _, fsm_state = _build_fsm_state()

    async def _invoke():
        await master.on_project_action(callback, fsm_state)

    asyncio.run(_invoke())
    assert callback._answers == [(None, False)]
    assert callback_message._edits, "Item list text should be refreshed"
    text, kwargs = callback_message._edits[0]
    assert text == "Please select an action:"
    assert kwargs["reply_markup"] is not None


def test_switch_to_running_project_updates_state(repo: ProjectRepository, tmp_path: Path, monkeypatch):
    manager = _build_manager(repo, tmp_path)
    master.MANAGER = manager
    master.PROJECT_REPOSITORY = repo
    manager.state_store.update("sample", model="codex", status="running")

    async def stop_worker_override(cfg: master.ProjectConfig, *, update_state: bool = True) -> None:
        manager.state_store.update(cfg.project_slug, status="stopped")

    async def run_worker_override(cfg: master.ProjectConfig, model: str | None = None) -> str:
        chosen = model or cfg.default_model
        manager.state_store.update(cfg.project_slug, model=chosen, status="running")
        return chosen

    monkeypatch.setattr(manager, "stop_worker", AsyncMock(side_effect=stop_worker_override))
    run_mock = AsyncMock(side_effect=run_worker_override)
    monkeypatch.setattr(manager, "run_worker", run_mock)

    callback = DummyCallback("project:switch_to:claudecode:sample")
    _, fsm_state = _build_fsm_state()

    async def _invoke():
        await master.on_project_action(callback, fsm_state)

    asyncio.run(_invoke())
    run_mock.assert_awaited()
    updated_state = manager.state_store.data["sample"]
    assert updated_state.model == "claudecode"
    assert updated_state.status == "running"
    assert any("ClaudeCode" in (text or "") for text, _ in callback._answers)


def test_switch_all_action_displays_model_options(repo: ProjectRepository, tmp_path: Path):
    manager = _build_manager(repo, tmp_path)
    master.MANAGER = manager
    master.PROJECT_REPOSITORY = repo
    callback = DummyCallback("project:switch_all:*")
    _, fsm_state = _build_fsm_state()

    async def _invoke():
        await master.on_project_action(callback, fsm_state)

    asyncio.run(_invoke())
    assert callback._answers == [(None, False)]
    assert callback.message._answers, "The global model selection keyboard should be shown"
    markup = callback.message._answers[-1][1]["reply_markup"]
    button_texts = [btn.text for row in markup.inline_keyboard for btn in row]
    assert any("Codex" in text for text in button_texts)
    assert any("ClaudeCode" in text for text in button_texts)
    assert any("Cancel" in text for text in button_texts)


def test_switch_all_to_updates_all_models(repo: ProjectRepository, tmp_path: Path, monkeypatch):
    second = ProjectRecord(
        bot_name="OtherBot",
        bot_token="222222:ABCDEFGHIJKLMNOPQRSTUVWXYZ111111",
        project_slug="other",
        default_model="codex",
        workdir=str(tmp_path),
        allowed_chat_id=None,
        legacy_name=None,
    )
    repo.insert_project(second)
    manager = _build_manager(repo, tmp_path)
    master.MANAGER = manager
    master.PROJECT_REPOSITORY = repo
    manager.state_store.update("sample", model="codex", status="running")
    manager.state_store.update("other", model="codex", status="stopped")

    async def fake_stop_worker(cfg: master.ProjectConfig, *, update_state: bool = True) -> None:
        manager.state_store.update(cfg.project_slug, status="stopped")

    stop_mock = AsyncMock(side_effect=fake_stop_worker)
    monkeypatch.setattr(manager, "stop_worker", stop_mock)

    callback = DummyCallback("project:switch_all_to:claudecode:*")
    _, fsm_state = _build_fsm_state()

    async def _invoke():
        await master.on_project_action(callback, fsm_state)

    asyncio.run(_invoke())
    assert stop_mock.await_count == len(manager.configs)
    for slug, state in manager.state_store.data.items():
        assert state.model == "claudecode"
        assert state.status == "stopped"
    summary_texts = [text for text, _ in callback.message._answers]
    assert any("All project models have been switched to ⚙️ ClaudeCode" in text for text in summary_texts)
