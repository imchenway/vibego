"""vibego CLI 通用配置与路径解析工具。"""

from __future__ import annotations

import os
import stat
from importlib import resources
from pathlib import Path
from typing import Dict


def _default_config_root() -> Path:
    """计算默认配置根目录，遵循 XDG Base Directory 规范。

    优先读取环境变量 `VIBEGO_CONFIG_DIR`，否则采用
    `~/.config/vibego`。
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
    """确保运行目录内存在 worker 依赖清单副本并返回其路径。"""

    source = resources.files("vibego_cli").joinpath("data/worker_requirements.txt")
    target = RUNTIME_DIR / "worker_requirements.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    with resources.as_file(source) as src_path:
        content = src_path.read_text(encoding="utf-8")
    if not target.exists() or target.read_text(encoding="utf-8") != content:
        target.write_text(content, encoding="utf-8")
    return target


def ensure_directories() -> None:
    """保证 CLI 运行所需的目录全部存在。"""

    for path in (CONFIG_ROOT, CONFIG_DIR, LOG_DIR, STATE_DIR, DATA_DIR, RUNTIME_DIR):
        path.mkdir(parents=True, exist_ok=True)


def parse_env_file(path: Path) -> Dict[str, str]:
    """解析简单的 KEY=VALUE 形式的 .env 文件。"""

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
    """写入 .env 文件，确保文件权限为 600。"""

    lines = [f"{key}={value}" for key, value in sorted(values.items())]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except PermissionError:
        # 在部分平台（例如 Windows）可能无法调整权限，忽略即可。
        pass
