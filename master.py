"""Master bot controller.

统一管理多个项目 worker：
- 读取 `config/master.db`（自动同步 `config/projects.json`）获取项目配置
- 维护 `state/state.json`，记录运行状态 / 当前模型 / 自动记录的 chat_id
- 暴露 /projects、/run、/stop、/switch、/authorize 等命令
- 调用 `scripts/run_bot.sh` / `scripts/stop_bot.sh` 控制 worker 进程
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import shutil
import subprocess
import sys
import signal
import shlex
import stat
import textwrap
import re
import threading
import unicodedata
import urllib.request
import uuid
from urllib.error import URLError
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple

from aiogram import Bot, Dispatcher, Router, F
from aiohttp import BasicAuth, ClientError
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    MenuButtonCommands,
    User,
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllChatAdministrators,
)
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

from codex_trust import CODEX_CONFIG_PATH as DEFAULT_CODEX_CONFIG_PATH, ensure_codex_project_trust
from logging_setup import create_logger
from project_repository import ProjectRepository, ProjectRecord
from tasks.fsm import ProjectDeleteStates
from command_center import (
    CommandDefinition,
    CommandService,
    CommandCreateStates,
    CommandEditStates,
    CommandAliasConflictError,
    CommandAlreadyExistsError,
    CommandNotFoundError,
    DEFAULT_GLOBAL_COMMANDS,
    REMOVED_GLOBAL_COMMAND_NAMES,
    GLOBAL_COMMAND_PROJECT_SLUG,
    GLOBAL_COMMAND_SCOPE,
    resolve_global_command_db,
)
from command_center.prompts import build_field_prompt_text
from vibego_cli import __version__

try:
    from packaging.version import Version, InvalidVersion
except ImportError:  # pragma: no cover
    Version = None  # type: ignore[assignment]

    class InvalidVersion(Exception):
        """占位异常，兼容缺失 packaging 时的版本解析错误。"""

ROOT_DIR = Path(__file__).resolve().parent
CODEX_CONFIG_PATH = DEFAULT_CODEX_CONFIG_PATH


def _default_config_root() -> Path:
    """
    解析配置根目录，兼容多种环境变量并回落到 XDG 规范。

    优先级：
    1. MASTER_CONFIG_ROOT（供 master.py 使用）
    2. VIBEGO_CONFIG_DIR（CLI 入口设置）
    3. $XDG_CONFIG_HOME/vibego 或 ~/.config/vibego
    """
    override = os.environ.get("MASTER_CONFIG_ROOT") or os.environ.get("VIBEGO_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    xdg_base = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_base).expanduser() if xdg_base else Path.home() / ".config"
    return base / "vibego"


CONFIG_ROOT = _default_config_root()
CONFIG_DIR = CONFIG_ROOT / "config"
STATE_DIR = CONFIG_ROOT / "state"
LOG_DIR = CONFIG_ROOT / "logs"
DATA_DIR = CONFIG_ROOT / "data"

CONFIG_DIR.mkdir(parents=True, exist_ok=True)
STATE_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

TELEGRAM_API_ROOT = (os.environ.get("MASTER_TELEGRAM_API_ROOT") or "https://api.telegram.org").rstrip("/")

CONFIG_PATH = Path(os.environ.get("MASTER_PROJECTS_PATH", CONFIG_DIR / "projects.json"))
CONFIG_DB_PATH = Path(os.environ.get("MASTER_PROJECTS_DB_PATH", CONFIG_DIR / "master.db"))
STATE_PATH = Path(os.environ.get("MASTER_STATE_PATH", STATE_DIR / "state.json"))
RUN_SCRIPT = ROOT_DIR / "scripts/run_bot.sh"
STOP_SCRIPT = ROOT_DIR / "scripts/stop_bot.sh"
GLOBAL_COMMAND_DB_PATH = resolve_global_command_db(CONFIG_ROOT)
GLOBAL_COMMAND_SERVICE = CommandService(
    GLOBAL_COMMAND_DB_PATH,
    GLOBAL_COMMAND_PROJECT_SLUG,
    scope=GLOBAL_COMMAND_SCOPE,
)


async def _ensure_default_global_commands() -> None:
    """在 master 启动阶段保证通用命令就绪，并同步最新脚本配置。"""

    try:
        await GLOBAL_COMMAND_SERVICE.initialize()
    except Exception as exc:  # noqa: BLE001
        log.error("通用命令数据库初始化失败：%s", exc)
        return

    # 启动时主动删除废弃的通用命令，避免旧环境残留
    for legacy_name in REMOVED_GLOBAL_COMMAND_NAMES:
        try:
            existing = await GLOBAL_COMMAND_SERVICE.resolve_by_trigger(legacy_name)
        except Exception as exc:  # noqa: BLE001
            log.error("查询废弃通用命令失败：%s", exc, extra={"command": legacy_name})
            continue
        if existing is None:
            continue
        try:
            await GLOBAL_COMMAND_SERVICE.delete_command(existing.id)
            log.info("已清理废弃通用命令：%s", legacy_name)
        except Exception as exc:  # noqa: BLE001
            log.error("删除废弃通用命令 %s 失败：%s", legacy_name, exc)

    for payload in DEFAULT_GLOBAL_COMMANDS:
        name = str(payload["name"])
        desired_aliases = tuple(payload.get("aliases") or ())
        desired_timeout = payload.get("timeout")
        try:
            existing = await GLOBAL_COMMAND_SERVICE.resolve_by_trigger(name)
        except Exception as exc:  # noqa: BLE001
            log.error("查询通用命令失败：%s", exc, extra={"command": name})
            continue

        if existing is None:
            try:
                await GLOBAL_COMMAND_SERVICE.create_command(**payload)
                log.info("已注入通用命令：%s", name)
            except (CommandAlreadyExistsError, CommandAliasConflictError) as exc:
                log.warning("通用命令 %s 注入冲突：%s", name, exc)
            except Exception as exc:  # noqa: BLE001
                log.error("通用命令 %s 创建失败：%s", name, exc)
            continue

        updates: dict[str, object] = {}
        for field in ("title", "command", "description"):
            value = payload.get(field)
            if value is not None and getattr(existing, field) != value:
                updates[field] = value
        if desired_timeout is not None and existing.timeout != desired_timeout:
            updates["timeout"] = desired_timeout

        if updates:
            try:
                await GLOBAL_COMMAND_SERVICE.update_command(existing.id, **updates)
                log.info("已更新通用命令：%s 字段=%s", name, ", ".join(updates.keys()))
            except Exception as exc:  # noqa: BLE001
                log.error("更新通用命令 %s 失败：%s", name, exc)

        existing_aliases = tuple(existing.aliases or ())
        if existing_aliases != desired_aliases:
            try:
                await GLOBAL_COMMAND_SERVICE.replace_aliases(existing.id, desired_aliases)
                alias_label = ", ".join(desired_aliases) if desired_aliases else "无"
                log.info("已重写通用命令别名：%s -> %s", name, alias_label)
            except Exception as exc:  # noqa: BLE001
                log.error("更新通用命令 %s 别名失败：%s", name, exc)

UPDATE_STATE_PATH = STATE_DIR / "update_state.json"
UPDATE_CHECK_INTERVAL = timedelta(hours=24)
_UPDATE_STATE_LOCK = threading.Lock()


def _get_restart_signal_path() -> Path:
    """
    获取重启信号文件路径，使用健壮的默认值逻辑。

    优先级：
    1. 环境变量 MASTER_RESTART_SIGNAL_PATH
    2. 配置目录 $MASTER_CONFIG_ROOT/state/restart_signal.json
    3. 代码目录 ROOT_DIR/state/restart_signal.json（兜底）

    这样可以确保 pipx 安装的版本和源码运行的版本使用同一个信号文件。
    """
    if env_path := os.environ.get("MASTER_RESTART_SIGNAL_PATH"):
        return Path(env_path)

    # 默认使用配置目录而非代码目录，确保跨安装方式的一致性
    config_root_raw = (
        os.environ.get("MASTER_CONFIG_ROOT")
        or os.environ.get("VIBEGO_CONFIG_DIR")
    )
    config_root = Path(config_root_raw).expanduser() if config_root_raw else _default_config_root()
    return config_root / "state/restart_signal.json"


RESTART_SIGNAL_PATH = _get_restart_signal_path()
LEGACY_RESTART_SIGNAL_PATHS: Tuple[Path, ...] = tuple(
    path
    for path in (ROOT_DIR / "state/restart_signal.json",)
    if path != RESTART_SIGNAL_PATH
)
RESTART_SIGNAL_TTL = int(os.environ.get("MASTER_RESTART_SIGNAL_TTL", "1800"))  # 默认 30 分钟


def _get_start_signal_path() -> Path:
    """解析自动 /start 信号文件路径，允许通过环境变量覆盖。"""

    if env_path := os.environ.get("MASTER_START_SIGNAL_PATH"):
        return Path(env_path)
    config_root_raw = os.environ.get("MASTER_CONFIG_ROOT") or os.environ.get("VIBEGO_CONFIG_DIR")
    config_root = Path(config_root_raw).expanduser() if config_root_raw else _default_config_root()
    return config_root / "state/start_signal.json"


START_SIGNAL_PATH = _get_start_signal_path()
START_SIGNAL_TTL = int(os.environ.get("MASTER_START_SIGNAL_TTL", "600"))
LOCAL_TZ = ZoneInfo(os.environ.get("MASTER_TIMEZONE", "Asia/Shanghai"))
JUMP_BUTTON_TEXT_WIDTH = 40

_DEFAULT_LOG_ROOT = LOG_DIR
LOG_ROOT_PATH = Path(os.environ.get("LOG_ROOT", str(_DEFAULT_LOG_ROOT))).expanduser()

WORKER_HEALTH_TIMEOUT = float(os.environ.get("WORKER_HEALTH_TIMEOUT", "20"))
WORKER_HEALTH_INTERVAL = float(os.environ.get("WORKER_HEALTH_INTERVAL", "0.5"))
WORKER_HEALTH_LOG_TAIL = int(os.environ.get("WORKER_HEALTH_LOG_TAIL", "80"))
HANDSHAKE_MARKERS = (
    "Telegram 连接正常",
)
WORKER_BOOT_ID_ENV = "VIBEGO_WORKER_BOOT_ID"
WORKER_BOOT_ID_LOG_PREFIX = "[run-bot] boot_id="
DELETE_CONFIRM_TIMEOUT = int(os.environ.get("MASTER_DELETE_CONFIRM_TIMEOUT", "120"))

_ENV_FILE_RAW = os.environ.get("MASTER_ENV_FILE")
MASTER_ENV_FILE = Path(_ENV_FILE_RAW).expanduser() if _ENV_FILE_RAW else None
_ENV_LOCK = threading.Lock()

MASTER_MENU_BUTTON_TEXT = "📂 项目列表"
# 旧版本键盘的文案，用于兼容仍显示英文的客户端消息
MASTER_MENU_BUTTON_LEGACY_TEXTS: Tuple[str, ...] = ("📂 Projects",)
# 允许触发项目列表的全部文案，优先匹配最新文案
MASTER_MENU_BUTTON_ALLOWED_TEXTS: Tuple[str, ...] = (
    MASTER_MENU_BUTTON_TEXT,
    *MASTER_MENU_BUTTON_LEGACY_TEXTS,
)
MASTER_MANAGE_BUTTON_TEXT = "⚙️ 项目管理"
MASTER_MANAGE_BUTTON_ALLOWED_TEXTS: Tuple[str, ...] = (MASTER_MANAGE_BUTTON_TEXT,)
MASTER_SETTINGS_BUTTON_TEXT = "🛠 系统设置"
MASTER_BOT_COMMANDS: List[Tuple[str, str]] = [
    ("start", "启动 master 菜单"),
    ("projects", "查看项目列表"),
    ("restart", "重启 master"),
    ("upgrade", "升级 vibego 至最新版"),
]
MASTER_BROADCAST_MESSAGE = os.environ.get("MASTER_BROADCAST_MESSAGE", "")
SWITCHABLE_MODELS: Tuple[Tuple[str, str], ...] = (
    ("codex", "⚙️ Codex"),
    ("claudecode", "⚙️ ClaudeCode"),
    ("gemini", "⚙️ Gemini"),
    ("copilot", "⚙️ Copilot"),
)
SYSTEM_SETTINGS_MENU_CALLBACK = "system:menu"
SYSTEM_SETTINGS_BACK_CALLBACK = "system:back"
GLOBAL_COMMAND_MENU_CALLBACK = "system:commands:menu"
GLOBAL_COMMAND_REFRESH_CALLBACK = "system:commands:refresh"
GLOBAL_COMMAND_NEW_CALLBACK = "system:commands:new"

_UPGRADE_COMMANDS: Tuple[Tuple[str, str], ...] = (
    ("pipx upgrade vibego", "升级 vibego 包"),
)
_UPGRADE_LOG_TAIL = int(os.environ.get("MASTER_UPGRADE_LOG_TAIL", "20"))
_UPGRADE_LOG_BUFFER_LIMIT = int(os.environ.get("MASTER_UPGRADE_LOG_BUFFER_LIMIT", "200"))
_UPGRADE_LINE_LIMIT = int(os.environ.get("MASTER_UPGRADE_LINE_LIMIT", "160"))
_UPGRADE_STATE_LOCK = asyncio.Lock()
_UPGRADE_TASK: Optional[asyncio.Task[None]] = None
_UPGRADE_RESTART_COMMAND = os.environ.get(
    "MASTER_UPGRADE_RESTART_COMMAND",
    "vibego stop && vibego start",
)
_UPGRADE_RESTART_DELAY = float(os.environ.get("MASTER_UPGRADE_RESTART_DELAY", "2.0"))
_UPGRADE_RESTART_LOG_PATH = Path(
    os.environ.get("MASTER_UPGRADE_RESTART_LOG_PATH", str(LOG_DIR / "upgrade_restart.log"))
)
_UPGRADE_REPORT_PATH = Path(
    os.environ.get("MASTER_UPGRADE_REPORT_PATH", STATE_DIR / "upgrade_report.json")
)
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_PIPX_VERSION_RE = re.compile(
    r"upgraded package\s+(?P<name>[\w\-.]+)\s+from\s+(?P<old>[0-9A-Za-z.\-+]+)\s+to\s+(?P<new>[0-9A-Za-z.\-+]+)",
    re.IGNORECASE,
)
GLOBAL_COMMAND_EDIT_PREFIX = "system:commands:edit:"
GLOBAL_COMMAND_FIELD_PREFIX = "system:commands:field:"
GLOBAL_COMMAND_TOGGLE_PREFIX = "system:commands:toggle:"
GLOBAL_COMMAND_DELETE_PROMPT_PREFIX = "system:commands:delete_prompt:"
GLOBAL_COMMAND_DELETE_CONFIRM_PREFIX = "system:commands:delete_confirm:"
GLOBAL_COMMAND_INLINE_LIMIT = 12
GLOBAL_COMMAND_STATE_KEY = "global_command_flow"

# Telegram 在不同客户端可能插入零宽字符或额外空白，提前归一化按钮文本。
ZERO_WIDTH_CHARACTERS: Tuple[str, ...] = ("\u200b", "\u200c", "\u200d", "\ufeff")


def _normalize_button_text(text: str) -> str:
    """归一化项目按钮文本，剔除零宽字符并统一大小写。"""

    filtered = "".join(ch for ch in text if ch not in ZERO_WIDTH_CHARACTERS)
    compacted = re.sub(r"\s+", " ", filtered).strip()
    return unicodedata.normalize("NFKC", compacted).casefold()


MASTER_MENU_BUTTON_CANONICAL_NORMALIZED = _normalize_button_text(MASTER_MENU_BUTTON_TEXT)
MASTER_MENU_BUTTON_ALLOWED_NORMALIZED = {
    _normalize_button_text(value) for value in MASTER_MENU_BUTTON_ALLOWED_TEXTS
}
MASTER_MENU_BUTTON_KEYWORDS: Tuple[str, ...] = ("项目列表", "project", "projects")


def _env_flag(name: str, default: bool = True) -> bool:
    """解析布尔开关环境变量。"""

    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if not normalized:
        return default
    return normalized not in {"0", "false", "off", "no"}


MASTER_FORCE_MENU_RESYNC = _env_flag("MASTER_FORCE_MENU_RESYNC", True)
MASTER_FORCE_COMMAND_RESYNC = _env_flag("MASTER_FORCE_COMMAND_RESYNC", True)


def _is_projects_menu_trigger(text: Optional[str]) -> bool:
    """判断消息文本是否可触发项目列表展示。"""

    if not text:
        return False
    normalized = _normalize_button_text(text)
    if not normalized:
        return False
    if normalized in MASTER_MENU_BUTTON_ALLOWED_NORMALIZED:
        return True
    return any(keyword in normalized for keyword in MASTER_MENU_BUTTON_KEYWORDS)


def _text_equals_master_button(text: str) -> bool:
    """判断文本是否等同于当前主按钮文案（允许空白差异）。"""

    return _normalize_button_text(text) == MASTER_MENU_BUTTON_CANONICAL_NORMALIZED


def _build_master_main_keyboard() -> ReplyKeyboardMarkup:
    """构造 Master Bot 主键盘，提供项目列表与管理入口。"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=MASTER_MENU_BUTTON_TEXT),
                KeyboardButton(text=MASTER_MANAGE_BUTTON_TEXT),
                KeyboardButton(text=MASTER_SETTINGS_BUTTON_TEXT),
            ]
        ],
        resize_keyboard=True,
    )


def _build_system_settings_menu() -> Tuple[str, InlineKeyboardMarkup]:
    """生成系统设置主菜单。"""

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📟 通用命令配置", callback_data=GLOBAL_COMMAND_MENU_CALLBACK))
    builder.row(InlineKeyboardButton(text="📂 返回项目列表", callback_data="project:refresh:*"))
    markup = _ensure_numbered_markup(builder.as_markup())
    text = "请选择需要调整的系统设置："
    return text, markup


async def _build_global_command_overview_view(
    notice: Optional[str] = None,
) -> Tuple[str, InlineKeyboardMarkup]:
    """渲染通用命令列表。"""

    commands = await GLOBAL_COMMAND_SERVICE.list_commands()
    lines: List[str] = [
        "【通用命令配置】",
        f"当前可用命令：{len(commands)}",
        "此处的命令将在所有项目的命令管理中合并显示，仅供 master 维护。",
        "",
    ]
    if not commands:
        lines.append("暂无通用命令，点击“🆕 新增通用命令”开始配置。")
    else:
        for command in commands[:GLOBAL_COMMAND_INLINE_LIMIT]:
            status = "启用" if command.enabled else "停用"
            lines.append(f"- {command.name}（{status}，超时 {command.timeout}s）")
    if notice:
        lines.append(f"\n提示：{notice}")
    markup = _build_global_command_keyboard(commands)
    return "\n".join(lines), markup


def _build_global_command_keyboard(commands: Sequence[CommandDefinition]) -> InlineKeyboardMarkup:
    """构造通用命令管理面板。"""

    inline_keyboard: List[List[InlineKeyboardButton]] = []
    for command in commands[:GLOBAL_COMMAND_INLINE_LIMIT]:
        toggle_label = "⏸ 停用" if command.enabled else "▶️ 启用"
        inline_keyboard.append(
            [
                InlineKeyboardButton(text=f"✏️ 编辑 {command.name}", callback_data=f"{GLOBAL_COMMAND_EDIT_PREFIX}{command.id}"),
                InlineKeyboardButton(text=toggle_label, callback_data=f"{GLOBAL_COMMAND_TOGGLE_PREFIX}{command.id}"),
            ]
        )
    inline_keyboard.append([InlineKeyboardButton(text="🆕 新增通用命令", callback_data=GLOBAL_COMMAND_NEW_CALLBACK)])
    inline_keyboard.append([InlineKeyboardButton(text="⬅️ 返回系统设置", callback_data=SYSTEM_SETTINGS_MENU_CALLBACK)])
    inline_keyboard.append([InlineKeyboardButton(text="📂 返回项目列表", callback_data="project:refresh:*")])
    return _ensure_numbered_markup(InlineKeyboardMarkup(inline_keyboard=inline_keyboard))


