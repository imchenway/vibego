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


@pytest.mark.parametrize("script_name", ["gen_preview.sh", "gen_upload.sh"])
def test_wx_scripts_do_not_require_bash4_associative_arrays(script_name: str) -> None:
    """微信脚本必须兼容 macOS 默认 Bash 3.2，不能依赖 Bash 4 关联数组。"""

    repo_root = Path(__file__).resolve().parents[1]
    script_text = (repo_root / "scripts" / script_name).read_text(encoding="utf-8")

    for declaration in ("declare -A", "local -A", "typeset -A"):
        assert declaration not in script_text


@pytest.mark.skipif(os.name != "posix", reason="微信脚本自动探测依赖 bash/Posix 环境")
@pytest.mark.parametrize(
    ("script_name", "expected_command"),
    [
        ("gen_preview.sh", "preview"),
        ("gen_upload.sh", "upload"),
    ],
)
@pytest.mark.parametrize(
    "selection_rule",
    [
        "fawn_shortest_and_deduplicated",
        "explicit_project_path",
        "project_hint",
        "equal_length_first_candidate",
    ],
)
def test_wx_scripts_preserve_project_selection_rules(
    tmp_path: Path,
    script_name: str,
    expected_command: str,
    selection_rule: str,
) -> None:
    """Bash 3.2 兼容改动不得改变显式路径、hint、最短路径和首次顺序。"""

    if sys.platform == "darwin" and Path("/bin/bash").is_file():
        bash_bin = "/bin/bash"
        bash_version = subprocess.run(
            [bash_bin, "--version"],
            check=False,
            capture_output=True,
            text=True,
        ).stdout
        assert "version 3.2" in bash_version
    else:
        bash_bin = shutil.which("bash")
    if bash_bin is None:
        pytest.skip("未检测到 bash")

    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / script_name
    assert script_path.is_file()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_cli = bin_dir / "fake-wx-cli-auto-detect"
    fake_cli.write_text(
        dedent(
            """\
            #!/bin/bash
            set -eu

            calls_file="${FAKE_WX_CLI_CALLS_FILE:?}"
            {
              printf '%s\n' '---'
              printf '%s\n' "$@"
            } >> "$calls_file"
            exit 37
            """
        ),
        encoding="utf-8",
    )
    fake_cli.chmod(0o755)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    duplicate_candidate: Path | None = None

    if selection_rule == "fawn_shortest_and_deduplicated":
        project_root = workspace / "frontend-mini"
        miniprogram_root = project_root / "miniprogram"
        miniprogram_root.mkdir(parents=True)
        (miniprogram_root / "app.json").write_text("{}", encoding="utf-8")
        (project_root / "project.config.json").write_text(
            json.dumps(
                {
                    "miniprogramRoot": "miniprogram",
                    "appid": "wx_APPID_REDACTED",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        duplicate_candidate = miniprogram_root
    elif selection_rule == "explicit_project_path":
        auto_candidate = workspace / "auto-candidate"
        auto_candidate.mkdir()
        (auto_candidate / "app.json").write_text("{}", encoding="utf-8")
        project_root = tmp_path / "explicit-project"
        project_root.mkdir()
        (project_root / "app.json").write_text("{}", encoding="utf-8")
    elif selection_rule == "project_hint":
        short_candidate = workspace / "a"
        short_candidate.mkdir()
        (short_candidate / "app.json").write_text("{}", encoding="utf-8")
        project_root = workspace / "long-hint-target"
        project_root.mkdir()
        (project_root / "app.json").write_text("{}", encoding="utf-8")
    else:
        project_root = workspace / "bb"
        second_candidate = workspace / "aa"
        project_root.mkdir()
        second_candidate.mkdir()
        first_app_json = project_root / "app.json"
        second_app_json = second_candidate / "app.json"
        first_app_json.write_text("{}", encoding="utf-8")
        second_app_json.write_text("{}", encoding="utf-8")
        fake_rg = bin_dir / "rg"
        fake_rg.write_text(
            dedent(
                """\
                #!/bin/bash
                set -eu
                case "$*" in
                  *"-g app.json"*) printf '%s\n' "${FAKE_RG_APP_FILES:?}" ;;
                  *"-g project.config.json"*) ;;
                  *) exit 2 ;;
                esac
                """
            ),
            encoding="utf-8",
        )
        fake_rg.chmod(0o755)

    calls_file = tmp_path / f"{script_name}.calls"
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["CLI_BIN"] = str(fake_cli)
    env["PROJECT_BASE"] = str(workspace)
    env["PORT"] = "45459"
    env["OUTPUT_QR"] = str(tmp_path / "preview.jpg")
    env["UPLOAD_RETRY_ON_FAIL"] = "0"
    env["FAKE_WX_CLI_CALLS_FILE"] = str(calls_file)
    env.pop("PROJECT_PATH", None)
    env.pop("PROJECT_HINT", None)
    if selection_rule == "explicit_project_path":
        env["PROJECT_PATH"] = str(project_root)
    elif selection_rule == "project_hint":
        env["PROJECT_HINT"] = "hint-target"
    elif selection_rule == "equal_length_first_candidate":
        env["FAKE_RG_APP_FILES"] = f"{project_root / 'app.json'}\n{second_candidate / 'app.json'}"

    proc = subprocess.run(
        [bash_bin, str(script_path)],
        check=False,
        capture_output=True,
        env=env,
        cwd=str(workspace),
    )
    stderr = (proc.stderr or b"").decode("utf-8", errors="replace")

    assert calls_file.is_file(), f"exit={proc.returncode}\nstderr:\n{stderr}"
    calls_text = calls_file.read_text(encoding="utf-8")
    assert expected_command in calls_text.splitlines()
    assert f"--project\n{project_root}" in calls_text
    assert "declare: -A" not in stderr
    assert "operand expected" not in stderr
    if duplicate_candidate is not None:
        assert stderr.count(str(duplicate_candidate)) == 1


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


def _run_gen_preview_port_drift_case(
    tmp_path: Path,
    *,
    behavior: str,
    make_config_unwritable: bool = False,
) -> tuple[subprocess.CompletedProcess[bytes], Path, Path, Path]:
    bash_bin = shutil.which("bash")
    if bash_bin is None:
        pytest.skip("未检测到 bash")

    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "gen_preview.sh"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls_file = tmp_path / "calls.txt"

    fake_cli = bin_dir / "fake-wx-cli-port-drift"
    fake_cli.write_text(
        dedent(
            """\
            #!/bin/bash
            set -euo pipefail

            output=""
            port=""
            while [[ $# -gt 0 ]]; do
              case "$1" in
                --qr-output)
                  output="$2"
                  shift 2
                  ;;
                --port)
                  port="$2"
                  shift 2
                  ;;
                *)
                  shift
                  ;;
              esac
            done
            printf '%s\n' "$port" >> "${FAKE_WX_CLI_CALLS_FILE:?}"

            case "${FAKE_WX_CLI_BEHAVIOR:?}" in
              success_after_mismatch)
                if [[ "$port" == "39198" ]]; then
                  echo "✖ IDE server has started on http://127.0.0.1:11620 and must be restarted on port 39198 first" >&2
                  exit 255
                fi
                ;;
              always_mismatch)
                echo "✖ IDE server has started on http://127.0.0.1:11620 and must be restarted on port ${port} first" >&2
                exit 255
                ;;
              unrecognized)
                echo "initialize failed for unknown reason" >&2
                exit 255
                ;;
              invalid_mismatch)
                echo "IDE server has started on http://127.0.0.1:70000 and must be restarted on port 39198 first" >&2
                exit 255
                ;;
              different_requested_port)
                echo "IDE server has started on http://127.0.0.1:11620 and must be restarted on port 49011 first" >&2
                exit 255
                ;;
            esac

            mkdir -p "$(dirname "$output")"
            printf 'fake-jpg' > "$output"
            """
        ),
        encoding="utf-8",
    )
    fake_cli.chmod(0o755)

    project_root = tmp_path / "mini"
    project_root.mkdir()
    (project_root / "app.json").write_text("{}", encoding="utf-8")
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    ports_file = config_dir / "wx_devtools_ports.json"
    ports_file.write_text(
        json.dumps(
            {
                "projects": {"fawnstudio": 39198, "unrelated": 45462},
                "paths": {str(project_root.resolve()): 39198, "/tmp/unrelated": 45462},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    output_qr = tmp_path / "output" / "qr.jpg"

    env = os.environ.copy()
    env["CLI_BIN"] = str(fake_cli)
    env["PYTHON_BIN"] = sys.executable
    env["PROJECT_PATH"] = str(project_root)
    env["PROJECT_BASE"] = str(project_root)
    env["OUTPUT_QR"] = str(output_qr)
    env["WX_DEVTOOLS_PORTS_FILE"] = str(ports_file)
    env["FAKE_WX_CLI_CALLS_FILE"] = str(calls_file)
    env["FAKE_WX_CLI_BEHAVIOR"] = behavior
    env.pop("PORT", None)
    env.pop("PROJECT_NAME", None)
    env.pop("PROJECT_SLUG", None)

    if make_config_unwritable:
        config_dir.chmod(0o500)
    try:
        proc = subprocess.run(
            [bash_bin, str(script_path)],
            check=False,
            capture_output=True,
            env=env,
            cwd=str(tmp_path),
        )
    finally:
        if make_config_unwritable:
            config_dir.chmod(0o700)
    return proc, calls_file, ports_file, output_qr


@pytest.mark.skipif(os.name != "posix", reason="wx-dev-preview 脚本依赖 bash/Posix 环境")
def test_gen_preview_recovers_from_port_drift_once_and_updates_mapping(tmp_path: Path) -> None:
    proc, calls_file, ports_file, output_qr = _run_gen_preview_port_drift_case(
        tmp_path,
        behavior="success_after_mismatch",
    )
    stdout = (proc.stdout or b"").decode("utf-8", errors="replace")
    stderr = (proc.stderr or b"").decode("utf-8", errors="replace")

    assert proc.returncode == 0, f"stdout:\n{stdout}\n\nstderr:\n{stderr}"
    assert calls_file.read_text(encoding="utf-8").splitlines() == ["39198", "11620"]
    assert output_qr.is_file()
    assert "VIBEGO_WX_PORT_RETRY_USED=1" in stderr
    mapping = json.loads(ports_file.read_text(encoding="utf-8"))
    assert mapping["paths"][str((tmp_path / "mini").resolve())] == 11620
    assert mapping["projects"]["fawnstudio"] == 11620
    assert mapping["projects"]["unrelated"] == 45462
    assert mapping["paths"]["/tmp/unrelated"] == 45462


@pytest.mark.skipif(os.name != "posix", reason="wx-dev-preview 脚本依赖 bash/Posix 环境")
def test_gen_preview_port_drift_retry_is_limited_to_one(tmp_path: Path) -> None:
    proc, calls_file, _ports_file, output_qr = _run_gen_preview_port_drift_case(
        tmp_path,
        behavior="always_mismatch",
    )
    stderr = (proc.stderr or b"").decode("utf-8", errors="replace")

    assert proc.returncode == 255
    assert calls_file.read_text(encoding="utf-8").splitlines() == ["39198", "11620"]
    assert not output_qr.exists()
    assert stderr.count("VIBEGO_WX_PORT_RETRY_USED=1") == 1


@pytest.mark.skipif(os.name != "posix", reason="wx-dev-preview 脚本依赖 bash/Posix 环境")
@pytest.mark.parametrize("behavior", ["unrecognized", "invalid_mismatch", "different_requested_port"])
def test_gen_preview_does_not_retry_untrusted_cli_error(tmp_path: Path, behavior: str) -> None:
    proc, calls_file, ports_file, output_qr = _run_gen_preview_port_drift_case(
        tmp_path,
        behavior=behavior,
    )

    assert proc.returncode == 255
    assert calls_file.read_text(encoding="utf-8").splitlines() == ["39198"]
    assert not output_qr.exists()
    mapping = json.loads(ports_file.read_text(encoding="utf-8"))
    assert mapping["projects"]["fawnstudio"] == 39198


@pytest.mark.skipif(os.name != "posix", reason="wx-dev-preview 脚本依赖 bash/Posix 环境")
def test_gen_preview_retries_when_port_mapping_write_fails(tmp_path: Path) -> None:
    proc, calls_file, ports_file, output_qr = _run_gen_preview_port_drift_case(
        tmp_path,
        behavior="success_after_mismatch",
        make_config_unwritable=True,
    )
    stdout = (proc.stdout or b"").decode("utf-8", errors="replace")
    stderr = (proc.stderr or b"").decode("utf-8", errors="replace")

    assert proc.returncode == 0, f"stdout:\n{stdout}\n\nstderr:\n{stderr}"
    assert calls_file.read_text(encoding="utf-8").splitlines() == ["39198", "11620"]
    assert output_qr.is_file()
    assert "端口映射写入失败" in stderr
    mapping = json.loads(ports_file.read_text(encoding="utf-8"))
    assert mapping["projects"]["fawnstudio"] == 39198


@pytest.mark.skipif(os.name != "posix", reason="wx-auto-preview 脚本依赖 bash/Posix 环境")
def test_gen_preview_auto_preview_mode_uses_auto_preview_without_qr(tmp_path: Path) -> None:
    """手机自动预览模式应调用 CLI auto-preview，不生成二维码回传标记。"""

    bash_bin = shutil.which("bash")
    if bash_bin is None:
        pytest.skip("未检测到 bash")

    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "gen_preview.sh"
    assert script_path.is_file()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    fake_cli = bin_dir / "fake-wx-cli-auto-preview"
    fake_cli.write_text(
        dedent(
            """\
            #!/bin/bash
            set -euo pipefail

            command="$1"
            shift

            port=""
            project=""
            info_output=""
            qr_output=""
            while [[ $# -gt 0 ]]; do
              case "$1" in
                --port)
                  port="$2"
                  shift 2
                  ;;
                --project)
                  project="$2"
                  shift 2
                  ;;
                --info-output)
                  info_output="$2"
                  shift 2
                  ;;
                --qr-output)
                  qr_output="$2"
                  shift 2
                  ;;
                --qr-format)
                  shift 2
                  ;;
                *)
                  shift
                  ;;
              esac
            done

            if [[ "$command" == "preview" ]]; then
              if [[ -z "$port" || -z "$project" || -z "$qr_output" ]]; then
                echo "missing preview args" >&2
                exit 4
              fi
              mkdir -p "$(dirname "$qr_output")"
              printf 'fake-qr' > "$qr_output"
              exit 0
            fi

            if [[ "$command" != "auto-preview" ]]; then
              echo "unexpected command: $command" >&2
              exit 2
            fi

            if [[ -z "$port" || -z "$project" || -z "$info_output" ]]; then
              echo "missing auto-preview args" >&2
              exit 4
            fi
            mkdir -p "$(dirname "$info_output")"
            printf '{"ok":true}' > "$info_output"
            if [[ -n "${FAKE_WX_CLI_PORT_FILE:-}" ]]; then
              printf '%s' "$port" > "$FAKE_WX_CLI_PORT_FILE"
            fi
            if [[ -n "${FAKE_WX_CLI_PROJECT_FILE:-}" ]]; then
              printf '%s' "$project" > "$FAKE_WX_CLI_PROJECT_FILE"
            fi
            """
        ),
        encoding="utf-8",
    )
    fake_cli.chmod(0o755)

    project_root = tmp_path / "mini"
    project_root.mkdir()
    (project_root / "app.json").write_text("{}", encoding="utf-8")

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

    port_capture_file = tmp_path / "port.txt"
    project_capture_file = tmp_path / "project.txt"
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["CLI_BIN"] = str(fake_cli)
    env["PROJECT_NAME"] = "hyphamall"
    env["PROJECT_PATH"] = str(project_root)
    env["PROJECT_BASE"] = str(project_root)
    env["WX_DEVTOOLS_PORTS_FILE"] = str(ports_file)
    env["WX_PREVIEW_ACTION"] = "auto-preview"
    env["FAKE_WX_CLI_PORT_FILE"] = str(port_capture_file)
    env["FAKE_WX_CLI_PROJECT_FILE"] = str(project_capture_file)
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
    assert "手机自动预览已触发" in stdout
    assert "TG_PHOTO_FILE" not in stdout
    assert port_capture_file.read_text(encoding="utf-8") == "45927"
    assert project_capture_file.read_text(encoding="utf-8") == str(project_root)


@pytest.mark.skipif(os.name != "posix", reason="wx-auto-preview 脚本依赖 bash/Posix 环境")
def test_gen_preview_auto_preview_fails_when_preview_compile_fails(tmp_path: Path) -> None:
    """自动预览前置编译失败时，不能仅因 auto-preview 可触发就显示成功。"""

    bash_bin = shutil.which("bash")
    if bash_bin is None:
        pytest.skip("未检测到 bash")

    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "gen_preview.sh"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_cli = bin_dir / "fake-wx-cli-auto-preview-fail"
    fake_cli.write_text(
        dedent(
            """\
            #!/bin/bash
            set -euo pipefail
            command="$1"
            shift
            if [[ "$command" == "preview" ]]; then
              echo "Error: wxml 编译错误，Bad attr wx:elif unexpected token ." >&2
              exit 10
            fi
            if [[ "$command" == "auto-preview" ]]; then
              exit 0
            fi
            echo "unexpected command: $command" >&2
            exit 2
            """
        ),
        encoding="utf-8",
    )
    fake_cli.chmod(0o755)

    project_root = tmp_path / "mini"
    project_root.mkdir()
    (project_root / "app.json").write_text("{}", encoding="utf-8")
    ports_file = tmp_path / "wx_devtools_ports.json"
    ports_file.write_text(
        json.dumps({"projects": {"hyphamall": 45927}, "paths": {str(project_root.resolve()): 45927}}),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["CLI_BIN"] = str(fake_cli)
    env["PROJECT_NAME"] = "hyphamall"
    env["PROJECT_PATH"] = str(project_root)
    env["PROJECT_BASE"] = str(project_root)
    env["WX_DEVTOOLS_PORTS_FILE"] = str(ports_file)
    env["WX_PREVIEW_ACTION"] = "auto-preview"
    env.pop("PORT", None)

    proc = subprocess.run([bash_bin, str(script_path)], check=False, capture_output=True, env=env, cwd=str(tmp_path))
    stdout = (proc.stdout or b"").decode("utf-8", errors="replace")
    stderr = (proc.stderr or b"").decode("utf-8", errors="replace")

    assert proc.returncode == 10, f"stdout:\n{stdout}\n\nstderr:\n{stderr}"
    assert "自动预览前置编译校验失败" in stderr
    assert "wxml 编译错误" in stderr
    assert "手机自动预览已触发" not in stdout


@pytest.mark.skipif(os.name != "posix", reason="wx-auto-preview 脚本依赖 bash/Posix 环境")
def test_gen_preview_auto_preview_fails_when_preview_does_not_generate_qr(tmp_path: Path) -> None:
    """preview 返回 0 但未生成校验二维码时，自动预览也必须失败。"""

    bash_bin = shutil.which("bash")
    if bash_bin is None:
        pytest.skip("未检测到 bash")

    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "gen_preview.sh"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_cli = bin_dir / "fake-wx-cli-auto-preview-no-qr"
    fake_cli.write_text(
        dedent(
            """\
            #!/bin/bash
            set -euo pipefail
            command="$1"
            shift
            if [[ "$command" == "preview" ]]; then
              echo "preview returned zero but produced no QR"
              exit 0
            fi
            if [[ "$command" == "auto-preview" ]]; then
              exit 0
            fi
            echo "unexpected command: $command" >&2
            exit 2
            """
        ),
        encoding="utf-8",
    )
    fake_cli.chmod(0o755)

    project_root = tmp_path / "mini"
    project_root.mkdir()
    (project_root / "app.json").write_text("{}", encoding="utf-8")
    ports_file = tmp_path / "wx_devtools_ports.json"
    ports_file.write_text(
        json.dumps({"projects": {"hyphamall": 45927}, "paths": {str(project_root.resolve()): 45927}}),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["CLI_BIN"] = str(fake_cli)
    env["PROJECT_NAME"] = "hyphamall"
    env["PROJECT_PATH"] = str(project_root)
    env["PROJECT_BASE"] = str(project_root)
    env["WX_DEVTOOLS_PORTS_FILE"] = str(ports_file)
    env["WX_PREVIEW_ACTION"] = "auto-preview"
    env.pop("PORT", None)

    proc = subprocess.run([bash_bin, str(script_path)], check=False, capture_output=True, env=env, cwd=str(tmp_path))
    stdout = (proc.stdout or b"").decode("utf-8", errors="replace")
    stderr = (proc.stderr or b"").decode("utf-8", errors="replace")

    assert proc.returncode == 3, f"stdout:\n{stdout}\n\nstderr:\n{stderr}"
    assert "自动预览前置编译校验未生成二维码文件" in stderr
    assert "手机自动预览已触发" not in stdout


@pytest.mark.skipif(os.name != "posix", reason="wx-remote-debug 脚本依赖 bash/Posix 环境")
def test_gen_preview_remote_debug_calls_node_executor_without_qr(tmp_path: Path) -> None:
    """真机调试 action 应复用项目/IDE 端口解析并调用 Node 执行器。"""

    bash_bin = shutil.which("bash")
    if bash_bin is None:
        pytest.skip("未检测到 bash")

    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "gen_preview.sh"
    project_root = tmp_path / "mini"
    project_root.mkdir()
    (project_root / "app.json").write_text("{}", encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_cli = bin_dir / "fake-wx-cli"
    fake_cli.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    fake_cli.chmod(0o755)
    args_file = tmp_path / "node-args.txt"
    fake_node = bin_dir / "fake-node"
    fake_node.write_text(
        dedent(
            """\
            #!/bin/bash
            set -euo pipefail
            printf '%s\n' "$@" > "$FAKE_NODE_ARGS_FILE"
            printf '%s\n' 'VIBEGO_WX_REMOTE_DEBUG_RESULT:{"status":"success","project":"fake","platform":"ios","system":"iOS 18","connectionEvidence":"Tool.onRemoteDebugConnected"}'
            """
        ),
        encoding="utf-8",
    )
    fake_node.chmod(0o755)

    ports_file = tmp_path / "wx_devtools_ports.json"
    ports_file.write_text(
        json.dumps({"paths": {str(project_root.resolve()): 45927}, "projects": {}}),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update(
        {
            "CLI_BIN": str(fake_cli),
            "NODE_BIN": str(fake_node),
            "FAKE_NODE_ARGS_FILE": str(args_file),
            "PROJECT_PATH": str(project_root),
            "PROJECT_BASE": str(project_root),
            "WX_DEVTOOLS_PORTS_FILE": str(ports_file),
            "WX_PREVIEW_ACTION": "remote-debug",
        }
    )
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
    args = args_file.read_text(encoding="utf-8").splitlines()
    assert args[0].endswith("vibego_cli/data/wx-remote-debug/trigger.cjs")
    assert args[args.index("--project") + 1] == str(project_root)
    assert args[args.index("--ide-port") + 1] == "45927"
    assert args[args.index("--cli") + 1] == str(fake_cli)
    assert "VIBEGO_WX_REMOTE_DEBUG_RESULT:" in stdout
    assert "TG_PHOTO_FILE" not in stdout


@pytest.mark.skipif(os.name != "posix", reason="wx-remote-debug 脚本依赖 bash/Posix 环境")
def test_gen_preview_remote_debug_missing_node_fails_closed(tmp_path: Path) -> None:
    """自动真机调试找不到 Node.js 时必须在触发手机前失败。"""

    bash_bin = shutil.which("bash")
    if bash_bin is None:
        pytest.skip("未检测到 bash")
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "gen_preview.sh"
    project_root = tmp_path / "mini"
    project_root.mkdir()
    (project_root / "app.json").write_text("{}", encoding="utf-8")
    fake_cli = tmp_path / "fake-cli"
    fake_cli.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    fake_cli.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "CLI_BIN": str(fake_cli),
            "NODE_BIN": str(tmp_path / "missing-node"),
            "PROJECT_PATH": str(project_root),
            "PROJECT_BASE": str(project_root),
            "WX_PREVIEW_ACTION": "remote-debug",
            "PORT": "45927",
        }
    )
    proc = subprocess.run(
        [bash_bin, str(script_path)],
        check=False,
        capture_output=True,
        env=env,
        cwd=str(tmp_path),
    )
    stderr = (proc.stderr or b"").decode("utf-8", errors="replace")
    assert proc.returncode != 0
    assert "需要 Node.js" in stderr
    assert "VIBEGO_WX_REMOTE_DEBUG_RESULT:" not in (proc.stdout or b"").decode("utf-8", errors="replace")


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
