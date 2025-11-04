"""Entry point and subcommand implementations for the vibego CLI."""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

from . import config
from .deps import (
    check_cli_dependencies,
    install_requirements,
    python_version_ok,
)
from project_repository import ProjectRepository


TOKEN_PATTERN = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{20,}$")
BOTFATHER_URL = "https://core.telegram.org/bots#botfather"


def _find_repo_root() -> Path:
    """Determine the current repository root."""

    return config.PACKAGE_ROOT


def _prompt_token(default: Optional[str] = None) -> str:
    """Interactively read the Telegram Bot token from stdin."""

    prompt = "Enter the Master Bot token"
    if default:
        prompt += " (press Enter to keep the existing value)"
    prompt += ": "
    while True:
        value = input(prompt).strip()
        if not value and default:
            value = default
        if not value:
            print("Token cannot be empty. Follow the official guide if you need to create a bot:", BOTFATHER_URL)
            continue
        if TOKEN_PATTERN.match(value):
            return value
        print("The token format looks invalid. Please verify and retry. Official instructions:", BOTFATHER_URL)


def _ensure_projects_assets() -> None:
    """Ensure ``projects.json`` and ``master.db`` exist and are initialised."""

    config.ensure_directories()
    if not config.PROJECTS_JSON.exists():
        config.PROJECTS_JSON.write_text("[]\n", encoding="utf-8")
    ProjectRepository(config.MASTER_DB, config.PROJECTS_JSON)


def _virtualenv_paths(venv_dir: Path) -> Tuple[Path, Path]:
    """Return the Python and pip executables inside the virtual environment."""

    if os.name == "nt":
        bin_dir = venv_dir / "Scripts"
    else:
        bin_dir = venv_dir / "bin"
    return bin_dir / "python", bin_dir / "pip"


def _ensure_virtualenv(repo_root: Path) -> Tuple[Path, Path]:
    """Create or upgrade the runtime virtual environment and install dependencies."""

    venv_dir = config.RUNTIME_DIR / "venv"
    python_exec, pip_exec = _virtualenv_paths(venv_dir)
    if not venv_dir.exists():
        print("Creating virtual environment:", venv_dir)
        subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
    if not python_exec.exists():
        raise RuntimeError(f"Virtual environment Python not found: {python_exec}")
    if not pip_exec.exists():
        raise RuntimeError(f"Virtual environment pip not found: {pip_exec}")

    marker = venv_dir / ".requirements.timestamp"
    req_file = config.ensure_worker_requirements_copy()
    if not marker.exists() or req_file.stat().st_mtime > marker.stat().st_mtime:
        print("Installing Python dependencies (this may take a while)...")
        install_requirements(req_file, pip_executable=pip_exec)
        marker.touch()
    return python_exec, pip_exec


def command_init(args: argparse.Namespace) -> None:
    """Implementation of ``vibego init``."""

    config.ensure_directories()
    env_values = config.parse_env_file(config.ENV_FILE)

    if not python_version_ok():
        print("Warning: running on Python earlier than 3.11; upgrade for full functionality.")

    missing = check_cli_dependencies()
    if missing:
        print("Missing dependencies detected:")
        for item in missing:
            print("-", item)
        print("Install the listed dependencies and rerun the command.")
        return

    if config.ENV_FILE.exists() and not args.force:
        print(".env already exists. Use --force to overwrite.")
    default_token = env_values.get("MASTER_BOT_TOKEN")
    token = args.token or _prompt_token(default_token)
    env_values["MASTER_BOT_TOKEN"] = token
    env_values.setdefault("MASTER_WHITELIST", "")
    env_values["MASTER_CONFIG_ROOT"] = str(config.CONFIG_ROOT)
    env_values.setdefault("MASTER_ADMINS", "")
    env_values.setdefault("TELEGRAM_PROXY", "")

    config.dump_env_file(config.ENV_FILE, env_values)
    _ensure_projects_assets()

    print("Initialisation complete. Configuration directory:", config.CONFIG_ROOT)
    print("Next steps:")
    print(f"  1. Edit {config.ENV_FILE} to configure proxy settings if needed.")
    print("  2. Run `vibego start` to launch the master service.")


