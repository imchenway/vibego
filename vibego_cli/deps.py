"""依赖检测与安装辅助函数。"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, Tuple


DEPENDENCY_COMMANDS: Tuple[Tuple[str, str], ...] = (
    ("python3", "请安装 Python 3.11+（推荐通过 Homebrew：brew install python）"),
    ("tmux", "请安装 tmux（brew install tmux）"),
)


def check_cli_dependencies() -> List[str]:
    """逐项检测 CLI 依赖，返回缺失项提示列表。"""

    missing: List[str] = []
    for command, hint in DEPENDENCY_COMMANDS:
        if shutil.which(command) is None:
            missing.append(f"{command} 未找到：{hint}")
    return missing


def ensure_python_packages(requirements: Iterable[str], *, pip_executable: Path) -> None:
    """确保指定 pip 安装了所需依赖。"""

    cmd = [str(pip_executable), "install", "-q", *requirements]
    subprocess.run(cmd, check=True)


def install_requirements(requirements_file: Path, *, pip_executable: Path) -> None:
    """基于 requirements.txt 安装项目依赖。"""

    subprocess.run(
        [str(pip_executable), "install", "-r", str(requirements_file)],
        check=True,
    )


def python_version_ok() -> bool:
    """检测当前运行 CLI 的 Python 版本是否符合要求。"""

    major, minor = sys.version_info.major, sys.version_info.minor
    return (major, minor) >= (3, 11)

