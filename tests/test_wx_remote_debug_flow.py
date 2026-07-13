import hashlib
import json
import os
import signal
import shutil
import subprocess
import time
from pathlib import Path
from textwrap import dedent

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TRIGGER_PATH = REPO_ROOT / "vibego_cli" / "data" / "wx-remote-debug" / "trigger.cjs"
LOCK_PATH = TRIGGER_PATH.with_name("package-lock.json")


def _require_node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("未检测到 node")
    return node


def _write_fake_cli(path: Path, args_file: Path) -> None:
    path.write_text(
        dedent(
            f"""\
            #!/bin/bash
            set -euo pipefail
            printf '%s\\n' "$@" > {shlex_quote(str(args_file))}
            if [[ -n "${{FAKE_CLI_PID_FILE:-}}" ]]; then
              printf '%s' "$$" > "$FAKE_CLI_PID_FILE"
            fi
            auto_port=""
            previous=""
            for argument in "$@"; do
              if [[ "$previous" == "--auto-port" ]]; then
                auto_port="$argument"
              fi
              previous="$argument"
            done
            if [[ -n "${{FAKE_CLI_PORTS_FILE:-}}" ]]; then
              printf '%s\\n' "$auto_port" >> "$FAKE_CLI_PORTS_FILE"
            fi
            if [[ -n "${{FAKE_CLI_READY_FILE:-}}" ]]; then
              printf '%s\\n' "$auto_port" >> "$FAKE_CLI_READY_FILE"
            fi
            if [[ -n "${{FAKE_CLI_CONFLICT_ONCE_FILE:-}}" ]]; then
              attempt=0
              if [[ -f "$FAKE_CLI_CONFLICT_ONCE_FILE" ]]; then
                attempt="$(cat "$FAKE_CLI_CONFLICT_ONCE_FILE")"
              fi
              attempt="$((attempt + 1))"
              printf '%s' "$attempt" > "$FAKE_CLI_CONFLICT_ONCE_FILE"
              if [[ "$attempt" == "1" ]]; then
                printf 'Port %s is in use\\n' "$auto_port" >&2
                exit 1
              fi
            fi
            if [[ "${{FAKE_CLI_HANG:-0}}" == "1" ]]; then
              trap 'printf terminated > "$FAKE_CLI_TERMINATED_FILE"; exit 0' TERM INT
              while true; do sleep 1; done
            fi
            exit 0
            """
        ),
        encoding="utf-8",
    )
    path.chmod(0o755)


def shlex_quote(value: str) -> str:
    import shlex

    return shlex.quote(value)


def _write_fake_automator(path: Path) -> None:
    path.write_text(
        dedent(
            """\
            const fs = require('fs');
            function event(name, payload) {
              fs.appendFileSync(process.env.FAKE_AUTOMATOR_EVENTS, JSON.stringify([name, payload]) + '\\n');
            }
            module.exports = {
              async connect(options) {
                event('connect', options);
                if (process.env.FAKE_CLI_READY_FILE) {
                  const ready = fs.existsSync(process.env.FAKE_CLI_READY_FILE)
                    ? fs.readFileSync(process.env.FAKE_CLI_READY_FILE, 'utf8').split(/\\r?\\n/)
                    : [];
                  const port = new URL(options.wsEndpoint).port;
                  if (!ready.includes(port)) throw new Error('cli not ready');
                }
                return {
                  async remote(auto) {
                    event('remote', auto);
                    if (process.env.FAKE_AUTOMATOR_MODE === 'timeout') {
                      return new Promise(() => {});
                    }
                    if (process.env.FAKE_AUTOMATOR_MODE === 'remote-error') {
                      throw new Error('remote failed');
                    }
                  },
                  async systemInfo() {
                    event('systemInfo', null);
                    if (process.env.FAKE_AUTOMATOR_MODE === 'empty-system-info') return {};
                    return { platform: 'ios', system: 'iOS 18.5', model: 'iPhone' };
                  },
                  disconnect() {
                    event('disconnect', null);
                  },
                };
              },
            };
            """
        ),
        encoding="utf-8",
    )


def _write_fake_npm(path: Path, automator_source: Path, count_file: Path) -> None:
    path.write_text(
        dedent(
            f"""\
            #!/bin/bash
            set -euo pipefail
            count=0
            if [[ -f {shlex_quote(str(count_file))} ]]; then
              count="$(cat {shlex_quote(str(count_file))})"
            fi
            printf '%s' "$((count + 1))" > {shlex_quote(str(count_file))}
            mkdir -p node_modules/miniprogram-automator
            cp {shlex_quote(str(automator_source))} node_modules/miniprogram-automator/index.js
            exit 0
            """
        ),
        encoding="utf-8",
    )
    path.chmod(0o755)


