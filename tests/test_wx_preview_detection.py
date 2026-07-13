import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import bot as bot_module
from bot import (
    WX_AUTO_PREVIEW_COMMAND_NAME,
    WX_PREVIEW_COMMAND_NAME,
    WX_UPLOAD_COMMAND_NAME,
    _detect_wx_preview_candidates,
    _is_wx_devtools_command,
    _resolve_miniprogram_app_dir,
    _wrap_wx_preview_command,
)
from command_center.defaults import DEFAULT_GLOBAL_COMMANDS
from command_center import CommandDefinition


WX_REMOTE_DEBUG_COMMAND_NAME = "wx-remote-debug"


def _write_app_json(dir_path: Path) -> None:
    """在目标目录写入最小化 app.json。"""

    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "app.json").write_text("{}", encoding="utf-8")


def test_detect_root_app_json(tmp_path: Path) -> None:
    _write_app_json(tmp_path)
    candidates = _detect_wx_preview_candidates(tmp_path)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.source == "current"
    assert candidate.project_root == tmp_path.resolve()
    assert candidate.app_dir == tmp_path.resolve()


def test_detect_root_miniprogram_root(tmp_path: Path) -> None:
    mini = tmp_path / "mini"
    _write_app_json(mini)
    config_path = tmp_path / "project.config.json"
    config_path.write_text(json.dumps({"miniprogramRoot": "mini"}), encoding="utf-8")

    candidates = _detect_wx_preview_candidates(tmp_path)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.source == "current"
    assert candidate.project_root == tmp_path.resolve()
    assert candidate.app_dir == mini.resolve()


def test_detect_child_app_json(tmp_path: Path) -> None:
    child = tmp_path / "frontend-mini"
    _write_app_json(child)

    candidates = _detect_wx_preview_candidates(tmp_path)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.source == "child"
    assert candidate.project_root == child.resolve()
    assert candidate.app_dir == child.resolve()


def test_detect_child_miniprogram_root(tmp_path: Path) -> None:
    child = tmp_path / "wxapp"
    mini = child / "src"
    _write_app_json(mini)
    config_path = child / "project.config.json"
    config_path.write_text(json.dumps({"miniprogramRoot": "src"}), encoding="utf-8")

    candidates = _detect_wx_preview_candidates(tmp_path)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.source == "child"
    assert candidate.project_root == child.resolve()
    assert candidate.app_dir == mini.resolve()


def test_detect_multiple_candidates(tmp_path: Path) -> None:
    _write_app_json(tmp_path)
    child = tmp_path / "frontend-mini"
    _write_app_json(child)

    candidates = _detect_wx_preview_candidates(tmp_path)
    sources = {(c.source, c.project_root) for c in candidates}
    assert sources == {
        ("current", tmp_path.resolve()),
        ("child", child.resolve()),
    }


def test_detect_none_returns_empty(tmp_path: Path) -> None:
    (tmp_path / "readme.txt").write_text("noop", encoding="utf-8")
    candidates = _detect_wx_preview_candidates(tmp_path)
    assert candidates == []


def test_resolve_app_dir_returns_none_when_missing_app(tmp_path: Path) -> None:
    """缺少 app.json 时应返回 None。"""

    result = _resolve_miniprogram_app_dir(tmp_path)
    assert result is None


def test_resolve_app_dir_invalid_miniprogram_root(tmp_path: Path) -> None:
    """miniprogramRoot 指向无效路径时应返回 None。"""

    cfg = tmp_path / "project.config.json"
    cfg.write_text(json.dumps({"miniprogramRoot": "not_exist"}), encoding="utf-8")
    result = _resolve_miniprogram_app_dir(tmp_path)
    assert result is None


def test_wrap_wx_preview_command_injects_path(tmp_path: Path) -> None:
    command = CommandDefinition(
        id=1,
        project_slug="demo",
        name=WX_PREVIEW_COMMAND_NAME,
        title="生成预览",
        command="echo ok",
        scope="project",
        description="",
        timeout=60,
        enabled=True,
        aliases=(),
    )
    wrapped = _wrap_wx_preview_command(command, tmp_path)
    assert str(tmp_path) in wrapped.command
    assert wrapped.command.startswith(f"PROJECT_PATH=")
    assert "PROJECT_BASE" in wrapped.command
    assert wrapped.id == command.id
    assert wrapped.project_slug == command.project_slug


def test_default_global_command_uses_project_base() -> None:
    """确保默认通用命令不会覆盖用户选择的目录。"""

    cmd = next(item for item in DEFAULT_GLOBAL_COMMANDS if item["name"] == WX_PREVIEW_COMMAND_NAME)
    command_text = str(cmd["command"])
    assert 'PROJECT_PATH="${PROJECT_PATH:-$MODEL_WORKDIR}"' not in command_text
    assert 'PROJECT_BASE="${PROJECT_BASE:-$MODEL_WORKDIR}"' in command_text
    assert 'OUTPUT_QR="${OUTPUT_QR:-/tmp/wx-preview-$(date +%s).jpg}"' in command_text
    assert "$HOME/Downloads" not in command_text


def test_default_global_upload_command_uses_project_base() -> None:
    """确保上传命令默认使用 PROJECT_BASE 并指向 gen_upload.sh。"""

    cmd = next(item for item in DEFAULT_GLOBAL_COMMANDS if item["name"] == WX_UPLOAD_COMMAND_NAME)
    command_text = str(cmd["command"])
    assert 'PROJECT_BASE="${PROJECT_BASE:-$MODEL_WORKDIR}"' in command_text
    assert "gen_upload.sh" in command_text


