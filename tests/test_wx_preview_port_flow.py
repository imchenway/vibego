import json
from pathlib import Path

import pytest

from bot import (
    _is_wx_preview_missing_port_error,
    _is_wx_preview_port_mismatch_error,
    _parse_numeric_port,
    _parse_wx_preview_port_mismatch,
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


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        ("", (None, None)),
        ("random error", (None, None)),
        (
            "✖ IDE server has started on http://127.0.0.1:34724 and must be restarted on port 64701 first",
            (34724, 64701),
        ),
        (
            "IDE server has started on https://localhost:12605 and must be restarted on port 64701 first",
            (12605, 64701),
        ),
        (
            "IDE server has started on http://127.0.0.1:1 and must be restarted on port 65535 first",
            (1, 65535),
        ),
        (
            "IDE server has started on http://127.0.0.1:0 and must be restarted on port 64701 first",
            (None, None),
        ),
        (
            "IDE server has started on http://127.0.0.1:70000 and must be restarted on port 64701 first",
            (None, None),
        ),
        (
            "IDE server has started on http://127.0.0.1:34724 and must be restarted on port 0 first",
            (None, None),
        ),
        (
            "IDE SERVER HAS STARTED ON http://127.0.0.1:34724 AND MUST BE RESTARTED ON PORT 64701 FIRST",
            (34724, 64701),
        ),
        (
            "prefix\nIDE server has started on http://127.0.0.1:34724 and must be restarted on port 64701 first\nsuffix",
            (34724, 64701),
        ),
    ],
)
def test_parse_wx_preview_port_mismatch(stderr: str, expected: tuple[int | None, int | None]) -> None:
    assert _parse_wx_preview_port_mismatch(stderr) == expected


def test_is_wx_preview_port_mismatch_error_matches() -> None:
    stderr = "✖ IDE server has started on http://127.0.0.1:34724 and must be restarted on port 64701 first"
    assert _is_wx_preview_port_mismatch_error(255, stderr) is True
    assert _is_wx_preview_port_mismatch_error(0, stderr) is False
    assert _is_wx_preview_port_mismatch_error(None, stderr) is False
    assert _is_wx_preview_port_mismatch_error(255, "其他错误") is False


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
