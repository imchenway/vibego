"""Master bot controller.

Responsibilities:
- Read `config/master.db` (kept in sync with `config/projects.json`) to load project configuration.
- Maintain `state/state.json`, recording runtime status, the active model, and automatically captured chat IDs.
- Expose the /projects, /run, /stop, /switch, and /authorize administrator commands.
- Invoke `scripts/run_bot.sh` / `scripts/stop_bot.sh` to manage worker processes.
"""
from __future__ import annotations

import asyncio
import json
import math
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
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple

from aiogram import Bot, Dispatcher, Router, F
from aiohttp import BasicAuth
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
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

from logging_setup import create_logger
from project_repository import ProjectRepository, ProjectRecord
from tasks.fsm import ProjectDeleteStates
from vibego_cli import __version__
from command_center.fsm import CommandPresetStates
from command_center.models import CommandPresetRecord, GLOBAL_COMMAND_PROJECT_SLUG
from command_center.service import CommandPresetService

try:
    from packaging.version import Version, InvalidVersion
except ImportError:  # pragma: no cover
    Version = None  # type: ignore[assignment]

    class InvalidVersion(Exception):
        """Placeholder exception, compatible with version parsing errors when packaging is missing. """

ROOT_DIR = Path(__file__).resolve().parent
def _default_config_root() -> Path:
    """
    Parse the configuration root directory, be compatible with multiple environment variables and fall back to the XDG specification.

    Priority:
    1. MASTER_CONFIG_ROOT(for master.py use)
    2. VIBEGO_CONFIG_DIR(CLI Entrance settings)
    3. $XDG_CONFIG_HOME/vibego or ~/.config/vibego
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

CONFIG_DIR.mkdir(parents=True, exist_ok=True)
STATE_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR = CONFIG_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = Path(os.environ.get("MASTER_PROJECTS_PATH", CONFIG_DIR / "projects.json"))
CONFIG_DB_PATH = Path(os.environ.get("MASTER_PROJECTS_DB_PATH", CONFIG_DIR / "master.db"))
STATE_PATH = Path(os.environ.get("MASTER_STATE_PATH", STATE_DIR / "state.json"))
RUN_SCRIPT = ROOT_DIR / "scripts/run_bot.sh"
STOP_SCRIPT = ROOT_DIR / "scripts/stop_bot.sh"

UPDATE_STATE_PATH = STATE_DIR / "update_state.json"
UPDATE_CHECK_INTERVAL = timedelta(hours=24)
_UPDATE_STATE_LOCK = threading.Lock()


def _get_restart_signal_path() -> Path:
    """
    Get the restart signal file path, using robust default value logic.

    Priority:
    1. Environment variable MASTER_RESTART_SIGNAL_PATH
    2. Configuration directory $MASTER_CONFIG_ROOT/state/restart_signal.json
    3. Code directory ROOT_DIR/state/restart_signal.json(full details)

    This ensures that the version installed by pipx and the version run from the source code use the same signal file.
    """
    if env_path := os.environ.get("MASTER_RESTART_SIGNAL_PATH"):
        return Path(env_path)

    # Use the configuration directory instead of the code directory by default to ensure consistency across installation methods
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
RESTART_SIGNAL_TTL = int(os.environ.get("MASTER_RESTART_SIGNAL_TTL", "1800"))  # Default 30 minutes
LOCAL_TZ = ZoneInfo(os.environ.get("MASTER_TIMEZONE", "Asia/Shanghai"))
JUMP_BUTTON_TEXT_WIDTH = 40

_DEFAULT_LOG_ROOT = LOG_DIR
LOG_ROOT_PATH = Path(os.environ.get("LOG_ROOT", str(_DEFAULT_LOG_ROOT))).expanduser()

WORKER_HEALTH_TIMEOUT = float(os.environ.get("WORKER_HEALTH_TIMEOUT", "20"))
WORKER_HEALTH_INTERVAL = float(os.environ.get("WORKER_HEALTH_INTERVAL", "0.5"))
WORKER_HEALTH_LOG_TAIL = int(os.environ.get("WORKER_HEALTH_LOG_TAIL", "80"))
HANDSHAKE_MARKERS = (
    "Telegram The connection is OK",
)
DELETE_CONFIRM_TIMEOUT = int(os.environ.get("MASTER_DELETE_CONFIRM_TIMEOUT", "120"))

_ENV_FILE_RAW = os.environ.get("MASTER_ENV_FILE")
MASTER_ENV_FILE = Path(_ENV_FILE_RAW).expanduser() if _ENV_FILE_RAW else None
_ENV_LOCK = threading.Lock()

MASTER_MENU_BUTTON_TEXT = "📂 Project list"
# Copywriting for the old version of the keyboard, for compatibility with client messages that still display in English
MASTER_MENU_BUTTON_LEGACY_TEXTS: Tuple[str, ...] = ("📂 Projects",)
# All copywriting in the project list is allowed to be triggered, and the latest copywriting will be matched first.
MASTER_MENU_BUTTON_ALLOWED_TEXTS: Tuple[str, ...] = (
    MASTER_MENU_BUTTON_TEXT,
    *MASTER_MENU_BUTTON_LEGACY_TEXTS,
)
MASTER_MANAGE_BUTTON_TEXT = "⚙️ Project Management"
MASTER_MANAGE_BUTTON_ALLOWED_TEXTS: Tuple[str, ...] = (MASTER_MANAGE_BUTTON_TEXT,)
MASTER_COMMAND_BUTTON_TEXT = "⚙️ Command Center"
MASTER_COMMAND_BUTTON_ALLOWED_TEXTS: Tuple[str, ...] = (MASTER_COMMAND_BUTTON_TEXT,)
MASTER_BOT_COMMANDS: List[Tuple[str, str]] = [
    ("start", "Start master menu"),
    ("projects", "View project list"),
    ("run", "Start worker"),
    ("stop", "Stop worker"),
    ("switch", "Switch worker model"),
    ("authorize", "Register chat"),
    ("restart", "Restart master"),
    ("upgrade", "Upgrade vibego to the latest version"),
]
MASTER_BROADCAST_MESSAGE = os.environ.get("MASTER_BROADCAST_MESSAGE", "")
MASTER_COMMAND_LIST_PAGE_SIZE = max(1, min(int(os.environ.get("MASTER_COMMAND_PAGE_SIZE", "5")), 20))
MASTER_COMMAND_LIST_CALLBACK = "mcmd:list"
MASTER_COMMAND_DETAIL_CALLBACK = "mcmd:detail"
MASTER_COMMAND_CREATE_CALLBACK = "mcmd:create"
MASTER_COMMAND_EDIT_CALLBACK = "mcmd:edit"
MASTER_COMMAND_DELETE_CALLBACK = "mcmd:delete"
MASTER_COMMAND_DELETE_EXECUTE_CALLBACK = "mcmd:delete_exec"
MASTER_COMMAND_TOGGLE_CONFIRM_CALLBACK = "mcmd:toggle_confirm"
MASTER_COMMAND_SKIP_TEXT = "Skip"
MASTER_COMMAND_CANCEL_TEXT = "Cancel"
MASTER_COMMAND_DB_PATH = DATA_DIR / "master_commands.db"
MASTER_COMMAND_SERVICE = CommandPresetService(MASTER_COMMAND_DB_PATH, GLOBAL_COMMAND_PROJECT_SLUG)
SWITCHABLE_MODELS: Tuple[Tuple[str, str], ...] = (
    ("codex", "⚙️ Codex"),
    ("claudecode", "⚙️ ClaudeCode"),
)

# Telegram Different clients may insert zero-width characters or extra whitespace to normalize button text in advance.
ZERO_WIDTH_CHARACTERS: Tuple[str, ...] = ("\u200b", "\u200c", "\u200d", "\ufeff")


def _normalize_button_text(text: str) -> str:
    """Normalize item button text, strip out zero-width characters and unify case. """

    filtered = "".join(ch for ch in text if ch not in ZERO_WIDTH_CHARACTERS)
    compacted = re.sub(r"\s+", " ", filtered).strip()
    return unicodedata.normalize("NFKC", compacted).casefold()


MASTER_MENU_BUTTON_CANONICAL_NORMALIZED = _normalize_button_text(MASTER_MENU_BUTTON_TEXT)
MASTER_MENU_BUTTON_ALLOWED_NORMALIZED = {
    _normalize_button_text(value) for value in MASTER_MENU_BUTTON_ALLOWED_TEXTS
}
MASTER_MENU_BUTTON_KEYWORDS: Tuple[str, ...] = ("Project list", "project", "projects")


def _is_projects_menu_trigger(text: Optional[str]) -> bool:
    """Determine whether the message text can trigger the display of the project list. """

    if not text:
        return False
    normalized = _normalize_button_text(text)
    if not normalized:
        return False
    if normalized in MASTER_MENU_BUTTON_ALLOWED_NORMALIZED:
        return True
    return any(keyword in normalized for keyword in MASTER_MENU_BUTTON_KEYWORDS)


def _text_equals_master_button(text: str) -> bool:
    """Determine whether the text is equal to the current main button copy (blank differences are allowed). """

    return _normalize_button_text(text) == MASTER_MENU_BUTTON_CANONICAL_NORMALIZED


def _build_master_main_keyboard() -> ReplyKeyboardMarkup:
    """Construct the Master Bot main keyboard, providing project list and management entrance. """
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=MASTER_MENU_BUTTON_TEXT),
                KeyboardButton(text=MASTER_MANAGE_BUTTON_TEXT),
            ],
            [
                KeyboardButton(text=MASTER_COMMAND_BUTTON_TEXT),
            ],
        ],
        resize_keyboard=True,
    )


def _format_command_preview(command: str) -> str:
    """Return the first line preview for command buttons."""

    if not command:
        return ""
    first_line = command.strip().splitlines()[0]
    if not first_line:
        return ""
    preview = first_line.strip()
    max_length = 30
    if len(preview) > max_length:
        preview = f"{preview[:max_length]}…"
    return preview


def _master_is_skip_message(value: Optional[str]) -> bool:
    """Return True if the payload represents a Skip action."""

    token = (value or "").strip().casefold()
    return token in {"skip", MASTER_COMMAND_SKIP_TEXT.casefold()}


def _master_is_cancel_message(value: Optional[str]) -> bool:
    """Return True when the payload requests cancellation."""

    token = (value or "").strip().casefold()
    return token in {"cancel", MASTER_COMMAND_CANCEL_TEXT.casefold()}


def _build_master_description_keyboard(include_skip: bool = True) -> ReplyKeyboardMarkup:
    """Reusable keyboard for command wizard inputs."""

    rows: list[list[KeyboardButton]] = []
    if include_skip:
        rows.append([KeyboardButton(text=MASTER_COMMAND_SKIP_TEXT)])
    rows.append([KeyboardButton(text=MASTER_COMMAND_CANCEL_TEXT)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


def _build_master_boolean_keyboard() -> ReplyKeyboardMarkup:
    """Keyboard for yes/no confirmation selection."""

    rows = [
        [KeyboardButton(text="Yes"), KeyboardButton(text="No")],
        [KeyboardButton(text=MASTER_COMMAND_CANCEL_TEXT)],
    ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


def _master_parse_confirmation_choice(value: Optional[str]) -> Optional[bool]:
    """Convert wizard confirmation input to boolean."""

    if value is None:
        return None
    token = value.strip().casefold()
    if token in {"yes", "需要", "true", "y", "1"}:
        return True
    if token in {"no", "无需", "false", "n", "0"}:
        return False
    return None


def _master_command_confirm_label(mode: str) -> str:
    """Return the final confirmation label for the wizard."""

    return "✅ 创建命令" if mode == "create" else "✅ 保存命令"


def _build_master_confirm_keyboard(mode: str) -> ReplyKeyboardMarkup:
    """Keyboard used at the wizard confirmation step."""

    rows = [
        [KeyboardButton(text=_master_command_confirm_label(mode))],
        [KeyboardButton(text=MASTER_COMMAND_CANCEL_TEXT)],
    ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


def _master_format_command_summary(
    *,
    name: str,
    command_text: str,
    workdir: Optional[str],
    require_confirmation: bool,
) -> str:
    """Render the command summary shown before confirmation."""

    lines = [
        "命令预览",
        f"名称: {name}",
        f"工作目录: {workdir or '继承当前终端会话'}",
        f"执行前确认: {'需要' if require_confirmation else '无需'}",
        "",
        "命令内容:",
        command_text.strip() or "(空)",
    ]
    return "\n".join(lines)


async def _master_get_wizard_data(state: FSMContext) -> Optional[dict]:
    """Return wizard context when the state belongs to the master command center."""

    data = await state.get_data()
    if data.get("context") != "master_command_center":
        return None
    return data


async def _master_cancel_command_wizard(message: Message, state: FSMContext) -> None:
    """Abort the master command wizard."""

    await state.clear()
    await message.answer("命令配置已取消。", reply_markup=_build_master_main_keyboard())


async def _build_master_command_list_view(page: int) -> tuple[str, InlineKeyboardMarkup]:
    """Build the master-side command list (global presets)."""

    total = await MASTER_COMMAND_SERVICE.count_presets()
    total_pages = max(1, math.ceil(total / MASTER_COMMAND_LIST_PAGE_SIZE))
    current_page = min(max(1, page), total_pages)
    presets = await MASTER_COMMAND_SERVICE.list_presets(current_page, MASTER_COMMAND_LIST_PAGE_SIZE)
    lines = [
        "⚙️ 全局命令管理",
        f"共 {total} 条，页码 {current_page}/{total_pages}，每页 {MASTER_COMMAND_LIST_PAGE_SIZE} 条。",
    ]
    if not presets:
        lines.append("尚未配置全局命令，点击“➕ 新建命令”开始。")
    else:
        lines.append("")
        lines.append("当前命令：")
        for preset in presets:
            lines.append(f"- {preset.title}")
    rows: list[list[InlineKeyboardButton]] = []
    for preset in presets:
        preview = _format_command_preview(preset.command)
        label = f"▶️ {preset.title}"
        if preview:
            label = f"{label} · {preview}"
        rows.append(
            [
                InlineKeyboardButton(
                    text=label[:60],
                    callback_data=f"{MASTER_COMMAND_DETAIL_CALLBACK}:{preset.id}:{current_page}",
                )
            ]
        )
    nav_row: list[InlineKeyboardButton] = []
    if current_page > 1:
        nav_row.append(
            InlineKeyboardButton(
                text="⬅️ 上一页",
                callback_data=f"{MASTER_COMMAND_LIST_CALLBACK}:{current_page-1}",
            )
        )
    if current_page < total_pages:
        nav_row.append(
            InlineKeyboardButton(
                text="下一页 ➡️",
                callback_data=f"{MASTER_COMMAND_LIST_CALLBACK}:{current_page+1}",
            )
        )
    if nav_row:
        rows.append(nav_row)
    rows.append(
        [
            InlineKeyboardButton(
                text="➕ 新建命令",
                callback_data=f"{MASTER_COMMAND_CREATE_CALLBACK}:{current_page}",
            ),
            InlineKeyboardButton(
                text="🔄 刷新",
                callback_data=f"{MASTER_COMMAND_LIST_CALLBACK}:{current_page}",
            ),
        ]
    )
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def _build_master_command_detail_view(preset: CommandPresetRecord, origin_page: int) -> tuple[str, InlineKeyboardMarkup]:
    """Build the command detail view for master."""

    lines = [
        f"命令：{preset.title}",
        f"ID: {preset.id}",
        f"工作目录: {preset.workdir or '继承当前终端会话'}",
        f"执行前确认: {'需要' if preset.require_confirmation else '无需'}",
        "",
        "命令内容:",
        preset.command.strip() or "(空)",
    ]
    confirm_label = "🔐 确认：开" if preset.require_confirmation else "🔓 确认：关"
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=confirm_label,
                    callback_data=f"{MASTER_COMMAND_TOGGLE_CONFIRM_CALLBACK}:{preset.id}:{origin_page}",
                ),
                InlineKeyboardButton(
                    text="✏️ 编辑",
                    callback_data=f"{MASTER_COMMAND_EDIT_CALLBACK}:{preset.id}:{origin_page}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🗑️ 删除",
                    callback_data=f"{MASTER_COMMAND_DELETE_CALLBACK}:{preset.id}:{origin_page}",
                ),
                InlineKeyboardButton(
                    text="⬅️ 返回列表",
                    callback_data=f"{MASTER_COMMAND_LIST_CALLBACK}:{origin_page}",
                ),
            ],
        ]
    )
    return "\n".join(lines), markup


async def _render_master_command_list_message(
    *,
    target_message: Optional[Message],
    fallback_message: Message,
    page: int,
) -> None:
    """Render the master command list, editing in place when possible."""

    text, markup = await _build_master_command_list_view(page)
    if target_message:
        try:
            await fallback_message.bot.edit_message_text(
                chat_id=target_message.chat.id,
                message_id=target_message.message_id,
                text=text,
                reply_markup=markup,
            )
            return
        except TelegramBadRequest:
            pass
    await fallback_message.answer(text, reply_markup=markup)


async def _render_master_command_detail_message(
    *,
    target_message: Optional[Message],
    fallback_message: Message,
    preset: CommandPresetRecord,
    origin_page: int,
) -> None:
    """Render the command detail view in place."""

    text, markup = _build_master_command_detail_view(preset, origin_page)
    if target_message:
        try:
            await fallback_message.bot.edit_message_text(
                chat_id=target_message.chat.id,
                message_id=target_message.message_id,
                text=text,
                reply_markup=markup,
            )
            return
        except TelegramBadRequest:
            pass
    await fallback_message.answer(text, reply_markup=markup)


async def _ensure_master_menu_button(bot: Bot) -> None:
    """Synchronize the chat menu button text on the master side and fix the cache problem of the old client. """
    try:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonCommands(text=MASTER_MENU_BUTTON_TEXT),
        )
    except TelegramBadRequest as exc:
        log.warning("Failed to set chat menu: %s", exc)
    else:
        log.info("Chat menu has been synchronized", extra={"text": MASTER_MENU_BUTTON_TEXT})


async def _ensure_master_commands(bot: Bot) -> None:
    """Synchronize the command list on the master side to ensure that new/deleted commands take effect immediately. """
    commands= [BotCommand(command=cmd, description=desc) for cmd, desc in MASTER_BOT_COMMANDS]
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
            log.warning("Set master command failed: %s", exc, extra={"scope": label})
        else:
            log.info("master Command synchronized", extra={"scope": label})


def _collect_master_broadcast_targets(manager: MasterManager) -> List[int]:
    """Summarize the chat_id that needs to be pushed to the keyboard to avoid repeated broadcasts. """
    targets: set[int] = set(manager.admin_ids or [])
    manager.refresh_state()
    for state in manager.state_store.data.values():
        if state.chat_id:
            targets.add(state.chat_id)
    return sorted(targets)


async def _broadcast_master_keyboard(bot: Bot, manager: MasterManager) -> None:
    """During the master startup phase, the menu keyboard is actively pushed to overwrite the Telegram side cache. """
    targets = _collect_master_broadcast_targets(manager)
    # When the broadcast message is empty, it means that the startup prompt will no longer be pushed to the administrator, meeting the requirement of "prohibit sending /task_list".
    if not MASTER_BROADCAST_MESSAGE:
        log.info("Startup broadcast disabled, skipping master keyboard push. ")
        return
    if not targets:
        log.info("No master chat objects to push")
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
            log.warning("Push menu disabled: %s", exc, extra={"chat": chat_id})
        except TelegramBadRequest as exc:
            log.warning("Push menu failed: %s", exc, extra={"chat": chat_id})
        except Exception as exc:
            log.error("Push menu exception: %s", exc, extra={"chat": chat_id})
        else:
            log.info("Menu pushed to chat_id=%s", chat_id)


def _ensure_numbered_markup(markup: Optional[InlineKeyboardMarkup]) -> Optional[InlineKeyboardMarkup]:
    """Keep the original copy for InlineKeyboard and no longer automatically append numbers. """
    return markup


def _get_project_runtime_state(manager: "MasterManager", slug: str) -> Optional["ProjectState"]:
    """Normalize query project running status to avoid misuse of FSMContext.

    Here we focus on handling the case of slug and commenting on the reasons to prevent overwriting aiogram in routing.
    provided `FSMContext`(For details, please see the official documentation: https://docs.aiogram.dev/en/dev-3.x/dispatcher/fsm/context.html).
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
    """Terminate other remaining master processes after the new master starts"""
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
            log.warning("Failed to terminate residual master process: %s", exc, extra={"pid": pid})
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
            log.warning("Forced termination of master process failed: %s", exc, extra={"pid": pid})
    if existing:
        log.info("Cleaning up other master processes completed", extra={"terminated": existing, "force": list(alive)})



def load_env(file: str = ".env") -> None:
    """load default .env and the configuration pointed to by MASTER_ENV_FILE. """

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
    """Aggregate all potential admin chat_ids to avoid missing broadcasts. """

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
    """Terminate all tmux sessions matching the prefix to avoid multi-instance conflicts. """

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


def _mask_proxy(url: str) -> str:
    """Hide credentials in the proxy URL, leaving only the host and port. """

    if "@" not in url:
        return url
    from urllib.parse import urlparse
    parsed = urlparse(url)
    host = parsed.hostname or "***"
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://***:***@{host}{port}"


def _parse_env_file(path: Path) -> Dict[str, str]:
    """read .env file and returns a key-value map. """

    result:Dict[str, str] = {}
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
        log.warning("Failed to parse MASTER_ENV_FILE: %s", exc, extra={"path": str(path)})
    return result


def _dump_env_file(path: Path, values: Dict[str, str]) -> None:
    """write .env,The default is 600 permissions. """

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"{key}={values[key]}" for key in sorted(values)]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        try:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except PermissionError:
            pass
    except Exception as exc:  # pylint: disable=broad-except
        log.warning("Failed to write to MASTER_ENV_FILE: %s", exc, extra={"path": str(path)})


