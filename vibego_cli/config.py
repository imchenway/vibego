"""Shared configuration and path utilities for the vibego CLI."""

from __future__ import annotations

import os
import stat
from importlib import resources
from pathlib import Path
from typing import Dict


def _default_config_root() -> Path:
    """Return the default configuration root following the XDG Base Directory spec.

    The function prefers the ``VIBEGO_CONFIG_DIR`` environment variable and falls
    back to ``~/.config/vibego`` when not provided.
    """

    override = os.environ.get("VIBEGO_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".config"
    return root / "vibego"


PACKAGE_ROOT: Path = Path(__file__).resolve().parent.parent


CONFIG_ROOT: Path = _default_config_root()
CONFIG_DIR: Path = CONFIG_ROOT / "config"
LOG_DIR: Path = CONFIG_ROOT / "logs"
STATE_DIR: Path = CONFIG_ROOT / "state"
DATA_DIR: Path = CONFIG_ROOT / "data"
RUNTIME_DIR: Path = CONFIG_ROOT / "runtime"

ENV_FILE: Path = CONFIG_ROOT / ".env"
PROJECTS_JSON: Path = CONFIG_DIR / "projects.json"
MASTER_DB: Path = CONFIG_DIR / "master.db"
MASTER_STATE: Path = STATE_DIR / "master_state.json"
MASTER_PID_FILE: Path = STATE_DIR / "master.pid"
RESTART_SIGNAL_PATH: Path = STATE_DIR / "restart_signal.json"
LOG_FILE: Path = LOG_DIR / "vibe.log"


def ensure_worker_requirements_copy() -> Path:
    """Ensure the runtime directory contains a fresh copy of worker dependencies."""

    source = resources.files("vibego_cli").joinpath("data/worker_requirements.txt")
    target = RUNTIME_DIR / "worker_requirements.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    with resources.as_file(source) as src_path:
        content = src_path.read_text(encoding="utf-8")
    if not target.exists() or target.read_text(encoding="utf-8") != content:
        target.write_text(content, encoding="utf-8")
    return target


def ensure_directories() -> None:
    """Create every directory required by the CLI if it does not exist."""

    for path in (CONFIG_ROOT, CONFIG_DIR, LOG_DIR, STATE_DIR, DATA_DIR, RUNTIME_DIR):
        path.mkdir(parents=True, exist_ok=True)


def parse_env_file(path: Path) -> Dict[str, str]:
    """Parse a simple ``KEY=VALUE`` style ``.env`` file."""

    if not path.exists():
        return {}
    content: Dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        content[key.strip()] = value.strip()
    return content


def dump_env_file(path: Path, values: Dict[str, str]) -> None:
    """Write a ``.env`` file and enforce permission ``600`` when possible."""

    lines = [f"{key}={value}" for key, value in sorted(values.items())]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except PermissionError:
        # Some platforms (e.g., Windows) may not allow chmod; ignore the failure.
        pass
