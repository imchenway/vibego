import json
from pathlib import Path

from bot import (
    WX_PREVIEW_COMMAND_NAME,
    _detect_wx_preview_candidates,
    _resolve_miniprogram_app_dir,
    _wrap_wx_preview_command,
)
from command_center.defaults import DEFAULT_GLOBAL_COMMANDS
from command_center import CommandDefinition


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