def _update_master_env(chat_id: Optional[int], user_id: Optional[int]) -> None:
    """Write the latest master interaction information .env."""

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
    """Format project status information for logging and notifications. """

    status = state.status if state else "stopped"
    model = state.model if state else cfg.default_model
    chat_id = state.chat_id if state else cfg.allowed_chat_id
    return (
        f"- {cfg.display_name}: status={status}, model={model}, chat_id={chat_id}, project={cfg.project_slug}"
    )


def _projects_overview(manager: MasterManager) -> Tuple[str, Optional[InlineKeyboardMarkup]]:
    """Generate overview text and action buttons based on the current project status. """

    builder = InlineKeyboardBuilder()
    button_count = 0
    model_name_map = dict(SWITCHABLE_MODELS)
    for cfg in manager.configs:
        state = manager.state_store.data.get(cfg.project_slug)
        status = state.status if state else "stopped"
        current_model = (state.model if state else cfg.default_model).lower()
        current_model_label = model_name_map.get(current_model, current_model)
        if status == "running":
            builder.row(
                InlineKeyboardButton(
                    text=f"{cfg.display_name}",
                    url=cfg.jump_url,
                ),
                InlineKeyboardButton(
                    text=f"⛔️ Stop ({current_model_label})",
                    callback_data=f"project:stop:{cfg.project_slug}",
                ),
            )
        else:
            builder.row(
                InlineKeyboardButton(
                    text=f"{cfg.display_name}",
                    url=cfg.jump_url,
                ),
                InlineKeyboardButton(
                    text=f"▶️ Start ({current_model_label})",
                    callback_data=f"project:run:{cfg.project_slug}",
                ),
            )
        button_count += 1
    builder.row(
        InlineKeyboardButton(text="🚀 Start all projects", callback_data="project:start_all:*")
    )
    builder.row(
        InlineKeyboardButton(text="⛔️ Stop all projects", callback_data="project:stop_all:*")
    )
    builder.row(
        InlineKeyboardButton(text="🔄 Restart Master", callback_data="project:restart_master:*")
    )
    markup = builder.as_markup()
    markup = _ensure_numbered_markup(markup)
    log.info("Project overview generated button count=%s", button_count)
    if button_count == 0:
        return (
            'No project configuration found. Open "⚙️ Project Management" to create a new project and try again.',
            markup,
        )
    return "Please select an action:", markup


