import json
import os
import shutil
import subprocess
import sys
from textwrap import dedent
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


@pytest.mark.skipif(os.name != "posix", reason="wx-dev-preview 脚本依赖 bash/Posix 环境")
def test_gen_preview_prefers_python3_over_python(tmp_path: Path) -> None:
    """确保脚本在 python 不可用时仍能用 python3 解析端口映射，避免误报“端口缺失”。

    回归点：部分环境仅提供 python3（或 python 指向 Python2），脚本若硬调用 python 会导致解析静默失败。
    """

    bash_bin = shutil.which("bash")
    if bash_bin is None:
        pytest.skip("未检测到 bash")

    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "gen_preview.sh"
    assert script_path.is_file()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    # 构造一个“坏的 python”（存在但执行失败），用于稳定复现旧逻辑的问题
    dummy_python = bin_dir / "python"
    dummy_python.write_text("#!/bin/bash\nexit 1\n", encoding="utf-8")
    dummy_python.chmod(0o755)

    # 提供 python3，可直接复用当前测试进程的解释器
    python3_wrapper = bin_dir / "python3"
    python3_wrapper.write_text(
        dedent(
            f"""\
            #!/bin/bash
            exec "{sys.executable}" "$@"
            """
        ),
        encoding="utf-8",
    )
    python3_wrapper.chmod(0o755)

    # 构造可执行的假 CLI：读取 --qr-output/--port，并写文件以便断言
    fake_cli = bin_dir / "fake-wx-cli"
    fake_cli.write_text(
        dedent(
            """\
            #!/bin/bash
            set -euo pipefail

            output=""
            port=""
            while [[ $# -gt 0 ]]; do
              if [[ "$1" == "--qr-output" ]]; then
                output="$2"
                shift 2
                continue
              fi
              if [[ "$1" == "--port" ]]; then
                port="$2"
                shift 2
                continue
              fi
              shift
            done

            if [[ -z "$output" ]]; then
              echo "missing --qr-output" >&2
              exit 2
            fi

            mkdir -p "$(dirname "$output")"
            printf 'fake-jpg' > "$output"

            if [[ -n "${FAKE_WX_CLI_PORT_FILE:-}" ]]; then
              printf '%s' "$port" > "$FAKE_WX_CLI_PORT_FILE"
            fi
            """
        ),
        encoding="utf-8",
    )
    fake_cli.chmod(0o755)

    # 构造最小小程序目录
    project_root = tmp_path / "mini"
    project_root.mkdir()
    (project_root / "app.json").write_text("{}", encoding="utf-8")

    # 构造端口映射文件（既写 projects，也写 paths，覆盖两种匹配路径）
    ports_file = tmp_path / "wx_devtools_ports.json"
    ports_file.write_text(
        json.dumps(
            {
                "projects": {"hyphamall": 45927},
                "paths": {str(project_root.resolve()): 45927},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    output_qr = tmp_path / "out" / "qr.jpg"

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["CLI_BIN"] = str(fake_cli)
    env["PROJECT_NAME"] = "hyphamall"
    env["PROJECT_PATH"] = str(project_root)
    env["PROJECT_BASE"] = str(project_root)
    env["OUTPUT_QR"] = str(output_qr)
    env["WX_DEVTOOLS_PORTS_FILE"] = str(ports_file)
    port_capture_file = tmp_path / "port.txt"
    env["FAKE_WX_CLI_PORT_FILE"] = str(port_capture_file)
    env.pop("PORT", None)

    proc = subprocess.run(
        [bash_bin, str(script_path)],
        check=False,
        capture_output=True,
        env=env,
        cwd=str(tmp_path),
    )
    stdout = (proc.stdout or b"").decode("utf-8", errors="replace")
    stderr = (proc.stderr or b"").decode("utf-8", errors="replace")
    assert proc.returncode == 0, f"stdout:\n{stdout}\n\nstderr:\n{stderr}"
    assert output_qr.is_file()
    assert port_capture_file.is_file()
    assert port_capture_file.read_text(encoding="utf-8") == "45927"
