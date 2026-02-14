from pathlib import Path

from bot import (
    WX_UPLOAD_COMMAND_NAME,
    _apply_command_env_overrides,
    _extract_command_args,
    _extract_command_trigger,
    _parse_wx_upload_args,
)
from command_center import CommandDefinition


def test_extract_command_trigger_and_args() -> None:
    prompt = "/wx-dev-upload --version 202602140001"
    assert _extract_command_trigger(prompt) == "wx-dev-upload"
    assert _extract_command_args(prompt) == "--version 202602140001"


def test_parse_wx_upload_args_empty() -> None:
    version, err = _parse_wx_upload_args("")
    assert version is None
    assert err is None


def test_parse_wx_upload_args_version() -> None:
    version, err = _parse_wx_upload_args("--version 202602140001")
    assert err is None
    assert version == "202602140001"


def test_parse_wx_upload_args_rejects_unknown_option() -> None:
    version, err = _parse_wx_upload_args("--robot 1")
    assert version is None
    assert err is not None


def test_parse_wx_upload_args_rejects_invalid_version() -> None:
    version, err = _parse_wx_upload_args("--version bad/version")
    assert version is None
    assert err is not None


def test_apply_command_env_overrides_injects_version(tmp_path: Path) -> None:
    command = CommandDefinition(
        id=1,
        project_slug="demo",
        name=WX_UPLOAD_COMMAND_NAME,
        title="上传",
        command='PROJECT_BASE="${PROJECT_BASE:-$MODEL_WORKDIR}" bash "$ROOT_DIR/scripts/gen_upload.sh"',
        scope="global",
        description="",
        timeout=600,
        enabled=True,
        aliases=(),
    )
    wrapped = _apply_command_env_overrides(command, {"VERSION": "202602140001"})
    assert wrapped.command.startswith("VERSION=202602140001 ")
    assert "gen_upload.sh" in wrapped.command