def _utcnow() -> datetime:
    """Returns the current time in UTC for easy serialization. """

    return datetime.now(timezone.utc)


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse ISO8601 string into UTC time, return None in case of exception. """

    if not value:
        return None
    try:
        parsed=datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load_update_state() -> Dict[str, Any]:
    """Read update detection status, returning an empty dictionary on failure. """

    with _UPDATE_STATE_LOCK:
        if not UPDATE_STATE_PATH.exists():
            return {}
        try:
            raw = UPDATE_STATE_PATH.read_text(encoding="utf-8")
            state = json.loads(raw) if raw.strip() else {}
            if not isinstance(state, dict):
                state = {}
            return state
        except Exception as exc:  # pragma: no cover - It will only be triggered under extreme circumstances
            log.warning("Failed to read update status: %s", exc)
            return {}


def _save_update_state(state: Dict[str, Any]) -> None:
    """Persistent update state to ensure atomic writes. """

    with _UPDATE_STATE_LOCK:
        tmp_path = UPDATE_STATE_PATH.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(UPDATE_STATE_PATH)


def _ensure_notified_list(state: Dict[str, Any]) -> List[int]:
    """The notification list is guaranteed to exist in the state and a mutable reference is returned. """

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
    """Query the latest version of vibego from PyPI, and return None when the network is abnormal. """

    url=os.environ.get("VIBEGO_PYPI_JSON", "https://pypi.org/pypi/vibego/json")

    def _request() -> Optional[str]:
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                payload = json.load(resp)
        except Exception as exc:  # pragma: no cover - Triggered when network abnormality occurs
            log.warning("Failed to get latest version of vibego: %s", exc)
            return None
        info = payload.get("info") if isinstance(payload, dict) else None
        version = info.get("version") if isinstance(info, dict) else None
        if isinstance(version, str) and version.strip():
            return version.strip()
        return None

    return await asyncio.to_thread(_request)


def _is_newer_version(latest: str, current: str) -> bool:
    """Compare version numbers and use packaging parsing first. """

    if not latest or latest == current:
        return False
    if Version is not None:
        try:
            return Version(latest) > Version(current)
        except InvalidVersion:
            pass
    # Fallback strategy: segment comparison by semantic version
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
    """Refresh the update status on demand, and trigger a network request every 24 hours by default. """

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
            # Reset the notification record when a new version appears to avoid missing reminders
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
    """Send a reminder if a new version is detected and the current chat has not been notified. """

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
        f"The latest vibego version v{latest} has been detected and the current running version is v{__version__}. \n"
        "Send /upgrade to automatically perform the upgrade and restart the service. "
    )
    try:
        await bot.send_message(chat_id=chat_id, text=message)
    except Exception as exc:
        log.warning("Failed to send upgrade reminder (chat=%s): %s", chat_id, exc)
        return False

    notified_ids.append(chat_id)
    current_state["last_notified_version"] = latest
    current_state["last_notified_at"] = _utcnow().isoformat()
    _save_update_state(current_state)
    return True


async def _notify_update_to_targets(bot: Bot, targets: Sequence[int], *, force_check: bool = False) -> None:
    """Push available updates to administrators in bulk. """

    if not targets:
        return
    state = await _ensure_update_state(force=force_check)
    sent = 0
    for chat_id in targets:
        if await _maybe_notify_update(bot, chat_id, state=state):
            sent += 1
    if sent:
        log.info("Upgrade reminder has been pushed to %s administrators", sent)


def _trigger_upgrade_pipeline() -> Tuple[bool, Optional[str]]:
    """Trigger the pipx upgrade process and run it in the background. """

    command = "pipx upgrade vibego && vibego stop && vibego start"
    try:
        subprocess.Popen(
            ["/bin/bash", "-lc", command],
            cwd=str(ROOT_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log.info("Upgrade command triggered: %s", command)
        return True, None
    except Exception as exc:
        log.error("Failed to trigger upgrade command: %s", exc)
        return False, str(exc)


async def _periodic_update_check(bot: Bot) -> None:
    """The background periodically checks for version updates and notifies the administrator. """

    await asyncio.sleep(10)
    while True:
        try:
            await _notify_update_to_targets(bot, _collect_admin_targets(), force_check=True)
        except Exception as exc:  # pragma: no cover - Use for downtime debugging
            log.error("Automatic version detection failed: %s", exc)
        await asyncio.sleep(int(UPDATE_CHECK_INTERVAL.total_seconds()))


def _detect_proxy() -> Tuple[Optional[str], Optional[BasicAuth], Optional[str]]:
    """Resolve available proxy configurations from environment variables. """

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
    log.info("Use proxy(%s): %s", source, _mask_proxy(proxy_raw))
    return proxy_raw, auth, source


def _sanitize_slug(text: str) -> str:
    """Convert an arbitrary string into a short tag usable by project_slug. """

    slug=text.lower().replace(" ", "-")
    slug = slug.replace("/", "-").replace("\\", "-")
    slug = slug.strip("-")
    return slug or "project"


@dataclass
class ProjectConfig:
    """Describes the static configuration of a single project. """

    bot_name: str
    bot_token: str
    project_slug: str
    default_model: str = "codex"
    workdir: Optional[str] = None
    allowed_chat_id: Optional[int] = None
    legacy_name: Optional[str] = None

    def __post_init__(self) -> None:
        """Make sure the bot name is legal and remove redundant prefixes and spaces. """

        clean_name = self.bot_name.strip()
        if clean_name.startswith("@"):  # Allow direct writing with @ in the configuration
            clean_name = clean_name[1:]
        clean_name = clean_name.strip()
        if not clean_name:
            raise ValueError("bot_name cannot be empty")
        self.bot_name = clean_name

    @property
    def display_name(self) -> str:
        """Returns the bot name used for display. """

        return self.bot_name

    @property
    def jump_url(self) -> str:
        """Generate a link to Telegram Bot. """

        return f"https://t.me/{self.bot_name}"

    @classmethod
    def from_dict(cls, data: dict) -> "ProjectConfig":
        """Constructs a ProjectConfig instance from a JSON dictionary. """

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
    """Represents the current running status of the project, which is persisted by StateStore. """

    model:str
    status: str = "stopped"
    chat_id: Optional[int] = None


class StateStore:
    """Responsible for maintaining file persistence of project running status. """

    def __init__(self, path: Path, configs: Dict[str, ProjectConfig]):
        """Initialize the state store, loading existing state files and using default values ​​for missing items. """

        self.path = path
        self.configs = configs  # key Use project_slug
        self.data: Dict[str, ProjectState] = {}
        self.refresh()
        self.save()

    def reset_configs(
        self,
        configs: Dict[str, ProjectConfig],
        preserve: Optional[Dict[str, ProjectState]] = None,
    ) -> None:
        """Update the configuration mapping, write the default state when adding a new item, and remove the record when deleting an item. """
        self.configs = configs
        # Remove deleted item status
        for slug in list(self.data.keys()):
            if slug not in configs:
                del self.data[slug]
        # Supplement the default status for new projects
        for slug, cfg in configs.items():
            if slug not in self.data:
                self.data[slug] = ProjectState(
                    model=cfg.default_model,
                    status="stopped",
                    chat_id=cfg.allowed_chat_id,
                )
        if preserve:
            self.data.update(preserve)
        self.save()

    def refresh(self) -> None:
        """Reload all project states from state files. """

        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                log.warning("Unable to parse state file %s, using empty state", self.path)
                raw = {}
        else:
            raw = {}
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
            self.data[slug] = ProjectState(model=model, status=status, chat_id=chat_id_value)

    def save(self) -> None:
        """Write the current memory state to a disk file. """

        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            slug: {
                "model": state.model,
                "status": state.status,
                "chat_id": state.chat_id,
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
    ) -> None:
        """Updates the state of the specified project and makes it persist immediately. """

        state = self.data[slug]
        if model is not None:
            state.model = model
        if status is not None:
            state.status = status
        if chat_id is not None:
            state.chat_id = chat_id
        self.save()


class MasterManager:
    """Encapsulates core logic such as project configuration, state persistence, and pre-checking. """

    def __init__(self, configs: List[ProjectConfig], *, state_store: StateStore):
        """Build the manager and build the slug/mention index based on the configuration. """

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
        """Search the configuration based on the project name or @bot name, and an error will be reported if it cannot be found. """

        cfg = self._resolve_project(name)
        if not cfg:
            raise ValueError(f"Unknown item {name}")
        return cfg

    def require_project_by_slug(self, slug: str) -> ProjectConfig:
        """Find configuration based on project_slug. """

        cfg = self._slug_index.get(slug)
        if not cfg:
            raise ValueError(f"Unknown item {slug}")
        return cfg

    def _resolve_project(self, identifier: str) -> Optional[ProjectConfig]:
        """Look for matching project configurations in the slug/mention index. """

        if not identifier:
            return None
        raw = identifier.strip()
        if not raw:
            return None
        if raw in self._slug_index:
            return self._slug_index[raw]
        if raw in self._mention_index:
            return self._mention_index[raw]
        if raw.startswith("@"):  # Allow users to enter @bot_name directly
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
        """Refresh the project configuration index so that it takes effect immediately after addition/deletion. """
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
        """Reload project running status from disk. """

        self.state_store.refresh()

    def list_states(self) -> Dict[str, ProjectState]:
        """Returns a dictionary of statuses for all current projects. """

        return self.state_store.data

    def is_authorized(self, chat_id: int) -> bool:
        """Checks whether the given chat_id is in the list of administrators. """

        return not self.admin_ids or chat_id in self.admin_ids

    @staticmethod
    def _format_issue_message(title: str, issues: Sequence[str]) -> str:
        """Assemble Markdown text according to the results of the project self-test. """

        lines: List[str] = []
        for issue in issues:
            if "\n" in issue:
                first, *rest = issue.splitlines()
                lines.append(f"- {first}")
                lines.extend(f"  {line}" for line in rest)
            else:
                lines.append(f"- {issue}")
        joined = "\n".join(lines) if lines else "- None"
        return f"{title}\n{joined}"

    def _collect_prerequisite_issues(self, cfg: ProjectConfig, model: str) -> List[str]:
        """Check the dependency conditions before starting the model and return all unsatisfied items. """

        issues: List[str] = []
        workdir_raw = (cfg.workdir or "").strip()
        if not workdir_raw:
            issues.append(
                "The workdir is not configured, please set the working directory for the project through the project management function"
            )
            expanded_dir = None
        else:
            expanded = Path(os.path.expandvars(os.path.expanduser(workdir_raw)))
            if not expanded.exists():
                issues.append(f"Working directory does not exist: {workdir_raw}")
                expanded_dir = None
            elif not expanded.is_dir():
                issues.append(f"Working directory is not a folder: {workdir_raw}")
                expanded_dir = None
            else:
                expanded_dir = expanded

        if not cfg.bot_token:
            issues.append("bot_token Not configured, please supplement this field through the project management function")

        if shutil.which("tmux") is None:
            issues.append("tmux not detected, installable via 'brew install tmux'")

        model_lower = (model or "").lower()
        model_cmd = os.environ.get("MODEL_CMD")
        if not model_cmd:
            if model_lower == "codex":
                model_cmd = os.environ.get("CODEX_CMD") or "codex"
            elif model_lower == "claudecode":
                model_cmd = os.environ.get("CLAUDE_CMD") or "claude"
            elif model_lower == "gemini":
                model_cmd = os.environ.get("GEMINI_CMD") or ""

        if model_cmd:
            try:
                executable = shlex.split(model_cmd)[0]
            except ValueError:
                executable = None
            if executable and shutil.which(executable) is None:
                issues.append(f"Model command {executable} not detected, please confirm it is installed")
        elif model_lower != "gemini":
            issues.append("Model command configuration not found, unable to start worker")

        if expanded_dir is None and workdir_raw:
            log.debug(
                "Working directory verification failed",
                extra={"project": cfg.project_slug, "workdir": workdir_raw},
            )

        return issues

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        """Detects whether the process with the specified PID is still running. """

        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        else:
            return True

    def _log_tail(self, path: Path, *, lines: int = WORKER_HEALTH_LOG_TAIL) -> str:
        """Read the tail of the log file to help diagnose the cause of startup failure. """

        if not path.exists():
            return ""
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as fh:
                data = fh.readlines()
        except Exception as exc:
            log.warning(
                "Failed to read log: %s",
                exc,
                extra={"log_path": str(path)},
            )
            return ""
        if not data:
            return ""
        tail = data[-lines:]
        return "".join(tail).rstrip()

    def _log_contains_handshake(self, path: Path) -> bool:
        """Determine whether the log contains a successful Telegram handshake mark. """

        if not path.exists():
            return False
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception as exc:
            log.warning(
                "Failed to read log: %s",
                exc,
                extra={"log_path": str(path)},
            )
            return False
        return any(marker in text for marker in HANDSHAKE_MARKERS)

    async def _health_check_worker(self, cfg: ProjectConfig, model: str) -> Optional[str]:
        """Verify the health status of the worker after it is started and return a failure description. """

        log_dir = LOG_ROOT_PATH/model/cfg.project_slug
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
                        "pid The content of file %s is abnormal",
                        str(pid_path),
                        extra={"content": pid_path.read_text(encoding="utf-8", errors="ignore")},
                    )
                    last_seen_pid = None
                except Exception as exc:
                    log.warning(
                        "Failed to read pid file: %s",
                        exc,
                        extra={"pid_path": str(pid_path)},
                    )

            if self._log_contains_handshake(run_log):
                return None

            await asyncio.sleep(WORKER_HEALTH_INTERVAL)

        issues: List[str] = []
        if last_seen_pid is None:
            issues.append("Bot not detected.pid Or the content is empty")
        else:
            if self._pid_alive(last_seen_pid):
                issues.append(
                    f"worker Process {last_seen_pid} is not in {WORKER_HEALTH_TIMEOUT:.1f}s Complete Telegram handshake"
                )
            else:
                issues.append(f"worker Process {last_seen_pid} has exited")

        log_tail = self._log_tail(run_log)
        if log_tail:
            issues.append(
                "Recent logs:\n" + textwrap.indent(log_tail, prefix="  ")
            )

        if not issues:
            return None

        return self._format_issue_message(
            f"{cfg.display_name} Startup failed",
            issues,
        )

    async def run_worker(self, cfg: ProjectConfig, model: Optional[str] = None) -> str:
        """Starts a worker for the specified project and returns the running model name. """

        self.refresh_state()
        state = self.state_store.data[cfg.project_slug]
        target_model = model or state.model or cfg.default_model
        issues = self._collect_prerequisite_issues(cfg, target_model)
        if issues:
            message = self._format_issue_message(
                f"{cfg.display_name} Startup failed, missing necessary dependencies or configuration",
                issues,
            )
            log.error(
                "Pre-start self-test failed: %s",
                message,
                extra={"project": cfg.project_slug, "model": target_model},
            )
            raise RuntimeError(message)
        chat_id_env = state.chat_id or cfg.allowed_chat_id
        env = os.environ.copy()
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
            }
        )
        cmd = [str(RUN_SCRIPT), "--model", target_model, "--project", cfg.project_slug]
        log.info(
            "Start worker: %s (model=%s, chat_id=%s)",
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
            issues = [f"run_bot.sh Exit code {rc}"]
            if tail_lines:
                issues.append("Script output:\n " + "\n ".join(tail_lines.splitlines()))
            message = self._format_issue_message(
                f"{cfg.display_name} Startup failed",
                issues,
            )
            log.error(
                "worker Startup failed: %s",
                message,
                extra={"project": cfg.project_slug, "model": target_model},
            )
            raise RuntimeError(message)
        health_issue = await self._health_check_worker(cfg, target_model)
        if health_issue:
            self.state_store.update(cfg.project_slug, status="stopped")
            log.error(
                "worker Health check failed: %s",
                health_issue,
                extra={"project": cfg.project_slug, "model": target_model},
            )
            raise RuntimeError(health_issue)

        self.state_store.update(cfg.project_slug, model=target_model, status="running")
        return target_model

    async def stop_worker(self, cfg: ProjectConfig, *, update_state: bool = True) -> None:
        """Stops the worker for the specified project and refreshes the status if necessary. """

        self.refresh_state()
        state = self.state_store.data[cfg.project_slug]
        model = state.model
        cmd = [str(STOP_SCRIPT), "--model", model, "--project", cfg.project_slug]
        proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(ROOT_DIR))
        await proc.wait()
        if update_state:
            self.state_store.update(cfg.project_slug, status="stopped")
        log.info("Stopped worker: %s", cfg.display_name, extra={"project": cfg.project_slug})

    async def stop_all(self, *, update_state: bool = False) -> None:
        """Stop workers for all projects in sequence. """

        for cfg in self.configs:
            try:
                await self.stop_worker(cfg, update_state=update_state)
            except Exception as exc:
                log.warning(
                    "Error stopping %s: %s",
                    cfg.display_name,
                    exc,
                    extra={"project": cfg.project_slug},
                )

    async def run_all(self) -> None:
        """Start all project workers that are not already running. """

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
                    "Error starting %s: %s",
                    cfg.display_name,
                    exc,
                    extra={"project": cfg.project_slug},
                )
                errors.append(f"{cfg.display_name}: {exc}")
        if errors:
            raise RuntimeError(
                self._format_issue_message("Some projects failed to start", errors)
            )

    async def restore_running(self) -> None:
        """Resume the workers that were still running in the previous round according to the state file. """

        self.refresh_state()
        for slug, state in self.state_store.data.items():
            if state.status == "running":
                cfg = self._slug_index.get(slug)
                if not cfg:
                    log.warning("Status file contains unknown item: %s", slug)
                    continue
                try:
                    await self.run_worker(cfg, model=state.model)
                except Exception as exc:
                    log.error(
                        "Restore %s failed: %s",
                        cfg.display_name,
                        exc,
                        extra={"project": cfg.project_slug, "model": state.model},
                    )
                    self.state_store.update(slug, status="stopped")

    def update_chat_id(self, slug: str, chat_id: int) -> None:
        """Record or update the chat_id binding information of the project. """

        cfg = self._resolve_project(slug)
        if not cfg:
            raise ValueError(f"Unknown item {slug}")
        self.state_store.update(cfg.project_slug, chat_id=chat_id)
        log.info(
            "Recorded chat_id=%s for %s",
            chat_id,
            cfg.display_name,
            extra={"project": cfg.project_slug},
        )


MANAGER: Optional[MasterManager] = None
PROJECT_REPOSITORY: Optional[ProjectRepository] = None
ProjectField = Literal["bot_name", "bot_token", "project_slug", "default_model", "workdir", "allowed_chat_id"]


@dataclass
class ProjectWizardSession:
    """Record project management conversation status for a single chat. """

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
PROJECT_MODEL_CHOICES: Tuple[str, ...] = ("codex", "claudecode", "gemini")
PROJECT_WIZARD_SESSIONS: Dict[int, ProjectWizardSession] = {}
PROJECT_WIZARD_LOCK = asyncio.Lock()
PROJECT_FIELD_PROMPTS_CREATE: Dict[ProjectField, str] = {
    "bot_name": "Please enter a bot name (without @, only letters, numbers, underscores or dots):",
    "bot_token": "Please enter Telegram Bot Token (format similar to 123456:ABCdef):",
    "project_slug": "Please enter the project slug (for the log directory, leave it blank to automatically generate based on the bot name): ",
    "default_model": "Please enter the default model (codex/claudecode/gemini, leave it blank to use codex):",
    "workdir": "Please enter the absolute path of the worker's working directory (you can leave it blank and complete it later): ",
    "allowed_chat_id": "Please enter the default chat_id (can be left blank, multiple are not supported at the moment): ",
}
PROJECT_FIELD_PROMPTS_EDIT: Dict[ProjectField, str] = {
    "bot_name": "Please enter a new bot name (without @, send - keep current value: {current}):",
    "bot_token": "Please enter new Bot Token (send - keep current value):",
    "project_slug": "Please enter new item slug (send - keep current value: {current}):",
    "default_model": "Please enter new default model (codex/claudecode/gemini, send - keep current value: {current}):",
    "workdir": "Please enter a new working directory (send - keep current value: {current}, can be left blank to not set): ",
    "allowed_chat_id": "Please enter a new chat_id (Send - keep current value: {current}, leave blank to cancel default):",
}


def _ensure_repository() -> ProjectRepository:
    """Get the project warehouse instance and throw an exception if it is not initialized. """
    if PROJECT_REPOSITORY is None:
        raise RuntimeError("Project warehouse is not initialized")
    return PROJECT_REPOSITORY


def _reload_manager_configs(
    manager: MasterManager,
    *,
    preserve: Optional[Dict[str, ProjectState]] = None,
) -> List[ProjectConfig]:
    """Reloads the project configuration and optionally preserves the specified state mapping. """
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
    """Verify field input and return the converted value and error message. """
    text = raw_text.strip()
    repository = _ensure_repository()
    # The editing process allows the use of "-" to maintain the original value
    if session.mode == "edit" and text == "-":
        return session.data.get(field_name), None

    if field_name in PROJECT_WIZARD_OPTIONAL_FIELDS and not text:
        return None, None

    if field_name == "bot_name":
        candidate = text.lstrip("@").strip()
        if not candidate:
            return None, "bot name cannot be empty"
        if not re.fullmatch(r"[A-Za-z0-9_.]{5,64}", candidate):
            return None, "bot Names only allow 5-64 letters, numbers, underscores or dots"
        existing = repository.get_by_bot_name(candidate)
        if existing and (session.mode == "create" or existing.project_slug != session.original_slug):
            return None, "The bot name is already occupied by another project"
        return candidate, None

    if field_name == "bot_token":
        if not re.fullmatch(r"\d+:[A-Za-z0-9_-]{20,128}", text):
            return None, "Bot Token The format is incorrect, please confirm your input"
        return text, None

    if field_name == "project_slug":
        candidate = _sanitize_slug(text or session.data.get("bot_name", ""))
        if not candidate:
            return None, "Unable to generate a valid slug, please re-enter"
        existing = repository.get_by_slug(candidate)
        if existing and (session.mode == "create" or existing.project_slug != session.original_slug):
            return None, "The slug already exists, please change it to another name"
        return candidate, None

    if field_name == "default_model":
        candidate = text.lower() if text else "codex"
        if candidate not in PROJECT_MODEL_CHOICES:
            return None, f"The default model only supports {', '.join(PROJECT_MODEL_CHOICES)}"
        return candidate, None

    if field_name == "workdir":
        expanded = os.path.expandvars(os.path.expanduser(text))
        path = Path(expanded)
        if not path.exists() or not path.is_dir():
            return None, f"Directory does not exist or is unavailable: {text}"
        return str(path), None

    if field_name == "allowed_chat_id":
        if not re.fullmatch(r"-?\d+", text):
            return None, "chat_id It needs to be an integer and can be left blank to skip"
        return int(text), None

    return text, None


def _format_field_prompt(
    session: ProjectWizardSession, field_name: ProjectField
) -> Tuple[str, Optional[InlineKeyboardMarkup]]:
    """Generate field prompts and optional operation keyboard according to the process. """

    if session.mode == "edit":
        current_value = session.data.get(field_name)
        if current_value is None:
            display = "Not set"
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
            text="Skip this",
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
    """Sends the user a prompt and optional skip button for the current field. """

    prompt, markup = _format_field_prompt(session, field_name)
    if prefix:
        text = f"{prefix}\n{prompt}"
    else:
        text = prompt
    await target_message.answer(text, reply_markup=markup)


def _session_to_record(session: ProjectWizardSession) -> ProjectRecord:
    """Convert session data to ProjectRecord, preserving legacy_name when editing. """
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
    """Commits session data and performs warehouse writes. """
    repository = _ensure_repository()
    record = _session_to_record(session)
    try:
        if session.mode == "create":
            repository.insert_project(record)
            _reload_manager_configs(manager)
            summary_prefix = "Added project successfully ✅"
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
            summary_prefix = "Project has been updated ✅"
        else:
            return False
    except Exception as exc:
        log.error("Project write failed: %s", exc, extra={"mode": session.mode})
        await message.answer(f"Save failed: {exc}")
        return False

    workdir_desc=record.workdir or "Not set"
    chat_desc=record.allowed_chat_id if record.allowed_chat_id is not None else "Not set"
    summary = (
        f"{summary_prefix}\n"
        f"bot:@{record.bot_name}\n"
        f"slug:{record.project_slug}\n"
        f"Model: {record.default_model}\n"
        f"Working directory: {workdir_desc}\n"
        f"chat_id:{chat_desc}"
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
    prefix: str = "Recorded ✅",
) -> bool:
    """Advance the project management process, validate inputs and trigger subsequent steps. """

    if session.step_index >= len(session.fields):
        await message.answer("The process has been completed. If you need to modify it again, please start again. ")
        return True

    if not session.fields:
        await message.answer("The process configuration is abnormal, please start again. ")
        async with PROJECT_WIZARD_LOCK:
            PROJECT_WIZARD_SESSIONS.pop(message.chat.id, None)
        return True

    field_name = session.fields[session.step_index]
    value, error = _validate_field_value(session, field_name, text)
    if error:
        await message.answer(f"{error}\nPlease re-enter:")
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

    # All fields are filled in, perform writing
    success = await _commit_wizard_session(session, manager, message)
    async with PROJECT_WIZARD_LOCK:
        PROJECT_WIZARD_SESSIONS.pop(message.chat.id, None)

    if success:
        await message.answer("The project management process is complete. ")
    return True


async def _start_project_create(callback: CallbackQuery, manager: MasterManager) -> None:
    """Start the new project process. """
    if callback.message is None or callback.from_user is None:
        return
    chat_id = callback.message.chat.id
    user_id = callback.from_user.id
    async with PROJECT_WIZARD_LOCK:
        if chat_id in PROJECT_WIZARD_SESSIONS:
            await callback.answer(
                'There is already a project wizard in progress for this chat. Complete it first or send "Cancel".',
                show_alert=True,
            )
            return
        session = ProjectWizardSession(
            chat_id=chat_id,
            user_id=user_id,
            mode="create",
            fields=PROJECT_WIZARD_FIELDS_CREATE,
        )
        PROJECT_WIZARD_SESSIONS[chat_id] = session
    await callback.answer("Started the new-project wizard.")
    await callback.message.answer(
        'The new-project wizard is now active. Send "Cancel" at any time to abort.',
    )
    first_field = session.fields[0]
    await _send_field_prompt(session, first_field, callback.message)


async def _start_project_edit(
    callback: CallbackQuery,
    cfg: ProjectConfig,
    manager: MasterManager,
) -> None:
    """Start the project editing process. """
    if callback.message is None or callback.from_user is None:
        return
    repository = _ensure_repository()
    record = repository.get_by_slug(cfg.project_slug)
    if record is None:
        await callback.answer("Project configuration not found", show_alert=True)
        return
    chat_id = callback.message.chat.id
    user_id = callback.from_user.id
    async with PROJECT_WIZARD_LOCK:
        if chat_id in PROJECT_WIZARD_SESSIONS:
            await callback.answer(
                'There is already a project wizard in progress for this chat. Complete it first or send "Cancel".',
                show_alert=True,
            )
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
    await callback.answer("Started the edit-project wizard.")
    await callback.message.answer(
        f'Entered the editing wizard for {cfg.display_name}. Send "Cancel" at any time to abort.',
    )
    field_name = session.fields[0]
    await _send_field_prompt(session, field_name, callback.message)


def _build_delete_confirmation_keyboard(slug: str) -> InlineKeyboardMarkup:
    """Build a button keyboard for deletion confirmation. """
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="Confirm deletion ✅",
            callback_data=f"project:delete_confirm:{slug}",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="Cancel",
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
    """Initiates the confirmation process for deleting the item. """
    if callback.message is None or callback.from_user is None:
        return
    repository = _ensure_repository()
    original_record = repository.get_by_slug(cfg.project_slug)
    original_slug = original_record.project_slug if original_record else cfg.project_slug
    # Read the running state again before deleting to avoid accidentally overwriting the FSM context.
    project_runtime_state = _get_project_runtime_state(manager, cfg.project_slug)
    if project_runtime_state and project_runtime_state.status == "running":
        await callback.answer("Please stop the worker of this project before deleting it.", show_alert=True)
        return
    current_state = await state.get_state()
    if current_state == ProjectDeleteStates.confirming.state:
        data = await state.get_data()
        existing_slug = str(data.get("project_slug", "")).lower()
        if existing_slug == cfg.project_slug.lower():
            await callback.answer("The current deletion process is being confirmed, please use the buttons to finish the operation.", show_alert=True)
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
    await callback.answer("Deletion confirmation sent")
    await callback.message.answer(
        f"Confirm deletion of project {cfg.display_name}? This action cannot be undone.\n"
        f"Use the buttons below to confirm or cancel within {DELETE_CONFIRM_TIMEOUT} seconds.",
        reply_markup=markup,
    )


async def _handle_wizard_message(
    message: Message,
    manager: MasterManager,
) -> bool:
    """Handle user input in the project management process. """
    if message.chat is None or message.from_user is None:
        return False
    chat_id = message.chat.id
    async with PROJECT_WIZARD_LOCK:
        session = PROJECT_WIZARD_SESSIONS.get(chat_id)
    if session is None:
        return False
    if message.from_user.id != session.user_id:
        await message.answer("Only the process initiator can proceed. ")
        return True
    text = (message.text or "").strip()
    if text.lower() in {"Cancel", "cancel", "/cancel"}:
        async with PROJECT_WIZARD_LOCK:
            PROJECT_WIZARD_SESSIONS.pop(chat_id, None)
        await message.answer("The project management process has been cancelled. ")
        return True

    return await _advance_wizard_session(session, manager, message, text)
router = Router()
log = create_logger("master", level_env="MASTER_LOG_LEVEL", stderr_env="MASTER_STDERR")

# Restart state locks and markers to avoid repeated triggering
_restart_lock: Optional[asyncio.Lock] = None
_restart_in_progress: bool = False


def _ensure_restart_lock() -> asyncio.Lock:
    """Lazily create the restart lock, ensuring it is initialised inside the event loop."""
    global _restart_lock
    if _restart_lock is None:
        _restart_lock = asyncio.Lock()
    return _restart_lock


def _log_update(message: Message, *, override_user: Optional[User] = None) -> None:
    """Log every update and sync recent chat messages in MASTER_ENV_FILE. """

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
    """Safely remove files and support retry mechanism

    Args:
        path: the path of the file to be deleted
        retries: Maximum number of retries (default 3)
    """
    if not path.exists():
        log.debug("The file does not exist, no need to delete it", extra={"path": str(path)})
        return

    for attempt in range(retries):
        try:
            path.unlink()
            log.info("The restart signal file has been deleted", extra={"path": str(path), "attempt": attempt + 1})
            return
        except FileNotFoundError:
            log.debug("The file has been deleted by another process", extra={"path": str(path)})
            return
        except Exception as exc:
            if attempt < retries - 1:
                log.warning(
                    "Failed to delete file, will try again (attempt %d/%d): %s",
                    attempt + 1,
                    retries,
                    exc,
                    extra={"path": str(path)}
                )
                import time
                time.sleep(0.1)  # Wait 100ms and try again
            else:
                log.error(
                    "Failed to delete file, maximum number of retries reached: %s",
                    exc,
                    extra={"path": str(path), "retries": retries}
                )


def _write_restart_signal(message: Message, *, override_user: Optional[User] = None) -> None:
    """Write the restart request information to the signal file for the new master to read after starting """
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
        "Logged restart signal: chat_id=%s user_id=%s file=%s",
        payload["chat_id"],
        payload["user_id"],
        RESTART_SIGNAL_PATH,
        extra={"chat": payload["chat_id"]},
    )


def _read_restart_signal() -> Tuple[Optional[dict], Optional[Path]]:
    """Read and verify restart signals, be compatible with historical paths and handle exceptions/timeouts"""
    candidates: Tuple[Path, ...] = (RESTART_SIGNAL_PATH, *LEGACY_RESTART_SIGNAL_PATHS)
    for path in candidates:
        if not path.exists():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("signal payload Must be an object")
        except Exception as exc:
            log.error("Failed to read restart signal: %s", exc, extra={"path": str(path)})
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
                        "Restart signal timed out, ignored",
                        extra={
                            "path": str(path),
                            "age_seconds": age_seconds,
                            "ttl": RESTART_SIGNAL_TTL,
                        },
                    )
                    _safe_remove(path)
                    continue
            except Exception as exc:
                log.warning("Failed to parse restart signal timestamp: %s", exc, extra={"path": str(path)})

        if path != RESTART_SIGNAL_PATH:
            log.info(
                "Read restart signal from compatible path",
                extra={"path": str(path), "primary": str(RESTART_SIGNAL_PATH)},
            )
        return raw, path

    return None, None


async def _notify_restart_success(bot: Bot) -> None:
    """Read the signal and notify the triggerer when the new master starts (improved version: supports timeout detection and detailed diagnosis)"""
    restart_expected=os.environ.pop("MASTER_RESTART_EXPECTED", None)
    payload, signal_path = _read_restart_signal()

    # Define restart health check thresholds (2 minutes)
    RESTART_HEALTHY_THRESHOLD = 120 # seconds
    RESTART_WARNING_THRESHOLD = 60 # Warn after more than 1 minute

    if not payload:
        if restart_expected:
            targets = _collect_admin_targets()
            log.warning(
                "If the restart signal file is not detected during startup, a cryptic reminder will be sent to the administrator.", extra={"targets": targets}
            )
            if targets:
                # Check the startup log for error messages
                error_log_dir = LOG_ROOT_PATH
                error_log_hint = ""
                try:
                    error_logs = sorted(error_log_dir.glob("master_error_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
                    if error_logs:
                        latest_error_log = error_logs[0]
                        if latest_error_log.stat().st_size > 0:
                            error_log_hint = f"\n⚠️ Error log found: {latest_error_log}"
                except Exception:
                    pass

                text_lines = [
                    "⚠️ Master It's back online, but no information about the restart trigger was found. ",
                    "",
                    "Possible reasons: ",
                    "1. Restart signal file writing failed",
                    "2. The signal file has timed out and been cleared (TTL=30 minutes)",
                    "3. File system permission issues",
                    "4. start.sh Cleaned up after failed startup",
                    "",
                    "Recommended to check: ",
                    f"- Startup log: {LOG_ROOT_PATH / 'start.log'}",
                    f"- Running log: {LOG_ROOT_PATH / 'vibe.log'}",
                    f"- Signal file: {RESTART_SIGNAL_PATH}",
                ]
                if error_log_hint:
                    text_lines.append(error_log_hint)

                text = "\n".join(text_lines)
                for chat in targets:
                    try:
                        await bot.send_message(chat_id=chat, text=text)
                        log.info("The complete restart notification has been sent", extra={"chat": chat})
                    except Exception as exc:
                        log.error("Failed to send full restart notification: %s", exc, extra={"chat": chat})
        else:
            log.info("The restart signal file was not detected during startup. It may be a normal startup. ")
        return

    chat_id_raw = payload.get("chat_id")
    try:
        chat_id = int(chat_id_raw)
    except (TypeError, ValueError):
        log.error("Restart signal chat_id is illegal: %s", chat_id_raw)
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

    # Calculate restart time
    if timestamp:
        try:
            ts = datetime.fromisoformat(timestamp)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=LOCAL_TZ)
            ts_local = ts.astimezone(LOCAL_TZ)
            timestamp_fmt = ts_local.strftime("%Y-%m-%d %H:%M:%S %Z")

            # Calculate restart time (seconds)
            now = datetime.now(LOCAL_TZ)
            restart_duration = int((now - ts_local).total_seconds())
        except Exception as exc:
            log.warning("Failed to parse restart time: %s", exc)

    details = []
    if username:
        details.append(f"Trigger: @{username}")
    elif user_id:
        details.append(f"Trigger ID: {user_id}")
    if timestamp_fmt:
        details.append(f"Request time:{timestamp_fmt}")

    # Add restart time-consuming information and health status
    message_lines = []
    if restart_duration is not None:
        if restart_duration <= RESTART_WARNING_THRESHOLD:
            message_lines.append(f"master Back online ✅(It took {restart_duration} seconds)")
        elif restart_duration <= RESTART_HEALTHY_THRESHOLD:
            message_lines.append(f"⚠️ master Back online (took {restart_duration} seconds, slightly slower)")
            details.append("💡 Suggestion: Check if dependency installation triggers a re-download")
        else:
            message_lines.append(f"⚠️ master Back online (took {restart_duration} seconds, unusually slow)")
            details.append("⚠️ Restarting takes too long, please check: ")
            details.append("  - Is the network connection normal?")
            details.append("  - Whether the dependency installation is stuck")
            details.append(f"  - Startup log: {LOG_ROOT_PATH / 'start.log'}")
    else:
        message_lines.append("master Back online ✅")

    if details:
        message_lines.extend(details)

    text = "\n".join(message_lines)

    try:
        await bot.send_message(chat_id=chat_id, text=text)
    except Exception as exc:
        log.error("Failed to send restart successful notification: %s", exc, extra={"chat": chat_id})
    else:
        # Restart succeeded; notify admins and push the refreshed project overview for quick status check.
        log.info("Restart successful notification has been sent", extra={"chat": chat_id, "duration": restart_duration})
        try:
            manager = await _ensure_manager()
        except RuntimeError as exc:  # pragma: no cover - defensive guard if startup order changes
            log.warning("Manager unavailable when trying to push project overview: %s", exc)
        else:
            try:
                await _send_projects_overview_to_chat(bot, chat_id, manager)
            except Exception as exc:  # pragma: no cover - avoid crashing startup hook
                log.error(
                    "Failed to send project overview after restart notification: %s",
                    exc,
                    extra={"chat": chat_id},
                )
    finally:
        candidates = (signal_path, RESTART_SIGNAL_PATH, *LEGACY_RESTART_SIGNAL_PATHS)
        for candidate in candidates:
            if candidate is None:
                continue
            _safe_remove(candidate)


async def _ensure_manager() -> MasterManager:
    """Make sure MANAGER is initialized, throw an exception if it is not initialized. """

    global MANAGER
    if MANAGER is None:
        raise RuntimeError("Master manager is not initialized")
    return MANAGER


async def _process_restart_request(
    message: message,
    *,
    trigger_user: Optional[User] = None,
    manager: Optional[MasterManager] = None,
) -> None:
    """Respond to /restart requests, write restart signals and trigger scripts. """

    if manager is None:
        manager = await _ensure_manager()
    if not manager.is_authorized(message.chat.id):
        await message.answer("Not authorized.")
        return

    lock = _ensure_restart_lock()
    async with lock:
        # Use global so the restart flag is shared across concurrent handlers
        global _restart_in_progress
        if _restart_in_progress:
            await message.answer("A restart request is already being executed, please try again later. ")
            return
        _restart_in_progress = True

    start_script = ROOT_DIR / "scripts/start.sh"
    if not start_script.exists():
        async with lock:
            _restart_in_progress = False
        await message.answer("not found ./start.sh,Unable to perform restart. ")
        return

    signal_error: Optional[str] = None
    try:
        _write_restart_signal(message, override_user=trigger_user)
    except Exception as exc:
        signal_error = str(exc)
        log.error("Record restart signal exception: %s", exc)

    notice = (
        "The restart command has been received. The master will be temporarily offline during operation. After restarting, all workers need to be started manually later. "
    )
    if signal_error:
        notice += (
            "\n⚠️ The restart signal writing failed and may not be automatically notified after the restart is completed. Reason: "
            f"{signal_error}"
        )

    await message.answer(notice)

    asyncio.create_task(_perform_restart(message, start_script))


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """Handles the /start command and returns project overview and status. """

    _log_update(message)
    manager = await _ensure_manager()
    if not manager.is_authorized(message.chat.id):
        await message.answer("Not authorized.")
        return
    manager.refresh_state()
    await message.answer(
        f"Master bot Started (v{__version__}). \n"
        f"Registered items: {len(manager.configs)} indivual. \n"
        "Use /projects to view status, /run or /stop to control workers. ",
        reply_markup=_build_master_main_keyboard(),
    )
    await _send_projects_overview_to_chat(
        message.bot,
        message.chat.id,
        manager,
        reply_to_message_id=message.message_id,
    )


async def _perform_restart(message: Message, start_script: Path) -> None:
    """Asynchronous execution ./start.sh,If it fails, roll back the mark and notify the administrator """
    # Use global so restart flag resets affect the module-level state
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
            text="Start restarting, the current master will exit and restart, please wait. ",
        )
    except Exception as notice_exc:
        notice_error = notice_exc
        log.warning("Failed to send startup notification: %s", notice_exc)
    try:
        # Use DEVNULL to avoid inheriting the current stdout/stderr and prevent the parent process from exiting and causing start.sh BrokenPipe is triggered when writing to the pipe.
        proc=subprocess.Popen(
            ["/bin/bash", str(start_script)],
            cwd=str(ROOT_DIR),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log.info("start triggered.sh Restart, pid=%s", proc.pid if proc else "-")
    except Exception as exc:
        log.error("implement ./start.sh Failure: %s", exc)
        async with lock:
            _restart_in_progress = False
        try:
            await bot.send_message(chat_id=chat_id, text=f"implement ./start.sh Failure: {exc}")
        except Exception as send_exc:
            log.error("Error sending restart failure notification: %s", send_exc)
        return
    else:
        if notice_error:
            log.warning("The startup notification was not delivered and execution of start has continued..sh")
        async with lock:
            _restart_in_progress = False
            log.debug("Restart execution, status flag has been reset in advance")


@router.message(Command("restart"))
async def cmd_restart(message: Message) -> None:
    """Process the /restart command to trigger a master restart. """

    _log_update(message)
    await _process_restart_request(message)


async def _send_projects_overview_to_chat(
    bot: Bot,
    chat_id: int,
    manager: MasterManager,
    reply_to_message_id: Optional[int] = None,
) -> None:
    """Send project overview and action buttons to the specified chat. """

    await _maybe_notify_update(bot, chat_id)
    manager.refresh_state()
    try:
        text, markup = _projects_overview(manager)
    except Exception as exc:
        log.exception("Failed to generate project overview: %s", exc)
        await bot.send_message(
            chat_id=chat_id,
            text="Project list generation failed, please try again later. ",
            reply_to_message_id=reply_to_message_id,
        )
        return
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=markup,
            reply_to_message_id=reply_to_message_id,
        )
    except TelegramBadRequest as exc:
        log.error("Failed to send project overview: %s", exc)
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_to_message_id=reply_to_message_id,
        )
    except Exception as exc:
        log.exception("Sending project overview triggers exception: %s", exc)
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_to_message_id=reply_to_message_id,
        )
    else:
        log.info("Project overview sent, button=%s", "None" if markup is None else "yes")


async def _refresh_project_overview(
    message: Optional[Message],
    manager: MasterManager,
) -> None:
    """Refresh the project overview on the original message and send a new message if editing is not possible. """

    if message is None:
        return
    manager.refresh_state()
    try:
        text, markup = _projects_overview(manager)
    except Exception as exc:
        log.exception("Failed to refresh project overview: %s", exc)
        return
    try:
        await message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as exc:
        log.warning("Failed to edit project overview, new message will be sent: %s", exc)
        try:
            await message.answer(text, reply_markup=markup)
        except Exception as send_exc:
            log.exception("Failed to send project overview: %s", send_exc)


@router.message(Command("projects"))
async def cmd_projects(message: Message) -> None:
    """Processes the /projects command, returning an overview of the latest projects. """

    _log_update(message)
    manager = await _ensure_manager()
    if not manager.is_authorized(message.chat.id):
        await message.answer("Not authorized.")
        return
    await _send_projects_overview_to_chat(
        message.bot,
        message.chat.id,
        manager,
        reply_to_message_id=message.message_id,
    )


@router.message(Command("upgrade"))
async def cmd_upgrade(message: Message) -> None:
    """Handle /upgrade command, trigger pipx upgrade and restart service. """

    _log_update(message)
    manager = await _ensure_manager()
    if not manager.is_authorized(message.chat.id):
        await message.answer("Not authorized.")
        return

    success, error = _trigger_upgrade_pipeline()
    if success:
        notice = (
            "Triggered `pipx upgrade vibego && vibego stop && vibego start`.\n"
            "The master will restart briefly during the upgrade process, please use /start to verify the status later. "
        )
        await message.answer(notice, parse_mode="Markdown")
    else:
        await message.answer(f"Failed to trigger upgrade command: {error}")


async def _run_and_reply(message: Message, action: str, coro) -> None:
    """Perform asynchronous operations and uniformly reply with success or failure prompts. """

    try:
        result = await coro
    except Exception as exc:
        log.error("%s Failure: %s", action, exc)
        await message.answer(f"{action} Failure: {exc}")
    else:
        reply_text: str
        reply_markup: Optional[InlineKeyboardMarkup] = None
        if isinstance(result, tuple):
            reply_text = result[0]
            if len(result) > 1:
                reply_markup = result[1]
        else:
            reply_text = result if isinstance(result, str) else f"{action} Complete"
        await message.answer(reply_text, reply_markup=_ensure_numbered_markup(reply_markup))


@router.callback_query(F.data.startswith("project:"))
async def on_project_action(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle callback buttons related to project management. """

    manager = await _ensure_manager()
    user_id = callback.from_user.id if callback.from_user else None
    if user_id is None or not manager.is_authorized(user_id):
        await callback.answer("Not authorized.", show_alert=True)
        return
    data = callback.data or ""
    # Skip deletion confirmation/cancellation and let the dedicated processor take over to avoid misjudgment of unknown operations.
    if data.startswith("project:delete_confirm:") or data == "project:delete_cancel":
        raise SkipHandler()
    parts = data.split(":")
    if len(parts) < 3:
        await callback.answer("Invalid operation", show_alert=True)
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

    if action == "refresh":
        # Refreshing the list is a global operation and does not depend on specific project slugs.
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
        await callback.answer("Unknown project", show_alert=True)
        return

    # Key: Avoid overwriting the FSMContext passed in by aiogram, so the running state is saved separately in project_runtime_state
    project_runtime_state = _get_project_runtime_state(manager, cfg.project_slug) if cfg else None
    model_name_map = dict(SWITCHABLE_MODELS)

    if cfg:
        log.info(
            "Button operation request: user=%s action=%s project=%s",
            user_id,
            action,
            cfg.display_name,
            extra={"project": cfg.project_slug},
        )
    else:
        log.info("Button action request: user=%s action=%s all items", user_id, action)

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
                text="⬅️ Cancel",
                callback_data="project:refresh:*",
            )
        )
        await callback.answer()
        await callback.message.answer(
            "Please select a global model:",
            reply_markup=_ensure_numbered_markup(builder.as_markup()),
        )
        return

    if action == "manage":
        if cfg is None or callback.message is None:
            await callback.answer("Unknown project", show_alert=True)
            return
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(
                text="📝 edit",
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
                text=f"🧠 Switch model (current model {current_model_label})",
                callback_data=f"project:switch_prompt:{cfg.project_slug}",
            )
        )
        builder.row(
            InlineKeyboardButton(
                text="🗑 delete",
                callback_data=f"project:delete:{cfg.project_slug}",
            )
        )
        builder.row(
            InlineKeyboardButton(
                text="⬅️ Return to project list",
                callback_data="project:refresh:*",
            )
        )
        markup = builder.as_markup()
        _ensure_numbered_markup(markup)
        await callback.answer()
        await callback.message.answer(
            f"Project {cfg.display_name} management options:",
            reply_markup=markup,
        )
        return

    if action == "switch_prompt":
        if cfg is None or callback.message is None:
            await callback.answer("Unknown project", show_alert=True)
            return
        current_model = (
            project_runtime_state.model if project_runtime_state else cfg.default_model
        ).lower()
        builder = InlineKeyboardBuilder()
        for value, label in SWITCHABLE_MODELS:
            prefix = "[active] " if current_model == value else ""
        builder.row(
            InlineKeyboardButton(
                text=f"{prefix}{label}",
                callback_data=f"project:switch_to:{value}:{cfg.project_slug}",
            )
        )
        builder.row(
            InlineKeyboardButton(
                text="⬅️ Return to project list",
                callback_data="project:refresh:*",
            )
        )
        markup = builder.as_markup()
        _ensure_numbered_markup(markup)
        await callback.answer()
        await callback.message.answer(
            f"Select a model for {cfg.display_name}:",
            reply_markup=markup,
        )
        return

    if action == "edit":
        if cfg is None:
            await callback.answer("Unknown project", show_alert=True)
            return
        await _start_project_edit(callback, cfg, manager)
        return

    if action == "delete":
        if cfg is None:
            await callback.answer("Unknown project", show_alert=True)
            return
        await _start_project_delete(callback, cfg, manager, state)
        return

    if action == "create":
        await _start_project_create(callback, manager)
        return

    if action == "restart_master":
        await callback.answer("Restart command received")

    try:
        if action == "stop_all":
            await manager.stop_all(update_state=True)
            log.info("Button operation successful: user=%s Stop all projects", user_id)
        elif action == "start_all":
            # Automatically record the initiator's chat_id for all projects
            if callback.message and callback.message.chat:
                for project_cfg in manager.configs:
                    current_state = manager.state_store.data.get(project_cfg.project_slug)
                    if not current_state or not current_state.chat_id:
                        manager.update_chat_id(project_cfg.project_slug, callback.message.chat.id)
                        log.info(
                            "Automatically record chat_id: project=%s, chat_id=%s",
                            project_cfg.project_slug,
                            callback.message.chat.id,
                            extra={"project": project_cfg.project_slug, "chat_id": callback.message.chat.id},
                        )
            await manager.run_all()
            log.info("Button operation successful: user=%s Start all projects", user_id)
            await callback.answer("All projects have been started and the list is being refreshed...")
        elif action == "restart_master":
            if callback.message is None:
                log.error("Restart button callback is missing message object", extra={"user": user_id})
                return
            _log_update(callback.message, override_user=callback.from_user)
            await _process_restart_request(
                callback.message,
                trigger_user=callback.from_user,
                manager=manager,
            )
            log.info("Button operation successful: user=%s Restart master", user_id)
            return  # Do not refresh the project list after restarting to avoid additional noise
        elif action == "run":
            # Automatically record the chat_id of the initiator
            if callback.message and callback.message.chat:
                current_state = manager.state_store.data.get(cfg.project_slug)
                if not current_state or not current_state.chat_id:
                    manager.update_chat_id(cfg.project_slug, callback.message.chat.id)
                    log.info(
                        "Automatically record chat_id: project=%s, chat_id=%s",
                        cfg.project_slug,
                        callback.message.chat.id,
                        extra={"project": cfg.project_slug, "chat_id": callback.message.chat.id},
                    )
            chosen = await manager.run_worker(cfg)
            log.info(
                "Button operation successful: user=%s starts %s (model=%s)",
                user_id,
                cfg.display_name,
                chosen,
                extra={"project": cfg.project_slug, "model": chosen},
            )
            await callback.answer("The project has been started, refreshing the list...")
        elif action == "stop":
            await manager.stop_worker(cfg)
            log.info(
                "Button operation successful: user=%s Stop %s",
                user_id,
                cfg.display_name,
                extra={"project": cfg.project_slug},
            )
            await callback.answer("The project has been stopped, refreshing the list...")
        elif action == "switch_all_to":
            model_map = dict(SWITCHABLE_MODELS)
            if target_model not in model_map:
                await callback.answer("Unsupported model", show_alert=True)
                return
            await callback.answer("Global switching, please wait...")
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
                    f"An attempt was made to switch all project models to {label}, but execution failed for some projects:\n{failure_lines}"
                )
                log.warning(
                    "Global model switching partial failure: user=%s model=%s failures=%s",
                    user_id,
                    target_model,
                    [name for name, _ in errors],
                )
            else:
                message_text = f"All project models have been switched to {label} and remain stopped. "
                log.info(
                    "Button operation successful: user=%s Switch all models to %s",
                    user_id,
                    target_model,
                )
            await callback.message.answer(message_text)
        elif action == "switch_to":
            model_map = dict(SWITCHABLE_MODELS)
            if target_model not in model_map:
                await callback.answer("Unsupported model", show_alert=True)
                return
            state = manager.state_store.data.get(cfg.project_slug)
            previous_model = state.model if state else cfg.default_model
            was_running = bool(state and state.status == "running")
            # Automatically record chat_id if not already available
            if callback.message and callback.message.chat:
                if not state or not state.chat_id:
                    manager.update_chat_id(cfg.project_slug, callback.message.chat.id)
                    log.info(
                        "Automatically record chat_id when switching models: project=%s, chat_id=%s",
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
                            "Model switch failed and recovery failed: %s",
                            restore_exc,
                            extra={"project": cfg.project_slug, "model": previous_model},
                        )
                raise
            else:
                if was_running:
                    await callback.answer(f"Switched to {model_map.get(chosen, chosen)}")
                    log.info(
                        "Button operation successful: user=%s switches %s to %s",
                        user_id,
                        cfg.display_name,
                        chosen,
                        extra={"project": cfg.project_slug, "model": chosen},
                    )
                else:
                    await callback.answer(f"The default model has been updated to {model_map.get(chosen, chosen)}")
                    log.info(
                        "Button operation successful: user=%s updates %s and the default model is %s",
                        user_id,
                        cfg.display_name,
                        chosen,
                        extra={"project": cfg.project_slug, "model": chosen},
                    )
        else:
            await callback.answer("Unknown operation", show_alert=True)
            return
    except Exception as exc:
        log.error(
            "Button operation failed: action=%s project=%s error=%s",
            action,
            (cfg.display_name if cfg else "*"),
            exc,
            extra={"project": cfg.project_slug if cfg else "*"},
        )
        if callback.message:
            await callback.message.answer(f"Operation failed: {exc}")
        await callback.answer("Operation failed", show_alert=True)
        return

    await _refresh_project_overview(callback.message, manager)