def _build_global_command_edit_keyboard(command: CommandDefinition) -> InlineKeyboardMarkup:
    """编辑通用命令的操作面板。"""

    toggle_label = "⏸ 停用" if command.enabled else "▶️ 启用"
    inline_keyboard = [
        [
            InlineKeyboardButton(text="📝 标题", callback_data=f"{GLOBAL_COMMAND_FIELD_PREFIX}title:{command.id}"),
            InlineKeyboardButton(text="💻 指令", callback_data=f"{GLOBAL_COMMAND_FIELD_PREFIX}command:{command.id}"),
        ],
        [
            InlineKeyboardButton(text="📛 描述", callback_data=f"{GLOBAL_COMMAND_FIELD_PREFIX}description:{command.id}"),
            InlineKeyboardButton(text="⏱ 超时", callback_data=f"{GLOBAL_COMMAND_FIELD_PREFIX}timeout:{command.id}"),
        ],
        [InlineKeyboardButton(text="🔁 别名", callback_data=f"{GLOBAL_COMMAND_FIELD_PREFIX}aliases:{command.id}")],
        [InlineKeyboardButton(text=toggle_label, callback_data=f"{GLOBAL_COMMAND_TOGGLE_PREFIX}{command.id}")],
        [
            InlineKeyboardButton(
                text="🗑 删除命令",
                callback_data=f"{GLOBAL_COMMAND_DELETE_PROMPT_PREFIX}{command.id}",
            )
        ],
        [InlineKeyboardButton(text="⬅️ 返回列表", callback_data=GLOBAL_COMMAND_REFRESH_CALLBACK)],
    ]
    return InlineKeyboardMarkup(inline_keyboard=inline_keyboard)


async def _send_global_command_overview_message(message: Message, notice: Optional[str] = None) -> None:
    """在聊天中发送最新的通用命令列表。"""

    text, markup = await _build_global_command_overview_view(notice)
    await message.answer(text, reply_markup=markup)


async def _edit_global_command_overview(callback: CallbackQuery, notice: Optional[str] = None) -> None:
    """在原消息上刷新通用命令列表。"""

    if callback.message is None:
        return
    text, markup = await _build_global_command_overview_view(notice)
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=markup)


async def _ensure_authorized_callback(callback: CallbackQuery) -> bool:
    """校验回调属于已授权聊天。"""

    if callback.message is None or callback.message.chat is None:
        await callback.answer("无法更新此消息", show_alert=True)
        return False
    manager = await _ensure_manager()
    if not manager.is_authorized(callback.message.chat.id):
        await callback.answer("未授权。", show_alert=True)
        return False
    return True


def _is_global_command_flow(state_data: Dict[str, Any], expected: str) -> bool:
    """判断当前 FSM 是否处于指定的通用命令流程。"""

    return state_data.get(GLOBAL_COMMAND_STATE_KEY) == expected


def _is_cancel_text(text: str) -> bool:
    """统一处理“取消”输入。"""

    normalized = (text or "").strip().lower()
    return normalized in {"取消", "cancel", "quit", "退出"}


def _parse_global_alias_input(text: str) -> List[str]:
    """解析别名输入，兼容中文逗号。"""

    sanitized = (text or "").replace("，", ",").strip()
    if not sanitized or sanitized == "-":
        return []
    parts = re.split(r"[,\s]+", sanitized)
    return [part for part in parts if part]


async def _detect_project_command_conflict(identifiers: Sequence[str]) -> Optional[str]:
    """检查指定名称或别名是否与任何项目命令冲突。"""

    candidates = [value for value in identifiers if value]
    if not candidates:
        return None
    repository = _ensure_repository()
    for record in repository.list_projects():
        slug = record.project_slug
        if not slug:
            continue
        db_path = DATA_DIR / f"{slug}.db"
        if not db_path.exists():
            continue
        service = CommandService(db_path, slug)
        for candidate in candidates:
            conflict = await service.resolve_by_trigger(candidate)
            if conflict:
                return record.bot_name or slug
    return None


async def _verify_master_menu_button(bot: Bot, expected_text: str) -> bool:
    """获取 Telegram 端菜单，确认文本与预期一致。"""
    try:
        current = await bot.get_chat_menu_button()
    except TelegramBadRequest as exc:
        log.warning("获取聊天菜单失败：%s", exc)
        return False
    if not isinstance(current, MenuButtonCommands):
        log.warning(
            "聊天菜单类型异常",
            extra={"type": getattr(current, "type", None)},
        )
        return False
    normalized_expected = _normalize_button_text(expected_text)
    normalized_actual = _normalize_button_text(current.text or "")
    if normalized_actual != normalized_expected:
        log.warning(
            "聊天菜单文本与预期不一致",
            extra={"expected": expected_text, "actual": current.text},
        )
        return False
    return True


async def _ensure_master_menu_button(bot: Bot) -> None:
    """同步 master 端聊天菜单按钮文本，修复旧客户端的缓存问题。"""
    if not MASTER_FORCE_MENU_RESYNC:
        log.info("菜单同步已禁用，跳过 set_chat_menu_button。")
        return
    button = MenuButtonCommands(text=MASTER_MENU_BUTTON_TEXT)
    try:
        await bot.set_chat_menu_button(menu_button=button)
    except TelegramBadRequest as exc:
        log.warning("设置聊天菜单失败：%s", exc)
        return
    if await _verify_master_menu_button(bot, MASTER_MENU_BUTTON_TEXT):
        log.info("聊天菜单已同步", extra={"text": MASTER_MENU_BUTTON_TEXT})
    else:
        log.warning("聊天菜单同步后校验失败，将保留现状。")


async def _ensure_master_commands(bot: Bot) -> None:
    """同步 master 侧命令列表，确保新增/删除命令立即生效。"""
    if not MASTER_FORCE_COMMAND_RESYNC:
        log.info("命令同步已禁用，跳过 set_my_commands。")
        return
    commands = [BotCommand(command=cmd, description=desc) for cmd, desc in MASTER_BOT_COMMANDS]
    scopes: List[Tuple[Optional[object], str]] = [
        (None, "default"),
        (BotCommandScopeAllPrivateChats(), "all_private"),
        (BotCommandScopeAllGroupChats(), "all_groups"),
        (BotCommandScopeAllChatAdministrators(), "group_admins"),
    ]
    for scope, label in scopes:
        try:
            if scope is None:
                await bot.set_my_commands(commands)
            else:
                await bot.set_my_commands(commands, scope=scope)
        except TelegramBadRequest as exc:
            log.warning("设置 master 命令失败：%s", exc, extra={"scope": label})
        else:
            if await _verify_master_commands(bot, commands, scope, label):
                log.info("master 命令已同步", extra={"scope": label})
            else:
                log.warning("master 命令校验失败", extra={"scope": label})


async def _verify_master_commands(
    bot: Bot,
    expected: Sequence[BotCommand],
    scope: Optional[object],
    label: str,
) -> bool:
    """读取并校验当前命令列表，确保 scope 内容一致。"""
    try:
        current = await bot.get_my_commands() if scope is None else await bot.get_my_commands(scope=scope)
    except TelegramBadRequest as exc:
        log.warning("获取 master 命令失败：%s", exc, extra={"scope": label})
        return False

    expected_pairs = [(cmd.command, cmd.description) for cmd in expected]
    current_pairs = [(cmd.command, cmd.description) for cmd in current]
    if current_pairs != expected_pairs:
        log.warning(
            "命令验证不一致",
            extra={"scope": label, "expected": expected_pairs, "actual": current_pairs},
        )
        return False
    return True


def _collect_master_broadcast_targets(manager: MasterManager) -> List[int]:
    """汇总需要推送键盘的 chat_id，避免重复广播。"""
    targets: set[int] = set(manager.admin_ids or [])
    manager.refresh_state()
    for state in manager.state_store.data.values():
        if state.chat_id:
            targets.add(state.chat_id)
    return sorted(targets)


async def _broadcast_master_keyboard(bot: Bot, manager: MasterManager) -> None:
    """在 master 启动阶段主动推送菜单键盘，覆盖 Telegram 端缓存。"""
    targets = _collect_master_broadcast_targets(manager)
    # 当广播消息为空时表示不再向管理员推送启动提示，满足“禁止发送 /task_list”需求。
    if not MASTER_BROADCAST_MESSAGE:
        log.info("启动广播已禁用，跳过 master 键盘推送。")
        return
    if not targets:
        log.info("无可推送的 master 聊天对象")
        return
    markup = _build_master_main_keyboard()
    for chat_id in targets:
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=MASTER_BROADCAST_MESSAGE,
                reply_markup=markup,
            )
        except TelegramForbiddenError as exc:
            log.warning("推送菜单被禁止：%s", exc, extra={"chat": chat_id})
        except TelegramBadRequest as exc:
            log.warning("推送菜单失败：%s", exc, extra={"chat": chat_id})
        except Exception as exc:
            log.error("推送菜单异常：%s", exc, extra={"chat": chat_id})
        else:
            log.info("已推送菜单至 chat_id=%s", chat_id)


def _ensure_numbered_markup(markup: Optional[InlineKeyboardMarkup]) -> Optional[InlineKeyboardMarkup]:
    """对 InlineKeyboard 保持原始文案，不再自动追加编号。"""
    return markup


def _get_project_runtime_state(manager: "MasterManager", slug: str) -> Optional["ProjectState"]:
    """归一化查询项目运行状态，避免误用 FSMContext。

    这里集中处理 slug 大小写并注释说明原因，防止在路由中覆盖 aiogram
    提供的 `FSMContext`（详见官方文档：https://docs.aiogram.dev/en/dev-3.x/dispatcher/fsm/context.html）。
    """

    normalized = (slug or "").strip().lower()
    if not normalized:
        return None
    store = manager.state_store
    if normalized in store.data:
        return store.data[normalized]
    for known_slug, runtime_state in store.data.items():
        if known_slug.lower() == normalized:
            return runtime_state
    return None


