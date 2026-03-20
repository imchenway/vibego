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


@pytest.mark.skipif(os.name != "posix", reason="wx-dev-preview 脚本依赖 bash/Posix 环境")
def test_gen_preview_normalizes_symlink_output_dir(tmp_path: Path) -> None:
    """输出目录若为符号链接，应先规范化为真实路径再传给微信 CLI。

    回归点：macOS 上 `/tmp -> /private/tmp`，微信开发者工具 CLI 会把符号链接目录判定为
    “二维码输出路径无效或不存在”，导致预览失败。
    """

    bash_bin = shutil.which("bash")
    if bash_bin is None:
        pytest.skip("未检测到 bash")

    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "gen_preview.sh"
    assert script_path.is_file()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    fake_cli = bin_dir / "fake-wx-cli"
    fake_cli.write_text(
        dedent(
            """\
            #!/bin/bash
            set -euo pipefail

            output=""
            while [[ $# -gt 0 ]]; do
              if [[ "$1" == "--qr-output" ]]; then
                output="$2"
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

            if [[ -n "${FAKE_WX_CLI_OUTPUT_FILE:-}" ]]; then
              printf '%s' "$output" > "$FAKE_WX_CLI_OUTPUT_FILE"
            fi
            """
        ),
        encoding="utf-8",
    )
    fake_cli.chmod(0o755)

    project_root = tmp_path / "mini"
    project_root.mkdir()
    (project_root / "app.json").write_text("{}", encoding="utf-8")

    real_output_dir = tmp_path / "real-output"
    real_output_dir.mkdir()
    symlink_output_dir = tmp_path / "link-output"
    symlink_output_dir.symlink_to(real_output_dir, target_is_directory=True)
    output_qr = symlink_output_dir / "qr.jpg"
    output_capture_file = tmp_path / "output.txt"

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["CLI_BIN"] = str(fake_cli)
    env["PROJECT_PATH"] = str(project_root)
    env["PROJECT_BASE"] = str(project_root)
    env["OUTPUT_QR"] = str(output_qr)
    env["PORT"] = "45459"
    env["FAKE_WX_CLI_OUTPUT_FILE"] = str(output_capture_file)

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
    assert output_capture_file.is_file()
    assert output_capture_file.read_text(encoding="utf-8") == str((real_output_dir / "qr.jpg").resolve())


@pytest.mark.skipif(os.name != "posix", reason="wx-dev-upload 脚本依赖 bash/Posix 环境")
def test_gen_upload_prefers_python3_over_python_and_honors_version(tmp_path: Path) -> None:
    """确保上传脚本在 python 不可用时仍能用 python3 解析端口，并携带指定版本号。"""

    bash_bin = shutil.which("bash")
    if bash_bin is None:
        pytest.skip("未检测到 bash")

    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "gen_upload.sh"
    assert script_path.is_file()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    dummy_python = bin_dir / "python"
    dummy_python.write_text("#!/bin/bash\nexit 1\n", encoding="utf-8")
    dummy_python.chmod(0o755)

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

    fake_cli = bin_dir / "fake-wx-cli-upload"
    fake_cli.write_text(
        dedent(
            """\
            #!/bin/bash
            set -euo pipefail

            command="$1"
            shift
            if [[ "$command" != "upload" ]]; then
              echo "unexpected command: $command" >&2
              exit 2
            fi
            if [[ "${1:-}" == "--help" ]]; then
              cat <<'EOF'
Upload mini program
  --upload-version  Version number
  --upload-desc     Description of the uploaded version
EOF
              exit 0
            fi

            version=""
            port=""
            while [[ $# -gt 0 ]]; do
              if [[ "$1" == "--upload-version" ]]; then
                version="$2"
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

            if [[ -z "$version" ]]; then
              echo "missing --upload-version" >&2
              exit 2
            fi
            if [[ -z "$port" ]]; then
              echo "missing --port" >&2
              exit 2
            fi

            if [[ -n "${FAKE_WX_CLI_PORT_FILE:-}" ]]; then
              printf '%s' "$port" > "$FAKE_WX_CLI_PORT_FILE"
            fi
            if [[ -n "${FAKE_WX_CLI_VERSION_FILE:-}" ]]; then
              printf '%s' "$version" > "$FAKE_WX_CLI_VERSION_FILE"
            fi
            echo "uploaded version: $version"
            """
        ),
        encoding="utf-8",
    )
    fake_cli.chmod(0o755)

    project_root = tmp_path / "mini"
    project_root.mkdir()
    (project_root / "app.json").write_text("{}", encoding="utf-8")
    (project_root / "project.config.json").write_text(
        json.dumps({"appid": "wx_APPID_REDACTED"}, ensure_ascii=False),
        encoding="utf-8",
    )

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

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["CLI_BIN"] = str(fake_cli)
    env["PROJECT_NAME"] = "hyphamall"
    env["PROJECT_PATH"] = str(project_root)
    env["PROJECT_BASE"] = str(project_root)
    env["WX_DEVTOOLS_PORTS_FILE"] = str(ports_file)
    env["VERSION"] = "202602140001"
    port_capture_file = tmp_path / "port.txt"
    version_capture_file = tmp_path / "version.txt"
    env["FAKE_WX_CLI_PORT_FILE"] = str(port_capture_file)
    env["FAKE_WX_CLI_VERSION_FILE"] = str(version_capture_file)
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
    assert port_capture_file.is_file()
    assert version_capture_file.is_file()
    assert port_capture_file.read_text(encoding="utf-8") == "45927"
    assert version_capture_file.read_text(encoding="utf-8") == "202602140001"
    assert "UPLOAD_VERSION: 202602140001" in stdout
    assert "UPLOAD_APPID: wx_APPID_REDACTED" in stdout