def _base_fixture(tmp_path: Path) -> tuple[dict[str, str], Path, Path, Path]:
    assert TRIGGER_PATH.is_file(), f"缺少 Node 执行器：{TRIGGER_PATH}"
    assert LOCK_PATH.is_file(), f"缺少依赖锁文件：{LOCK_PATH}"

    project = tmp_path / "mini"
    project.mkdir()
    (project / "app.json").write_text("{}", encoding="utf-8")
    events = tmp_path / "events.jsonl"
    cli_args = tmp_path / "cli-args.txt"
    fake_cli = tmp_path / "fake-cli"
    _write_fake_cli(fake_cli, cli_args)
    automator_source = tmp_path / "fake-automator.cjs"
    _write_fake_automator(automator_source)
    npm_count = tmp_path / "npm-count.txt"
    cli_ready = tmp_path / "cli-ready-ports.txt"
    fake_npm = tmp_path / "fake-npm"
    _write_fake_npm(fake_npm, automator_source, npm_count)

    env = os.environ.copy()
    env.pop("MASTER_CONFIG_ROOT", None)
    env.pop("VIBEGO_RUNTIME_ROOT", None)
    env.pop("XDG_CONFIG_HOME", None)
    env.update(
        {
            "VIBEGO_CONFIG_DIR": str(tmp_path / "config"),
            "NPM_BIN": str(fake_npm),
            "FAKE_AUTOMATOR_EVENTS": str(events),
            "FAKE_CLI_READY_FILE": str(cli_ready),
            "WX_AUTOMATION_PORT": "19425",
            "WX_REMOTE_DEBUG_CLI_TIMEOUT_MS": "3000",
            "WX_REMOTE_DEBUG_CONNECT_TIMEOUT_MS": "1000",
            "WX_REMOTE_DEBUG_PROBE_TIMEOUT_MS": "1000",
            "WX_REMOTE_DEBUG_INSTALL_TIMEOUT_MS": "3000",
        }
    )
    return env, project, fake_cli, npm_count


def _run_trigger(env: dict[str, str], project: Path, fake_cli: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            _require_node(),
            str(TRIGGER_PATH),
            "--project",
            str(project),
            "--ide-port",
            "45927",
            "--cli",
            str(fake_cli),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
    )