def _load_env_or_fail() -> Dict[str, str]:
    """Read ``.env`` and raise when initialisation has not been performed."""

    if not config.ENV_FILE.exists():
        raise RuntimeError(f"{config.ENV_FILE} not found. Please run `vibego init` first.")
    values = config.parse_env_file(config.ENV_FILE)
    if "MASTER_BOT_TOKEN" not in values or not values["MASTER_BOT_TOKEN"].strip():
        raise RuntimeError("MASTER_BOT_TOKEN is missing. Rerun `vibego init`.")
    return values


def _build_master_env(base_env: Dict[str, str]) -> Dict[str, str]:
    """Construct the environment variables required by ``master.py``."""

    env = os.environ.copy()
    env.update(base_env)
    env["MASTER_BOT_TOKEN"] = base_env["MASTER_BOT_TOKEN"].strip()
    env["MASTER_PROJECTS_PATH"] = str(config.PROJECTS_JSON)
    env["MASTER_PROJECTS_DB_PATH"] = str(config.MASTER_DB)
    env["MASTER_STATE_PATH"] = str(config.MASTER_STATE)
    env["MASTER_RESTART_SIGNAL_PATH"] = str(config.RESTART_SIGNAL_PATH)
    env["LOG_ROOT"] = str(config.LOG_DIR)
    env["TASKS_DATA_ROOT"] = str(config.DATA_DIR)
    env["LOG_FILE"] = str(config.LOG_FILE)
    env["MASTER_ENV_FILE"] = str(config.ENV_FILE)
    env["VIBEGO_PACKAGE_ROOT"] = str(config.PACKAGE_ROOT)
    env["VIBEGO_RUNTIME_ROOT"] = str(config.RUNTIME_DIR)
    requirements_path = config.ensure_worker_requirements_copy()
    env["VIBEGO_REQUIREMENTS_PATH"] = str(requirements_path)
    env.setdefault("MASTER_WHITELIST", base_env.get("MASTER_WHITELIST", ""))
    if base_env.get("TELEGRAM_PROXY"):
        env["TELEGRAM_PROXY"] = base_env["TELEGRAM_PROXY"]
    return env


def _write_pid(pid: int) -> None:
    """Persist the master process PID."""

    config.MASTER_PID_FILE.write_text(str(pid), encoding="utf-8")


def _read_pid() -> Optional[int]:
    """Return the persisted master PID if present."""

    if not config.MASTER_PID_FILE.exists():
        return None
    raw = config.MASTER_PID_FILE.read_text(encoding="utf-8").strip()
    return int(raw) if raw.isdigit() else None


def command_start(args: argparse.Namespace) -> None:
    """Implementation of ``vibego start``."""

    env_values = _load_env_or_fail()
    _ensure_projects_assets()

    if not python_version_ok():
        raise RuntimeError(
            "Python earlier than 3.11 cannot run the master. "
            "Upgrade via `brew install python@3.11` or another method."
        )

    missing = check_cli_dependencies()
    if missing:
        print("Missing dependencies detected:")
        for item in missing:
            print("-", item)
        print("Install the listed dependencies before retrying.")
        return

    repo_root = _find_repo_root()
    try:
        python_exec, _ = _ensure_virtualenv(repo_root)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Virtual environment initialisation failed: {exc}") from exc

    master_env = _build_master_env(env_values)
    log_file = config.LOG_FILE
    log_file.parent.mkdir(parents=True, exist_ok=True)

    if _read_pid():
        print("Master already running. Execute `vibego stop` before restarting.")
        return

    print("Starting master service...")
    with open(log_file, "a", encoding="utf-8") as log_fp:
        process = subprocess.Popen(
            [str(python_exec), "master.py"],
            cwd=str(repo_root),
            env=master_env,
            stdout=log_fp,
            stderr=log_fp,
            start_new_session=True,
        )
    _write_pid(process.pid)

    time.sleep(2)
    if process.poll() is not None:
        raise RuntimeError("Master process failed to start. Check the logs.")

    print("Master started. PID:", process.pid)
    print("Log file:", log_file)
    print("Send /start to the bot in Telegram to complete authorisation.")