@pytest.mark.skipif(os.name != "posix", reason="wx-dev-upload 脚本依赖 bash/Posix 环境")
def test_gen_upload_supports_new_cli_flags_and_info_output(tmp_path: Path) -> None:
    """确保脚本可兼容新版 upload 参数，并使用 info-output 校验版本与 AppID。"""

    bash_bin = shutil.which("bash")
    if bash_bin is None:
        pytest.skip("未检测到 bash")

    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "gen_upload.sh"
    assert script_path.is_file()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    fake_cli = bin_dir / "fake-wx-cli-upload-new"
    fake_cli.write_text(
        dedent(
            """\
            #!/bin/bash
            set -euo pipefail

            command="$1"
            shift
            if [[ "$command" != "upload" ]]; then
              echo "unexpected command: $command" >&2
              exit 2
            fi
            if [[ "${1:-}" == "--help" ]]; then
              cat <<'EOF'
Upload mini program
  --version      Version number
  --desc         Description of the uploaded version
  --info-output  write upload result into file
EOF
              exit 0
            fi

            version=""
            desc=""
            port=""
            info_output=""
            while [[ $# -gt 0 ]]; do
              case "$1" in
                --version)
                  version="$2"
                  shift 2
                  ;;
                --desc)
                  desc="$2"
                  shift 2
                  ;;
                --port)
                  port="$2"
                  shift 2
                  ;;
                --info-output)
                  info_output="$2"
                  shift 2
                  ;;
                *)
                  shift
                  ;;
              esac
            done

            if [[ -z "$version" || -z "$desc" || -z "$port" || -z "$info_output" ]]; then
              echo "missing required parameters" >&2
              exit 2
            fi
            cat >"$info_output" <<EOF
{"version":"$version","appid":"wx_APPID_REDACTED","desc":"$desc"}
EOF
            """
        ),
        encoding="utf-8",
    )
    fake_cli.chmod(0o755)

    project_root = tmp_path / "mini"
    project_root.mkdir()
    (project_root / "app.json").write_text("{}", encoding="utf-8")
    (project_root / "project.config.json").write_text(
        json.dumps({"appid": "wx_APPID_REDACTED"}, ensure_ascii=False),
        encoding="utf-8",
    )

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

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["CLI_BIN"] = str(fake_cli)
    env["PROJECT_NAME"] = "hyphamall"
    env["PROJECT_PATH"] = str(project_root)
    env["PROJECT_BASE"] = str(project_root)
    env["WX_DEVTOOLS_PORTS_FILE"] = str(ports_file)
    env["VERSION"] = "202602140002"
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
    assert "UPLOAD_VERSION: 202602140002" in stdout
    assert "UPLOAD_APPID: wx_APPID_REDACTED" in stdout
    assert "upload 参数探测：版本参数=--version，描述参数=--desc，支持 --info-output=1" in stderr


@pytest.mark.skipif(os.name != "posix", reason="wx-dev-upload 脚本依赖 bash/Posix 环境")
def test_gen_upload_fails_when_reported_version_mismatches(tmp_path: Path) -> None:
    """确保上传返回的版本号与命令参数不一致时，脚本返回失败。"""

    bash_bin = shutil.which("bash")
    if bash_bin is None:
        pytest.skip("未检测到 bash")

    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "gen_upload.sh"
    assert script_path.is_file()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    fake_cli = bin_dir / "fake-wx-cli-upload-mismatch-version"
    fake_cli.write_text(
        dedent(
            """\
            #!/bin/bash
            set -euo pipefail

            command="$1"
            shift
            if [[ "$command" != "upload" ]]; then
              echo "unexpected command: $command" >&2
              exit 2
            fi
            if [[ "${1:-}" == "--help" ]]; then
              cat <<'EOF'
Upload mini program
  --version      Version number
  --desc         Description of the uploaded version
  --info-output  write upload result into file
EOF
              exit 0
            fi
            info_output=""
            while [[ $# -gt 0 ]]; do
              if [[ "$1" == "--info-output" ]]; then
                info_output="$2"
                shift 2
                continue
              fi
              shift
            done
            cat >"$info_output" <<EOF
{"version":"202602140003-MISMATCH","appid":"wx_APPID_REDACTED"}
EOF
            exit 0
            """
        ),
        encoding="utf-8",
    )
    fake_cli.chmod(0o755)

    project_root = tmp_path / "mini"
    project_root.mkdir()
    (project_root / "app.json").write_text("{}", encoding="utf-8")
    (project_root / "project.config.json").write_text(
        json.dumps({"appid": "wx_APPID_REDACTED"}, ensure_ascii=False),
        encoding="utf-8",
    )

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

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["CLI_BIN"] = str(fake_cli)
    env["PROJECT_NAME"] = "hyphamall"
    env["PROJECT_PATH"] = str(project_root)
    env["PROJECT_BASE"] = str(project_root)
    env["WX_DEVTOOLS_PORTS_FILE"] = str(ports_file)
    env["VERSION"] = "202602140003"
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
    assert proc.returncode == 4, f"stdout:\n{stdout}\n\nstderr:\n{stderr}"
    assert "版本号不一致" in stderr