@router.message(Command("run"))
async def cmd_run(message: Message) -> None:
    """Processes the /run command, starting the specified project and optionally switching models. """

    _log_update(message)
    manager = await _ensure_manager()
    if not manager.is_authorized(message.chat.id):
        await message.answer("Not authorized.")
        return
    parts=message.text.split()
    if len(parts) < 2:
        await message.answer("Usage: /run <project> [model]")
        return
    project_raw = parts[1]
    model = parts[2] if len(parts) >= 3 else None
    try:
        cfg = manager.require_project(project_raw)
    except ValueError as exc:
        await message.answer(str(exc))
        return

    async def runner():
        """call manager.run_worker Start the project and return the prompt text. """

        chosen = await manager.run_worker(cfg, model=model)
        return f"Started {cfg.display_name} (model={chosen})"

    await _run_and_reply(message, "start up", runner())


@router.message(Command("stop"))
async def cmd_stop(message: Message) -> None:
    """Process the /stop command to stop the specified project. """

    _log_update(message)
    manager = await _ensure_manager()
    if not manager.is_authorized(message.chat.id):
        await message.answer("Not authorized.")
        return
    parts=message.text.split()
    if len(parts) < 2:
        await message.answer("Usage: /stop <project>")
        return
    project_raw = parts[1]
    try:
        cfg = manager.require_project(project_raw)
    except ValueError as exc:
        await message.answer(str(exc))
        return

    async def stopper():
        """Stops the specified project and updates the status. """

        await manager.stop_worker(cfg, update_state=True)
        return f"Stopped {cfg.display_name}"

    await _run_and_reply(message, "stop", stopper())