def test_wx_auto_preview_is_default_global_command() -> None:
    """手机自动预览应作为默认通用命令接入命令管理。"""

    assert getattr(bot_module, "WX_AUTO_PREVIEW_COMMAND_NAME", None) == "wx-auto-preview"
    assert _is_wx_devtools_command("wx-auto-preview") is True
    cmd = next((item for item in DEFAULT_GLOBAL_COMMANDS if item["name"] == "wx-auto-preview"), None)
    assert cmd is not None
    command_text = str(cmd["command"])
    assert 'PROJECT_BASE="${PROJECT_BASE:-$MODEL_WORKDIR}"' in command_text
    assert 'WX_PREVIEW_ACTION=auto-preview' in command_text
    assert "gen_preview.sh" in command_text


def test_wx_remote_debug_is_default_global_command() -> None:
    """自动真机调试应作为免扫码的默认通用命令接入命令管理。"""

    assert getattr(bot_module, "WX_REMOTE_DEBUG_COMMAND_NAME", None) == WX_REMOTE_DEBUG_COMMAND_NAME
    assert _is_wx_devtools_command(WX_REMOTE_DEBUG_COMMAND_NAME) is True
    cmd = next(
        (item for item in DEFAULT_GLOBAL_COMMANDS if item["name"] == WX_REMOTE_DEBUG_COMMAND_NAME),
        None,
    )
    assert cmd is not None
    assert cmd["title"] == "启动微信自动真机调试"
    command_text = str(cmd["command"])
    assert 'PROJECT_BASE="${PROJECT_BASE:-$MODEL_WORKDIR}"' in command_text
    assert "WX_PREVIEW_ACTION=remote-debug" in command_text
    assert "gen_preview.sh" in command_text
    assert "二维码" not in str(cmd["description"])


class _DummyWxPreviewState:
    """记录微信目录选择 FSM 调用，便于断言是否进入人工选择。"""

    def __init__(self) -> None:
        self.cleared = 0
        self.states: list[object] = []
        self.data: list[dict] = []

    async def clear(self) -> None:
        self.cleared += 1

    async def set_state(self, state) -> None:
        self.states.append(state)

    async def update_data(self, **kwargs) -> None:
        self.data.append(kwargs)


def _build_wx_command(name: str) -> CommandDefinition:
    """构造微信开发命令对象。"""

    return CommandDefinition(
        id=101,
        project_slug="__global__",
        name=name,
        title=name,
        command="echo run",
        scope="global",
        description="",
        timeout=60,
        enabled=True,
        aliases=(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command_name",
    [WX_PREVIEW_COMMAND_NAME, WX_AUTO_PREVIEW_COMMAND_NAME, WX_REMOTE_DEBUG_COMMAND_NAME],
)
async def test_wx_preview_single_candidate_executes_without_choice(monkeypatch, tmp_path: Path, command_name: str) -> None:
    """预览类命令只有一个小程序候选时，应自动选择并直接执行。"""

    mini = tmp_path / "frontend-mini"
    _write_app_json(mini)
    state = _DummyWxPreviewState()
    messages: list[str] = []
    executed: list[CommandDefinition] = []
    reply_message = SimpleNamespace()

    async def fake_answer_with_markdown(_message, text: str, *, reply_markup=None):
        messages.append(text)
        return SimpleNamespace(message_id=len(messages), chat=SimpleNamespace(id=1))

    async def fake_execute_command_definition(**kwargs):
        executed.append(kwargs["command"])

    monkeypatch.setattr(bot_module, "_command_workdir", lambda: tmp_path)
    monkeypatch.setattr(bot_module, "_answer_with_markdown", fake_answer_with_markdown)
    monkeypatch.setattr(bot_module, "_execute_command_definition", fake_execute_command_definition)

    handled = await bot_module._maybe_handle_wx_preview(
        command=_build_wx_command(command_name),
        reply_message=reply_message,
        trigger="按钮",
        actor_user=None,
        service=SimpleNamespace(),
        history_detail_prefix="cmd:detail:",
        fsm_state=state,
    )

    assert handled is True
    assert len(executed) == 1
    assert str(mini) in executed[0].command
    assert "PROJECT_PATH=" in executed[0].command
    assert state.states == []
    assert messages and "仅发现 1 个小程序目录" in messages[0]


@pytest.mark.asyncio
async def test_wx_upload_single_candidate_still_requires_choice(monkeypatch, tmp_path: Path) -> None:
    """上传命令即使只有一个候选，也保留人工确认，避免误上传。"""

    mini = tmp_path / "frontend-mini"
    _write_app_json(mini)
    state = _DummyWxPreviewState()
    messages: list[tuple[str, object]] = []
    executed: list[CommandDefinition] = []
    reply_message = SimpleNamespace()

    async def fake_answer_with_markdown(_message, text: str, *, reply_markup=None):
        messages.append((text, reply_markup))
        return SimpleNamespace(message_id=len(messages), chat=SimpleNamespace(id=1))

    async def fake_execute_command_definition(**kwargs):
        executed.append(kwargs["command"])

    monkeypatch.setattr(bot_module, "_command_workdir", lambda: tmp_path)
    monkeypatch.setattr(bot_module, "_answer_with_markdown", fake_answer_with_markdown)
    monkeypatch.setattr(bot_module, "_execute_command_definition", fake_execute_command_definition)

    handled = await bot_module._maybe_handle_wx_preview(
        command=_build_wx_command(WX_UPLOAD_COMMAND_NAME),
        reply_message=reply_message,
        trigger="按钮",
        actor_user=None,
        service=SimpleNamespace(),
        history_detail_prefix="cmd:detail:",
        fsm_state=state,
    )

    assert handled is True
    assert executed == []
    assert state.states == [bot_module.WxPreviewStates.waiting_choice]
    assert messages and "请选择要上传代码的小程序目录" in messages[0][0]
