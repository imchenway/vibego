"""Helper functions for dependency detection and installation."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, Tuple


DEPENDENCY_COMMANDS: Tuple[Tuple[str, str], ...] = (
    ("python3", "Install Python 3.11+ (Homebrew: brew install python)"),
    ("tmux", "Install tmux (brew install tmux)"),
)


def check_cli_dependencies() -> List[str]:
    """Check every CLI dependency and return a list of missing ones."""

    missing: List[str] = []
    for command, hint in DEPENDENCY_COMMANDS:
        if shutil.which(command) is None:
            missing.append(f"{command} is missing: {hint}")
    return missing


def ensure_python_packages(requirements: Iterable[str], *, pip_executable: Path) -> None:
    """Install the required Python packages using the provided pip executable."""

    cmd = [str(pip_executable), "install", "-q", *requirements]
    subprocess.run(cmd, check=True)


def install_requirements(requirements_file: Path, *, pip_executable: Path) -> None:
    """Install project dependencies from a requirements file."""

    subprocess.run(
        [str(pip_executable), "install", "-r", str(requirements_file)],
        check=True,
    )


def python_version_ok() -> bool:
    """Return True when the running Python version satisfies the minimum requirement."""

    major, minor = sys.version_info.major, sys.version_info.minor
    return (major, minor) >= (3, 11)