def _terminate_other_master_processes(grace: float = 3.0) -> None:
    """在新 master 启动后终止其他残留 master 进程"""
    existing: list[int] = []
    try:
        result = subprocess.run(
            ["pgrep", "-f", "[Pp]ython.*master.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return
    my_pid = os.getpid()
    for line in result.stdout.split():
        try:
            pid = int(line.strip())
        except ValueError:
            continue
        if pid == my_pid:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            existing.append(pid)
        except ProcessLookupError:
            continue
        except PermissionError as exc:
            log.warning("终止残留 master 进程失败: %s", exc, extra={"pid": pid})
    if not existing:
        return
    deadline = time.monotonic() + grace
    alive = set(existing)
    while alive and time.monotonic() < deadline:
        time.sleep(0.2)
        for pid in list(alive):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                alive.discard(pid)
    for pid in alive:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            continue
        except PermissionError as exc:
            log.warning("强制终止 master 进程失败: %s", exc, extra={"pid": pid})
    if existing:
        log.info("清理其他 master 进程完成", extra={"terminated": existing, "force": list(alive)})



def load_env(file: str = ".env") -> None:
    """加载默认 .env 以及 MASTER_ENV_FILE 指向的配置。"""

    candidates: List[Path] = []
    if MASTER_ENV_FILE:
        candidates.append(MASTER_ENV_FILE)
    env_path = ROOT_DIR / file
    candidates.append(env_path)
    for path in candidates:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def _collect_admin_targets() -> List[int]:
    """汇总所有潜在管理员 chat_id，避免广播遗漏。"""

    if MANAGER is not None and getattr(MANAGER, "admin_ids", None):
        return sorted(MANAGER.admin_ids)
    env_value = os.environ.get("MASTER_ADMIN_IDS") or os.environ.get("ALLOWED_CHAT_ID", "")
    targets: List[int] = []
    for item in env_value.split(","):
        item = item.strip()
        if not item:
            continue
        if item.isdigit():
            targets.append(int(item))
    chat_env = os.environ.get("MASTER_CHAT_ID", "")
    if chat_env.isdigit():
        targets.append(int(chat_env))
    return sorted(set(targets))


def _kill_existing_tmux(prefix: str) -> None:
    """终止所有匹配前缀的 tmux 会话，避免多实例冲突。"""

    if shutil.which("tmux") is None:
        return
    try:
        result = subprocess.run(
            ["tmux", "-u", "list-sessions"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
    except OSError:
        return
    full_prefix = prefix if prefix.endswith("-") else f"{prefix}-"
    sessions = []
    for line in result.stdout.splitlines():
        name = line.split(":", 1)[0].strip()
        if name.startswith(full_prefix):
            sessions.append(name)
    for name in sessions:
        subprocess.run(["tmux", "-u", "kill-session", "-t", name], check=False)


def _list_tmux_session_names() -> list[str]:
    """读取当前全部 tmux 会话名，供精细化清理使用。"""

    if shutil.which("tmux") is None:
        return []
    try:
        result = subprocess.run(
            ["tmux", "-u", "list-sessions"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
    except OSError:
        return []

    sessions: list[str] = []
    for line in result.stdout.splitlines():
        name = line.split(":", 1)[0].strip()
        if name:
            sessions.append(name)
    return sessions


def _parallel_tmux_prefix_for_project(project_slug: str) -> str:
    """根据项目 slug 计算该项目并行 tmux 会话前缀。"""

    normalized = (project_slug or "").strip().lower()
    if not normalized:
        return "vibe-par-"
    return f"vibe-par-{normalized[:12]}-"


def _clear_related_tmux_sessions(project_slug: Optional[str] = None) -> None:
    """清理 vibego 相关 tmux；指定项目时仅清理该项目主会话与并行会话。"""

    sessions = _list_tmux_session_names()
    if not sessions:
        return

    tmux_prefix = os.environ.get("TMUX_SESSION_PREFIX", "vibe").strip() or "vibe"
    worker_prefix = tmux_prefix if tmux_prefix.endswith("-") else f"{tmux_prefix}-"
    matched: list[str] = []

    if project_slug:
        normalized_slug = (project_slug or "").strip().lower()
        worker_session = f"{worker_prefix}{normalized_slug}"
        parallel_prefix = _parallel_tmux_prefix_for_project(normalized_slug)
        for name in sessions:
            if name == worker_session or name.startswith(parallel_prefix):
                matched.append(name)
    else:
        for name in sessions:
            if name.startswith(worker_prefix) or name.startswith("vibe-par-"):
                matched.append(name)

    for name in dict.fromkeys(matched):
        subprocess.run(["tmux", "-u", "kill-session", "-t", name], check=False)


def _mask_proxy(url: str) -> str:
    """隐藏代理 URL 中的凭据，仅保留主机与端口。"""

    if "@" not in url:
        return url
    from urllib.parse import urlparse
    parsed = urlparse(url)
    host = parsed.hostname or "***"
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://***:***@{host}{port}"


def _parse_env_file(path: Path) -> Dict[str, str]:
    """读取 .env 文件并返回键值映射。"""

    result: Dict[str, str] = {}
    if not path.exists():
        return result
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip()
    except Exception as exc:  # pylint: disable=broad-except
        log.warning("解析 MASTER_ENV_FILE 失败: %s", exc, extra={"path": str(path)})
    return result


def _dump_env_file(path: Path, values: Dict[str, str]) -> None:
    """写入 .env，默认采用 600 权限。"""

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"{key}={values[key]}" for key in sorted(values)]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        try:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except PermissionError:
            pass
    except Exception as exc:  # pylint: disable=broad-except
        log.warning("写入 MASTER_ENV_FILE 失败: %s", exc, extra={"path": str(path)})


def _update_master_env(chat_id: Optional[int], user_id: Optional[int]) -> None:
    """将最近一次 master 交互信息写入 .env。"""

    if not MASTER_ENV_FILE:
        return
    with _ENV_LOCK:
        env_map = _parse_env_file(MASTER_ENV_FILE)
        changed = False
        if chat_id is not None:
            value = str(chat_id)
            if env_map.get("MASTER_CHAT_ID") != value:
                env_map["MASTER_CHAT_ID"] = value
                changed = True
            os.environ["MASTER_CHAT_ID"] = value
        if user_id is not None:
            value = str(user_id)
            if env_map.get("MASTER_USER_ID") != value:
                env_map["MASTER_USER_ID"] = value
                changed = True
            os.environ["MASTER_USER_ID"] = value
        if changed:
            _dump_env_file(MASTER_ENV_FILE, env_map)


def _format_project_line(cfg: "ProjectConfig", state: Optional[ProjectState]) -> str:
    """格式化项目状态信息，用于日志与通知。"""

    status = state.status if state else "stopped"
    model = state.model if state else cfg.default_model
    chat_id = state.chat_id if state else cfg.allowed_chat_id
    return (
        f"- {cfg.display_name}: status={status}, model={model}, chat_id={chat_id}, project={cfg.project_slug}"
    )


def _project_jump_url(cfg: "ProjectConfig", state: Optional[ProjectState]) -> str:
    """优先使用 worker 上报的实际 username 构建跳转链接。"""

    username = state.actual_username if state and state.actual_username else cfg.bot_name
    return f"https://t.me/{username}"


def _projects_overview(manager: MasterManager) -> Tuple[str, Optional[InlineKeyboardMarkup]]:
    """根据当前项目状态生成概览文本与操作按钮。"""

    builder = InlineKeyboardBuilder()
    button_count = 0
    model_name_map = dict(SWITCHABLE_MODELS)
    for cfg in manager.configs:
        state = manager.state_store.data.get(cfg.project_slug)
        status = state.status if state else "stopped"
        current_model = (state.model if state else cfg.default_model).lower()
        current_model_label = model_name_map.get(current_model, current_model)
        jump_url = _project_jump_url(cfg, state)
        if status == "running":
            builder.row(
                InlineKeyboardButton(
                    text=f"{cfg.display_name}",
                    url=jump_url,
                ),
                InlineKeyboardButton(
                    text=f"⛔️ 停止 ({current_model_label})",
                    callback_data=f"project:stop:{cfg.project_slug}",
                ),
            )
        else:
            builder.row(
                InlineKeyboardButton(
                    text=f"{cfg.display_name}",
                    url=jump_url,
                ),
                InlineKeyboardButton(
                    text=f"▶️ 启动 ({current_model_label})",
                    callback_data=f"project:run:{cfg.project_slug}",
                ),
            )
        button_count += 1
    builder.row(
        InlineKeyboardButton(text="🚀 启动全部项目", callback_data="project:start_all:*")
    )
    builder.row(
        InlineKeyboardButton(text="⛔️ 停止全部项目", callback_data="project:stop_all:*")
    )
    builder.row(
        InlineKeyboardButton(text="🔄 重启 Master", callback_data="project:restart_master:*")
    )
    markup = builder.as_markup()
    markup = _ensure_numbered_markup(markup)
    log.info("项目概览生成按钮数量=%s", button_count)
    if button_count == 0:
        return "暂无项目配置，请在“⚙️ 项目管理”创建新项目后再尝试。", markup
    return "请选择操作：", markup


def _utcnow() -> datetime:
    """返回 UTC 当前时间，便于序列化。"""

    return datetime.now(timezone.utc)


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    """解析 ISO8601 字符串为 UTC 时间，异常时返回 None。"""

    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load_update_state() -> Dict[str, Any]:
    """读取更新检测状态，失败时返回空字典。"""

    with _UPDATE_STATE_LOCK:
        if not UPDATE_STATE_PATH.exists():
            return {}
        try:
            raw = UPDATE_STATE_PATH.read_text(encoding="utf-8")
            state = json.loads(raw) if raw.strip() else {}
            if not isinstance(state, dict):
                state = {}
            return state
        except Exception as exc:  # pragma: no cover - 极端情况下才会触发
            log.warning("读取更新状态失败：%s", exc)
            return {}


def _save_update_state(state: Dict[str, Any]) -> None:
    """持久化更新状态，确保原子写入。"""

    with _UPDATE_STATE_LOCK:
        tmp_path = UPDATE_STATE_PATH.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(UPDATE_STATE_PATH)


def _ensure_notified_list(state: Dict[str, Any]) -> List[int]:
    """保证状态中存在通知列表，并返回可变引用。"""

    notified = state.get("notified_chat_ids")
    if isinstance(notified, list):
        filtered = []
        for item in notified:
            try:
                filtered.append(int(item))
            except (TypeError, ValueError):
                continue
        state["notified_chat_ids"] = filtered
        return filtered
    state["notified_chat_ids"] = []
    return state["notified_chat_ids"]


async def _fetch_latest_version() -> Optional[str]:
    """从 PyPI 查询 vibego 最新版本，网络异常时返回 None。"""

    url = os.environ.get("VIBEGO_PYPI_JSON", "https://pypi.org/pypi/vibego/json")

    def _request() -> Optional[str]:
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                payload = json.load(resp)
        except Exception as exc:  # pragma: no cover - 网络异常时触发
            log.warning("获取 vibego 最新版本失败：%s", exc)
            return None
        info = payload.get("info") if isinstance(payload, dict) else None
        version = info.get("version") if isinstance(info, dict) else None
        if isinstance(version, str) and version.strip():
            return version.strip()
        return None

    return await asyncio.to_thread(_request)


def _is_newer_version(latest: str, current: str) -> bool:
    """比较版本号，优先使用 packaging 解析。"""

    if not latest or latest == current:
        return False
    if Version is not None:
        try:
            return Version(latest) > Version(current)
        except InvalidVersion:
            pass
    # 后备策略：按语义化版本分段比较
    def _split(value: str) -> Tuple[int, ...]:
        parts: List[int] = []
        for chunk in value.replace("-", ".").split("."):
            if not chunk:
                continue
            if chunk.isdigit():
                parts.append(int(chunk))
            else:
                return tuple(parts)
        return tuple(parts)

    return _split(latest) > _split(current)


async def _ensure_update_state(force: bool = False) -> Dict[str, Any]:
    """按需刷新更新状态，默认 24 小时触发一次网络请求。"""

    state = _load_update_state()
    now = _utcnow()
    last_check = _parse_iso_datetime(state.get("last_check"))
    need_check = force or last_check is None or (now - last_check) >= UPDATE_CHECK_INTERVAL
    if not need_check:
        return state

    latest = await _fetch_latest_version()
    state["last_check"] = now.isoformat()
    if latest:
        previous = state.get("latest_version")
        state["latest_version"] = latest
        if previous != latest:
            # 新版本出现时重置通知记录，避免遗漏提醒
            state["last_notified_version"] = ""
            state["notified_chat_ids"] = []
            state["last_notified_at"] = None
    _save_update_state(state)
    return state


async def _maybe_notify_update(
    bot: Bot,
    chat_id: int,
    *,
    force_check: bool = False,
    state: Optional[Dict[str, Any]] = None,
) -> bool:
    """若检测到新版本且未通知当前 chat，则发送提示。"""

    current_state = state if state is not None else await _ensure_update_state(force=force_check)
    latest = current_state.get("latest_version")
    if not isinstance(latest, str) or not latest.strip():
        return False
    latest = latest.strip()
    if not _is_newer_version(latest, __version__):
        return False

    notified_ids = _ensure_notified_list(current_state)
    if chat_id in notified_ids:
        return False

    message = (
        f"检测到 vibego 最新版本 v{latest}，当前运行版本为 v{__version__}。\n"
        "发送 /upgrade 可自动执行升级并重启服务。"
    )
    try:
        await bot.send_message(chat_id=chat_id, text=message)
    except Exception as exc:
        log.warning("发送升级提醒失败(chat=%s)：%s", chat_id, exc)
        return False

    notified_ids.append(chat_id)
    current_state["last_notified_version"] = latest
    current_state["last_notified_at"] = _utcnow().isoformat()
    _save_update_state(current_state)
    return True


async def _notify_update_to_targets(bot: Bot, targets: Sequence[int], *, force_check: bool = False) -> None:
    """批量向管理员推送可用更新。"""

    if not targets:
        return
    state = await _ensure_update_state(force=force_check)
    sent = 0
    for chat_id in targets:
        if await _maybe_notify_update(bot, chat_id, state=state):
            sent += 1
    if sent:
        log.info("已向 %s 个管理员推送升级提示", sent)


def _sanitize_upgrade_line(raw: str) -> str:
    """去除 ANSI 控制字符并限制单行长度。"""

    if not raw:
        return ""
    text = raw.replace("\r", "")
    text = _ANSI_ESCAPE_RE.sub("", text)
    filtered = "".join(ch for ch in text if ch == "\t" or ch == " " or ch.isprintable())
    cleaned = filtered.strip("\n")
    if len(cleaned) > _UPGRADE_LINE_LIMIT:
        return cleaned[: _UPGRADE_LINE_LIMIT - 1] + "…"
    return cleaned


def _render_upgrade_preview(lines: Sequence[str]) -> str:
    """渲染最近若干行日志，便于推送到 Telegram。"""

    if not lines:
        return "（暂无输出）"
    tail = list(lines[-_UPGRADE_LOG_TAIL:])
    return "\n".join(tail)


def _extract_upgrade_versions(lines: Sequence[str]) -> Tuple[Optional[str], Optional[str]]:
    """从 pipx 输出中提取旧/新版本，若未匹配则返回 None。"""

    for line in reversed(lines):
        match = _PIPX_VERSION_RE.search(line)
        if match:
            return match.group("old"), match.group("new")
    return None, None


async def _safe_edit_upgrade_message(
    bot: Bot,
    chat_id: int,
    message_id: int,
    text: str,
) -> None:
    """安全地更新升级状态消息，忽略不可修改的异常。"""

    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            disable_web_page_preview=True,
        )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            log.warning("升级状态消息更新失败: %s", exc)
    except TelegramForbiddenError as exc:
        log.warning("升级状态消息已无法访问(chat=%s): %s", chat_id, exc)
    except Exception as exc:  # pragma: no cover - 捕获不可预期错误，避免任务崩溃
        log.error("升级状态消息更新遇到异常: %s", exc)


async def _run_single_upgrade_step(
    command: str,
    description: str,
    step_index: int,
    total_steps: int,
    bot: Bot,
    chat_id: int,
    message_id: int,
) -> Tuple[int, List[str]]:
    """执行单个升级命令并实时推送日志。"""

    process = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        stdin=asyncio.subprocess.DEVNULL,  # 升级流程走后台运行，显式接管 /dev/null 防止 stdin 失效
        cwd=str(ROOT_DIR),
    )
    assert process.stdout is not None  # mypy 安心用
    lines: List[str] = []
    loop = asyncio.get_running_loop()
    last_push = 0.0

    async def _push(status: str, *, force: bool = False) -> None:
        """按节流频率将最新日志写回 Telegram。"""

        nonlocal last_push
        now = loop.time()
        if not force and (now - last_push) < 1.0:
            return
        last_push = now
        preview = _render_upgrade_preview(lines)
        text = (
            f"升级流水线进行中（步骤 {step_index}/{total_steps}）\n"
            f"当前动作：{description}\n"
            f"命令：{command}\n"
            f"状态：{status}\n\n"
            f"最近输出（最多 {_UPGRADE_LOG_TAIL} 行）：\n{preview}"
        )
        await _safe_edit_upgrade_message(bot, chat_id, message_id, text)

    await _push("准备执行", force=True)
    while True:
        chunk = await process.stdout.readline()
        if not chunk:
            break
        sanitized = _sanitize_upgrade_line(chunk.decode(errors="ignore"))
        if not sanitized:
            continue
        lines.append(sanitized)
        if len(lines) > _UPGRADE_LOG_BUFFER_LIMIT:
            del lines[0]
        await _push("执行中", force=False)

    returncode = await process.wait()
    await _push(f"步骤结束（退出码 {returncode}）", force=True)
    return returncode, lines


async def _notify_upgrade_failure(
    bot: Bot,
    chat_id: int,
    message_id: int,
    description: str,
    command: str,
    lines: Sequence[str],
    returncode: Optional[int] = None,
    *,
    error: Optional[str] = None,
) -> None:
    """升级失败后推送详细日志，方便管理员排障。"""

    reason = f"退出码：{returncode}" if returncode is not None else ""
    if error:
        reason = f"异常：{error}"
    preview = _render_upgrade_preview(lines)
    text = (
        "升级流程失败 ❌\n"
        f"失败步骤：{description}\n"
        f"命令：{command}\n"
        f"{reason}\n"
        "请登录服务器手动执行 `pipx upgrade vibego && vibego stop && vibego start` 检查详情。\n\n"
        f"最近输出：\n{preview}"
    )
    await _safe_edit_upgrade_message(bot, chat_id, message_id, text)


def _persist_upgrade_report(
    chat_id: int,
    lines: Sequence[str],
    elapsed: float,
    restart_command: str,
    restart_delay: float,
) -> None:
    """将 pipx 阶段的输出写入升级报告，供新 master 启动后推送。"""

    old_version, new_version = _extract_upgrade_versions(lines)
    payload = {
        "chat_id": chat_id,
        "log_tail": list(lines[-_UPGRADE_LOG_TAIL:]),
        "elapsed": round(elapsed, 3),
        "restart_command": restart_command,
        "restart_delay": restart_delay,
        "restart_log_path": str(_UPGRADE_RESTART_LOG_PATH),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "version": __version__,
        "old_version": old_version,
        "new_version": new_version,
    }
    _UPGRADE_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _UPGRADE_REPORT_PATH.with_suffix(_UPGRADE_REPORT_PATH.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(_UPGRADE_REPORT_PATH)


def _spawn_detached_restart(command: str, delay: float) -> Optional[subprocess.Popen[str]]:
    """以延迟方式异步执行 stop/start，确保 master 停止后仍能继续。"""

    cleaned = command.strip()
    if not cleaned:
        return None
    safe_delay = max(0.0, delay)
    shell_command = f"sleep {safe_delay:.3f} && {cleaned}"
    log_fp = None
    try:
        _UPGRADE_RESTART_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        log_fp = _UPGRADE_RESTART_LOG_PATH.open("a", encoding="utf-8")
        log_fp.write(
            f"\n[{_utcnow().isoformat()}] 安排升级后重启：delay={safe_delay:.3f}s command={cleaned}\n"
        )
        log_fp.flush()
    except OSError:
        if log_fp:
            log_fp.close()
        log_fp = None

    try:
        return subprocess.Popen(
            ["bash", "-lc", shell_command],
            cwd=str(ROOT_DIR),
            stdin=subprocess.DEVNULL,  # 重启命令也在后台执行，stdin 绑定 /dev/null 避免描述符被关闭
            stdout=log_fp or subprocess.DEVNULL,
            stderr=log_fp or subprocess.DEVNULL,
            start_new_session=True,
        )
    finally:
        if log_fp:
            log_fp.close()


async def _announce_upgrade_completion(
    bot: Bot,
    chat_id: int,
    message_id: int,
    lines: Sequence[str],
    started_at: float,
) -> None:
    """记录成功结果并提示即将重启或保持在线。"""

    elapsed = time.monotonic() - started_at
    preview = _render_upgrade_preview(lines)
    restart_command = _UPGRADE_RESTART_COMMAND.strip()
    if not restart_command:
        text = (
            "升级流程完成 ✅\n"
            f"pipx upgrade 耗时 {elapsed:.1f} 秒。\n"
            "未配置自动重启命令，请手动执行 `vibego stop && vibego start` 完成切换。\n\n"
            f"最近输出（最多 {_UPGRADE_LOG_TAIL} 行）：\n{preview}"
        )
        await _safe_edit_upgrade_message(bot, chat_id, message_id, text)
        return

    _persist_upgrade_report(chat_id, lines, elapsed, restart_command, _UPGRADE_RESTART_DELAY)
    text = (
        "升级流程完成（pipx 阶段） ✅\n"
        f"pipx upgrade 耗时 {elapsed:.1f} 秒，将在 {_UPGRADE_RESTART_DELAY:.1f} 秒后执行：{restart_command}\n"
        "master 即将重启并短暂离线，稍后使用 /start 验证状态。\n\n"
        f"重启日志：{_UPGRADE_RESTART_LOG_PATH}\n\n"
        f"最近输出（最多 {_UPGRADE_LOG_TAIL} 行）：\n{preview}"
    )
    await _safe_edit_upgrade_message(bot, chat_id, message_id, text)
    proc = _spawn_detached_restart(restart_command, _UPGRADE_RESTART_DELAY)
    if proc:
        log.info("已安排升级后自动重启", extra={"pid": proc.pid, "delay": _UPGRADE_RESTART_DELAY})
    else:
        log.warning("升级成功但未能启动自动重启命令", extra={"command": restart_command})


async def _run_upgrade_pipeline(bot: Bot, chat_id: int, message_id: int) -> None:
    """串行执行 pipx upgrade / stop / start，并实时推送日志。"""

    started_at = time.monotonic()
    total_steps = len(_UPGRADE_COMMANDS)
    last_lines: List[str] = []
    for index, (command, description) in enumerate(_UPGRADE_COMMANDS, start=1):
        log.info("升级步骤 %s/%s：%s", index, total_steps, command)
        try:
            returncode, lines = await _run_single_upgrade_step(
                command,
                description,
                index,
                total_steps,
                bot,
                chat_id,
                message_id,
            )
        except Exception as exc:  # pragma: no cover - 捕获不可预期异常
            log.exception("升级步骤 %s 发生异常", description)
            await _notify_upgrade_failure(
                bot,
                chat_id,
                message_id,
                description,
                command,
                [],
                error=str(exc),
            )
            return

        if returncode != 0:
            await _notify_upgrade_failure(
                bot,
                chat_id,
                message_id,
                description,
                command,
                lines,
                returncode,
            )
            return
        last_lines = lines

    await _announce_upgrade_completion(bot, chat_id, message_id, last_lines, started_at)


async def _periodic_update_check(bot: Bot) -> None:
    """后台周期性检查版本更新并通知管理员。"""

    await asyncio.sleep(10)
    while True:
        try:
            await _notify_update_to_targets(bot, _collect_admin_targets(), force_check=True)
        except Exception as exc:  # pragma: no cover - 宕机调试使用
            log.error("自动版本检测失败: %s", exc)
        await asyncio.sleep(int(UPDATE_CHECK_INTERVAL.total_seconds()))


def _detect_proxy() -> Tuple[Optional[str], Optional[BasicAuth], Optional[str]]:
    """从环境变量解析可用的代理配置。"""

    candidates = [
        ("TELEGRAM_PROXY", os.environ.get("TELEGRAM_PROXY")),
        ("https_proxy", os.environ.get("https_proxy")),
        ("HTTPS_PROXY", os.environ.get("HTTPS_PROXY")),
        ("http_proxy", os.environ.get("http_proxy")),
        ("HTTP_PROXY", os.environ.get("HTTP_PROXY")),
        ("all_proxy", os.environ.get("all_proxy")),
        ("ALL_PROXY", os.environ.get("ALL_PROXY")),
    ]
    proxy_raw: Optional[str] = None
    source: Optional[str] = None
    for key, value in candidates:
        if value:
            proxy_raw = value.strip()
            source = key
            break
    if not proxy_raw:
        return None, None, None
    from urllib.parse import urlparse
    parsed = urlparse(proxy_raw)
    auth: Optional[BasicAuth] = None
    if parsed.username:
        password = parsed.password or ""
        auth = BasicAuth(parsed.username, password)
        netloc = parsed.hostname or ""
        if parsed.port:
            netloc += f":{parsed.port}"
        proxy_raw = parsed._replace(netloc=netloc, path="", params="", query="", fragment="").geturl()
    log.info("使用代理(%s): %s", source, _mask_proxy(proxy_raw))
    return proxy_raw, auth, source


def _sanitize_slug(text: str) -> str:
    """将任意字符串转换为 project_slug 可用的短标签。"""

    slug = text.lower().replace(" ", "-")
    slug = slug.replace("/", "-").replace("\\", "-")
    slug = slug.strip("-")
    return slug or "project"


@dataclass
class ProjectConfig:
    """描述单个项目的静态配置。"""

    bot_name: str
    bot_token: str
    project_slug: str
    default_model: str = "codex"
    workdir: Optional[str] = None
    allowed_chat_id: Optional[int] = None
    legacy_name: Optional[str] = None

    def __post_init__(self) -> None:
        """保证 bot 名称合法，去除多余前缀与空白。"""

        clean_name = self.bot_name.strip()
        if clean_name.startswith("@"):  # 允许配置中直接写带@
            clean_name = clean_name[1:]
        clean_name = clean_name.strip()
        if not clean_name:
            raise ValueError("bot_name 不能为空")
        self.bot_name = clean_name

    @property
    def display_name(self) -> str:
        """返回展示用的 bot 名称。"""

        return self.bot_name

    @property
    def jump_url(self) -> str:
        """生成跳转到 Telegram Bot 的链接。"""

        return f"https://t.me/{self.bot_name}"

    @classmethod
    def from_dict(cls, data: dict) -> "ProjectConfig":
        """从 JSON 字典构造 ProjectConfig 实例。"""

        raw_bot_name = data.get("bot_name") or data.get("name")
        if not raw_bot_name:
            raise KeyError("bot_name")
        bot_name = str(raw_bot_name)
        slug_source = data.get("project_slug") or bot_name
        allowed = data.get("allowed_chat_id")
        if isinstance(allowed, str) and allowed.isdigit():
            allowed = int(allowed)
        cfg = cls(
            bot_name=bot_name,
            bot_token=data["bot_token"].strip(),
            project_slug=_sanitize_slug(slug_source),
            default_model=data.get("default_model", "codex"),
            workdir=data.get("workdir"),
            allowed_chat_id=allowed,
            legacy_name=str(data.get("name", "")).strip() or None,
        )
        return cfg


@dataclass
class ProjectState:
    """表示项目当前运行状态，由 StateStore 持久化。"""

    model: str
    status: str = "stopped"
    chat_id: Optional[int] = None
    actual_username: Optional[str] = None
    telegram_user_id: Optional[int] = None


class StateStore:
    """负责维护项目运行状态的文件持久化。"""

    def __init__(self, path: Path, configs: Dict[str, ProjectConfig]):
        """初始化状态存储，加载已有 state 文件并对缺失项使用默认值。"""

        self.path = path
        self.configs = configs  # key 使用 project_slug
        self.data: Dict[str, ProjectState] = {}
        self.refresh()
        self.save()

    def reset_configs(
        self,
        configs: Dict[str, ProjectConfig],
        preserve: Optional[Dict[str, ProjectState]] = None,
    ) -> None:
        """更新配置映射，新增项目时写入默认状态，删除项目时移除记录。"""
        self.configs = configs
        dirty = False
        # 移除已删除项目的状态
        for slug in list(self.data.keys()):
            if slug not in configs:
                del self.data[slug]
                dirty = True
        # 为新增项目补充默认状态
        for slug, cfg in configs.items():
            if slug not in self.data:
                self.data[slug] = ProjectState(
                    model=cfg.default_model,
                    status="stopped",
                    chat_id=cfg.allowed_chat_id,
                )
                dirty = True
            if self._sync_bot_identity(slug):
                dirty = True
        if preserve:
            self.data.update(preserve)
            dirty = True
        if dirty or not self.path.exists():
            self.save()

    def refresh(self) -> None:
        """从 state 文件重新加载所有项目状态。"""

        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                log.warning("无法解析 state 文件 %s，使用空状态", self.path)
                raw = {}
        else:
            raw = {}
        dirty = False
        for slug, cfg in self.configs.items():
            item = (
                raw.get(slug)
                or raw.get(cfg.bot_name)
                or raw.get(f"@{cfg.bot_name}")
                or (cfg.legacy_name and raw.get(cfg.legacy_name))
                or {}
            )
            model = item.get("model", cfg.default_model)
            status = item.get("status", "stopped")
            chat_id_value = item.get("chat_id", cfg.allowed_chat_id)
            if isinstance(chat_id_value, str) and chat_id_value.isdigit():
                chat_id_value = int(chat_id_value)
            username = item.get("actual_username")
            if isinstance(username, str):
                username = username.strip() or None
            telegram_user_id = item.get("telegram_user_id")
            if isinstance(telegram_user_id, str) and telegram_user_id.isdigit():
                telegram_user_id = int(telegram_user_id)
            elif not isinstance(telegram_user_id, int):
                telegram_user_id = None
            self.data[slug] = ProjectState(
                model=model,
                status=status,
                chat_id=chat_id_value,
                actual_username=username,
                telegram_user_id=telegram_user_id,
            )
            if self._sync_bot_identity(slug):
                dirty = True
        if dirty:
            self.save()

    def save(self) -> None:
        """将当前内存状态写入磁盘文件。"""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            slug: {
                "model": state.model,
                "status": state.status,
                "chat_id": state.chat_id,
                **(
                    {"actual_username": state.actual_username}
                    if state.actual_username
                    else {}
                ),
                **(
                    {"telegram_user_id": state.telegram_user_id}
                    if state.telegram_user_id is not None
                    else {}
                ),
            }
            for slug, state in self.data.items()
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def update(
        self,
        slug: str,
        *,
        model: Optional[str] = None,
        status: Optional[str] = None,
        chat_id: Optional[int] = None,
        actual_username: Optional[str] = None,
        telegram_user_id: Optional[int] = None,
    ) -> None:
        """更新指定项目的状态并立即持久化。"""

        state = self.data[slug]
        if model is not None:
            state.model = model
        if status is not None:
            state.status = status
        if chat_id is not None:
            state.chat_id = chat_id
        if actual_username is not None:
            cleaned = actual_username.strip() if isinstance(actual_username, str) else actual_username
            state.actual_username = cleaned or None
        if telegram_user_id is not None:
            state.telegram_user_id = telegram_user_id
        self.save()

    def _sync_bot_identity(self, slug: str) -> bool:
        """根据 bot token 自动补全 Telegram username。"""

        cfg = self.configs.get(slug)
        state = self.data.get(slug)
        if not cfg or not state or state.actual_username:
            return False
        try:
            username, telegram_user_id = _fetch_bot_identity(cfg.bot_token)
        except BotIdentityError as exc:
            log.debug(
                "自动解析 %s username 失败：%s",
                cfg.display_name,
                exc,
                extra={"project": slug},
            )
            return False
        state.actual_username = username
        if telegram_user_id is not None:
            state.telegram_user_id = telegram_user_id
        log.info(
            "已自动写入 %s 的 username=%s",
            cfg.display_name,
            username,
            extra={"project": slug},
        )
        return True


class BotIdentityError(Exception):
    """表示从 Telegram Bot API 拉取身份信息失败。"""


def _fetch_bot_identity(bot_token: str) -> Tuple[str, Optional[int]]:
    """调用 Telegram getMe 接口获取 username/id。"""

    token = bot_token.strip()
    if not token:
        raise BotIdentityError("bot_token 为空")
    url = f"{TELEGRAM_API_ROOT}/bot{token}/getMe"
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            raw = response.read().decode("utf-8")
    except URLError as exc:
        raise BotIdentityError(f"网络请求失败：{exc}") from exc
    except OSError as exc:
        raise BotIdentityError(f"请求 Telegram API 失败：{exc}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BotIdentityError("无法解析 getMe 响应为 JSON") from exc
    if not isinstance(payload, dict) or not payload.get("ok"):
        raise BotIdentityError(f"getMe 返回异常：{payload}")
    result = payload.get("result") or {}
    username = result.get("username")
    if not isinstance(username, str) or not username.strip():
        raise BotIdentityError("getMe 响应缺少 username")
    user_id = result.get("id")
    if isinstance(user_id, str) and user_id.isdigit():
        user_id = int(user_id)
    elif not isinstance(user_id, int):
        user_id = None
    return username.strip(), user_id


class MasterManager:
    """封装项目配置、状态持久化与前置检查等核心逻辑。"""

    def __init__(self, configs: List[ProjectConfig], *, state_store: StateStore):
        """构建 manager，并基于配置建立 slug/mention 索引。"""

        self.configs = configs
        self._slug_index: Dict[str, ProjectConfig] = {cfg.project_slug: cfg for cfg in configs}
        self._mention_index: Dict[str, ProjectConfig] = {}
        for cfg in configs:
            self._mention_index[cfg.bot_name] = cfg
            self._mention_index[f"@{cfg.bot_name}"] = cfg
            if cfg.legacy_name:
                self._mention_index[cfg.legacy_name] = cfg
        self.state_store = state_store
        admins = os.environ.get("MASTER_ADMIN_IDS") or os.environ.get("ALLOWED_CHAT_ID", "")
        self.admin_ids = {int(x) for x in admins.split(",") if x.strip().isdigit()}

    def require_project(self, name: str) -> ProjectConfig:
        """根据项目名或 @bot 名查找配置，找不到时报错。"""

        cfg = self._resolve_project(name)
        if not cfg:
            raise ValueError(f"未知项目 {name}")
        return cfg

    def require_project_by_slug(self, slug: str) -> ProjectConfig:
        """根据 project_slug 查找配置。"""

        cfg = self._slug_index.get(slug)
        if not cfg:
            raise ValueError(f"未知项目 {slug}")
        return cfg

    def _resolve_project(self, identifier: str) -> Optional[ProjectConfig]:
        """在 slug/mention 索引中寻找匹配的项目配置。"""

        if not identifier:
            return None
        raw = identifier.strip()
        if not raw:
            return None
        if raw in self._slug_index:
            return self._slug_index[raw]
        if raw in self._mention_index:
            return self._mention_index[raw]
        if raw.startswith("@"):  # 允许用户直接输入 @bot_name
            stripped = raw[1:]
            if stripped in self._mention_index:
                return self._mention_index[stripped]
        else:
            mention_form = f"@{raw}"
            if mention_form in self._mention_index:
                return self._mention_index[mention_form]
        return None

    def rebuild_configs(
        self,
        configs: List[ProjectConfig],
        preserve: Optional[Dict[str, ProjectState]] = None,
    ) -> None:
        """刷新项目配置索引，便于在新增/删除后立即生效。"""
        self.configs = configs
        self._slug_index = {cfg.project_slug: cfg for cfg in configs}
        self._mention_index = {}
        for cfg in configs:
            self._mention_index[cfg.bot_name] = cfg
            self._mention_index[f"@{cfg.bot_name}"] = cfg
            if cfg.legacy_name:
                self._mention_index[cfg.legacy_name] = cfg
        self.state_store.reset_configs({cfg.project_slug: cfg for cfg in configs}, preserve=preserve)

    def refresh_state(self) -> None:
        """从磁盘重新加载项目运行状态。"""

        self.state_store.refresh()

    def list_states(self) -> Dict[str, ProjectState]:
        """返回当前所有项目的状态字典。"""

        return self.state_store.data

    def is_authorized(self, chat_id: int) -> bool:
        """检查给定 chat_id 是否在管理员名单中。"""

        return not self.admin_ids or chat_id in self.admin_ids

    @staticmethod
    def _format_issue_message(title: str, issues: Sequence[str]) -> str:
        """按照项目自检的结果拼装 Markdown 文本。"""

        lines: List[str] = []
        for issue in issues:
            if "\n" in issue:
                first, *rest = issue.splitlines()
                lines.append(f"- {first}")
                lines.extend(f"  {line}" for line in rest)
            else:
                lines.append(f"- {issue}")
        joined = "\n".join(lines) if lines else "- 无"
        return f"{title}\n{joined}"

    def _collect_prerequisite_issues(self, cfg: ProjectConfig, model: str) -> List[str]:
        """检查模型启动前的依赖条件，返回所有未满足的项。"""

        issues: List[str] = []
        workdir_raw = (cfg.workdir or "").strip()
        if not workdir_raw:
            issues.append(
                "未配置 workdir，请通过项目管理功能为该项目设置工作目录"
            )
            expanded_dir = None
        else:
            expanded = Path(os.path.expandvars(os.path.expanduser(workdir_raw)))
            if not expanded.exists():
                issues.append(f"工作目录不存在: {workdir_raw}")
                expanded_dir = None
            elif not expanded.is_dir():
                issues.append(f"工作目录不是文件夹: {workdir_raw}")
                expanded_dir = None
            else:
                expanded_dir = expanded

        if not cfg.bot_token:
            issues.append("bot_token 未配置，请通过项目管理功能补充该字段")

        if shutil.which("tmux") is None:
            issues.append("未检测到 tmux，可通过 'brew install tmux' 安装")

        model_lower = (model or "").lower()
        model_cmd = os.environ.get("MODEL_CMD")
        if not model_cmd:
            if model_lower == "codex":
                model_cmd = os.environ.get("CODEX_CMD") or "codex"
            elif model_lower == "claudecode":
                model_cmd = os.environ.get("CLAUDE_CMD") or "claude"
            elif model_lower == "gemini":
                # Gemini 默认可直接通过 `gemini` 命令启动（Homebrew: gemini-cli）
                model_cmd = os.environ.get("GEMINI_CMD") or "gemini"
            elif model_lower == "copilot":
                model_cmd = os.environ.get("COPILOT_CMD") or "copilot"

        if model_cmd:
            try:
                executable = shlex.split(model_cmd)[0]
            except ValueError:
                executable = None
            if executable and shutil.which(executable) is None:
                issues.append(f"未检测到模型命令 {executable}，请确认已安装")
        elif model_lower != "gemini":
            issues.append("未找到模型命令配置，无法启动 worker")

        if expanded_dir is None and workdir_raw:
            log.debug(
                "工作目录校验失败",
                extra={"project": cfg.project_slug, "workdir": workdir_raw},
            )

        return issues

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        """检测指定 PID 的进程是否仍在运行。"""

        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        else:
            return True

    def _log_tail(self, path: Path, *, lines: int = WORKER_HEALTH_LOG_TAIL) -> str:
        """读取日志文件尾部，协助诊断启动失败原因。"""

        if not path.exists():
            return ""
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as fh:
                data = fh.readlines()
        except Exception as exc:
            log.warning(
                "读取日志失败: %s",
                exc,
                extra={"log_path": str(path)},
            )
            return ""
        if not data:
            return ""
        tail = data[-lines:]
        return "".join(tail).rstrip()

    def _log_contains_handshake(self, path: Path, *, boot_id: Optional[str] = None) -> bool:
        """判断日志中是否包含 Telegram 握手成功的标记。

        run_bot.sh 默认以追加模式写入 run_bot.log，旧版本的“握手成功”日志可能导致误判。
        若提供 boot_id，则只在对应 boot_id 之后的日志片段中匹配握手标记。
        """

        if not path.exists():
            return False

        # 仅扫描尾部，避免大文件在健康检查轮询中频繁全量读取。
        text = self._log_tail(path, lines=max(WORKER_HEALTH_LOG_TAIL, 200))
        if not text:
            return False

        if boot_id:
            token = f"{WORKER_BOOT_ID_LOG_PREFIX}{boot_id}"
            idx = text.rfind(token)
            if idx < 0:
                return False
            text = text[idx:]

        return any(marker in text for marker in HANDSHAKE_MARKERS)

    async def _health_check_worker(
        self,
        cfg: ProjectConfig,
        model: str,
        *,
        boot_id: Optional[str] = None,
    ) -> Optional[str]:
        """验证 worker 启动后的健康状态，返回失败描述。"""

        log_dir = LOG_ROOT_PATH / model / cfg.project_slug
        pid_path = log_dir / "bot.pid"
        run_log = log_dir / "run_bot.log"

        deadline = time.monotonic() + WORKER_HEALTH_TIMEOUT
        last_seen_pid: Optional[int] = None

        while time.monotonic() < deadline:
            if pid_path.exists():
                try:
                    pid_text = pid_path.read_text(encoding="utf-8", errors="ignore").strip()
                    if pid_text:
                        last_seen_pid = int(pid_text)
                        if not self._pid_alive(last_seen_pid):
                            break
                except ValueError:
                    log.warning(
                        "pid 文件 %s 内容异常",
                        str(pid_path),
                        extra={"content": pid_path.read_text(encoding="utf-8", errors="ignore")},
                    )
                    last_seen_pid = None
                except Exception as exc:
                    log.warning(
                        "读取 pid 文件失败: %s",
                        exc,
                        extra={"pid_path": str(pid_path)},
                    )

            if self._log_contains_handshake(run_log, boot_id=boot_id):
                return None

            await asyncio.sleep(WORKER_HEALTH_INTERVAL)

        issues: List[str] = []
        if last_seen_pid is None:
            issues.append("未检测到 bot.pid 或内容为空")
        else:
            if self._pid_alive(last_seen_pid):
                issues.append(
                    f"worker 进程 {last_seen_pid} 未在 {WORKER_HEALTH_TIMEOUT:.1f}s 内完成 Telegram 握手"
                )
            else:
                issues.append(f"worker 进程 {last_seen_pid} 已退出")

        log_tail = self._log_tail(run_log)
        if log_tail:
            issues.append(
                "最近日志:\n" + textwrap.indent(log_tail, prefix="  ")
            )

        if not issues:
            return None

        return self._format_issue_message(
            f"{cfg.display_name} 启动失败",
            issues,
        )

    async def run_worker(self, cfg: ProjectConfig, model: Optional[str] = None) -> str:
        """启动指定项目的 worker，并返回运行模型名称。"""

        self.refresh_state()
        state = self.state_store.data[cfg.project_slug]
        target_model = model or state.model or cfg.default_model
        issues = self._collect_prerequisite_issues(cfg, target_model)
        if issues:
            message = self._format_issue_message(
                f"{cfg.display_name} 启动失败，缺少必要依赖或配置",
                issues,
            )
            log.error(
                "启动前自检失败: %s",
                message,
                extra={"project": cfg.project_slug, "model": target_model},
            )
            raise RuntimeError(message)
        workdir_path = Path(os.path.expandvars(os.path.expanduser(cfg.workdir or "")))
        if (target_model or "").strip().lower() == "codex":
            try:
                ensure_codex_project_trust(workdir_path, config_path=CODEX_CONFIG_PATH)
            except Exception as exc:
                message = f"项目目录 Codex trusted 自动配置失败：{exc}"
                log.error(
                    "项目启动前的 Codex trusted 校验失败: %s",
                    message,
                    extra={"project": cfg.project_slug, "model": target_model, "workdir": str(workdir_path)},
                )
                raise RuntimeError(message) from exc
        chat_id_env = state.chat_id or cfg.allowed_chat_id
        env = os.environ.copy()
        boot_id = uuid.uuid4().hex
        env.update(
            {
                "BOT_TOKEN": cfg.bot_token,
                "MODEL_DEFAULT": target_model,
                "PROJECT_NAME": cfg.project_slug,
                "MODEL_WORKDIR": cfg.workdir or "",
                "CODEX_WORKDIR": cfg.workdir or env.get("CODEX_WORKDIR", ""),
                "CLAUDE_WORKDIR": cfg.workdir or env.get("CLAUDE_WORKDIR", ""),
                "GEMINI_WORKDIR": cfg.workdir or env.get("GEMINI_WORKDIR", ""),
                "STATE_FILE": str(STATE_PATH),
                WORKER_BOOT_ID_ENV: boot_id,
            }
        )
        cmd = [str(RUN_SCRIPT), "--model", target_model, "--project", cfg.project_slug]
        log.info(
            "启动 worker: %s (model=%s, chat_id=%s)",
            cfg.display_name,
            target_model,
            chat_id_env,
            extra={"project": cfg.project_slug, "model": target_model},
        )
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(ROOT_DIR),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()
        rc = proc.returncode
        output_chunks: List[str] = []
        if stdout_bytes:
            output_chunks.append(stdout_bytes.decode("utf-8", errors="ignore"))
        if stderr_bytes:
            output_chunks.append(stderr_bytes.decode("utf-8", errors="ignore"))
        combined_output = "".join(output_chunks).strip()
        if rc != 0:
            tail_lines = "\n".join(combined_output.splitlines()[-20:]) if combined_output else ""
            issues = [f"run_bot.sh 退出码 {rc}"]
            if tail_lines:
                issues.append("脚本输出:\n  " + "\n  ".join(tail_lines.splitlines()))
            message = self._format_issue_message(
                f"{cfg.display_name} 启动失败",
                issues,
            )
            log.error(
                "worker 启动失败: %s",
                message,
                extra={"project": cfg.project_slug, "model": target_model},
            )
            raise RuntimeError(message)
        health_issue = await self._health_check_worker(cfg, target_model, boot_id=boot_id)
        if health_issue:
            self.state_store.update(cfg.project_slug, status="stopped")
            log.error(
                "worker 健康检查失败: %s",
                health_issue,
                extra={"project": cfg.project_slug, "model": target_model},
            )
            raise RuntimeError(health_issue)

        self.state_store.update(cfg.project_slug, model=target_model, status="running")
        return target_model

    async def stop_worker(self, cfg: ProjectConfig, *, update_state: bool = True) -> None:
        """停止指定项目的 worker，必要时刷新状态。"""

        self.refresh_state()
        state = self.state_store.data[cfg.project_slug]
        model = state.model
        cmd = [str(STOP_SCRIPT), "--model", model, "--project", cfg.project_slug]
        proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(ROOT_DIR))
        await proc.wait()
        _clear_related_tmux_sessions(cfg.project_slug)
        if update_state:
            self.state_store.update(cfg.project_slug, status="stopped")
        log.info("已停止 worker: %s", cfg.display_name, extra={"project": cfg.project_slug})

    async def stop_all(self, *, update_state: bool = False) -> None:
        """依次停止所有项目的 worker。"""

        for cfg in self.configs:
            try:
                await self.stop_worker(cfg, update_state=update_state)
            except Exception as exc:
                log.warning(
                    "停止 %s 时出错: %s",
                    cfg.display_name,
                    exc,
                    extra={"project": cfg.project_slug},
                )

    async def run_all(self) -> None:
        """启动所有尚未运行的项目 worker。"""

        self.refresh_state()
        errors: List[str] = []
        for cfg in self.configs:
            state = self.state_store.data.get(cfg.project_slug)
            if state and state.status == "running":
                continue
            try:
                await self.run_worker(cfg)
            except Exception as exc:
                log.warning(
                    "启动 %s 时出错: %s",
                    cfg.display_name,
                    exc,
                    extra={"project": cfg.project_slug},
                )
                errors.append(f"{cfg.display_name}: {exc}")
        if errors:
            raise RuntimeError(
                self._format_issue_message("部分项目启动失败", errors)
            )

    async def restore_running(self) -> None:
        """根据 state 文件恢复上一轮仍在运行的 worker。"""

        self.refresh_state()
        for slug, state in self.state_store.data.items():
            if state.status == "running":
                cfg = self._slug_index.get(slug)
                if not cfg:
                    log.warning("状态文件包含未知项目: %s", slug)
                    continue
                try:
                    await self.run_worker(cfg, model=state.model)
                except Exception as exc:
                    log.error(
                        "恢复 %s 失败: %s",
                        cfg.display_name,
                        exc,
                        extra={"project": cfg.project_slug, "model": state.model},
                    )
                    self.state_store.update(slug, status="stopped")

    def update_chat_id(self, slug: str, chat_id: int) -> None:
        """记录或更新项目的 chat_id 绑定信息。"""

        cfg = self._resolve_project(slug)
        if not cfg:
            raise ValueError(f"未知项目 {slug}")
        self.state_store.update(cfg.project_slug, chat_id=chat_id)
        log.info(
            "记录 %s 的 chat_id=%s",
            cfg.display_name,
            chat_id,
            extra={"project": cfg.project_slug},
        )


MANAGER: Optional[MasterManager] = None
PROJECT_REPOSITORY: Optional[ProjectRepository] = None
ProjectField = Literal["bot_name", "bot_token", "project_slug", "default_model", "workdir", "allowed_chat_id"]


@dataclass
class ProjectWizardSession:
    """记录单个聊天的项目管理对话状态。"""

    chat_id: int
    user_id: int
    mode: Literal["create", "edit", "delete"]
    original_slug: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)
    step_index: int = 0
    original_record: Optional[ProjectRecord] = None
    fields: Tuple[ProjectField, ...] = field(default_factory=tuple)


PROJECT_WIZARD_FIELDS_CREATE: Tuple[ProjectField, ...] = (
    "bot_name",
    "bot_token",
    "default_model",
    "workdir",
)
PROJECT_WIZARD_FIELDS_EDIT: Tuple[ProjectField, ...] = (
    "bot_name",
    "bot_token",
    "project_slug",
    "default_model",
    "workdir",
    "allowed_chat_id",
)
PROJECT_WIZARD_OPTIONAL_FIELDS: Tuple[ProjectField, ...] = ("workdir", "allowed_chat_id")
PROJECT_MODEL_CHOICES: Tuple[str, ...] = ("codex", "claudecode", "gemini", "copilot")
PROJECT_WIZARD_SESSIONS: Dict[int, ProjectWizardSession] = {}
PROJECT_WIZARD_LOCK: Optional[asyncio.Lock] = None
PROJECT_FIELD_PROMPTS_CREATE: Dict[ProjectField, str] = {
    "bot_name": "请输入 bot 名称（不含 @，仅字母、数字、下划线或点）：",
    "bot_token": "请输入 Telegram Bot Token（格式类似 123456:ABCdef）：",
    "project_slug": "请输入项目 slug（用于日志目录，留空自动根据 bot 名生成）：",
    "default_model": "请输入默认模型（codex/claudecode/gemini/copilot，留空采用 codex）：",
    "workdir": "请输入 worker 工作目录绝对路径（可留空稍后补全）：",
    "allowed_chat_id": "请输入预设 chat_id（可留空，暂不支持多个）：",
}
PROJECT_FIELD_PROMPTS_EDIT: Dict[ProjectField, str] = {
    "bot_name": "请输入新的 bot 名（不含 @，发送 - 保持当前值：{current}）：",
    "bot_token": "请输入新的 Bot Token（发送 - 保持当前值）：",
    "project_slug": "请输入新的项目 slug（发送 - 保持当前值：{current}）：",
    "default_model": "请输入新的默认模型（codex/claudecode/gemini/copilot，发送 - 保持当前值：{current}）：",
    "workdir": "请输入新的工作目录（发送 - 保持当前值：{current}，可留空改为未设置）：",
    "allowed_chat_id": "请输入新的 chat_id（发送 - 保持当前值：{current}，留空表示取消预设）：",
}


def get_project_wizard_lock() -> asyncio.Lock:
    """惰性创建项目向导锁，兼容 Python 3.9 未初始化事件循环的场景。"""

    global PROJECT_WIZARD_LOCK
    if PROJECT_WIZARD_LOCK is None:
        PROJECT_WIZARD_LOCK = asyncio.Lock()
    return PROJECT_WIZARD_LOCK


def reset_project_wizard_lock() -> None:
    """测试或重启 master 时调用，强制下次请求重新创建锁。"""

    global PROJECT_WIZARD_LOCK
    PROJECT_WIZARD_LOCK = None


def _ensure_repository() -> ProjectRepository:
    """获取项目仓库实例，未初始化时抛出异常。"""
    if PROJECT_REPOSITORY is None:
        raise RuntimeError("项目仓库未初始化")
    return PROJECT_REPOSITORY


def _reload_manager_configs(
    manager: MasterManager,
    *,
    preserve: Optional[Dict[str, ProjectState]] = None,
) -> List[ProjectConfig]:
    """重新加载项目配置，并可选地保留指定状态映射。"""
    repository = _ensure_repository()
    records = repository.list_projects()
    configs = [ProjectConfig.from_dict(record.to_dict()) for record in records]
    manager.rebuild_configs(configs, preserve=preserve)
    return configs


def _validate_field_value(
    session: ProjectWizardSession,
    field_name: ProjectField,
    raw_text: str,
) -> Tuple[Optional[Any], Optional[str]]:
    """校验字段输入，返回转换后的值与错误信息。"""
    text = raw_text.strip()
    repository = _ensure_repository()
    # 编辑流程允许使用 "-" 保持原值
    if session.mode == "edit" and text == "-":
        return session.data.get(field_name), None

    if field_name in PROJECT_WIZARD_OPTIONAL_FIELDS and not text:
        return None, None

    if field_name == "bot_name":
        candidate = text.lstrip("@").strip()
        if not candidate:
            return None, "bot 名不能为空"
        if not re.fullmatch(r"[A-Za-z0-9_.]{5,64}", candidate):
            return None, "bot 名仅允许 5-64 位字母、数字、下划线或点"
        existing = repository.get_by_bot_name(candidate)
        if existing and (session.mode == "create" or existing.project_slug != session.original_slug):
            return None, "该 bot 名已被其它项目占用"
        return candidate, None

    if field_name == "bot_token":
        if not re.fullmatch(r"\d+:[A-Za-z0-9_-]{20,128}", text):
            return None, "Bot Token 格式不正确，请确认输入"
        return text, None

    if field_name == "project_slug":
        candidate = _sanitize_slug(text or session.data.get("bot_name", ""))
        if not candidate:
            return None, "无法生成有效的 slug，请重新输入"
        existing = repository.get_by_slug(candidate)
        if existing and (session.mode == "create" or existing.project_slug != session.original_slug):
            return None, "该 slug 已存在，请更换其它名称"
        return candidate, None

    if field_name == "default_model":
        candidate = text.lower() if text else "codex"
        if candidate not in PROJECT_MODEL_CHOICES:
            return None, f"默认模型仅支持 {', '.join(PROJECT_MODEL_CHOICES)}"
        return candidate, None

    if field_name == "workdir":
        expanded = os.path.expandvars(os.path.expanduser(text))
        path = Path(expanded)
        if not path.exists() or not path.is_dir():
            return None, f"目录不存在或不可用：{text}"
        return str(path), None

    if field_name == "allowed_chat_id":
        if not re.fullmatch(r"-?\d+", text):
            return None, "chat_id 需为整数，可留空跳过"
        return int(text), None

    return text, None


def _format_field_prompt(
    session: ProjectWizardSession, field_name: ProjectField
) -> Tuple[str, Optional[InlineKeyboardMarkup]]:
    """根据流程生成字段提示语与可选操作键盘。"""

    if session.mode == "edit":
        current_value = session.data.get(field_name)
        if current_value is None:
            display = "未设置"
        elif field_name == "bot_token":
            display = f"{str(current_value)[:6]}***"
        else:
            display = str(current_value)
        template = PROJECT_FIELD_PROMPTS_EDIT[field_name]
        prompt = template.format(current=display)
    else:
        prompt = PROJECT_FIELD_PROMPTS_CREATE[field_name]

    markup: Optional[InlineKeyboardMarkup] = None
    skip_enabled = False
    if field_name in {"workdir", "allowed_chat_id"}:
        skip_enabled = True
    elif field_name == "default_model" and session.mode == "create":
        skip_enabled = True

    if skip_enabled:
        builder = InlineKeyboardBuilder()
        builder.button(
            text="跳过此项",
            callback_data=f"project:wizard:skip:{field_name}",
        )
        markup = builder.as_markup()

    return prompt, markup


async def _send_field_prompt(
    session: ProjectWizardSession,
    field_name: ProjectField,
    target_message: Message,
    *,
    prefix: str = "",
) -> None:
    """向用户发送当前字段的提示语与可选跳过按钮。"""

    prompt, markup = _format_field_prompt(session, field_name)
    if prefix:
        text = f"{prefix}\n{prompt}"
    else:
        text = prompt
    await target_message.answer(text, reply_markup=markup)


def _session_to_record(session: ProjectWizardSession) -> ProjectRecord:
    """将会话数据转换为 ProjectRecord，编辑时保留 legacy_name。"""
    legacy_name = session.original_record.legacy_name if session.original_record else None
    return ProjectRecord(
        bot_name=session.data["bot_name"],
        bot_token=session.data["bot_token"],
        project_slug=session.data.get("project_slug") or _sanitize_slug(session.data["bot_name"]),
        default_model=session.data["default_model"],
        workdir=session.data.get("workdir"),
        allowed_chat_id=session.data.get("allowed_chat_id"),
        legacy_name=legacy_name,
    )


async def _commit_wizard_session(
    session: ProjectWizardSession,
    manager: MasterManager,
    message: Message,
) -> bool:
    """提交会话数据并执行仓库写入。"""
    repository = _ensure_repository()
    record = _session_to_record(session)
    try:
        if session.mode == "create":
            repository.insert_project(record)
            _reload_manager_configs(manager)
            summary_prefix = "新增项目成功 ✅"
        elif session.mode == "edit":
            original_slug = session.original_slug or record.project_slug
            preserve: Optional[Dict[str, ProjectState]] = None
            old_state = manager.state_store.data.get(original_slug)
            if original_slug != record.project_slug and old_state is not None:
                preserve = {record.project_slug: old_state}
            repository.update_project(original_slug, record)
            if original_slug != record.project_slug and original_slug in manager.state_store.data:
                del manager.state_store.data[original_slug]
            _reload_manager_configs(manager, preserve=preserve)
            summary_prefix = "项目已更新 ✅"
        else:
            return False
    except Exception as exc:
        log.error("项目写入失败: %s", exc, extra={"mode": session.mode})
        await message.answer(f"保存失败：{exc}")
        return False

    workdir_desc = record.workdir or "未设置"
    chat_desc = record.allowed_chat_id if record.allowed_chat_id is not None else "未设置"
    summary = (
        f"{summary_prefix}\n"
        f"bot：@{record.bot_name}\n"
        f"slug：{record.project_slug}\n"
        f"模型：{record.default_model}\n"
        f"工作目录：{workdir_desc}\n"
        f"chat_id：{chat_desc}"
    )
    await message.answer(summary)
    await _send_projects_overview_to_chat(message.bot, message.chat.id, manager)
    return True


async def _advance_wizard_session(
    session: ProjectWizardSession,
    manager: MasterManager,
    message: Message,
    text: str,
    *,
    prefix: str = "已记录 ✅",
) -> bool:
    """推进项目管理流程，校验输入并触发后续步骤。"""

    if session.step_index >= len(session.fields):
        await message.answer("流程已完成，如需再次修改请重新开始。")
        return True

    if not session.fields:
        await message.answer("流程配置异常，请重新开始。")
        async with get_project_wizard_lock():
            PROJECT_WIZARD_SESSIONS.pop(message.chat.id, None)
        return True

    field_name = session.fields[session.step_index]
    value, error = _validate_field_value(session, field_name, text)
    if error:
        await message.answer(f"{error}\n请重新输入：")
        return True

    session.data[field_name] = value
    session.step_index += 1

    if session.mode == "create" and field_name == "bot_name":
        repository = _ensure_repository()
        base_slug = _sanitize_slug(session.data["bot_name"])
        candidate = base_slug
        suffix = 1
        while repository.get_by_slug(candidate):
            suffix += 1
            candidate = f"{base_slug}-{suffix}"
        session.data["project_slug"] = candidate

    if session.step_index < len(session.fields):
        next_field = session.fields[session.step_index]
        await _send_field_prompt(session, next_field, message, prefix=prefix)
        return True

    # 所有字段已填写，执行写入
    success = await _commit_wizard_session(session, manager, message)
    async with get_project_wizard_lock():
        PROJECT_WIZARD_SESSIONS.pop(message.chat.id, None)

    if success:
        await message.answer("项目管理流程已完成。")
    return True


async def _start_project_create(callback: CallbackQuery, manager: MasterManager) -> None:
    """启动新增项目流程。"""
    if callback.message is None or callback.from_user is None:
        return
    chat_id = callback.message.chat.id
    user_id = callback.from_user.id
    async with get_project_wizard_lock():
        if chat_id in PROJECT_WIZARD_SESSIONS:
            await callback.answer("当前会话已有流程进行中，请先完成或发送“取消”。", show_alert=True)
            return
        session = ProjectWizardSession(
            chat_id=chat_id,
            user_id=user_id,
            mode="create",
            fields=PROJECT_WIZARD_FIELDS_CREATE,
        )
        PROJECT_WIZARD_SESSIONS[chat_id] = session
    await callback.answer("开始新增项目流程")
    await callback.message.answer(
        "已进入新增项目流程，随时可发送“取消”终止。",
    )
    first_field = session.fields[0]
    await _send_field_prompt(session, first_field, callback.message)


async def _start_project_edit(
    callback: CallbackQuery,
    cfg: ProjectConfig,
    manager: MasterManager,
) -> None:
    """启动项目编辑流程。"""
    if callback.message is None or callback.from_user is None:
        return
    repository = _ensure_repository()
    record = repository.get_by_slug(cfg.project_slug)
    if record is None:
        await callback.answer("未找到项目配置", show_alert=True)
        return
    chat_id = callback.message.chat.id
    user_id = callback.from_user.id
    async with get_project_wizard_lock():
        if chat_id in PROJECT_WIZARD_SESSIONS:
            await callback.answer("当前会话已有流程进行中，请先完成或发送“取消”。", show_alert=True)
            return
        session = ProjectWizardSession(
            chat_id=chat_id,
            user_id=user_id,
            mode="edit",
            original_slug=cfg.project_slug,
            original_record=record,
            fields=PROJECT_WIZARD_FIELDS_EDIT,
        )
        session.data = {
            "bot_name": record.bot_name,
            "bot_token": record.bot_token,
            "project_slug": record.project_slug,
            "default_model": record.default_model,
            "workdir": record.workdir,
            "allowed_chat_id": record.allowed_chat_id,
        }
        PROJECT_WIZARD_SESSIONS[chat_id] = session
    await callback.answer("开始编辑项目")
    await callback.message.answer(
        f"已进入编辑流程：{cfg.display_name}，随时可发送“取消”终止。",
    )
    field_name = session.fields[0]
    await _send_field_prompt(session, field_name, callback.message)


def _build_delete_confirmation_keyboard(slug: str) -> InlineKeyboardMarkup:
    """构建删除确认用的按钮键盘。"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="确认删除 ✅",
            callback_data=f"project:delete_confirm:{slug}",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="取消",
            callback_data="project:delete_cancel",
        )
    )
    markup = builder.as_markup()
    return _ensure_numbered_markup(markup)


async def _start_project_delete(
    callback: CallbackQuery,
    cfg: ProjectConfig,
    manager: MasterManager,
    state: FSMContext,
) -> None:
    """启动删除项目的确认流程。"""
    if callback.message is None or callback.from_user is None:
        return
    repository = _ensure_repository()
    original_record = repository.get_by_slug(cfg.project_slug)
    original_slug = original_record.project_slug if original_record else cfg.project_slug
    # 删除前再次读取运行态，避免 FSM 上下文被误覆盖
    project_runtime_state = _get_project_runtime_state(manager, cfg.project_slug)
    if project_runtime_state and project_runtime_state.status == "running":
        await callback.answer("请先停止该项目的 worker 后再删除。", show_alert=True)
        return
    current_state = await state.get_state()
    if current_state == ProjectDeleteStates.confirming.state:
        data = await state.get_data()
        existing_slug = str(data.get("project_slug", "")).lower()
        if existing_slug == cfg.project_slug.lower():
            await callback.answer("当前删除流程已在确认中，请使用按钮完成操作。", show_alert=True)
            return
        await state.clear()
    await state.set_state(ProjectDeleteStates.confirming)
    await state.update_data(
        project_slug=cfg.project_slug,
        display_name=cfg.display_name,
        initiator_id=callback.from_user.id,
        expires_at=time.time() + DELETE_CONFIRM_TIMEOUT,
        original_slug=original_slug,
        bot_name=cfg.bot_name,
    )
    markup = _build_delete_confirmation_keyboard(cfg.project_slug)
    await callback.answer("删除确认已发送")
    await callback.message.answer(
        f"确认删除项目 {cfg.display_name}？此操作不可恢复。\n"
        f"请在 {DELETE_CONFIRM_TIMEOUT} 秒内使用下方按钮确认或取消。",
        reply_markup=markup,
    )


async def _handle_wizard_message(
    message: Message,
    manager: MasterManager,
) -> bool:
    """处理项目管理流程中的用户输入。"""
    if message.chat is None or message.from_user is None:
        return False
    chat_id = message.chat.id
    async with get_project_wizard_lock():
        session = PROJECT_WIZARD_SESSIONS.get(chat_id)
    if session is None:
        return False
    if message.from_user.id != session.user_id:
        await message.answer("仅流程发起者可以继续操作。")
        return True
    text = (message.text or "").strip()
    if text.lower() in {"取消", "cancel", "/cancel"}:
        async with get_project_wizard_lock():
            PROJECT_WIZARD_SESSIONS.pop(chat_id, None)
        await message.answer("已取消项目管理流程。")
        return True

    return await _advance_wizard_session(session, manager, message, text)
router = Router()
log = create_logger("master", level_env="MASTER_LOG_LEVEL", stderr_env="MASTER_STDERR")

# 重启状态锁与标记，避免重复触发
_restart_lock: Optional[asyncio.Lock] = None
_restart_in_progress: bool = False


def _ensure_restart_lock() -> asyncio.Lock:
    """延迟创建 asyncio.Lock，确保在事件循环内初始化"""
    global _restart_lock
    if _restart_lock is None:
        _restart_lock = asyncio.Lock()
    return _restart_lock


def _log_update(message: Message, *, override_user: Optional[User] = None) -> None:
    """记录每条更新并同步 MASTER_ENV_FILE 中的最近聊天信息。"""

    user = override_user or message.from_user
    username = user.username if user and user.username else None
    log.info(
        "update chat=%s user=%s username=%s text=%s",
        message.chat.id,
        user.id if user else None,
        username,
        message.text,
    )
    chat_id = message.chat.id
    user_id = user.id if user else None
    _update_master_env(chat_id, user_id)


def _safe_remove(path: Path, *, retries: int = 3) -> None:
    """安全移除文件，支持重试机制

    Args:
        path: 要删除的文件路径
        retries: 最大重试次数（默认 3 次）
    """
    if not path.exists():
        log.debug("文件不存在，无需删除", extra={"path": str(path)})
        return

    for attempt in range(retries):
        try:
            path.unlink()
            log.info("文件已删除", extra={"path": str(path), "attempt": attempt + 1})
            return
        except FileNotFoundError:
            log.debug("文件已被其他进程删除", extra={"path": str(path)})
            return
        except Exception as exc:
            if attempt < retries - 1:
                log.warning(
                    "删除文件失败，将重试 (attempt %d/%d): %s",
                    attempt + 1,
                    retries,
                    exc,
                    extra={"path": str(path)}
                )
                import time
                time.sleep(0.1)  # 等待 100ms 后重试
            else:
                log.error(
                    "删除文件失败，已达最大重试次数: %s",
                    exc,
                    extra={"path": str(path), "retries": retries}
                )


def _write_restart_signal(message: Message, *, override_user: Optional[User] = None) -> None:
    """将重启请求信息写入 signal 文件，供新 master 启动后读取"""
    now_local = datetime.now(LOCAL_TZ)
    actor = override_user or message.from_user
    payload = {
        "chat_id": message.chat.id,
        "user_id": actor.id if actor else None,
        "username": actor.username if actor and actor.username else None,
        "timestamp": now_local.isoformat(),
        "message_id": message.message_id,
    }
    RESTART_SIGNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = RESTART_SIGNAL_PATH.with_suffix(RESTART_SIGNAL_PATH.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp_path.replace(RESTART_SIGNAL_PATH)
    log.info(
        "已记录重启信号: chat_id=%s user_id=%s 文件=%s",
        payload["chat_id"],
        payload["user_id"],
        RESTART_SIGNAL_PATH,
        extra={"chat": payload["chat_id"]},
    )


def _read_restart_signal() -> Tuple[Optional[dict], Optional[Path]]:
    """读取并验证重启 signal，兼容历史路径并处理异常/超时情况"""
    candidates: Tuple[Path, ...] = (RESTART_SIGNAL_PATH, *LEGACY_RESTART_SIGNAL_PATHS)
    for path in candidates:
        if not path.exists():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("signal payload 必须是对象")
        except Exception as exc:
            log.error("读取重启信号失败: %s", exc, extra={"path": str(path)})
            _safe_remove(path)
            continue

        timestamp_raw = raw.get("timestamp")
        if timestamp_raw:
            try:
                ts = datetime.fromisoformat(timestamp_raw)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=LOCAL_TZ)
                ts_utc = ts.astimezone(timezone.utc)
                age_seconds = (datetime.now(timezone.utc) - ts_utc).total_seconds()
                if age_seconds > RESTART_SIGNAL_TTL:
                    log.info(
                        "重启信号超时，忽略",
                        extra={
                            "path": str(path),
                            "age_seconds": age_seconds,
                            "ttl": RESTART_SIGNAL_TTL,
                        },
                    )
                    _safe_remove(path)
                    continue
            except Exception as exc:
                log.warning("解析重启信号时间戳失败: %s", exc, extra={"path": str(path)})

        if path != RESTART_SIGNAL_PATH:
            log.info(
                "从兼容路径读取重启信号",
                extra={"path": str(path), "primary": str(RESTART_SIGNAL_PATH)},
            )
        return raw, path

    return None, None


def _read_start_signal() -> Tuple[Optional[dict], Optional[Path]]:
    """读取 CLI 写入的自动 /start 信号。"""

    path = START_SIGNAL_PATH
    if not path.exists():
        return None, None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("payload 必须是对象")
    except Exception as exc:
        log.error("读取启动信号失败: %s", exc, extra={"path": str(path)})
        _safe_remove(path)
        return None, None

    raw_ids = payload.get("chat_ids") or []
    if not isinstance(raw_ids, list):
        log.warning("启动信号 chat_ids 字段无效，已忽略", extra={"path": str(path)})
        _safe_remove(path)
        return None, None

    chat_ids: list[int] = []
    for item in raw_ids:
        try:
            candidate = int(item)
        except (TypeError, ValueError):
            continue
        if candidate not in chat_ids:
            chat_ids.append(candidate)
    if not chat_ids:
        log.info("启动信号未包含有效 chat_id，跳过自动推送", extra={"path": str(path)})
        _safe_remove(path)
        return None, None
    payload["chat_ids"] = chat_ids

    timestamp_raw = payload.get("timestamp")
    if timestamp_raw:
        try:
            ts = datetime.fromisoformat(timestamp_raw)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age_seconds = (datetime.now(timezone.utc) - ts.astimezone(timezone.utc)).total_seconds()
            if age_seconds > START_SIGNAL_TTL:
                log.info(
                    "启动信号已过期，忽略处理",
                    extra={"path": str(path), "age_seconds": age_seconds, "ttl": START_SIGNAL_TTL},
                )
                _safe_remove(path)
                return None, None
        except Exception as exc:
            log.warning("解析启动信号时间戳失败: %s", exc, extra={"path": str(path)})
    return payload, path


async def _send_restart_project_overview(bot: Bot, chat_ids: Sequence[int]) -> None:
    """在重启提示后追加一次项目列表推送，保证触发方能立即查看。"""

    if not chat_ids:
        return
    try:
        manager = await _ensure_manager()
    except RuntimeError as exc:
        log.error("重启后推送项目列表失败：manager 未就绪", extra={"error": str(exc)})
        return

    # 留出时间让状态刷新，防止刚启动时全部显示 stopped。
    await asyncio.sleep(3)
    delivered: set[int] = set()
    for chat_id in chat_ids:
        if chat_id in delivered:
            continue
        try:
            await _send_projects_overview_to_chat(bot, chat_id, manager)
        except Exception as exc:  # pragma: no cover - 网络异常只记录日志
            log.error("发送重启项目列表失败: %s", exc, extra={"chat": chat_id})
        else:
            delivered.add(chat_id)


async def _notify_restart_success(bot: Bot) -> None:
    """在新 master 启动时读取 signal 并通知触发者（改进版：支持超时检测和详细诊断）"""
    restart_expected = os.environ.pop("MASTER_RESTART_EXPECTED", None)
    payload, signal_path = _read_restart_signal()

    # 定义重启健康检查阈值（2 分钟）
    RESTART_HEALTHY_THRESHOLD = 120  # 秒
    RESTART_WARNING_THRESHOLD = 60   # 超过 1 分钟发出警告

    if not payload:
        if restart_expected:
            targets = _collect_admin_targets()
            log.warning(
                "启动时未检测到重启信号文件，将向管理员发送兜底提醒", extra={"targets": targets}
            )
            if targets:
                # 检查启动日志是否有错误信息
                error_log_dir = LOG_ROOT_PATH
                error_log_hint = ""
                try:
                    error_logs = sorted(error_log_dir.glob("master_error_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
                    if error_logs:
                        latest_error_log = error_logs[0]
                        if latest_error_log.stat().st_size > 0:
                            error_log_hint = f"\n⚠️ 发现错误日志：{latest_error_log}"
                except Exception:
                    pass

                text_lines = [
                    "⚠️ Master 已重新上线，但未找到重启触发者信息。",
                    "",
                    "可能原因：",
                    "1. 重启信号文件写入失败",
                    "2. 信号文件已超时被清理（TTL=30分钟）",
                    "3. 文件系统权限问题",
                    "4. start.sh 启动失败后被清理",
                    "",
                    "建议检查：",
                    f"- 启动日志: {LOG_ROOT_PATH / 'start.log'}",
                    f"- 运行日志: {LOG_ROOT_PATH / 'vibe.log'}",
                    f"- 信号文件: {RESTART_SIGNAL_PATH}",
                ]
                if error_log_hint:
                    text_lines.append(error_log_hint)

                text = "\n".join(text_lines)
                for chat in targets:
                    try:
                        await bot.send_message(chat_id=chat, text=text)
                        log.info("兜底重启通知已发送", extra={"chat": chat})
                    except Exception as exc:
                        log.error("发送兜底重启通知失败: %s", exc, extra={"chat": chat})
                await _send_restart_project_overview(bot, targets)
        else:
            log.info("启动时未检测到重启信号文件，可能是正常启动。")
        return

    chat_id_raw = payload.get("chat_id")
    try:
        chat_id = int(chat_id_raw)
    except (TypeError, ValueError):
        log.error("重启信号 chat_id 非法: %s", chat_id_raw)
        await _send_restart_project_overview(bot, _collect_admin_targets())
        targets = (signal_path, RESTART_SIGNAL_PATH, *LEGACY_RESTART_SIGNAL_PATHS)
        for candidate in targets:
            if candidate is None:
                continue
            _safe_remove(candidate)
        return

    username = payload.get("username")
    user_id = payload.get("user_id")
    timestamp = payload.get("timestamp")
    timestamp_fmt: Optional[str] = None
    restart_duration: Optional[int] = None

    # 计算重启耗时
    if timestamp:
        try:
            ts = datetime.fromisoformat(timestamp)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=LOCAL_TZ)
            ts_local = ts.astimezone(LOCAL_TZ)
            timestamp_fmt = ts_local.strftime("%Y-%m-%d %H:%M:%S %Z")

            # 计算重启耗时（秒）
            now = datetime.now(LOCAL_TZ)
            restart_duration = int((now - ts_local).total_seconds())
        except Exception as exc:
            log.warning("解析重启时间失败: %s", exc)

    details = []
    if username:
        details.append(f"触发人：@{username}")
    elif user_id:
        details.append(f"触发人ID：{user_id}")
    if timestamp_fmt:
        details.append(f"请求时间：{timestamp_fmt}")

    # 添加重启耗时信息和健康状态
    message_lines = []
    if restart_duration is not None:
        if restart_duration <= RESTART_WARNING_THRESHOLD:
            message_lines.append(f"master 已重新上线 ✅（耗时 {restart_duration}秒）")
        elif restart_duration <= RESTART_HEALTHY_THRESHOLD:
            message_lines.append(f"⚠️ master 已重新上线（耗时 {restart_duration}秒，略慢）")
            details.append("💡 建议：检查依赖安装是否触发了重新下载")
        else:
            message_lines.append(f"⚠️ master 已重新上线（耗时 {restart_duration}秒，异常缓慢）")
            details.append("⚠️ 重启耗时过长，建议检查：")
            details.append("  - 网络连接是否正常")
            details.append("  - 依赖安装是否卡住")
            details.append(f"  - 启动日志: {LOG_ROOT_PATH / 'start.log'}")
    else:
        message_lines.append("master 已重新上线 ✅")

    if details:
        message_lines.extend(details)

    text = "\n".join(message_lines)

    try:
        await bot.send_message(chat_id=chat_id, text=text)
    except Exception as exc:
        log.error("发送重启成功通知失败: %s", exc, extra={"chat": chat_id})
        await _send_restart_project_overview(bot, _collect_admin_targets())
    else:
        # 重启成功提醒本身仍不附带项目列表，改为单独发送概览，减少消息体积。
        log.info("重启成功通知已发送", extra={"chat": chat_id, "duration": restart_duration})
        await _send_restart_project_overview(bot, [chat_id])
    finally:
        candidates = (signal_path, RESTART_SIGNAL_PATH, *LEGACY_RESTART_SIGNAL_PATHS)
        for candidate in candidates:
            if candidate is None:
                continue
            _safe_remove(candidate)


async def _notify_start_signal(bot: Bot) -> None:
    """启动后读取 CLI 写入的自动 /start 信号并推送通知。"""

    payload, signal_path = _read_start_signal()
    if not payload:
        return
    chat_ids = payload.get("chat_ids") or []
    if not chat_ids:
        return
    try:
        manager = await _ensure_manager()
    except RuntimeError as exc:
        log.error("自动 /start 通知失败：manager 未就绪", extra={"error": str(exc)})
        return

    # 等待 bot 完成菜单同步，避免 UI 数据尚未准备好
    await asyncio.sleep(2)
    for chat_id in chat_ids:
        try:
            await _deliver_master_start_overview(bot, chat_id, manager)
        except Exception as exc:
            log.error("发送自动启动通知失败: %s", exc, extra={"chat": chat_id})

    if signal_path:
        _safe_remove(signal_path)


def _read_upgrade_report() -> Optional[dict]:
    """读取升级完成报告，供新 master 启动时推送。"""

    path = _UPGRADE_REPORT_PATH
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("upgrade report must be object")
        return payload
    except Exception as exc:
        log.error("读取升级报告失败: %s", exc, extra={"path": str(path)})
        _safe_remove(path)
        return None


async def _notify_upgrade_report(bot: Bot) -> None:
    """若存在升级报告，则在 master 启动后向管理员推送摘要。"""

    payload = _read_upgrade_report()
    if not payload:
        return
    chat_id = payload.get("chat_id")
    if not isinstance(chat_id, int):
        log.warning("升级报告缺少有效 chat_id，已忽略", extra={"payload": payload})
        _safe_remove(_UPGRADE_REPORT_PATH)
        return

    elapsed = payload.get("elapsed")
    elapsed_text = f"{elapsed:.1f}" if isinstance(elapsed, (int, float)) else "未知"
    old_version = payload.get("old_version") or payload.get("version") or "未知"
    new_version = payload.get("new_version") or __version__
    text = (
        f"✅ 升级流程完成，执行耗时 {elapsed_text} 秒。\n"
        f"📦 旧版本 {old_version} -> 新版本 {new_version}\n"
        "🚀 master 已重新上线，请使用 /start 校验项目状态。"
    )

    try:
        await bot.send_message(chat_id=chat_id, text=text)
    except Exception as exc:
        log.error("发送升级完成通知失败: %s", exc, extra={"chat": chat_id})
    finally:
        _safe_remove(_UPGRADE_REPORT_PATH)


async def _ensure_manager() -> MasterManager:
    """确保 MANAGER 已初始化，未初始化时抛出异常。"""

    global MANAGER
    if MANAGER is None:
        raise RuntimeError("Master manager 未初始化")
    return MANAGER


async def _process_restart_request(
    message: Message,
    *,
    trigger_user: Optional[User] = None,
    manager: Optional[MasterManager] = None,
) -> None:
    """响应 /restart 请求，写入重启信号并触发脚本。"""

    if manager is None:
        manager = await _ensure_manager()
    if not manager.is_authorized(message.chat.id):
        await message.answer("未授权。")
        return

    lock = _ensure_restart_lock()
    async with lock:
        global _restart_in_progress
        if _restart_in_progress:
            await message.answer("已有重启请求在执行，请稍候再试。")
            return
        _restart_in_progress = True

    start_script = ROOT_DIR / "scripts/start.sh"
    if not start_script.exists():
        async with lock:
            _restart_in_progress = False
        await message.answer("未找到 ./start.sh，无法执行重启。")
        return

    signal_error: Optional[str] = None
    try:
        _write_restart_signal(message, override_user=trigger_user)
    except Exception as exc:
        signal_error = str(exc)
        log.error("记录重启信号异常: %s", exc)

    notice = (
        "已收到重启指令，运行期间 master 会短暂离线，重启后所有 worker 需稍后手动启动。"
    )
    if signal_error:
        notice += (
            "\n⚠️ 重启信号写入失败，可能无法在重启完成后自动通知。原因: "
            f"{signal_error}"
        )

    await message.answer(notice)

    asyncio.create_task(_perform_restart(message, start_script))


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """处理 /start 命令，返回项目概览与状态。"""

    _log_update(message)
    manager = await _ensure_manager()
    if not manager.is_authorized(message.chat.id):
        await message.answer("未授权。")
        return
    await _deliver_master_start_overview(
        message.bot,
        message.chat.id,
        manager,
        reply_to_message_id=message.message_id,
    )


async def _perform_restart(message: Message, start_script: Path) -> None:
    """异步执行 ./start.sh，若失败则回滚标记并通知管理员"""
    global _restart_in_progress
    lock = _ensure_restart_lock()
    bot = message.bot
    chat_id = message.chat.id
    await asyncio.sleep(1.0)
    env = os.environ.copy()
    env["MASTER_RESTART_EXPECTED"] = "1"
    notice_error: Optional[Exception] = None
    try:
        await bot.send_message(
            chat_id=chat_id,
            text="开始重启，当前 master 将退出并重新拉起，请稍候。",
        )
    except Exception as notice_exc:
        notice_error = notice_exc
        log.warning("发送启动通知失败: %s", notice_exc)
    try:
        _clear_related_tmux_sessions()
        # 使用 DEVNULL 避免继承当前 stdout/stderr，防止父进程退出导致 start.sh 写入管道时触发 BrokenPipe。
        proc = subprocess.Popen(
            ["/bin/bash", str(start_script)],
            cwd=str(ROOT_DIR),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log.info("已触发 start.sh 进行重启，pid=%s", proc.pid if proc else "-")
    except Exception as exc:
        log.error("执行 ./start.sh 失败: %s", exc)
        async with lock:
            _restart_in_progress = False
        try:
            await bot.send_message(chat_id=chat_id, text=f"执行 ./start.sh 失败：{exc}")
        except Exception as send_exc:
            log.error("发送重启失败通知时出错: %s", send_exc)
        return
    else:
        if notice_error:
            log.warning("启动通知未送达，已继续执行 start.sh")
        async with lock:
            _restart_in_progress = False
            log.debug("重启执行中，已提前重置状态标记")


async def _deliver_master_start_overview(
    bot: Bot,
    chat_id: int,
    manager: MasterManager,
    *,
    reply_to_message_id: Optional[int] = None,
) -> None:
    """统一推送 /start 内容与项目列表，供手动或自动场景复用。"""

    summary = (
        f"Master bot 已启动（v{__version__}）。\n"
        f"已登记项目: {len(manager.configs)} 个。\n"
        "使用 /projects 查看状态，/run 或 /stop 控制 worker。"
    )
    await bot.send_message(
        chat_id=chat_id,
        text=summary,
        reply_markup=_build_master_main_keyboard(),
        reply_to_message_id=reply_to_message_id,
    )
    await _send_projects_overview_to_chat(
        bot,
        chat_id,
        manager,
        reply_to_message_id=reply_to_message_id,
    )


@router.message(Command("restart"))
async def cmd_restart(message: Message) -> None:
    """处理 /restart 命令，触发 master 重启。"""

    _log_update(message)
    await _process_restart_request(message)


async def _send_message_with_retry(
    bot: Bot,
    *,
    chat_id: int,
    text: str,
    retries: int = 3,
    base_delay: float = 0.8,
    **kwargs: Any,
) -> None:
    """发送消息时对瞬时网络异常进行有限重试，降低“偶发无响应”概率。"""

    last_exc: Optional[Exception] = None
    for attempt in range(retries):
        try:
            await bot.send_message(chat_id=chat_id, text=text, **kwargs)
            return
        except TelegramRetryAfter as exc:
            last_exc = exc
            if attempt >= retries - 1:
                break
            await asyncio.sleep(max(float(exc.retry_after), base_delay))
        except (TelegramNetworkError, ClientError, asyncio.TimeoutError, OSError) as exc:
            last_exc = exc
            if attempt >= retries - 1:
                break
            await asyncio.sleep(base_delay * (attempt + 1))
    if last_exc is not None:
        raise last_exc


async def _send_projects_overview_to_chat(
    bot: Bot,
    chat_id: int,
    manager: MasterManager,
    reply_to_message_id: Optional[int] = None,
) -> None:
    """向指定聊天发送项目概览及操作按钮。"""

    await _maybe_notify_update(bot, chat_id)
    manager.refresh_state()
    try:
        text, markup = _projects_overview(manager)
    except Exception as exc:
        log.exception("生成项目概览失败: %s", exc)
        try:
            await _send_message_with_retry(
                bot,
                chat_id=chat_id,
                text="项目列表生成失败，请稍后再试。",
                reply_to_message_id=reply_to_message_id,
            )
        except Exception as send_exc:
            log.error("项目列表失败提示发送失败: %s", send_exc)
        return
    try:
        await _send_message_with_retry(
            bot,
            chat_id=chat_id,
            text=text,
            reply_markup=markup,
            reply_to_message_id=reply_to_message_id,
        )
    except TelegramBadRequest as exc:
        log.error("发送项目概览失败: %s", exc)
        try:
            await _send_message_with_retry(
                bot,
                chat_id=chat_id,
                text=text,
                reply_to_message_id=reply_to_message_id,
            )
        except Exception as send_exc:
            log.error("项目概览降级发送失败: %s", send_exc)
    except Exception as exc:
        log.exception("发送项目概览触发异常: %s", exc)
        try:
            await _send_message_with_retry(
                bot,
                chat_id=chat_id,
                text=text,
                reply_to_message_id=reply_to_message_id,
            )
        except Exception as send_exc:
            log.error("项目概览重试后仍失败: %s", send_exc)
    else:
        log.info("已发送项目概览，按钮=%s", "无" if markup is None else "有")


async def _refresh_project_overview(
    message: Optional[Message],
    manager: MasterManager,
) -> None:
    """在原消息上刷新项目概览，无法编辑时发送新消息。"""

    if message is None:
        return
    manager.refresh_state()
    try:
        text, markup = _projects_overview(manager)
    except Exception as exc:
        log.exception("刷新项目概览失败: %s", exc)
        return
    try:
        await message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as exc:
        log.warning("编辑项目概览失败，将发送新消息: %s", exc)
        try:
            await message.answer(text, reply_markup=markup)
        except Exception as send_exc:
            log.exception("发送项目概览失败: %s", send_exc)


@router.message(Command("projects"))
async def cmd_projects(message: Message) -> None:
    """处理 /projects 命令，返回最新项目概览。"""

    _log_update(message)
    manager = await _ensure_manager()
    if not manager.is_authorized(message.chat.id):
        await message.answer("未授权。")
        return
    await _send_projects_overview_to_chat(
        message.bot,
        message.chat.id,
        manager,
        reply_to_message_id=message.message_id,
    )


@router.message(Command("upgrade"))
async def cmd_upgrade(message: Message) -> None:
    """处理 /upgrade 命令，触发 pipx 升级并重启服务。"""

    _log_update(message)
    manager = await _ensure_manager()
    if not manager.is_authorized(message.chat.id):
        await message.answer("未授权。")
        return

    bot = message.bot
    if bot is None:
        await message.answer("Bot 实例未就绪，请稍后重试。")
        return

    async with _UPGRADE_STATE_LOCK:
        global _UPGRADE_TASK
        if _UPGRADE_TASK is not None and _UPGRADE_TASK.done():
            _UPGRADE_TASK = None
        if _UPGRADE_TASK is not None:
            await message.answer("已有升级任务在执行，请等待其完成后再试。")
            return

        status_message = await message.answer(
            "已收到升级指令，将依次执行 pipx upgrade / vibego stop / vibego start，日志会实时更新，请勿重复点击。",
            disable_web_page_preview=True,
        )
        message_id = getattr(status_message, "message_id", None)
        if message_id is None:
            await message.answer("无法追踪状态消息，升级已取消。")
            return

        loop = asyncio.get_running_loop()
        task = loop.create_task(
            _run_upgrade_pipeline(bot, message.chat.id, message_id),
            name="master-upgrade-pipeline",
        )
        _UPGRADE_TASK = task

        async def _clear_reference() -> None:
            async with _UPGRADE_STATE_LOCK:
                global _UPGRADE_TASK
                if _UPGRADE_TASK is task:
                    _UPGRADE_TASK = None

        def _on_done(completed: asyncio.Task) -> None:
            try:
                completed.result()
            except Exception as exc:  # pragma: no cover - 记录后台异常
                log.error("升级流水线执行失败: %s", exc)
            loop.create_task(_clear_reference())

        task.add_done_callback(_on_done)


async def _run_and_reply(message: Message, action: str, coro) -> None:
    """执行异步操作并统一回复成功或失败提示。"""

    try:
        result = await coro
    except Exception as exc:
        log.error("%s 失败: %s", action, exc)
        await message.answer(f"{action} 失败: {exc}")
    else:
        reply_text: str
        reply_markup: Optional[InlineKeyboardMarkup] = None
        if isinstance(result, tuple):
            reply_text = result[0]
            if len(result) > 1:
                reply_markup = result[1]
        else:
            reply_text = result if isinstance(result, str) else f"{action} 完成"
        await message.answer(reply_text, reply_markup=_ensure_numbered_markup(reply_markup))


@router.callback_query(F.data.startswith("project:"))
async def on_project_action(callback: CallbackQuery, state: FSMContext) -> None:
    """处理项目管理相关的回调按钮。"""

    manager = await _ensure_manager()
    user_id = callback.from_user.id if callback.from_user else None
    if user_id is None or not manager.is_authorized(user_id):
        await callback.answer("未授权。", show_alert=True)
        return
    data = callback.data or ""
    # 跳过删除确认/取消，让专用处理器接管，避免误判为未知操作。
    if data.startswith("project:delete_confirm:") or data == "project:delete_cancel":
        raise SkipHandler()
    parts = data.split(":")
    if len(parts) < 3:
        await callback.answer("无效操作", show_alert=True)
        return
    _, action, *rest = parts
    identifier = rest[0] if rest else "*"
    extra_args = rest[1:]
    target_model: Optional[str] = None
    project_slug = identifier
    if action == "switch_to":
        target_model = identifier
        project_slug = extra_args[0] if extra_args else ""
    elif action == "switch_all_to":
        target_model = identifier
        project_slug = "*"

    async def _answer_callback_safely(text: Optional[str] = None, *, show_alert: bool = False) -> None:
        """安全答复 callback，避免网络抖动导致主流程被中断。"""

        try:
            await callback.answer(text, show_alert=show_alert)
        except Exception as exc:
            log.warning(
                "回调答复失败(忽略): %s",
                exc,
                extra={"project": project_slug or "*"},
            )

    if action == "refresh":
        # 刷新列表属于全局操作，不依赖具体项目 slug
        if callback.message:
            _reload_manager_configs(manager)
            manager.refresh_state()
            text, markup = _projects_overview(manager)
            await callback.message.edit_text(
                text,
                reply_markup=_ensure_numbered_markup(markup),
            )
        await callback.answer()
        return

    try:
        if action in {"stop_all", "start_all", "restart_master", "create", "switch_all", "switch_all_to"}:
            cfg = None
        else:
            cfg = manager.require_project_by_slug(project_slug)
    except ValueError:
        await callback.answer("未知项目", show_alert=True)
        return

    # 关键：避免覆盖 aiogram 传入的 FSMContext，因此运行态单独保存在 project_runtime_state 中
    project_runtime_state = _get_project_runtime_state(manager, cfg.project_slug) if cfg else None
    model_name_map = dict(SWITCHABLE_MODELS)

    if cfg:
        log.info(
            "按钮操作请求: user=%s action=%s project=%s",
            user_id,
            action,
            cfg.display_name,
            extra={"project": cfg.project_slug},
        )
    else:
        log.info("按钮操作请求: user=%s action=%s 所有项目", user_id, action)

    if action == "switch_all":
        builder = InlineKeyboardBuilder()
        for value, label in SWITCHABLE_MODELS:
            builder.row(
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"project:switch_all_to:{value}:*",
                )
            )
        builder.row(
            InlineKeyboardButton(
                text="⬅️ 取消",
                callback_data="project:refresh:*",
            )
        )
        await callback.answer()
        await callback.message.answer(
            "请选择全局模型：",
            reply_markup=_ensure_numbered_markup(builder.as_markup()),
        )
        return

    if action == "manage":
        if cfg is None or callback.message is None:
            await callback.answer("未知项目", show_alert=True)
            return
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(
                text="📝 编辑",
                callback_data=f"project:edit:{cfg.project_slug}",
            )
        )
        current_model_value = (
            project_runtime_state.model if project_runtime_state else cfg.default_model
        )
        current_model_key = (current_model_value or "").lower()
        current_model_label = model_name_map.get(current_model_key, current_model_value or current_model_key or "-")
        builder.row(
            InlineKeyboardButton(
                text=f"🧠 切换模型（当前模型 {current_model_label}）",
                callback_data=f"project:switch_prompt:{cfg.project_slug}",
            )
        )
        builder.row(
            InlineKeyboardButton(
                text="🗑 删除",
                callback_data=f"project:delete:{cfg.project_slug}",
            )
        )
        builder.row(
            InlineKeyboardButton(
                text="⬅️ 返回项目列表",
                callback_data="project:refresh:*",
            )
        )
        markup = builder.as_markup()
        _ensure_numbered_markup(markup)
        await callback.answer()
        await callback.message.answer(
            f"项目 {cfg.display_name} 的管理操作：",
            reply_markup=markup,
        )
        return

    if action == "switch_prompt":
        if cfg is None or callback.message is None:
            await callback.answer("未知项目", show_alert=True)
            return
        current_model = (
            project_runtime_state.model if project_runtime_state else cfg.default_model
        ).lower()
        builder = InlineKeyboardBuilder()
        for value, label in SWITCHABLE_MODELS:
            prefix = "✅ " if current_model == value else ""
            builder.row(
                InlineKeyboardButton(
                    text=f"{prefix}{label}",
                    callback_data=f"project:switch_to:{value}:{cfg.project_slug}",
                )
            )
        builder.row(
            InlineKeyboardButton(
                text="⬅️ 返回项目列表",
                callback_data="project:refresh:*",
            )
        )
        markup = builder.as_markup()
        _ensure_numbered_markup(markup)
        await callback.answer()
        await callback.message.answer(
            f"请选择 {cfg.display_name} 要使用的模型：",
            reply_markup=markup,
        )
        return

    if action == "edit":
        if cfg is None:
            await callback.answer("未知项目", show_alert=True)
            return
        await _start_project_edit(callback, cfg, manager)
        return

    if action == "delete":
        if cfg is None:
            await callback.answer("未知项目", show_alert=True)
            return
        await _start_project_delete(callback, cfg, manager, state)
        return

    if action == "create":
        await _start_project_create(callback, manager)
        return

    if action == "restart_master":
        await callback.answer("已收到重启指令")

    try:
        if action == "stop_all":
            await _answer_callback_safely("正在停止全部项目，请稍候…")
            await manager.stop_all(update_state=True)
            log.info("按钮操作成功: user=%s 停止全部项目", user_id)
        elif action == "start_all":
            await _answer_callback_safely("全部项目启动中，请稍候…")
            # 为所有项目自动记录启动者的 chat_id
            if callback.message and callback.message.chat:
                for project_cfg in manager.configs:
                    current_state = manager.state_store.data.get(project_cfg.project_slug)
                    if not current_state or not current_state.chat_id:
                        manager.update_chat_id(project_cfg.project_slug, callback.message.chat.id)
                        log.info(
                            "自动记录 chat_id: project=%s, chat_id=%s",
                            project_cfg.project_slug,
                            callback.message.chat.id,
                            extra={"project": project_cfg.project_slug, "chat_id": callback.message.chat.id},
                        )
            await manager.run_all()
            log.info("按钮操作成功: user=%s 启动全部项目", user_id)
        elif action == "restart_master":
            if callback.message is None:
                log.error("重启按钮回调缺少 message 对象", extra={"user": user_id})
                return
            _log_update(callback.message, override_user=callback.from_user)
            await _process_restart_request(
                callback.message,
                trigger_user=callback.from_user,
                manager=manager,
            )
            log.info("按钮操作成功: user=%s 重启 master", user_id)
            return  # 重启后不刷新项目列表，避免产生额外噪音
        elif action == "run":
            await _answer_callback_safely("项目启动中，请稍候…")
            # 自动记录启动者的 chat_id
            if callback.message and callback.message.chat:
                current_state = manager.state_store.data.get(cfg.project_slug)
                if not current_state or not current_state.chat_id:
                    manager.update_chat_id(cfg.project_slug, callback.message.chat.id)
                    log.info(
                        "自动记录 chat_id: project=%s, chat_id=%s",
                        cfg.project_slug,
                        callback.message.chat.id,
                        extra={"project": cfg.project_slug, "chat_id": callback.message.chat.id},
                    )
            chosen = await manager.run_worker(cfg)
            log.info(
                "按钮操作成功: user=%s 启动 %s (model=%s)",
                user_id,
                cfg.display_name,
                chosen,
                extra={"project": cfg.project_slug, "model": chosen},
            )
        elif action == "stop":
            await _answer_callback_safely("项目停止中，请稍候…")
            await manager.stop_worker(cfg)
            log.info(
                "按钮操作成功: user=%s 停止 %s",
                user_id,
                cfg.display_name,
                extra={"project": cfg.project_slug},
            )
        elif action == "switch_all_to":
            model_map = dict(SWITCHABLE_MODELS)
            if target_model not in model_map:
                await callback.answer("不支持的模型", show_alert=True)
                return
            await callback.answer("全局切换中，请稍候…")
            errors: list[tuple[str, str]] = []
            updated: list[str] = []
            for project_cfg in manager.configs:
                try:
                    await manager.stop_worker(project_cfg, update_state=True)
                except Exception as exc:
                    errors.append((project_cfg.display_name, str(exc)))
                    continue
                manager.state_store.update(project_cfg.project_slug, model=target_model, status="stopped")
                updated.append(project_cfg.display_name)
            manager.state_store.save()
            label = model_map[target_model]
            if errors:
                failure_lines = "\n".join(f"- {name}: {err}" for name, err in errors)
                message_text = (
                    f"已尝试将全部项目模型切换为 {label}，但部分项目执行失败：\n{failure_lines}"
                )
                log.warning(
                    "全局模型切换部分失败: user=%s model=%s failures=%s",
                    user_id,
                    target_model,
                    [name for name, _ in errors],
                )
            else:
                message_text = f"所有项目模型已切换为 {label}，并保持停止状态。"
                log.info(
                    "按钮操作成功: user=%s 全部切换模型至 %s",
                    user_id,
                    target_model,
                )
            await callback.message.answer(message_text)
        elif action == "switch_to":
            model_map = dict(SWITCHABLE_MODELS)
            if target_model not in model_map:
                await callback.answer("不支持的模型", show_alert=True)
                return
            state = manager.state_store.data.get(cfg.project_slug)
            previous_model = state.model if state else cfg.default_model
            was_running = bool(state and state.status == "running")
            # 自动记录 chat_id（如果还没有的话）
            if callback.message and callback.message.chat:
                if not state or not state.chat_id:
                    manager.update_chat_id(cfg.project_slug, callback.message.chat.id)
                    log.info(
                        "模型切换时自动记录 chat_id: project=%s, chat_id=%s",
                        cfg.project_slug,
                        callback.message.chat.id,
                        extra={"project": cfg.project_slug, "chat_id": callback.message.chat.id},
                    )
            try:
                if was_running:
                    await manager.stop_worker(cfg, update_state=True)
                manager.state_store.update(cfg.project_slug, model=target_model)
                if was_running:
                    chosen = await manager.run_worker(cfg, model=target_model)
                else:
                    chosen = target_model
            except Exception:
                manager.state_store.update(cfg.project_slug, model=previous_model)
                if was_running:
                    try:
                        await manager.run_worker(cfg, model=previous_model)
                    except Exception as restore_exc:
                        log.error(
                            "模型切换失败且恢复失败: %s",
                            restore_exc,
                            extra={"project": cfg.project_slug, "model": previous_model},
                        )
                raise
            else:
                if was_running:
                    await callback.answer(f"已切换至 {model_map.get(chosen, chosen)}")
                    log.info(
                        "按钮操作成功: user=%s 将 %s 切换至 %s",
                        user_id,
                        cfg.display_name,
                        chosen,
                        extra={"project": cfg.project_slug, "model": chosen},
                    )
                else:
                    await callback.answer(f"默认模型已更新为 {model_map.get(chosen, chosen)}")
                    log.info(
                        "按钮操作成功: user=%s 更新 %s 默认模型为 %s",
                        user_id,
                        cfg.display_name,
                        chosen,
                        extra={"project": cfg.project_slug, "model": chosen},
                    )
        else:
            await callback.answer("未知操作", show_alert=True)
            return
    except Exception as exc:
        log.error(
            "按钮操作失败: action=%s project=%s error=%s",
            action,
            (cfg.display_name if cfg else "*"),
            exc,
            extra={"project": cfg.project_slug if cfg else "*"},
        )
        if callback.message:
            await callback.message.answer(f"操作失败: {exc}")
        await _answer_callback_safely("操作失败", show_alert=True)
        return

    await _refresh_project_overview(callback.message, manager)


@router.message(Command("run"))
async def cmd_run(message: Message) -> None:
    """处理 /run 命令，启动指定项目并可选切换模型。"""

    _log_update(message)
    manager = await _ensure_manager()
    if not manager.is_authorized(message.chat.id):
        await message.answer("未授权。")
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("用法: /run <project> [model]")
        return
    project_raw = parts[1]
    model = parts[2] if len(parts) >= 3 else None
    try:
        cfg = manager.require_project(project_raw)
    except ValueError as exc:
        await message.answer(str(exc))
        return

    async def runner():
        """调用 manager.run_worker 启动项目并返回提示文本。"""

        chosen = await manager.run_worker(cfg, model=model)
        return f"已启动 {cfg.display_name} (model={chosen})"

    await _run_and_reply(message, "启动", runner())


@router.message(Command("stop"))
async def cmd_stop(message: Message) -> None:
    """处理 /stop 命令，停止指定项目。"""

    _log_update(message)
    manager = await _ensure_manager()
    if not manager.is_authorized(message.chat.id):
        await message.answer("未授权。")
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("用法: /stop <project>")
        return
    project_raw = parts[1]
    try:
        cfg = manager.require_project(project_raw)
    except ValueError as exc:
        await message.answer(str(exc))
        return

    async def stopper():
        """停止指定项目并更新状态。"""

        await manager.stop_worker(cfg, update_state=True)
        return f"已停止 {cfg.display_name}"

    await _run_and_reply(message, "停止", stopper())


@router.message(Command("switch"))
async def cmd_switch(message: Message) -> None:
    """处理 /switch 命令，停机后以新模型重启项目。"""

    _log_update(message)
    manager = await _ensure_manager()
    if not manager.is_authorized(message.chat.id):
        await message.answer("未授权。")
        return
    parts = message.text.split()
    if len(parts) < 3:
        await message.answer("用法: /switch <project> <model>")
        return
    project_raw, model = parts[1], parts[2]
    try:
        cfg = manager.require_project(project_raw)
    except ValueError as exc:
        await message.answer(str(exc))
        return

    async def switcher():
        """重新启动项目并切换到新的模型。"""

        await manager.stop_worker(cfg, update_state=True)
        chosen = await manager.run_worker(cfg, model=model)
        return f"已切换 {cfg.display_name} 至 {chosen}"

    await _run_and_reply(message, "切换", switcher())


@router.message(Command("authorize"))
async def cmd_authorize(message: Message) -> None:
    """处理 /authorize 命令，为项目登记 chat_id。"""

    _log_update(message)
    manager = await _ensure_manager()
    if not manager.is_authorized(message.chat.id):
        await message.answer("未授权。")
        return
    parts = message.text.split()
    if len(parts) < 3:
        await message.answer("用法: /authorize <project> <chat_id>")
        return
    project_raw, chat_raw = parts[1], parts[2]
    if not chat_raw.isdigit():
        await message.answer("chat_id 需要是数字")
        return
    chat_id = int(chat_raw)
    try:
        cfg = manager.require_project(project_raw)
    except ValueError as exc:
        await message.answer(str(exc))
        return
    manager.update_chat_id(cfg.project_slug, chat_id)
    await message.answer(
        f"已记录 {cfg.display_name} 的 chat_id={chat_id}"
    )


@router.callback_query(F.data.startswith("project:wizard:skip:"))
async def on_project_wizard_skip(callback: CallbackQuery) -> None:
    """处理向导中的“跳过此项”按钮。"""

    if callback.message is None or callback.message.chat is None:
        return
    chat_id = callback.message.chat.id
    async with get_project_wizard_lock():
        session = PROJECT_WIZARD_SESSIONS.get(chat_id)
    if session is None:
        await callback.answer("当前没有进行中的项目流程。", show_alert=True)
        return
    if session.step_index >= len(session.fields):
        await callback.answer("当前流程已结束。", show_alert=True)
        return
    _, _, field = callback.data.partition("project:wizard:skip:")
    current_field = session.fields[session.step_index]
    if field != current_field:
        await callback.answer("当前步骤已变更，请按最新提示操作。", show_alert=True)
        return
    manager = await _ensure_manager()
    await callback.answer("已跳过")
    await _advance_wizard_session(
        session,
        manager,
        callback.message,
        "",
        prefix="已跳过 ✅",
    )


@router.message(F.text.func(_is_projects_menu_trigger))
async def on_master_projects_button(message: Message) -> None:
    """处理常驻键盘触发的项目概览请求。"""
    _log_update(message)
    manager = await _ensure_manager()
    if not manager.is_authorized(message.chat.id):
        await message.answer("未授权。")
        return
    requested_text = message.text or ""
    reply_to_message_id: Optional[int] = message.message_id
    if not _text_equals_master_button(requested_text):
        log.info(
            "收到旧版项目列表按钮，准备刷新聊天键盘",
            extra={"text": requested_text, "chat_id": message.chat.id},
        )
        await message.answer(
            "主菜单按钮已更新为“📂 项目列表”，当前会话已同步最新文案。",
            reply_markup=_build_master_main_keyboard(),
            reply_to_message_id=reply_to_message_id,
        )
        # 已推送最新键盘，后续回复无需继续引用原消息，避免重复引用提示
        reply_to_message_id = None
    await _send_projects_overview_to_chat(
        message.bot,
        message.chat.id,
        manager,
        reply_to_message_id=reply_to_message_id,
    )


@router.message(F.text.in_(MASTER_MANAGE_BUTTON_ALLOWED_TEXTS))
async def on_master_manage_button(message: Message) -> None:
    """处理常驻键盘的项目管理入口。"""
    _log_update(message)
    manager = await _ensure_manager()
    if not manager.is_authorized(message.chat.id):
        await message.answer("未授权。")
        return
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="➕ 新增项目", callback_data="project:create:*"))
    model_name_map = dict(SWITCHABLE_MODELS)
    for cfg in manager.configs:
        state = manager.state_store.data.get(cfg.project_slug)
        current_model_value = state.model if state else cfg.default_model
        current_model_key = (current_model_value or "").lower()
        current_model_label = model_name_map.get(current_model_key, current_model_value or current_model_key or "-")
        builder.row(
            InlineKeyboardButton(
                text=f"⚙️ 管理 {cfg.display_name}",
                callback_data=f"project:manage:{cfg.project_slug}",
            ),
            InlineKeyboardButton(
                text=f"🧠 切换模型（当前模型 {current_model_label}）",
                callback_data=f"project:switch_prompt:{cfg.project_slug}",
            ),
        )
    builder.row(
        InlineKeyboardButton(
            text="🔁 全部切换模型",
            callback_data="project:switch_all:*",
        )
    )
    builder.row(InlineKeyboardButton(text="📂 返回列表", callback_data="project:refresh:*"))
    markup = builder.as_markup()
    _ensure_numbered_markup(markup)
    await message.answer(
        "请选择要管理的项目，或点击“➕ 新增项目”创建新的 worker。",
        reply_markup=markup,
    )


@router.message(F.text == MASTER_SETTINGS_BUTTON_TEXT)
async def on_master_settings_button(message: Message) -> None:
    """处理系统设置入口。"""

    _log_update(message)
    manager = await _ensure_manager()
    if not manager.is_authorized(message.chat.id):
        await message.answer("未授权。")
        return
    text, markup = _build_system_settings_menu()
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == SYSTEM_SETTINGS_MENU_CALLBACK)
async def on_system_settings_menu_callback(callback: CallbackQuery) -> None:
    """回到系统设置主菜单。"""

    if not await _ensure_authorized_callback(callback):
        return
    text, markup = _build_system_settings_menu()
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=markup)
    await callback.answer("已返回系统设置")


@router.callback_query(F.data == GLOBAL_COMMAND_MENU_CALLBACK)
async def on_global_command_menu(callback: CallbackQuery) -> None:
    """展示通用命令列表。"""

    if not await _ensure_authorized_callback(callback):
        return
    await _edit_global_command_overview(callback)
    await callback.answer("已加载通用命令")


@router.callback_query(F.data == GLOBAL_COMMAND_REFRESH_CALLBACK)
async def on_global_command_refresh(callback: CallbackQuery) -> None:
    """刷新通用命令列表。"""

    if not await _ensure_authorized_callback(callback):
        return
    await _edit_global_command_overview(callback, notice="列表已刷新。")
    await callback.answer("已刷新")


@router.callback_query(F.data == GLOBAL_COMMAND_NEW_CALLBACK)
async def on_global_command_new(callback: CallbackQuery, state: FSMContext) -> None:
    """启动通用命令创建流程。"""

    if not await _ensure_authorized_callback(callback):
        return
    await state.clear()
    await state.update_data({GLOBAL_COMMAND_STATE_KEY: "create"})
    await state.set_state(CommandCreateStates.waiting_name)
    if callback.message:
        await callback.message.answer("请输入通用命令名称（字母开头，可含数字/下划线/短横线），发送“取消”可终止。")
    await callback.answer("请输入命令名称")


@router.callback_query(F.data.startswith(GLOBAL_COMMAND_EDIT_PREFIX))
async def on_global_command_edit(callback: CallbackQuery, state: FSMContext) -> None:
    """进入通用命令编辑面板。"""

    if not await _ensure_authorized_callback(callback):
        return
    raw_id = (callback.data or "")[len(GLOBAL_COMMAND_EDIT_PREFIX) :]
    if not raw_id.isdigit():
        await callback.answer("命令标识无效", show_alert=True)
        return
    command_id = int(raw_id)
    try:
        command = await GLOBAL_COMMAND_SERVICE.get_command(command_id)
    except CommandNotFoundError:
        await callback.answer("通用命令不存在", show_alert=True)
        await _edit_global_command_overview(callback, notice="目标命令已被删除。")
        return
    await state.update_data(
        {
            GLOBAL_COMMAND_STATE_KEY: "edit",
            "command_id": command_id,
        }
    )
    await state.set_state(CommandEditStates.waiting_choice)
    if callback.message:
        await callback.message.answer(
            f"正在编辑 {command.name}，请选择需要修改的字段：",
            reply_markup=_build_global_command_edit_keyboard(command),
        )
    await callback.answer("请选择字段")


@router.callback_query(F.data.startswith(GLOBAL_COMMAND_FIELD_PREFIX))
async def on_global_command_field(callback: CallbackQuery, state: FSMContext) -> None:
    """提示用户输入新的字段值。"""

    if not await _ensure_authorized_callback(callback):
        return
    data = (callback.data or "")[len(GLOBAL_COMMAND_FIELD_PREFIX) :]
    field, _, raw_id = data.partition(":")
    if not raw_id.isdigit():
        await callback.answer("字段标识无效", show_alert=True)
        return
    command_id = int(raw_id)
    try:
        command = await GLOBAL_COMMAND_SERVICE.get_command(command_id)
    except CommandNotFoundError:
        await callback.answer("通用命令不存在", show_alert=True)
        await _edit_global_command_overview(callback, notice="目标命令已被删除。")
        return
    prompt_text = build_field_prompt_text(command, field)
    if prompt_text is None:
        await callback.answer("暂不支持该字段", show_alert=True)
        return
    await state.update_data(
        {
            GLOBAL_COMMAND_STATE_KEY: "edit",
            "command_id": command_id,
            "field": field,
        }
    )
    if field == "aliases":
        await state.set_state(CommandEditStates.waiting_aliases)
    else:
        await state.set_state(CommandEditStates.waiting_value)
    if callback.message:
        await callback.message.answer(prompt_text)
    await callback.answer("请发送新的值")


@router.callback_query(F.data.startswith(GLOBAL_COMMAND_TOGGLE_PREFIX))
async def on_global_command_toggle(callback: CallbackQuery) -> None:
    """切换通用命令启用状态。"""

    if not await _ensure_authorized_callback(callback):
        return
    raw_id = (callback.data or "")[len(GLOBAL_COMMAND_TOGGLE_PREFIX) :]
    if not raw_id.isdigit():
        await callback.answer("命令标识无效", show_alert=True)
        return
    command_id = int(raw_id)
    try:
        command = await GLOBAL_COMMAND_SERVICE.get_command(command_id)
    except CommandNotFoundError:
        await callback.answer("通用命令不存在", show_alert=True)
        await _edit_global_command_overview(callback, notice="目标命令已被删除。")
        return
    updated = await GLOBAL_COMMAND_SERVICE.update_command(command_id, enabled=not command.enabled)
    action_text = "已启用" if updated.enabled else "已停用"
    await _edit_global_command_overview(callback, notice=f"{updated.name} {action_text}")
    await callback.answer(action_text)


@router.callback_query(F.data.startswith(GLOBAL_COMMAND_DELETE_PROMPT_PREFIX))
async def on_global_command_delete_prompt(callback: CallbackQuery) -> None:
    """提醒管理员确认删除命令。"""

    if not await _ensure_authorized_callback(callback):
        return
    raw_id = (callback.data or "")[len(GLOBAL_COMMAND_DELETE_PROMPT_PREFIX) :]
    if not raw_id.isdigit():
        await callback.answer("命令标识无效", show_alert=True)
        return
    command_id = int(raw_id)
    try:
        command = await GLOBAL_COMMAND_SERVICE.get_command(command_id)
    except CommandNotFoundError:
        await callback.answer("通用命令不存在", show_alert=True)
        await _edit_global_command_overview(callback, notice="目标命令已被删除。")
        return
    confirm_markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ 确认删除",
                    callback_data=f"{GLOBAL_COMMAND_DELETE_CONFIRM_PREFIX}{command_id}",
                ),
                InlineKeyboardButton(
                    text="取消",
                    callback_data=f"{GLOBAL_COMMAND_EDIT_PREFIX}{command_id}",
                ),
            ]
        ]
    )
    if callback.message:
        await callback.message.answer(
            f"确定要删除通用命令 {command.name} 吗？此操作不可恢复。",
            reply_markup=confirm_markup,
        )
    await callback.answer("请确认删除")


@router.callback_query(F.data.startswith(GLOBAL_COMMAND_DELETE_CONFIRM_PREFIX))
async def on_global_command_delete_confirm(callback: CallbackQuery) -> None:
    """执行命令删除操作。"""

    if not await _ensure_authorized_callback(callback):
        return
    raw_id = (callback.data or "")[len(GLOBAL_COMMAND_DELETE_CONFIRM_PREFIX) :]
    if not raw_id.isdigit():
        await callback.answer("命令标识无效", show_alert=True)
        return
    command_id = int(raw_id)
    try:
        await GLOBAL_COMMAND_SERVICE.delete_command(command_id)
    except CommandNotFoundError:
        await callback.answer("通用命令不存在", show_alert=True)
        await _edit_global_command_overview(callback, notice="目标命令已被删除。")
        return
    await _edit_global_command_overview(callback, notice="通用命令已彻底删除。")
    await callback.answer("已删除")


@router.message(CommandCreateStates.waiting_name)
async def on_global_command_create_name(message: Message, state: FSMContext) -> None:
    """处理通用命令名称输入。"""

    data = await state.get_data()
    if not _is_global_command_flow(data, "create"):
        return
    text = (message.text or "").strip()
    if _is_cancel_text(text):
        await state.clear()
        await message.answer("通用命令创建已取消。")
        return
    if not CommandService.NAME_PATTERN.match(text):
        await message.answer("名称需以字母开头，可含数字/下划线/短横线，长度 3-64，请重新输入：")
        return
    existing = await GLOBAL_COMMAND_SERVICE.resolve_by_trigger(text)
    if existing:
        await message.answer("同名通用命令或别名已存在，请换一个名称：")
        return
    conflict_slug = await _detect_project_command_conflict([text])
    if conflict_slug:
        await message.answer(f"与项目 {conflict_slug} 的命令冲突，请更换名称。")
        return
    await state.update_data(name=text)
    await state.set_state(CommandCreateStates.waiting_shell)
    await message.answer("请输入需要执行的命令（例如 ./scripts/deploy.sh）：")


@router.message(CommandCreateStates.waiting_shell)
async def on_global_command_create_shell(message: Message, state: FSMContext) -> None:
    """处理通用命令的执行脚本输入。"""

    data = await state.get_data()
    if not _is_global_command_flow(data, "create"):
        return
    text = (message.text or "").strip()
    if _is_cancel_text(text):
        await state.clear()
        await message.answer("通用命令创建已取消。")
        return
    if not text:
        await message.answer("命令内容不能为空，请重新输入：")
        return
    name = data.get("name")
    if not name:
        await state.clear()
        await message.answer("上下文已失效，请重新点击“🆕 新增通用命令”。")
        return
    try:
        created = await GLOBAL_COMMAND_SERVICE.create_command(
            name=name,
            title=name,
            command=text,
            description="",
            aliases=(),
        )
    except (ValueError, CommandAlreadyExistsError, CommandAliasConflictError) as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    await message.answer(f"通用命令 {created.name} 已创建，描述与别名可稍后在编辑面板补齐。")
    await _send_global_command_overview_message(message, notice="新的通用命令已生效。")


@router.message(CommandEditStates.waiting_value)
async def on_global_command_edit_value(message: Message, state: FSMContext) -> None:
    """处理通用命令字段更新。"""

    data = await state.get_data()
    if not _is_global_command_flow(data, "edit"):
        return
    text = (message.text or "").strip()
    if _is_cancel_text(text):
        await state.clear()
        await message.answer("通用命令编辑已取消。")
        return
    command_id = data.get("command_id")
    field = data.get("field")
    if not command_id or not field:
        await state.clear()
        await message.answer("上下文已失效，请重新选择通用命令。")
        return
    updates: Dict[str, object] = {}
    if field == "title":
        updates["title"] = text
    elif field == "command":
        if not text:
            await message.answer("命令内容不能为空，请重新输入：")
            return
        updates["command"] = text
    elif field == "description":
        updates["description"] = text
    elif field == "timeout":
        try:
            updates["timeout"] = int(text)
        except ValueError:
            await message.answer("超时需为整数秒，请重新输入：")
            return
    else:
        await message.answer("暂不支持该字段。")
        await state.clear()
        return
    try:
        updated = await GLOBAL_COMMAND_SERVICE.update_command(command_id, **updates)
    except (ValueError, CommandAlreadyExistsError, CommandNotFoundError) as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    await message.answer(f"通用命令 {updated.name} 已更新。")
    await _send_global_command_overview_message(message, notice="通用命令字段已更新。")


@router.message(CommandEditStates.waiting_aliases)
async def on_global_command_edit_aliases(message: Message, state: FSMContext) -> None:
    """处理通用命令别名更新。"""

    data = await state.get_data()
    if not _is_global_command_flow(data, "edit"):
        return
    text = (message.text or "").strip()
    if _is_cancel_text(text):
        await state.clear()
        await message.answer("通用命令编辑已取消。")
        return
    command_id = data.get("command_id")
    if not command_id:
        await state.clear()
        await message.answer("上下文已失效，请重新选择通用命令。")
        return
    aliases = _parse_global_alias_input(text)
    conflict_slug = await _detect_project_command_conflict(aliases)
    if conflict_slug:
        await message.answer(f"别名与项目 {conflict_slug} 的命令冲突，请重新输入：")
        return
    try:
        updated_aliases = await GLOBAL_COMMAND_SERVICE.replace_aliases(command_id, aliases)
    except (ValueError, CommandAliasConflictError, CommandNotFoundError) as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    if updated_aliases:
        alias_text = ", ".join(updated_aliases)
        await message.answer(f"别名已更新：{alias_text}")
    else:
        await message.answer("别名已清空。")
    await _send_global_command_overview_message(message, notice="别名已同步至通用命令。")


@router.message()
async def cmd_fallback(message: Message) -> None:
    """兜底处理器：尝试继续向导，否则提示可用命令。"""

    _log_update(message)
    manager = await _ensure_manager()
    if not manager.is_authorized(message.chat.id):
        await message.answer("未授权。")
        return
    handled = await _handle_wizard_message(message, manager)
    if handled:
        return
    await message.answer("未识别的命令，请使用 /projects /run /stop /switch /authorize。")



def _delete_project_with_fallback(
    repository: ProjectRepository,
    *,
    stored_slug: str,
    original_slug: str,
    bot_name: str,
) -> Tuple[Optional[Exception], List[Tuple[str, Exception]]]:
    """尝试以多种标识删除项目，提升大小写与别名兼容性。"""

    attempts: List[Tuple[str, Exception]] = []

    def _attempt(candidate: str) -> Optional[Exception]:
        """实际执行删除，失败返回异常供后续兜底。"""
        slug = (candidate or "").strip()
        if not slug:
            return ValueError("slug 为空")
        try:
            repository.delete_project(slug)
        except ValueError as delete_exc:
            return delete_exc
        return None

    primary_error = _attempt(stored_slug)
    if primary_error is None:
        return None, attempts
    attempts.append((stored_slug, primary_error))

    if original_slug and original_slug != stored_slug:
        secondary_error = _attempt(original_slug)
        if secondary_error is None:
            return None, attempts
        attempts.append((original_slug, secondary_error))

    if bot_name:
        try:
            fallback_record = repository.get_by_bot_name(bot_name)
        except Exception as lookup_exc:
            attempts.append((f"bot:{bot_name}", lookup_exc))
        else:
            if fallback_record:
                fallback_slug = fallback_record.project_slug
                if not any(slug.lower() == fallback_slug.lower() for slug, _ in attempts):
                    fallback_error = _attempt(fallback_slug)
                    if fallback_error is None:
                        return None, attempts
                    attempts.append((fallback_slug, fallback_error))

    return primary_error, attempts


@router.callback_query(F.data.startswith("project:delete_confirm:"))
async def on_project_delete_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    """处理删除确认按钮的回调逻辑。"""
    manager = await _ensure_manager()
    user_id = callback.from_user.id if callback.from_user else None
    if user_id is None or not manager.is_authorized(user_id):
        await callback.answer("未授权。", show_alert=True)
        return
    if callback.message is None:
        await callback.answer("无效操作", show_alert=True)
        return
    parts = callback.data.split(":", 2)
    if len(parts) != 3:
        await callback.answer("无效操作", show_alert=True)
        return
    target_slug = parts[2]
    log.info(
        "删除确认回调: user=%s slug=%s",
        user_id,
        target_slug,
        extra={"project": target_slug},
    )
    current_state = await state.get_state()
    if current_state != ProjectDeleteStates.confirming.state:
        await callback.answer("确认流程已过期，请重新发起删除。", show_alert=True)
        return
    data = await state.get_data()
    stored_slug = str(data.get("project_slug", "")).strip()
    if stored_slug.lower() != target_slug.lower():
        await state.clear()
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        await callback.answer("确认信息已失效，请重新发起删除。", show_alert=True)
        return
    initiator_id = data.get("initiator_id")
    if initiator_id and initiator_id != user_id:
        await callback.answer("仅流程发起者可以确认删除。", show_alert=True)
        return
    expires_at = float(data.get("expires_at") or 0)
    if expires_at and time.time() > expires_at:
        await state.clear()
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        await callback.answer("确认已超时，请重新发起删除。", show_alert=True)
        return
    repository = _ensure_repository()
    original_slug = str(data.get("original_slug") or "").strip()
    bot_name = str(data.get("bot_name") or "").strip()
    error, attempts = _delete_project_with_fallback(
        repository,
        stored_slug=stored_slug,
        original_slug=original_slug,
        bot_name=bot_name,
    )
    if error is not None:
        log.error(
            "删除项目失败: %s",
            error,
            extra={
                "slug": stored_slug,
                "attempts": [slug for slug, _ in attempts],
            },
        )
        await callback.answer("删除失败，请稍后重试。", show_alert=True)
        await callback.message.answer(f"删除失败：{error}")
        return
    await state.clear()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    _reload_manager_configs(manager)
    display_name = data.get("display_name") or stored_slug
    await callback.answer("项目已删除")
    await callback.message.answer(f"项目 {display_name} 已删除 ✅")
    await _send_projects_overview_to_chat(callback.message.bot, callback.message.chat.id, manager)


@router.callback_query(F.data == "project:delete_cancel")
async def on_project_delete_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    """处理删除流程的取消按钮。"""
    manager = await _ensure_manager()
    user_id = callback.from_user.id if callback.from_user else None
    if user_id is None or not manager.is_authorized(user_id):
        await callback.answer("未授权。", show_alert=True)
        return
    if callback.message is None:
        await callback.answer("无效操作", show_alert=True)
        return
    current_state = await state.get_state()
    if current_state != ProjectDeleteStates.confirming.state:
        await callback.answer("当前没有待确认的删除流程。", show_alert=True)
        return
    data = await state.get_data()
    log.info(
        "删除取消回调: user=%s slug=%s",
        user_id,
        data.get("project_slug"),
    )
    initiator_id = data.get("initiator_id")
    if initiator_id and initiator_id != user_id:
        await callback.answer("仅流程发起者可以取消删除。", show_alert=True)
        return
    expires_at = float(data.get("expires_at") or 0)
    if expires_at and time.time() > expires_at:
        await state.clear()
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        await callback.answer("确认已超时，请重新发起删除。", show_alert=True)
        return
    await state.clear()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    display_name = data.get("display_name") or data.get("project_slug") or ""
    await callback.answer("删除流程已取消")
    await callback.message.answer(f"已取消删除项目 {display_name}。")


@router.message(ProjectDeleteStates.confirming)
async def on_project_delete_text(message: Message, state: FSMContext) -> None:
    """兼容旧版交互，允许通过文本指令确认或取消删除。"""
    manager = await _ensure_manager()
    user = message.from_user
    if user is None or not manager.is_authorized(user.id):
        await message.answer("未授权。")
        return
    data = await state.get_data()
    initiator_id = data.get("initiator_id")
    if initiator_id and initiator_id != user.id:
        await message.answer("仅流程发起者可以继续此删除流程。")
        return
    expires_at = float(data.get("expires_at") or 0)
    if expires_at and time.time() > expires_at:
        await state.clear()
        prompt = getattr(message, "reply_to_message", None)
        if prompt:
            try:
                await prompt.edit_reply_markup(reply_markup=None)
            except TelegramBadRequest:
                pass
        await message.answer("确认已超时，请重新发起删除。")
        return

    raw_text = (message.text or "").strip()
    if not raw_text:
        await message.answer("请使用按钮或输入“确认删除”/“取消”完成操作。")
        return
    normalized = raw_text.casefold().strip()
    normalized = normalized.rstrip("。.!？?")
    normalized_compact = normalized.replace(" ", "")
    confirm_tokens = {"确认删除", "确认", "confirm", "y", "yes"}
    cancel_tokens = {"取消", "cancel", "n", "no"}

    if normalized in cancel_tokens or normalized_compact in cancel_tokens:
        await state.clear()
        prompt = getattr(message, "reply_to_message", None)
        if prompt:
            try:
                await prompt.edit_reply_markup(reply_markup=None)
            except TelegramBadRequest:
                pass
        display_name = data.get("display_name") or data.get("project_slug") or ""
        await message.answer(f"已取消删除项目 {display_name}。")
        return

    if not (
        normalized in confirm_tokens
        or normalized_compact in confirm_tokens
        or normalized.startswith("确认删除")
    ):
        await message.answer("请输入“确认删除”或通过按钮完成操作。")
        return

    stored_slug = str(data.get("project_slug", "")).strip()
    if not stored_slug:
        await state.clear()
        await message.answer("删除流程状态异常，请重新发起删除。")
        return
    original_slug = str(data.get("original_slug") or "").strip()
    bot_name = str(data.get("bot_name") or "").strip()
    repository = _ensure_repository()
    error, attempts = _delete_project_with_fallback(
        repository,
        stored_slug=stored_slug,
        original_slug=original_slug,
        bot_name=bot_name,
    )
    if error is not None:
        log.error(
            "删除项目失败(文本确认): %s",
            error,
            extra={
                "slug": stored_slug,
                "attempts": [slug for slug, _ in attempts],
            },
        )
        await message.answer(f"删除失败：{error}")
        return

    await state.clear()
    prompt = getattr(message, "reply_to_message", None)
    if prompt:
        try:
            await prompt.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
    _reload_manager_configs(manager)
    display_name = data.get("display_name") or stored_slug
    await message.answer(f"项目 {display_name} 已删除 ✅")
    await _send_projects_overview_to_chat(message.bot, message.chat.id, manager)



async def bootstrap_manager() -> MasterManager:
    """初始化项目仓库、状态存储与 manager，启动前清理旧 worker。"""

    load_env()
    _clear_related_tmux_sessions()
    try:
        repository = ProjectRepository(CONFIG_DB_PATH, CONFIG_PATH)
    except Exception as exc:
        log.error("初始化项目仓库失败: %s", exc)
        sys.exit(1)

    records = repository.list_projects()
    if not records:
        log.warning("项目配置为空，将以空项目列表启动。")

    configs = [ProjectConfig.from_dict(record.to_dict()) for record in records]

    state_store = StateStore(STATE_PATH, {cfg.project_slug: cfg for cfg in configs})
    manager = MasterManager(configs, state_store=state_store)

    await manager.stop_all(update_state=True)
    log.info("已清理历史 tmux 会话，worker 需手动启动。")

    global MANAGER
    global PROJECT_REPOSITORY
    MANAGER = manager
    PROJECT_REPOSITORY = repository
    return manager


async def main() -> None:
    """master.py 的异步入口，完成 bot 启动与调度器绑定。"""

    manager = await bootstrap_manager()
    await _ensure_default_global_commands()

    # 诊断日志：记录重启信号文件路径，便于排查问题
    log.info(
        "重启信号文件路径: %s (存在: %s)",
        RESTART_SIGNAL_PATH,
        RESTART_SIGNAL_PATH.exists(),
        extra={
            "signal_path": str(RESTART_SIGNAL_PATH),
            "signal_exists": RESTART_SIGNAL_PATH.exists(),
            "env_override": os.environ.get("MASTER_RESTART_SIGNAL_PATH"),
        },
    )

    master_token = os.environ.get("MASTER_BOT_TOKEN")
    if not master_token:
        log.error("MASTER_BOT_TOKEN 未设置")
        sys.exit(1)

    proxy_url, proxy_auth, _ = _detect_proxy()
    session_kwargs = {}
    if proxy_url:
        session_kwargs["proxy"] = proxy_url
    if proxy_auth:
        session_kwargs["proxy_auth"] = proxy_auth
    session = AiohttpSession(**session_kwargs)
    bot = Bot(token=master_token, session=session)
    if proxy_url:
        session._connector_init.update({  # type: ignore[attr-defined]
            "family": __import__('socket').AF_INET,
            "ttl_dns_cache": 60,
        })
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    dp.startup.register(_notify_restart_success)
    dp.startup.register(_notify_start_signal)
    dp.startup.register(_notify_upgrade_report)

    log.info("Master 已启动，监听管理员指令。")
    await _ensure_master_menu_button(bot)
    await _ensure_master_commands(bot)
    await _broadcast_master_keyboard(bot, manager)
    asyncio.create_task(_periodic_update_check(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    _terminate_other_master_processes()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Master 停止")