def command_stop(args: argparse.Namespace) -> None:
    """Implementation of ``vibego stop``."""

    pid = _read_pid()
    if not pid:
        print("Master process not detected.")
        return

    print("Stopping master (PID =", pid, ")...")
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        print("Process not found; treating as already stopped.")
    else:
        for _ in range(20):
            time.sleep(0.5)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
        else:
            print("Master did not exit within the expected time. Please investigate manually.")
    config.MASTER_PID_FILE.unlink(missing_ok=True)
    print("Stop completed.")


def command_status(args: argparse.Namespace) -> None:
    """Implementation of ``vibego status``."""

    env_values = config.parse_env_file(config.ENV_FILE)
    pid = _read_pid()
    running = False
    if pid:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            running = False
        else:
            running = True

    status = {
        "config_root": str(config.CONFIG_ROOT),
        "env_exists": config.ENV_FILE.exists(),
        "projects_json": config.PROJECTS_JSON.exists(),
        "master_db": config.MASTER_DB.exists(),
        "master_pid": pid,
        "master_running": running,
        "log_file": str(config.LOG_FILE),
        "token_configured": bool(env_values.get("MASTER_BOT_TOKEN")),
        "master_chat_id": env_values.get("MASTER_CHAT_ID"),
        "master_user_id": env_values.get("MASTER_USER_ID"),
    }
    print(json.dumps(status, indent=2, ensure_ascii=False))


def command_doctor(args: argparse.Namespace) -> None:
    """Implementation of ``vibego doctor``. Outputs a diagnostic report."""

    report = {
        "python_version": sys.version,
        "python_ok": python_version_ok(),
        "dependencies": check_cli_dependencies(),
        "config_root": str(config.CONFIG_ROOT),
        "env_exists": config.ENV_FILE.exists(),
        "projects_json_exists": config.PROJECTS_JSON.exists(),
        "master_db_exists": config.MASTER_DB.exists(),
        "master_chat_id": config.parse_env_file(config.ENV_FILE).get("MASTER_CHAT_ID"),
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argparse parser."""

    parser = argparse.ArgumentParser(prog="vibego", description="vibego CLI utilities")
    parser.add_argument(
        "--config-dir",
        dest="config_dir",
        help="Custom configuration directory (default ~/.config/vibego)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Initialise the configuration directory and master token")
    init_parser.add_argument("--token", help="Provide the master bot token directly to avoid interactive input")
    init_parser.add_argument("--force", action="store_true", help="Overwrite the existing .env file")
    init_parser.set_defaults(func=command_init)

    start_parser = subparsers.add_parser("start", help="Start the master service")
    start_parser.set_defaults(func=command_start)

    stop_parser = subparsers.add_parser("stop", help="Stop the master service")
    stop_parser.set_defaults(func=command_stop)

    status_parser = subparsers.add_parser("status", help="Display the current running status")
    status_parser.set_defaults(func=command_status)

    doctor_parser = subparsers.add_parser("doctor", help="Run dependency and configuration diagnostics")
    doctor_parser.set_defaults(func=command_doctor)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> None:
    """CLI main entrypoint."""

    parser = build_parser()
    args = parser.parse_args(argv)
    if args.config_dir:
        os.environ["VIBEGO_CONFIG_DIR"] = args.config_dir
        # Reload path configuration after overriding the config directory.
        from importlib import reload

        reload(config)

    try:
        args.func(args)
    except Exception as exc:  # pylint: disable=broad-except
        print("Execution failed:", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