@router.message(Command("switch"))
async def cmd_switch(message: Message) -> None:
    """Handle the /switch command and restart the project with the new model after shutdown. """

    _log_update(message)
    manager = await _ensure_manager()
    if not manager.is_authorized(message.chat.id):
        await message.answer("Not authorized.")
        return
    parts=message.text.split()
    if len(parts) < 3:
        await message.answer("Usage: /switch <project> <model>")
        return
    project_raw, model = parts[1], parts[2]
    try:
        cfg = manager.require_project(project_raw)
    except ValueError as exc:
        await message.answer(str(exc))
        return

    async def switcher():
        """Restart the project and switch to the new model. """

        await manager.stop_worker(cfg, update_state=True)
        chosen = await manager.run_worker(cfg, model=model)
        return f"Switched {cfg.display_name} to {chosen}"

    await _run_and_reply(message, "switch", switcher())


@router.message(Command("authorize"))
async def cmd_authorize(message: Message) -> None:
    """Process the /authorize command to register the chat_id for the project. """

    _log_update(message)
    manager = await _ensure_manager()
    if not manager.is_authorized(message.chat.id):
        await message.answer("Not authorized.")
        return
    parts=message.text.split()
    if len(parts) < 3:
        await message.answer("Usage: /authorize <project> <chat_id>")
        return
    project_raw, chat_raw = parts[1], parts[2]
    if not chat_raw.isdigit():
        await message.answer("chat_id Needs to be a number")
        return
    chat_id = int(chat_raw)
    try:
        cfg = manager.require_project(project_raw)
    except ValueError as exc:
        await message.answer(str(exc))
        return
    manager.update_chat_id(cfg.project_slug, chat_id)
    await message.answer(
        f"Logged {cfg.display_name} of chat_id={chat_id}"
    )


