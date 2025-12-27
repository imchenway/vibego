import json
from pathlib import Path

import pytest

from bot import (
    _is_wx_preview_missing_port_error,
    _parse_numeric_port,
    _upsert_wx_devtools_ports_file,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", 1),
        ("80", 80),
        ("64701", 64701),
        (" 64701 ", 64701),
        ("\n64701\t", 64701),
        ("0", None),
        ("65536", None),
        ("-1", None),
        ("abc", None),
        ("64701 1", None),
        ("", None),
    ],
)
def test_parse_numeric_port(raw: str, expected: int | None) -> None:
    assert _parse_numeric_port(raw) == expected


def test_is_wx_preview_missing_port_error_matches() -> None:
    stderr = "[错误] 未配置微信开发者工具 IDE 服务端口，无法生成预览二维码。"
    assert _is_wx_preview_missing_port_error(2, stderr) is True
    assert _is_wx_preview_missing_port_error(1, stderr) is False
    assert _is_wx_preview_missing_port_error(2, "其他错误") is False


def test_upsert_wx_devtools_ports_file_creates_new(tmp_path: Path) -> None:
    ports_file = tmp_path / "wx_devtools_ports.json"
    project_root = tmp_path / "mini"
    project_root.mkdir()
    _upsert_wx_devtools_ports_file(
        ports_file=ports_file,
        project_slug="hyphamall",
        project_root=project_root,
        port=64701,
    )
    data = json.loads(ports_file.read_text(encoding="utf-8"))
    assert data["projects"]["hyphamall"] == 64701
    assert data["paths"][str(project_root.resolve())] == 64701


def test_upsert_wx_devtools_ports_file_upgrades_legacy_format(tmp_path: Path) -> None:
    ports_file = tmp_path / "wx_devtools_ports.json"
    ports_file.write_text(json.dumps({"legacy": 12605}, ensure_ascii=False), encoding="utf-8")

    project_root = tmp_path / "mini"
    project_root.mkdir()
    _upsert_wx_devtools_ports_file(
        ports_file=ports_file,
        project_slug="hyphamall",
        project_root=project_root,
        port=64701,
    )
    data = json.loads(ports_file.read_text(encoding="utf-8"))
    assert data["projects"]["legacy"] == 12605
    assert data["projects"]["hyphamall"] == 64701
    assert data["paths"][str(project_root.resolve())] == 64701