def _events(path: Path) -> list[list[object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def test_trigger_uses_cli_auto_remote_true_and_runtime_probe(tmp_path: Path) -> None:
    """执行器必须使用公开 auto/connect/remote(true) 链路并以运行时信息收口。"""

    env, project, fake_cli, _npm_count = _base_fixture(tmp_path)
    proc = _run_trigger(env, project, fake_cli)
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"

    result_line = next(line for line in proc.stdout.splitlines() if line.startswith("VIBEGO_WX_REMOTE_DEBUG_RESULT:"))
    result = json.loads(result_line.split(":", 1)[1])
    assert result["status"] == "success"
    assert result["platform"] == "ios"
    assert result["system"] == "iOS 18.5"
    assert result["connectionEvidence"] == "Tool.onRemoteDebugConnected"

    cli_args_path = tmp_path / "cli-args.txt"
    deadline = time.monotonic() + 1.0
    while not cli_args_path.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    cli_args = cli_args_path.read_text(encoding="utf-8").splitlines()
    assert cli_args == [
        "auto",
        "--project",
        str(project.resolve()),
        "--auto-port",
        "19425",
        "--port",
        "45927",
    ]
    events = _events(tmp_path / "events.jsonl")
    assert events[0] == ["connect", {"wsEndpoint": "ws://127.0.0.1:19425"}]
    assert ["remote", True] in events
    assert ["systemInfo", None] in events
    assert events[-1] == ["disconnect", None]


def test_trigger_fails_when_runtime_probe_has_no_device_identity(tmp_path: Path) -> None:
    """remote(true) 返回但 systemInfo 缺少平台/系统时必须失败并断开连接。"""

    env, project, fake_cli, _npm_count = _base_fixture(tmp_path)
    env["FAKE_AUTOMATOR_MODE"] = "empty-system-info"
    proc = _run_trigger(env, project, fake_cli)
    assert proc.returncode != 0
    assert "运行时探测" in proc.stderr
    assert "VIBEGO_WX_REMOTE_DEBUG_RESULT:" not in proc.stdout
    assert _events(tmp_path / "events.jsonl")[-1] == ["disconnect", None]


def test_trigger_times_out_remote_connection_and_disconnects(tmp_path: Path) -> None:
    """连接事件未在预算内发生时必须失败，且只释放 automation 连接。"""

    env, project, fake_cli, _npm_count = _base_fixture(tmp_path)
    env["FAKE_AUTOMATOR_MODE"] = "timeout"
    env["WX_REMOTE_DEBUG_CONNECT_TIMEOUT_MS"] = "50"
    proc = _run_trigger(env, project, fake_cli)
    assert proc.returncode != 0
    assert "真机连接超时" in proc.stderr
    assert "开发者工具可能仍停留在准备态，可手动关闭" in proc.stderr
    events = _events(tmp_path / "events.jsonl")
    assert events[-1] == ["disconnect", None]
    assert all(name not in {"close", "quit"} for name, _payload in events)


def test_trigger_failure_terminates_its_long_running_cli_child(tmp_path: Path) -> None:
    """失败清理必须终止本次启动的 cli auto 子进程，不能遗留孤儿。"""

    env, project, fake_cli, _npm_count = _base_fixture(tmp_path)
    pid_file = tmp_path / "cli.pid"
    terminated_file = tmp_path / "cli-terminated.txt"
    env.update(
        {
            "FAKE_AUTOMATOR_MODE": "empty-system-info",
            "FAKE_CLI_HANG": "1",
            "FAKE_CLI_PID_FILE": str(pid_file),
            "FAKE_CLI_TERMINATED_FILE": str(terminated_file),
        }
    )
    proc = _run_trigger(env, project, fake_cli)
    assert proc.returncode != 0
    deadline = time.monotonic() + 2.0
    while not pid_file.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    pid = int(pid_file.read_text(encoding="utf-8"))
    try:
        while _process_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not _process_alive(pid)
        assert terminated_file.read_text(encoding="utf-8") == "terminated"
    finally:
        if _process_alive(pid):
            os.kill(pid, signal.SIGKILL)


def test_trigger_retries_once_with_new_dynamic_port_after_cli_conflict(tmp_path: Path) -> None:
    """动态 WebSocket 端口在 CLI 启动阶段冲突时应换端口重试一次。"""

    env, project, fake_cli, _npm_count = _base_fixture(tmp_path)
    ports_file = tmp_path / "cli-ports.txt"
    attempt_file = tmp_path / "cli-attempts.txt"
    env.pop("WX_AUTOMATION_PORT")
    env["FAKE_CLI_PORTS_FILE"] = str(ports_file)
    env["FAKE_CLI_CONFLICT_ONCE_FILE"] = str(attempt_file)
    proc = _run_trigger(env, project, fake_cli)
    assert proc.returncode == 0, proc.stderr
    ports = ports_file.read_text(encoding="utf-8").splitlines()
    assert ports == ["19420", "19421"]
    assert attempt_file.read_text(encoding="utf-8") == "2"
    connect_events = [payload for name, payload in _events(tmp_path / "events.jsonl") if name == "connect"]
    assert connect_events[-1] == {"wsEndpoint": "ws://127.0.0.1:19421"}


def test_trigger_installs_locked_dependency_once_then_reuses_cache(tmp_path: Path) -> None:
    """首次运行原子安装锁定依赖，后续运行应复用同一缓存。"""

    env, project, fake_cli, npm_count = _base_fixture(tmp_path)
    first = _run_trigger(env, project, fake_cli)
    second = _run_trigger(env, project, fake_cli)
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert npm_count.read_text(encoding="utf-8") == "1"

    lock_hash = hashlib.sha256(LOCK_PATH.read_bytes()).hexdigest()
    runtime_dir = Path(env["VIBEGO_CONFIG_DIR"]) / "runtime" / "wx-remote-debug" / "0.12.1"
    assert (runtime_dir / ".package-lock.sha256").read_text(encoding="utf-8").strip() == lock_hash


def test_trigger_missing_npm_fails_closed_before_remote_debug(tmp_path: Path) -> None:
    """首次安装时 npm 不可用必须直接失败，不能尝试连接手机。"""

    env, project, fake_cli, _npm_count = _base_fixture(tmp_path)
    env["NPM_BIN"] = str(tmp_path / "missing-npm")
    proc = _run_trigger(env, project, fake_cli)
    assert proc.returncode != 0
    assert "npm" in proc.stderr.lower()
    assert not (tmp_path / "events.jsonl").exists()


def test_trigger_rejects_concurrent_run_for_same_project(tmp_path: Path) -> None:
    """同一项目已有活跃任务锁时应立即失败，不排队或重启 IDE。"""

    env, project, fake_cli, npm_count = _base_fixture(tmp_path)
    project_hash = hashlib.sha256(str(project.resolve()).encode("utf-8")).hexdigest()
    lock_dir = (
        Path(env["VIBEGO_CONFIG_DIR"])
        / "locks"
        / "wx-remote-debug"
        / "projects"
        / project_hash
    )
    lock_dir.mkdir(parents=True)
    (lock_dir / "owner.json").write_text(
        json.dumps({"pid": os.getpid(), "createdAt": "2026-07-13T00:00:00Z"}),
        encoding="utf-8",
    )
    proc = _run_trigger(env, project, fake_cli)
    assert proc.returncode != 0
    assert "已有自动真机调试任务" in proc.stderr
    assert not (tmp_path / "events.jsonl").exists()
    assert not npm_count.exists(), "项目并发锁必须早于共享依赖安装，第二次触发应立即失败"


def test_trigger_recovers_stale_dependency_install_lock(tmp_path: Path) -> None:
    """依赖安装进程异常退出遗留的死 PID 锁应自动回收。"""

    env, project, fake_cli, _npm_count = _base_fixture(tmp_path)
    install_lock = (
        Path(env["VIBEGO_CONFIG_DIR"])
        / "runtime"
        / "wx-remote-debug"
        / ".install-0.12.1.lock"
    )
    install_lock.mkdir(parents=True)
    (install_lock / "owner.json").write_text(
        json.dumps({"pid": 999_999_999, "createdAt": "2026-07-13T00:00:00Z"}),
        encoding="utf-8",
    )
    proc = _run_trigger(env, project, fake_cli)
    assert proc.returncode == 0, proc.stderr
    assert not install_lock.exists()


def test_trigger_honors_master_config_and_runtime_roots_with_tilde(tmp_path: Path) -> None:
    """Node 运行时应与 worker 配置根优先级一致，并支持独立 runtime root。"""

    env, project, fake_cli, _npm_count = _base_fixture(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    env.pop("VIBEGO_CONFIG_DIR")
    env["HOME"] = str(home)
    env["MASTER_CONFIG_ROOT"] = "~/vibego-config-custom"
    env["VIBEGO_RUNTIME_ROOT"] = "~/vibego-runtime-custom"
    proc = _run_trigger(env, project, fake_cli)
    assert proc.returncode == 0, proc.stderr
    runtime_dir = home / "vibego-runtime-custom" / "wx-remote-debug" / "0.12.1"
    assert (runtime_dir / "node_modules" / "miniprogram-automator" / "index.js").is_file()
    assert not (home / ".config" / "vibego" / "runtime" / "wx-remote-debug").exists()


def test_trigger_source_never_closes_devtools_or_miniprogram() -> None:
    """自动真机调试清理只允许 disconnect，不得关闭用户 IDE。"""

    source = TRIGGER_PATH.read_text(encoding="utf-8")
    assert "miniProgram.disconnect()" in source
    assert "miniProgram.close(" not in source
    assert "'close'" not in source
    assert "'quit'" not in source


def test_trigger_rejects_node_older_than_16_before_processing_arguments() -> None:
    """package engines 之外还必须由执行器显式阻断旧 Node。"""

    proc = subprocess.run(
        [
            _require_node(),
            "-e",
            "Object.defineProperty(process.versions, 'node', {value: '14.21.3'}); require(process.argv[1]);",
            str(TRIGGER_PATH),
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode != 0
    assert "Node.js >=16" in proc.stderr


def test_wx_remote_debug_assets_are_configured_for_distribution() -> None:
    """wheel/sdist 配置必须显式包含 Node 执行器与锁定依赖清单。"""

    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    manifest = (REPO_ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    assert "data/wx-remote-debug/*.cjs" in pyproject
    assert "data/wx-remote-debug/*.json" in pyproject
    assert "recursive-include vibego_cli/data/wx-remote-debug" in manifest
    package = json.loads(TRIGGER_PATH.with_name("package.json").read_text(encoding="utf-8"))
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    assert package["dependencies"] == {"miniprogram-automator": "0.12.1"}
    assert package["engines"] == {"node": ">=16"}
    assert lock["packages"]["node_modules/miniprogram-automator"]["version"] == "0.12.1"