@router.callback_query(F.data.startswith("project:wizard:skip:"))
async def on_project_wizard_skip(callback: CallbackQuery) -> None:
    """Handle the "Skip this" button in the wizard. """

    if callback.message is None or callback.message.chat is None:
        return
    chat_id = callback.message.chat.id
    async with PROJECT_WIZARD_LOCK:
        session = PROJECT_WIZARD_SESSIONS.get(chat_id)
    if session is None:
        await callback.answer("There are currently no ongoing project processes. ", show_alert=True)
        return
    if session.step_index >= len(session.fields):
        await callback.answer("The current process has ended. ", show_alert=True)
        return
    _, _, field = callback.data.partition("project:wizard:skip:")
    current_field = session.fields[session.step_index]
    if field != current_field:
        await callback.answer("The current steps have been changed, please follow the latest prompts. ", show_alert=True)
        return
    manager = await _ensure_manager()
    await callback.answer("skipped")
    await _advance_wizard_session(
        session,
        manager,
        callback.message,
        "",
        prefix="skipped ✅",
    )


@router.message(F.text.func(_is_projects_menu_trigger))
async def on_master_projects_button(message: Message) -> None:
    """Handle project overview requests triggered by resident keyboard. """
    _log_update(message)
    manager = await _ensure_manager()
    if not manager.is_authorized(message.chat.id):
        await message.answer("Not authorized.")
        return
    requested_text = message.text or ""
    reply_to_message_id: Optional[int] = message.message_id
    if not _text_equals_master_button(requested_text):
        log.info(
            "Received an outdated project list button; refreshing the chat keyboard.",
            extra={"text": requested_text, "chat_id": message.chat.id},
        )
        await message.answer(
            'The main menu button is now "📂 Project List"; the session text has been synchronised.',
            reply_markup=_build_master_main_keyboard(),
            reply_to_message_id=reply_to_message_id,
        )
        # The latest keyboard has been pushed. There is no need to continue to quote the original message in subsequent replies to avoid repeated citation prompts.
        reply_to_message_id = None
    await _send_projects_overview_to_chat(
        message.bot,
        message.chat.id,
        manager,
        reply_to_message_id=reply_to_message_id,
    )


