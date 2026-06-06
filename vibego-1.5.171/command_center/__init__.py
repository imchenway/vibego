"""命令管理模块的公共导出。"""

from pathlib import Path

from .models import CommandDefinition, CommandHistoryRecord
from .service import (
    CommandService,
    CommandError,
    CommandNotFoundError,
    CommandAlreadyExistsError,
    CommandAliasConflictError,
    CommandHistoryNotFoundError,
)
from .fsm import CommandCreateStates, CommandEditStates, WxPreviewStates
from .defaults import DEFAULT_GLOBAL_COMMANDS, REMOVED_GLOBAL_COMMAND_NAMES

GLOBAL_COMMAND_SCOPE = "global"
GLOBAL_COMMAND_PROJECT_SLUG = "__global__"
GLOBAL_COMMAND_DB_NAME = "master_commands.db"


def resolve_global_command_db(config_root: Path) -> Path:
    """根据配置根目录推导通用命令数据库路径。"""

    data_dir = Path(config_root) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / GLOBAL_COMMAND_DB_NAME


__all__ = [
    "CommandDefinition",
    "CommandHistoryRecord",
    "CommandService",
    "CommandError",
    "CommandNotFoundError",
    "CommandAlreadyExistsError",
    "CommandAliasConflictError",
    "CommandHistoryNotFoundError",
    "CommandCreateStates",
    "CommandEditStates",
    "WxPreviewStates",
    "DEFAULT_GLOBAL_COMMANDS",
    "REMOVED_GLOBAL_COMMAND_NAMES",
    "GLOBAL_COMMAND_SCOPE",
    "GLOBAL_COMMAND_PROJECT_SLUG",
    "GLOBAL_COMMAND_DB_NAME",
    "resolve_global_command_db",
]