@router.message(F.text.in_(MASTER_MANAGE_BUTTON_ALLOWED_TEXTS))
async def on_master_manage_button(message: Message) -> None:
    """Handle the project management entry for the resident keyboard. """
    _log_update(message)
    manager = await _ensure_manager()
    if not manager.is_authorized(message.chat.id):
        await message.answer("Not authorized.")
        return
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="➕ New project", callback_data="project:create:*"))
    model_name_map = dict(SWITCHABLE_MODELS)
    for cfg in manager.configs:
        state = manager.state_store.data.get(cfg.project_slug)
        current_model_value = state.model if state else cfg.default_model
        current_model_key = (current_model_value or "").lower()
        current_model_label = model_name_map.get(current_model_key, current_model_value or current_model_key or "-")
        builder.row(
            InlineKeyboardButton(
                text=f"⚙️ Manage {cfg.display_name}",
                callback_data=f"project:manage:{cfg.project_slug}",
            ),
            InlineKeyboardButton(
                text=f"🧠 Switch model (current model {current_model_label})",
                callback_data=f"project:switch_prompt:{cfg.project_slug}",
            ),
        )
    builder.row(
        InlineKeyboardButton(
            text="🔁 Switch all models",
            callback_data="project:switch_all:*",
        )
    )
    builder.row(InlineKeyboardButton(text="📂 Return to list", callback_data="project:refresh:*"))
    markup = builder.as_markup()
    _ensure_numbered_markup(markup)
    await message.answer(
        'Select a project to manage, or tap "➕ New project" to create a new worker.',
        reply_markup=markup,
    )


@router.message(F.text.in_(MASTER_COMMAND_BUTTON_ALLOWED_TEXTS))
async def on_master_command_button(message: Message) -> None:
    """Entry point for master-level command management."""

    _log_update(message)
    manager = await _ensure_manager()
    if not manager.is_authorized(message.chat.id):
        await message.answer("Not authorized.")
        return
    await _render_master_command_list_message(
        target_message=None,
        fallback_message=message,
        page=1,
    )


@router.callback_query(F.data.startswith(f"{MASTER_COMMAND_LIST_CALLBACK}:"))
async def on_master_command_list_callback(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer("原始消息不存在", show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) != 2:
        await callback.answer("参数错误", show_alert=True)
        return
    try:
        page = int(parts[1])
    except ValueError:
        page = 1
    await _render_master_command_list_message(
        target_message=callback.message,
        fallback_message=callback.message,
        page=page,
    )
    await callback.answer()


@router.callback_query(F.data.startswith(f"{MASTER_COMMAND_DETAIL_CALLBACK}:"))
async def on_master_command_detail_callback(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer("原始消息不存在", show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("参数错误", show_alert=True)
        return
    try:
        preset_id = int(parts[1])
        page = int(parts[2])
    except ValueError:
        await callback.answer("参数错误", show_alert=True)
        return
    preset = await MASTER_COMMAND_SERVICE.get_preset(preset_id)
    if preset is None:
        await callback.answer("命令不存在，返回列表。", show_alert=True)
        await _render_master_command_list_message(
            target_message=callback.message,
            fallback_message=callback.message,
            page=page,
        )
        return
    await _render_master_command_detail_message(
        target_message=callback.message,
        fallback_message=callback.message,
        preset=preset,
        origin_page=page,
    )
    await callback.answer()


@router.callback_query(F.data.startswith(f"{MASTER_COMMAND_CREATE_CALLBACK}:"))
async def on_master_command_create_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer("缺少消息上下文", show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) != 2:
        await callback.answer("参数错误", show_alert=True)
        return
    try:
        page = int(parts[1])
    except ValueError:
        page = 1
    await state.clear()
    await state.update_data(
        context="master_command_center",
        mode="create",
        origin_page=page,
    )
    await state.set_state(CommandPresetStates.waiting_name)
    await callback.answer()
    await callback.message.answer("请输入命令名称：", reply_markup=_build_master_description_keyboard(include_skip=False))


@router.callback_query(F.data.startswith(f"{MASTER_COMMAND_EDIT_CALLBACK}:"))
async def on_master_command_edit_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer("缺少消息上下文", show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("参数错误", show_alert=True)
        return
    try:
        preset_id = int(parts[1])
        page = int(parts[2])
    except ValueError:
        await callback.answer("参数错误", show_alert=True)
        return
    preset = await MASTER_COMMAND_SERVICE.get_preset(preset_id)
    if preset is None:
        await callback.answer("命令不存在", show_alert=True)
        return
    await state.clear()
    await state.update_data(
        context="master_command_center",
        mode="edit",
        preset_id=preset.id,
        origin_page=page,
        name=preset.title,
        command_text=preset.command,
        workdir=preset.workdir,
        require_confirmation=preset.require_confirmation,
    )
    await state.set_state(CommandPresetStates.waiting_name)
    await callback.answer()
    await callback.message.answer(
        f"当前名称：{preset.title}\n请输入新的命令名称，或发送 Skip 保持不变：",
        reply_markup=_build_master_description_keyboard(),
    )


@router.callback_query(F.data.startswith(f"{MASTER_COMMAND_TOGGLE_CONFIRM_CALLBACK}:"))
async def on_master_command_toggle_confirm(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer("缺少消息上下文", show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("参数错误", show_alert=True)
        return
    try:
        preset_id = int(parts[1])
        page = int(parts[2])
    except ValueError:
        await callback.answer("参数错误", show_alert=True)
        return
    preset = await MASTER_COMMAND_SERVICE.toggle_confirmation(preset_id)
    if preset is None:
        await callback.answer("命令不存在", show_alert=True)
        return
    await _render_master_command_detail_message(
        target_message=callback.message,
        fallback_message=callback.message,
        preset=preset,
        origin_page=page,
    )
    await callback.answer("执行前确认状态已更新")


@router.callback_query(F.data.startswith(f"{MASTER_COMMAND_DELETE_CALLBACK}:"))
async def on_master_command_delete_callback(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer("缺少消息上下文", show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("参数错误", show_alert=True)
        return
    try:
        preset_id = int(parts[1])
        page = int(parts[2])
    except ValueError:
        await callback.answer("参数错误", show_alert=True)
        return
    preset = await MASTER_COMMAND_SERVICE.get_preset(preset_id)
    if preset is None:
        await callback.answer("命令不存在", show_alert=True)
        return
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ 确认删除",
                    callback_data=f"{MASTER_COMMAND_DELETE_EXECUTE_CALLBACK}:{preset.id}:{page}",
                ),
                InlineKeyboardButton(
                    text="⬅️ 返回详情",
                    callback_data=f"{MASTER_COMMAND_DETAIL_CALLBACK}:{preset.id}:{page}",
                ),
            ]
        ]
    )
    await callback.message.answer(f"确认删除命令「{preset.title}」吗？", reply_markup=markup)
    await callback.answer("请确认删除")


@router.callback_query(F.data.startswith(f"{MASTER_COMMAND_DELETE_EXECUTE_CALLBACK}:"))
async def on_master_command_delete_execute(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer("缺少消息上下文", show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("参数错误", show_alert=True)
        return
    try:
        preset_id = int(parts[1])
        page = int(parts[2])
    except ValueError:
        await callback.answer("参数错误", show_alert=True)
        return
    deleted = await MASTER_COMMAND_SERVICE.delete_preset(preset_id)
    if not deleted:
        await callback.answer("命令不存在", show_alert=True)
        return
    await callback.answer("命令已删除")
    await callback.message.answer("命令已删除，返回列表。")
    await _render_master_command_list_message(
        target_message=None,
        fallback_message=callback.message,
        page=page,
    )


@router.message(CommandPresetStates.waiting_name)
async def on_master_command_wizard_name(message: Message, state: FSMContext) -> None:
    data = await _master_get_wizard_data(state)
    if data is None:
        return
    raw_text = (message.text or "").strip()
    if _master_is_cancel_message(raw_text):
        await _master_cancel_command_wizard(message, state)
        return
    mode = data.get("mode", "create")
    allow_skip = mode == "edit"
    if _master_is_skip_message(raw_text):
        if not allow_skip:
            await message.answer("创建模式不支持 Skip，请输入命令名称：", reply_markup=_build_master_description_keyboard(include_skip=False))
            return
        name = data.get("name")
    else:
        if not raw_text:
            await message.answer("请输入非空的命令名称：", reply_markup=_build_master_description_keyboard(include_skip=allow_skip))
            return
        name = raw_text
    await state.update_data(name=name)
    await state.set_state(CommandPresetStates.waiting_command)
    await message.answer("请输入命令内容（支持多行），或发送 Skip 保持不变：", reply_markup=_build_master_description_keyboard(include_skip=allow_skip))


@router.message(CommandPresetStates.waiting_command)
async def on_master_command_wizard_command(message: Message, state: FSMContext) -> None:
    data = await _master_get_wizard_data(state)
    if data is None:
        return
    raw_text = message.text or ""
    trimmed = raw_text.strip()
    if _master_is_cancel_message(trimmed):
        await _master_cancel_command_wizard(message, state)
        return
    mode = data.get("mode", "create")
    allow_skip = mode == "edit"
    if _master_is_skip_message(trimmed):
        if not allow_skip:
            await message.answer("创建模式不支持 Skip，请输入命令内容：", reply_markup=_build_master_description_keyboard(include_skip=False))
            return
        command_text = data.get("command_text")
    else:
        if not trimmed:
            await message.answer("请输入非空的命令内容：", reply_markup=_build_master_description_keyboard(include_skip=allow_skip))
            return
        command_text = raw_text
    await state.update_data(command_text=command_text)
    await state.set_state(CommandPresetStates.waiting_workdir)
    await message.answer("请输入工作目录（可选），发送 Skip 使用默认目录：", reply_markup=_build_master_description_keyboard(include_skip=True))


@router.message(CommandPresetStates.waiting_workdir)
async def on_master_command_wizard_workdir(message: Message, state: FSMContext) -> None:
    data = await _master_get_wizard_data(state)
    if data is None:
        return
    raw_text = (message.text or "").strip()
    if _master_is_cancel_message(raw_text):
        await _master_cancel_command_wizard(message, state)
        return
    if _master_is_skip_message(raw_text):
        workdir = data.get("workdir")
    else:
        workdir = raw_text or None
    await state.update_data(workdir=workdir)
    await state.set_state(CommandPresetStates.waiting_confirmation_flag)
    await message.answer("执行前是否需要确认？回复 Yes 或 No（或发送 Cancel 退出）：", reply_markup=_build_master_boolean_keyboard())


@router.message(CommandPresetStates.waiting_confirmation_flag)
async def on_master_command_wizard_confirm_flag(message: Message, state: FSMContext) -> None:
    data = await _master_get_wizard_data(state)
    if data is None:
        return
    raw_text = (message.text or "").strip()
    if _master_is_cancel_message(raw_text):
        await _master_cancel_command_wizard(message, state)
        return
    choice = _master_parse_confirmation_choice(raw_text)
    if choice is None:
        await message.answer("请输入 Yes 或 No（或发送 Cancel 退出）：", reply_markup=_build_master_boolean_keyboard())
        return
    await state.update_data(require_confirmation=choice)
    name = data.get("name") or ""
    command_text = data.get("command_text") or ""
    workdir = data.get("workdir")
    summary = _master_format_command_summary(
        name=name,
        command_text=command_text,
        workdir=workdir,
        require_confirmation=choice,
    )
    mode = data.get("mode", "create")
    await state.set_state(CommandPresetStates.waiting_final_confirmation)
    await message.answer(
        summary + "\n\n请确认保存：",
        reply_markup=_build_master_confirm_keyboard(mode),
    )


@router.message(CommandPresetStates.waiting_final_confirmation)
async def on_master_command_wizard_final(message: Message, state: FSMContext) -> None:
    data = await _master_get_wizard_data(state)
    if data is None:
        return
    raw_text = (message.text or "").strip()
    if _master_is_cancel_message(raw_text):
        await _master_cancel_command_wizard(message, state)
        return
    mode = data.get("mode", "create")
    expected = _master_command_confirm_label(mode)
    if raw_text != expected:
        await message.answer("请输入确认选项，或发送 Cancel 退出：", reply_markup=_build_master_confirm_keyboard(mode))
        return
    name = data.get("name")
    command_text = data.get("command_text")
    workdir = data.get("workdir")
    require_confirmation = bool(data.get("require_confirmation", True))
    origin_page = int(data.get("origin_page") or 1)
    if not name or not command_text:
        await _master_cancel_command_wizard(message, state)
        return
    if mode == "edit":
        preset_id = data.get("preset_id")
        if not preset_id:
            await _master_cancel_command_wizard(message, state)
            return
        record = await MASTER_COMMAND_SERVICE.update_preset(
            preset_id,
            title=name,
            command=command_text,
            workdir=workdir,
            require_confirmation=require_confirmation,
        )
        if record is None:
            await message.answer("命令不存在或已删除。", reply_markup=_build_master_main_keyboard())
            await state.clear()
            return
    else:
        record = await MASTER_COMMAND_SERVICE.create_preset(
            title=name,
            command=command_text,
            workdir=workdir,
            require_confirmation=require_confirmation,
        )
    await state.clear()
    await message.answer("命令已保存。", reply_markup=_build_master_main_keyboard())
    detail_text, detail_markup = _build_master_command_detail_view(record, origin_page)
    await message.answer(detail_text, reply_markup=detail_markup)
@router.message()
async def cmd_fallback(message: Message) -> None:
    """Fallback handler: resume the wizard when possible; otherwise prompt for the available commands."""

    _log_update(message)
    manager = await _ensure_manager()
    if not manager.is_authorized(message.chat.id):
        await message.answer("Not authorized.")
        return
    handled = await _handle_wizard_message(message, manager)
    if handled:
        return
    await message.answer("Unrecognized command, please use /projects /run /stop /switch /authorize. ")



def _delete_project_with_fallback(
    repository: ProjectRepository,
    *,
    stored_slug: str,
    original_slug: str,
    bot_name: str,
) -> Tuple[Optional[Exception], List[Tuple[str, Exception]]]:
    """Try deleting items with multiple identifiers to improve case and alias compatibility. """

    attempts: List[Tuple[str, Exception]] = []

    def _attempt(candidate: str) -> Optional[Exception]:
        """The deletion is actually executed, and an exception is returned on failure for subsequent clarification. """
        slug = (candidate or "").strip()
        if not slug:
            return ValueError("slug is empty")
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
    """Handle the callback logic for deleting the confirmation button. """
    manager = await _ensure_manager()
    user_id = callback.from_user.id if callback.from_user else None
    if user_id is None or not manager.is_authorized(user_id):
        await callback.answer("Not authorized.", show_alert=True)
        return
    if callback.message is None:
        await callback.answer("Invalid operation", show_alert=True)
        return
    parts = callback.data.split(":", 2)
    if len(parts) != 3:
        await callback.answer("Invalid operation", show_alert=True)
        return
    target_slug = parts[2]
    log.info(
        "Delete confirmation callback: user=%s slug=%s",
        user_id,
        target_slug,
        extra={"project": target_slug},
    )
    current_state = await state.get_state()
    if current_state != ProjectDeleteStates.confirming.state:
        await callback.answer("The confirmation process has expired, please initiate the deletion again. ", show_alert=True)
        return
    data = await state.get_data()
    stored_slug = str(data.get("project_slug", "")).strip()
    if stored_slug.lower() != target_slug.lower():
        await state.clear()
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        await callback.answer("The confirmation information has expired, please initiate deletion again. ", show_alert=True)
        return
    initiator_id = data.get("initiator_id")
    if initiator_id and initiator_id != user_id:
        await callback.answer("Only the process initiator can confirm the deletion. ", show_alert=True)
        return
    expires_at = float(data.get("expires_at") or 0)
    if expires_at and time.time() > expires_at:
        await state.clear()
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        await callback.answer("The confirmation has timed out, please initiate deletion again. ", show_alert=True)
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
            "Failed to delete item: %s",
            error,
            extra={
                "slug": stored_slug,
                "attempts": [slug for slug, _ in attempts],
            },
        )
        await callback.answer("Deletion failed, please try again later. ", show_alert=True)
        await callback.message.answer(f"Delete failed: {error}")
        return
    await state.clear()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    _reload_manager_configs(manager)
    display_name = data.get("display_name") or stored_slug
    await callback.answer("Project deleted.")
    await callback.message.answer(f"Project {display_name} deleted ✅")
    await _send_projects_overview_to_chat(callback.message.bot, callback.message.chat.id, manager)


@router.callback_query(F.data == "project:delete_cancel")
async def on_project_delete_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    """Cancel button that handles the deletion process. """
    manager = await _ensure_manager()
    user_id = callback.from_user.id if callback.from_user else None
    if user_id is None or not manager.is_authorized(user_id):
        await callback.answer("Not authorized.", show_alert=True)
        return
    if callback.message is None:
        await callback.answer("Invalid operation", show_alert=True)
        return
    current_state = await state.get_state()
    if current_state != ProjectDeleteStates.confirming.state:
        await callback.answer("There are currently no pending deletion processes. ", show_alert=True)
        return
    data = await state.get_data()
    log.info(
        "Delete cancellation callback: user=%s slug=%s",
        user_id,
        data.get("project_slug"),
    )
    initiator_id = data.get("initiator_id")
    if initiator_id and initiator_id != user_id:
        await callback.answer("Only the process initiator can cancel the deletion. ", show_alert=True)
        return
    expires_at = float(data.get("expires_at") or 0)
    if expires_at and time.time() > expires_at:
        await state.clear()
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        await callback.answer("The confirmation has timed out, please initiate deletion again. ", show_alert=True)
        return
    await state.clear()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    display_name = data.get("display_name") or data.get("project_slug") or ""
    await callback.answer("Deletion cancelled.")
    await callback.message.answer(f"Deletion cancelled for project {display_name}.")


@router.message(ProjectDeleteStates.confirming)
async def on_project_delete_text(message: Message, state: FSMContext) -> None:
    """Compatible with older interactions, allowing text commands to confirm or cancel deletion. """
    manager = await _ensure_manager()
    user=message.from_user
    if user is None or not manager.is_authorized(user.id):
        await message.answer("Not authorized.")
        return
    data = await state.get_data()
    initiator_id = data.get("initiator_id")
    if initiator_id and initiator_id != user.id:
        await message.answer("Only the process initiator can proceed with this deletion process. ")
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
        await message.answer("The confirmation has timed out, please initiate deletion again. ")
        return

    raw_text = (message.text or "").strip()
    if not raw_text:
        await message.answer('Use the buttons or type "Confirm deletion" / "Cancel" to finish the operation.')
        return
    normalized = raw_text.casefold().strip()
    normalized = normalized.rstrip("..!??")
    normalized_compact = normalized.replace(" ", "")
    confirm_tokens = {"confirm deletion", "confirm", "y", "yes"}
    cancel_tokens = {"cancel", "n", "no"}

    if normalized in cancel_tokens or normalized_compact in cancel_tokens:
        await state.clear()
        prompt = getattr(message, "reply_to_message", None)
        if prompt:
            try:
                await prompt.edit_reply_markup(reply_markup=None)
            except TelegramBadRequest:
                pass
        display_name = data.get("display_name") or data.get("project_slug") or ""
        await message.answer(f"Deletion cancelled for project {display_name}.")
        return

    if not (
        normalized in confirm_tokens
        or normalized_compact in confirm_tokens
        or normalized.startswith("Confirm deletion")
    ):
        await message.answer('Type "Confirm deletion" or use the buttons to finish the operation.')
        return

    stored_slug = str(data.get("project_slug", "")).strip()
    if not stored_slug:
        await state.clear()
        await message.answer("The deletion process status is abnormal, please initiate deletion again. ")
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
            "Failed to delete item (text confirmation): %s",
            error,
            extra={
                "slug": stored_slug,
                "attempts": [slug for slug, _ in attempts],
            },
        )
        await message.answer(f"Delete failed: {error}")
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
    await message.answer(f"Item {display_name} deleted ✅")
    await _send_projects_overview_to_chat(message.bot, message.chat.id, manager)



async def bootstrap_manager() -> MasterManager:
    """Initialize the project warehouse, state storage and manager, and clean up old workers before starting. """

    load_env()
    tmux_prefix=os.environ.get("TMUX_SESSION_PREFIX", "vibe")
    _kill_existing_tmux(tmux_prefix)
    try:
        repository = ProjectRepository(CONFIG_DB_PATH, CONFIG_PATH)
    except Exception as exc:
        log.error("Failed to initialize project repository: %s", exc)
        sys.exit(1)

    records = repository.list_projects()
    if not records:
        log.warning("The project configuration is empty and will start with an empty project list. ")

    configs= [ProjectConfig.from_dict(record.to_dict()) for record in records]

    state_store = StateStore(STATE_PATH, {cfg.project_slug: cfg for cfg in configs})
    manager = MasterManager(configs, state_store=state_store)

    await manager.stop_all(update_state=True)
    log.info("The historical tmux session has been cleared, and the worker needs to be started manually. ")

    global MANAGER
    global PROJECT_REPOSITORY
    MANAGER = manager
    PROJECT_REPOSITORY = repository
    return manager


async def main() -> None:
    """master.py The asynchronous entry completes the bot startup and binding to the scheduler. """

    manager = await bootstrap_manager()

    # Diagnosis log: record the restart signal file path to facilitate troubleshooting
    log.info(
        "Restart signal file path: %s (Exists: %s)",
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
        log.error("MASTER_BOT_TOKEN not set")
        sys.exit(1)
    try:
        await MASTER_COMMAND_SERVICE.initialize()
    except Exception as exc:
        log.error("Global command store initialization failed: %s", exc)
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

    log.info("Master Started, listening for administrator commands. ")
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
        log.info("Master stop")
