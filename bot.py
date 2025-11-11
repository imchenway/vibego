# bot.py — Telegram prompt → macOS execution → pushback (aiogram 3.x)
# Notes:
# - Uses long polling, no public endpoint required.
# - MODE=A: invoke your agent/Codex CLI/HTTP as a subprocess (CLI example shown here).
# - MODE=B: inject prompts into a tmux session (e.g., vibe) and read output from pipe-pane logs.
# - Security: only allow ALLOWED_CHAT_ID (your private chat_id); BOT_TOKEN is loaded from .env; never hardcode the token.

from __future__ import annotations

import asyncio, os, sys, time, uuid, shlex, subprocess, socket, re, json, shutil, hashlib, html, mimetypes, math, unicodedata, threading
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple, List, Callable, Awaitable, Literal
from dataclasses import dataclass
from urllib.parse import urlparse, quote, unquote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.filters.command import CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.types import (
    Message,
    BufferedInputFile,
    CallbackQuery,
    MessageEntity,
    BotCommand,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    MenuButtonCommands,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
    User,
)
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.utils.formatting import Text
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramForbiddenError,
)
from aiohttp import BasicAuth, ClientError

from logging_setup import create_logger
from command_center.fsm import CommandPresetStates
from command_center.models import (
    CommandPresetRecord,
    ScopedCommandPreset,
    COMMAND_SCOPE_GLOBAL,
    COMMAND_SCOPE_PROJECT,
    GLOBAL_COMMAND_PROJECT_SLUG,
)
from command_center.service import CommandPresetService
from tasks import TaskHistoryRecord, TaskNoteRecord, TaskRecord, TaskService
from tasks.commands import parse_simple_kv, parse_structured_text
from tasks.constants import (
    DEFAULT_PAGE_SIZE,
    DEFAULT_PRIORITY,
    NOTE_TYPES,
    STATUS_ALIASES,
    TASK_STATUSES,
    TASK_TYPES,
)
from tasks.fsm import (
    TaskBugReportStates,
    TaskCreateStates,
    TaskDescriptionStates,
    TaskEditStates,
    TaskListSearchStates,
    TaskNoteStates,
    TaskPushStates,
)
# --- Simple .env loading ---
def load_env(p: str = ".env"):
    """Load dotenv-style key/value pairs into the process environment."""

    if not os.path.exists(p): 
        return
    for line in Path(p).read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"): 
            continue
        if "=" in s:
            k, v = s.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

load_env()

# --- Logging & context ---
PROJECT_NAME = os.environ.get("PROJECT_NAME", "").strip()
ACTIVE_MODEL = (os.environ.get("ACTIVE_MODEL") or os.environ.get("MODEL_NAME") or "").strip()
worker_log = create_logger(
    "worker",
    project=PROJECT_NAME or "-",
    model=ACTIVE_MODEL or "-",
    level_env="WORKER_LOG_LEVEL",
    stderr_env="WORKER_STDERR",
)

def _default_config_root() -> Path:
    """Resolve the configuration root, preferring explicit environment variables with XDG fallback."""

    override = os.environ.get("MASTER_CONFIG_ROOT") or os.environ.get("VIBEGO_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    xdg_base = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_base).expanduser() if xdg_base else Path.home() / ".config"
    return base / "vibego"


CONFIG_ROOT_PATH = _default_config_root()
CONFIG_DIR_PATH = CONFIG_ROOT_PATH / "config"
STATE_DIR_PATH = CONFIG_ROOT_PATH / "state"
LOG_DIR_PATH = CONFIG_ROOT_PATH / "logs"
for _path in (CONFIG_DIR_PATH, STATE_DIR_PATH, LOG_DIR_PATH):
    _path.mkdir(parents=True, exist_ok=True)

SESSION_OFFSET_STORE_PATH = STATE_DIR_PATH / "session_offsets.json"
SESSION_OFFSET_STORE_LOCK = threading.Lock()
SESSION_OFFSET_FLUSH_TASK: Optional[asyncio.Task] = None
SESSION_OFFSET_FLUSH_INTERVAL = 0.5


def _load_session_offset_store() -> dict[str, int]:
    if not SESSION_OFFSET_STORE_PATH.exists():
        return {}
    try:
        raw = SESSION_OFFSET_STORE_PATH.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        worker_log.warning(
            "Failed to parse session offset store; a new file will be created.",
            extra={"store": str(SESSION_OFFSET_STORE_PATH)},
        )
        return {}
    cleaned: dict[str, int] = {}
    for key, value in data.items():
        try:
            cleaned[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return cleaned


SESSION_OFFSET_STORE: dict[str, int] = _load_session_offset_store()


def _write_session_offset_store(data: dict[str, int]) -> None:
    tmp_path = SESSION_OFFSET_STORE_PATH.with_suffix(".tmp")
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    tmp_path.write_text(payload, encoding="utf-8")
    tmp_path.replace(SESSION_OFFSET_STORE_PATH)


async def _flush_session_offsets_async() -> None:
    global SESSION_OFFSET_FLUSH_TASK
    try:
        await asyncio.sleep(SESSION_OFFSET_FLUSH_INTERVAL)
        with SESSION_OFFSET_STORE_LOCK:
            _write_session_offset_store(SESSION_OFFSET_STORE)
    finally:
        SESSION_OFFSET_FLUSH_TASK = None


def _schedule_session_offset_flush() -> None:
    global SESSION_OFFSET_FLUSH_TASK
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        with SESSION_OFFSET_STORE_LOCK:
            _write_session_offset_store(SESSION_OFFSET_STORE)
        return
    if SESSION_OFFSET_FLUSH_TASK is None or SESSION_OFFSET_FLUSH_TASK.done():
        SESSION_OFFSET_FLUSH_TASK = loop.create_task(_flush_session_offsets_async())


def _get_persisted_session_offset(session_key: str) -> Optional[int]:
    with SESSION_OFFSET_STORE_LOCK:
        value = SESSION_OFFSET_STORE.get(session_key)
    return value


def _store_session_offset(session_key: str, offset: int) -> None:
    normalized = max(int(offset or 0), 0)
    current = SESSION_OFFSETS.get(session_key)
    if current == normalized:
        # Still mirror the latest value to ensure callers rely on the dictionary even when unchanged.
        SESSION_OFFSETS[session_key] = normalized
        return
    SESSION_OFFSETS[session_key] = normalized
    with SESSION_OFFSET_STORE_LOCK:
        previous = SESSION_OFFSET_STORE.get(session_key)
        if previous == normalized:
            return
        SESSION_OFFSET_STORE[session_key] = normalized
    _schedule_session_offset_flush()


def _init_session_offset(session_path: Path, *, allow_backtrack: bool = False) -> None:
    session_key = str(session_path)
    if session_key in SESSION_OFFSETS:
        return
    persisted = _get_persisted_session_offset(session_key)
    if persisted is not None:
        _store_session_offset(session_key, persisted)
        return
    try:
        size = session_path.stat().st_size
    except FileNotFoundError:
        size = 0
    backtrack = max(SESSION_INITIAL_BACKTRACK_BYTES, 0)
    if not allow_backtrack or backtrack <= 0:
        _store_session_offset(session_key, size)
        return
    _store_session_offset(session_key, max(size - backtrack, 0))

def _env_int(name: str, default: int) -> int:
    """Read an integer environment variable, falling back to the default on failure."""

    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        worker_log.warning("Failed to parse environment variable %s=%r as int. Using default %s", name, raw, default)
        return default


def _env_float(name: str, default: float) -> float:
    """Read a float environment variable, falling back to the default on failure."""

    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw.strip())
    except ValueError:
        worker_log.warning("Failed to parse environment variable %s=%r as float. Using default %s", name, raw, default)
        return default

_PARSE_MODE_CANDIDATES: Dict[str, Optional[ParseMode]] = {
    "": None,
    "none": None,
    "markdown": ParseMode.MARKDOWN,
    "md": ParseMode.MARKDOWN,
    "markdownv2": ParseMode.MARKDOWN_V2,
    "mdv2": ParseMode.MARKDOWN_V2,
    "html": ParseMode.HTML,
}

# Append agents.md metadata to stage prompts to keep audit records consistent.
AGENTS_PHASE_SUFFIX = ", please execute according to the prompt words of the specified stage, then list the triggered agents.md stage, task name, and task code (e.g., /TASK_0001).the following is the description of this task and the execution history.\n"
# Stage prompts sent to models (vibe and test) reuse the unified suffix for consistent output.
VIBE_PHASE_PROMPT = f"Enter vibe stage{AGENTS_PHASE_SUFFIX}"
TEST_PHASE_PROMPT = f"Enter test stage{AGENTS_PHASE_SUFFIX}"
# Dedicated prefix when reporting defects, inserted before the unified stage prompt.
BUG_REPORT_PREFIX = "Report a defect. please execute according to the prompt words of the specified stage, Refer to the latest defect description at the bottom.\n"

_parse_mode_env = (os.environ.get("TELEGRAM_PARSE_MODE") or "Markdown").strip()
_parse_mode_key = _parse_mode_env.replace("-", "").replace("_", "").lower()
MODEL_OUTPUT_PARSE_MODE: Optional[ParseMode]
if _parse_mode_key in _PARSE_MODE_CANDIDATES:
    MODEL_OUTPUT_PARSE_MODE = _PARSE_MODE_CANDIDATES[_parse_mode_key]
    if MODEL_OUTPUT_PARSE_MODE is None:
        worker_log.info("Model output will be sent as plain text")
    else:
        mode_value = (
            MODEL_OUTPUT_PARSE_MODE.value
            if isinstance(MODEL_OUTPUT_PARSE_MODE, ParseMode)
            else str(MODEL_OUTPUT_PARSE_MODE)
        )
        worker_log.info("Model output parse_mode: %s", mode_value)
else:
    MODEL_OUTPUT_PARSE_MODE = ParseMode.MARKDOWN_V2
    worker_log.warning(
        "Unrecognised TELEGRAM_PARSE_MODE=%s, falling back to MarkdownV2",
        _parse_mode_env,
    )

_plan_parse_mode_env = (os.environ.get("PLAN_PROGRESS_PARSE_MODE") or "").strip()
_plan_parse_mode_key = _plan_parse_mode_env.replace("-", "").replace("_", "").lower()
PLAN_PROGRESS_PARSE_MODE: Optional[ParseMode]
if not _plan_parse_mode_key:
    PLAN_PROGRESS_PARSE_MODE = None
    worker_log.info("Plan progress messages default to plain text")
elif _plan_parse_mode_key in _PARSE_MODE_CANDIDATES:
    PLAN_PROGRESS_PARSE_MODE = _PARSE_MODE_CANDIDATES[_plan_parse_mode_key]
    if PLAN_PROGRESS_PARSE_MODE is None:
        worker_log.info("Plan progress messages will be sent as plain text")
    else:
        mode_value = (
            PLAN_PROGRESS_PARSE_MODE.value
            if isinstance(PLAN_PROGRESS_PARSE_MODE, ParseMode)
            else str(PLAN_PROGRESS_PARSE_MODE)
        )
        worker_log.info("Plan progress parse_mode: %s", mode_value)
else:
    PLAN_PROGRESS_PARSE_MODE = None
    worker_log.warning(
        "Unrecognised PLAN_PROGRESS_PARSE_MODE=%s; plan progress messages default to plain text",
        _plan_parse_mode_env,
    )

_IS_MARKDOWN_V2 = MODEL_OUTPUT_PARSE_MODE == ParseMode.MARKDOWN_V2
_IS_MARKDOWN = MODEL_OUTPUT_PARSE_MODE == ParseMode.MARKDOWN


def _parse_mode_value() -> Optional[str]:
    """Return the Telegram parse_mode used for model output."""

    if MODEL_OUTPUT_PARSE_MODE is None:
        return None
    return MODEL_OUTPUT_PARSE_MODE.value if isinstance(MODEL_OUTPUT_PARSE_MODE, ParseMode) else str(MODEL_OUTPUT_PARSE_MODE)


def _plan_parse_mode_value() -> Optional[str]:
    """Return the Telegram parse_mode used for plan progress messages."""

    if PLAN_PROGRESS_PARSE_MODE is None:
        return None
    return (
        PLAN_PROGRESS_PARSE_MODE.value
        if isinstance(PLAN_PROGRESS_PARSE_MODE, ParseMode)
        else str(PLAN_PROGRESS_PARSE_MODE)
    )

# --- Configuration ---
BOT_TOKEN = os.environ.get("BOT_TOKEN") or ""
if not BOT_TOKEN:
    worker_log.error("BOT_TOKEN is not configured; exiting.")
    sys.exit(1)

MODE = os.environ.get("MODE", "B").upper()                      # Modes: A or B

# Mode A (CLI)
AGENT_CMD = os.environ.get("AGENT_CMD", "")  # Example: codex --project /path/to/proj --prompt -
# Extend via HTTP: AGENT_HTTP=http://127.0.0.1:7001/api/run

# Mode B (tmux)
TMUX_SESSION = os.environ.get("TMUX_SESSION", "vibe")
TMUX_LOG = os.environ.get("TMUX_LOG", str(Path(__file__).resolve().parent / "vibe.out.log"))
IDLE_SECONDS = float(os.environ.get("IDLE_SECONDS", "3"))
MAX_RETURN_CHARS = int(os.environ.get("MAX_RETURN_CHARS", "200000"))  # Oversized response sent as attachment
TELEGRAM_PROXY = os.environ.get("TELEGRAM_PROXY", "").strip()        # Optional proxy URL
CODEX_WORKDIR = os.environ.get("CODEX_WORKDIR", "").strip()
CODEX_SESSION_FILE_PATH = os.environ.get("CODEX_SESSION_FILE_PATH", "").strip()
CODEX_SESSIONS_ROOT = os.environ.get("CODEX_SESSIONS_ROOT", "").strip()
MODEL_SESSION_ROOT = os.environ.get("MODEL_SESSION_ROOT", "").strip()
MODEL_SESSION_GLOB = os.environ.get("MODEL_SESSION_GLOB", "rollout-*.jsonl").strip() or "rollout-*.jsonl"
SESSION_LOCK_FILE_PATH = os.environ.get("SESSION_LOCK_FILE_PATH", "").strip()
SESSION_POLL_TIMEOUT = float(os.environ.get("SESSION_POLL_TIMEOUT", "2"))
WATCH_MAX_WAIT = float(os.environ.get("WATCH_MAX_WAIT", "0"))
WATCH_INTERVAL = float(os.environ.get("WATCH_INTERVAL", "2"))
SEND_RETRY_ATTEMPTS = int(os.environ.get("SEND_RETRY_ATTEMPTS", "3"))
SEND_RETRY_BASE_DELAY = float(os.environ.get("SEND_RETRY_BASE_DELAY", "0.5"))
SEND_FAILURE_NOTICE_COOLDOWN = float(os.environ.get("SEND_FAILURE_NOTICE_COOLDOWN", "30"))
SESSION_INITIAL_BACKTRACK_BYTES = int(os.environ.get("SESSION_INITIAL_BACKTRACK_BYTES", "16384"))
ENABLE_PLAN_PROGRESS = (os.environ.get("ENABLE_PLAN_PROGRESS", "1").strip().lower() not in {"0", "false", "no", "off"})
AUTO_COMPACT_THRESHOLD = max(_env_int("AUTO_COMPACT_THRESHOLD", 0), 0)
SESSION_LOCK_REQUIRED = (os.environ.get("SESSION_LOCK_REQUIRED", "1").strip().lower() not in {"0", "false", "no", "off"})

PLAN_STATUS_LABELS = {
    "completed": "✅",
    "in_progress": "🔄",
    "pending": "⏳",
}

DELIVERABLE_KIND_MESSAGE = "message"
DELIVERABLE_KIND_PLAN = "plan_update"
MODEL_COMPLETION_PREFIX = "✅ Model execution completed. Response follows:"
TELEGRAM_MESSAGE_LIMIT = 4096  # Telegram sendMessage single-message limit


def _canonical_model_name(raw_model: Optional[str] = None) -> str:
    """Normalise the model name to simplify downstream branching."""

    source = raw_model
    if source is None:
        source = (os.environ.get("MODEL_NAME") or ACTIVE_MODEL or "codex").strip()
    normalized = source.replace("-", "").replace("_", "").lower()
    return normalized or "codex"


def _model_display_label() -> str:
    """Return a user-friendly name for the active model."""

    raw = (os.environ.get("MODEL_NAME") or ACTIVE_MODEL or "codex").strip()
    normalized = _canonical_model_name(raw)
    mapping = {
        "codex": "Codex",
        "claudecode": "ClaudeCode",
        "gemini": "Gemini",
    }
    return mapping.get(normalized, raw or "Model")


MODEL_CANONICAL_NAME = _canonical_model_name()
MODEL_DISPLAY_LABEL = _model_display_label()


def _is_claudecode_model() -> bool:
    """Return True when the worker operates a ClaudeCode model."""

    return MODEL_CANONICAL_NAME == "claudecode"


@dataclass
class SessionDeliverable:
    """Describe a single deliverable item within the JSONL session."""

    offset: int
    kind: str
    text: str
    timestamp: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

ENV_ISSUES: list[str] = []
PRIMARY_WORKDIR: Optional[Path] = None

storage = MemoryStorage()
router = Router()
dp = Dispatcher(storage=storage)
dp.include_router(router)

_bot: Bot | None = None


def _mask_proxy(url: str) -> str:
    """Mask credentials when printing proxy details on stderr."""
    if "@" not in url:
        return url
    parsed = urlparse(url)
    host = parsed.hostname or "***"
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://***:***@{host}{port}"


def _detect_proxy() -> tuple[Optional[str], Optional[BasicAuth], Optional[str]]:
    """Prefer TELEGRAM_PROXY and fall back to common environment variables."""
    candidates = [
        ("TELEGRAM_PROXY", TELEGRAM_PROXY),
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

    parsed = urlparse(proxy_raw)
    auth: Optional[BasicAuth] = None
    if parsed.username:
        password = parsed.password or ""
        auth = BasicAuth(parsed.username, password)
        netloc = parsed.hostname or ""
        if parsed.port:
            netloc += f":{parsed.port}"
        proxy_raw = parsed._replace(netloc=netloc, path="", params="", query="", fragment="").geturl()

    worker_log.info("Using proxy (%s): %s", source, _mask_proxy(proxy_raw))
    return proxy_raw, auth, source

# Always force IPv4 for Telegram to avoid IPv6 drops in certain environments
def build_bot() -> Bot:
    """Create the aiogram Bot instance based on network and proxy configuration."""

    proxy_url, proxy_auth, _ = _detect_proxy()
    session_kwargs = {
        "proxy": proxy_url,
        "timeout": 60,
        "limit": 100,
    }
    if proxy_auth is not None:
        session_kwargs["proxy_auth"] = proxy_auth

    session = AiohttpSession(**session_kwargs)
    # `_connector_init` controls TCPConnector creation; enforce IPv4 parameters here
    session._connector_init.update({  # type: ignore[attr-defined]
        "family": socket.AF_INET,
        "ttl_dns_cache": 60,
    })
    return Bot(token=BOT_TOKEN, session=session)

def current_bot() -> Bot:
    """Return the lazily initialised global Bot instance."""

    global _bot
    if _bot is None:
        _bot = build_bot()
    return _bot

# --- Utility helpers ---
async def _send_with_retry(coro_factory, *, attempts: int = SEND_RETRY_ATTEMPTS) -> None:
    """Execute a Telegram call with a bounded number of retries."""

    delay = SEND_RETRY_BASE_DELAY
    last_exc: Optional[Exception] = None
    for attempt in range(attempts):
        try:
            await coro_factory()
            return
        except TelegramRetryAfter as exc:
            last_exc = exc
            if attempt >= attempts - 1:
                break
            await asyncio.sleep(max(float(exc.retry_after), SEND_RETRY_BASE_DELAY))
        except TelegramNetworkError as exc:
            last_exc = exc
            if attempt >= attempts - 1:
                break
            await asyncio.sleep(delay)
            delay *= 2
        except TelegramBadRequest:
            raise

    if last_exc is not None:
        raise last_exc


def _escape_markdown_v2(text: str) -> str:
    """Escape MarkdownV2 special characters while preserving code blocks.

    Notes:
    - Process segments so that code blocks (```...``` and `...`) stay intact.
    - ``Text().as_markdown()`` escapes every MarkdownV2 special character.
    - Remove hyphen escapes only between plain English words (e.g., ``pre-release``).
    - Keep escapes for numbers/timestamps (e.g., ``2025-10-23``).
    - Leave code block contents untouched.
    """

    def _escape_segment(segment: str) -> str:
        """Escape a non-code segment."""
        escaped = Text(segment).as_markdown()
        # Remove hyphen escapes only when surrounded by English letters
        escaped = re.sub(r"(?<=[a-zA-Z])\\-(?=[a-zA-Z])", "-", escaped)
        # Remove escaping from forward slashes
        escaped = escaped.replace("\\/", "/")
        return escaped

    # Process sections: keep code blocks intact, escape normal text
    pieces: list[str] = []
    last_index = 0

    for match in CODE_SEGMENT_RE.finditer(text):
        # Escape the normal text before the code block
        normal_part = text[last_index:match.start()]
        if normal_part:
            pieces.append(_escape_segment(normal_part))

        # Keep code blocks untouched
        pieces.append(match.group(0))
        last_index = match.end()

    # Escape the trailing normal text segment
    if last_index < len(text):
        remaining = text[last_index:]
        pieces.append(_escape_segment(remaining))

    return "".join(pieces) if pieces else _escape_segment(text)


LEGACY_DOUBLE_BOLD = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
LEGACY_DOUBLE_UNDERLINE = re.compile(r"__(.+?)__", re.DOTALL)
CODE_SEGMENT_RE = re.compile(r"(```.*?```|`[^`]*`)", re.DOTALL)
# Markdown heading pattern (# - ####)
MARKDOWN_HEADING = re.compile(r"^(#{1,4})\s+(.+)$", re.MULTILINE)


def _normalize_legacy_markdown(text: str) -> str:
    def _replace_double_star(match: re.Match[str]) -> str:
        content = match.group(1)
        return f"*{content}*"

    def _replace_double_underline(match: re.Match[str]) -> str:
        content = match.group(1)
        return f"_{content}_"

    def _normalize_segment(segment: str) -> str:
        converted = LEGACY_DOUBLE_BOLD.sub(_replace_double_star, segment)
        converted = LEGACY_DOUBLE_UNDERLINE.sub(_replace_double_underline, converted)
        return converted

    pieces: list[str] = []
    last_index = 0
    for match in CODE_SEGMENT_RE.finditer(text):
        normal_part = text[last_index:match.start()]
        if normal_part:
            pieces.append(_normalize_segment(normal_part))
        pieces.append(match.group(0))
        last_index = match.end()

    if last_index < len(text):
        pieces.append(_normalize_segment(text[last_index:]))

    return "".join(pieces)


# MarkdownV2 escape pattern (detects already-escaped text)
_ESCAPED_MARKDOWN_PATTERN = re.compile(
    r"\\[_*\[\]()~`>#+=|{}.!:-]"  # Colon included
)

# Escaped code block pattern (escaped backticks)
_ESCAPED_CODE_BLOCK_PATTERN = re.compile(
    r"(\\\`\\\`\\\`.*?\\\`\\\`\\\`|\\\`[^\\\`]*?\\\`)",
    re.DOTALL
)

def _is_already_escaped(text: str) -> bool:
    """Detect whether the text already contains MarkdownV2 escaping.

    Heuristics:
    - If escaped characters make up at least 3% of the text, treat it as escaped.
    - If there are two or more consecutive escape patterns (e.g., ``\*\*``), treat it as escaped.
    - If escaped code block markers are present, treat it as escaped.
    """
    if not text:
        return False

    # Check for escaped code block markers
    if _ESCAPED_CODE_BLOCK_PATTERN.search(text):
        return True

    matches = _ESCAPED_MARKDOWN_PATTERN.findall(text)
    if not matches:
        return False

    # Relax criteria for short text inputs
    if len(text) < 20:
        # Any escape in short text counts as already escaped to avoid double escaping
        if len(matches) >= 1:
            return True
    else:
        # Check the escape character density (threshold lowered to 3%)
        escape_count = len(matches)
        text_length = len(text)
        density = escape_count / text_length

        if density >= 0.03:  # Density above 3% counts as escaped
            return True

    # Check for consecutive escape patterns (e.g., ``\#\#\#`` or ``\*\*``)
    consecutive_pattern = re.compile(r"(?:\\[_*\[\]()~`>#+=|{}.!:-]){2,}")
    if consecutive_pattern.search(text):
        return True

    return False


def _unescape_markdown_v2(text: str) -> str:
    """Unescape MarkdownV2 special characters (e.g., ``\*`` → ``*``)."""
    # Remove all MarkdownV2 escape backslashes; match backslash + special char (including colon)
    return re.sub(r"\\([_*\[\]()~`>#+=|{}.!:-])", r"\1", text)


def _force_unescape_markdown(text: str) -> str:
    """Forcefully remove MarkdownV2 escapes while preserving code block syntax."""
    if not text:
        return text

    processed = text
    code_blocks: list[str] = []

    def _preserve_code_block(match: re.Match[str]) -> str:
        """Temporarily replace code blocks to prevent undesired unescaping inside."""
        block = match.group(0)
        if block.startswith(r"\`\`\`"):
            # Multiline code blocks: keep content, fix boundary backticks
            unescaped_block = block.replace(r"\`", "`", 6)
        else:
            # Single-line code blocks: adjust leading/trailing backticks
            unescaped_block = block.replace(r"\`", "`", 2)

        placeholder = f"__CODE_BLOCK_{len(code_blocks)}__"
        code_blocks.append(unescaped_block)
        return placeholder

    processed = _ESCAPED_CODE_BLOCK_PATTERN.sub(_preserve_code_block, processed)
    processed = _unescape_markdown_v2(processed)

    for index, block in enumerate(code_blocks):
        processed = processed.replace(f"__CODE_BLOCK_{index}__", block)

    return processed


def _unescape_if_already_escaped(text: str) -> str:
    """Heuristically clean pre-escaped text and force unescape when required."""
    if not text:
        return text
    if not _is_already_escaped(text):
        return text
    return _force_unescape_markdown(text)


def _prepare_model_payload(text: str) -> str:
    if _IS_MARKDOWN_V2:
        cleaned = _unescape_if_already_escaped(text)
        return _escape_markdown_v2(cleaned)
    if _IS_MARKDOWN:
        return _normalize_legacy_markdown(text)
    return text


def _prepare_model_payload_variants(text: str) -> tuple[str, Optional[str]]:
    """Return the primary payload and an optional fallback variant."""

    payload = _prepare_model_payload(text)
    return payload, None


def _extract_bad_request_message(exc: TelegramBadRequest) -> str:
    message = getattr(exc, "message", None)
    if not message:
        args = getattr(exc, "args", ())
        if args:
            message = str(args[0])
        else:
            message = str(exc)
    return message


def _is_markdown_parse_error(exc: TelegramBadRequest) -> bool:
    reason = _extract_bad_request_message(exc).lower()
    return any(
        hint in reason
        for hint in (
            "can't parse entities",
            "can't parse formatted text",
            "wrong entity data",
            "expected end of entity",
        )
    )


def _escape_markdown_legacy(text: str) -> str:
    escape_chars = "_[]()"

    def _escape_segment(segment: str) -> str:
        result = segment
        for ch in escape_chars:
            result = result.replace(ch, f"\\{ch}")
        return result

    pieces: list[str] = []
    last_index = 0
    for match in CODE_SEGMENT_RE.finditer(text):
        normal_part = text[last_index:match.start()]
        if normal_part:
            pieces.append(_escape_segment(normal_part))
        pieces.append(match.group(0))
        last_index = match.end()

    if last_index < len(text):
        pieces.append(_escape_segment(text[last_index:]))

    return "".join(pieces)


async def _send_with_markdown_guard(
    text: str,
    sender: Callable[[str], Awaitable[None]],
    *,
    raw_sender: Optional[Callable[[str], Awaitable[None]]] = None,
    fallback_payload: Optional[str] = None,
) -> str:
    try:
        await sender(text)
        return text
    except TelegramBadRequest as exc:
        if not _is_markdown_parse_error(exc):
            raise

        if fallback_payload and fallback_payload != text:
            try:
                await sender(fallback_payload)
                worker_log.debug(
                    "Markdown fallback succeeded with strictly escaped variant",
                    extra={"length": len(fallback_payload)},
                )
                return fallback_payload
            except TelegramBadRequest as fallback_exc:
                if not _is_markdown_parse_error(fallback_exc):
                    raise
                exc = fallback_exc

        sanitized: Optional[str]
        if _IS_MARKDOWN_V2:
            sanitized = _escape_markdown_v2(text)
            # Preserve native code block markers; they are valid Markdown syntax
            if "```" in text:
                sanitized = sanitized.replace(r"\`\`\`", "```")
            if "`" in text:
                sanitized = sanitized.replace(r"\`", "`")
        elif _IS_MARKDOWN:
            sanitized = _escape_markdown_legacy(text)
        else:
            sanitized = None

        if sanitized and sanitized != text:
            worker_log.debug(
                "Markdown parsing failed; retrying with escaped payload",
                extra={"length": len(text)},
            )
            try:
                await sender(sanitized)
                return sanitized
            except TelegramBadRequest as exc_sanitized:
                if not _is_markdown_parse_error(exc_sanitized):
                    raise

        if raw_sender is None:
            raise

        worker_log.warning(
            "Markdown parsing continues to fail; sending as plain text",
            extra={"length": len(text)},
        )
        await raw_sender(text)
        return text


async def _notify_send_failure_message(chat_id: int) -> None:
    """Notify the user about network issues while throttling repeated alerts."""

    now = time.monotonic()
    last_notice = CHAT_FAILURE_NOTICES.get(chat_id)
    if last_notice is not None and (now - last_notice) < SEND_FAILURE_NOTICE_COOLDOWN:
        return

    notice = "Network issues detected while sending the response. Retrying shortly; please wait."
    bot = current_bot()

    try:
        async def _send_notice() -> None:
            async def _do() -> None:
                await bot.send_message(chat_id=chat_id, text=notice, parse_mode=None)

            await _send_with_retry(_do)

        await _send_notice()
    except (TelegramNetworkError, TelegramRetryAfter, TelegramBadRequest):
        CHAT_FAILURE_NOTICES[chat_id] = now
        return

    CHAT_FAILURE_NOTICES[chat_id] = now


def _prepend_completion_header(text: str) -> str:
    """Prefix the model output with a completion header if missing."""

    if text.startswith(MODEL_COMPLETION_PREFIX):
        return text
    if text:
        return f"{MODEL_COMPLETION_PREFIX}\n\n{text}"
    return MODEL_COMPLETION_PREFIX


def _strip_completion_header(text: str) -> str:
    """Remove the completion header that _prepend_completion_header() adds."""

    if not text:
        return ""
    prefix = MODEL_COMPLETION_PREFIX
    if not text.startswith(prefix):
        return text
    remainder = text[len(prefix) :]
    # _prepend_completion_header inserts two newlines between the prefix and body
    return remainder.lstrip("\n")


def _hash_delivery_variants(text: str) -> set[str]:
    """Return sha256 hashes for the original payload and its header-stripped variant."""

    payload = _prepare_model_payload(text)
    hashes = {
        hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest(),
    }
    stripped = _strip_completion_header(text)
    if stripped != text:
        stripped_payload = _prepare_model_payload(stripped)
        hashes.add(hashlib.sha256(stripped_payload.encode("utf-8", errors="ignore")).hexdigest())
    return hashes

# pylint: disable=too-many-locals
async def reply_large_text(
    chat_id: int,
    text: str,
    *,
    parse_mode: Optional[str] = None,
    preformatted: bool = False,
) -> str:
    """Send potentially long text to a chat, degrading to an attachment if required.

    :param chat_id: Telegram chat identifier.
    :param text: Content to be delivered.
    :param parse_mode: Explicit parse_mode override; defaults to global setting.
    :param preformatted: When True, assume ``text`` already matches the parse_mode.
    """
    bot = current_bot()
    parse_mode_value = parse_mode if parse_mode is not None else _parse_mode_value()
    if preformatted:
        prepared = text
        fallback_payload = None
    else:
        prepared, fallback_payload = _prepare_model_payload_variants(text)

    async def _send_formatted_message(payload: str) -> None:
        await bot.send_message(
            chat_id=chat_id,
            text=payload,
            parse_mode=parse_mode_value,
        )

    async def _send_raw_message(payload: str) -> None:
        await bot.send_message(chat_id=chat_id, text=payload, parse_mode=None)

    if len(prepared) <= TELEGRAM_MESSAGE_LIMIT:
        delivered = await _send_with_markdown_guard(
            prepared,
            _send_formatted_message,
            raw_sender=_send_raw_message,
            fallback_payload=fallback_payload,
        )

        worker_log.info(
            "Delivered single message successfully",
            extra={
                "chat": chat_id,
                "mode": "single",
                "length": str(len(delivered)),
            },
        )
        return delivered

    attachment_name = f"model-response-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    summary_text = (
        f"{MODEL_COMPLETION_PREFIX}\n\n"
        f"The content is lengthy. An appendix `{attachment_name}` has been generated as an attachment. "
        "Please download to view the full response."
    )

    summary_prepared, summary_fallback = _prepare_model_payload_variants(summary_text)
    delivered_summary = await _send_with_markdown_guard(
        summary_prepared,
        _send_formatted_message,
        raw_sender=_send_raw_message,
        fallback_payload=summary_fallback,
    )

    document = BufferedInputFile(text.encode("utf-8"), filename=attachment_name)

    async def _send_document() -> None:
        await bot.send_document(chat_id=chat_id, document=document)

    await _send_with_retry(_send_document)

    worker_log.info(
        "Delivered text as attachment due to length",
        extra={
            "chat": chat_id,
            "mode": "attachment",
            "length": str(len(prepared)),
            "attachment_name": attachment_name,
        },
    )

    return delivered_summary

def run_subprocess_capture(cmd: str, input_text: str = "") -> Tuple[int, str]:
    # Run the CLI synchronously, piping prompt text via stdin and capturing stdout+stderr
    p = subprocess.Popen(
        shlex.split(cmd),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True
    )
    out, _ = p.communicate(input=input_text, timeout=None)
    return p.returncode, out

def tmux_bin() -> str:
    return subprocess.check_output("command -v tmux", shell=True, text=True).strip()


def _tmux_cmd(tmux: str, *args: str) -> list[str]:
    return [tmux, "-u", *args]


def tmux_send_line(session: str, line: str):
    tmux = tmux_bin()
    subprocess.check_call(_tmux_cmd(tmux, "has-session", "-t", session))
    # Send ESC once to exit potential Codex menus or input modes
    subprocess.call(
        _tmux_cmd(tmux, "send-keys", "-t", session, "Escape"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.05)
    try:
        pane_in_mode = subprocess.check_output(
            _tmux_cmd(tmux, "display-message", "-p", "-t", session, "#{pane_in_mode}"),
            text=True,
        ).strip()
    except subprocess.CalledProcessError:
        pane_in_mode = "0"
    if pane_in_mode == "1":
        subprocess.check_call(_tmux_cmd(tmux, "send-keys", "-t", session, "-X", "cancel"))
        time.sleep(0.05)
    chunks = line.split("\n")
    for idx, chunk in enumerate(chunks):
        if chunk:
            subprocess.check_call(_tmux_cmd(tmux, "send-keys", "-t", session, "--", chunk))
        if idx < len(chunks) - 1:
            subprocess.check_call(_tmux_cmd(tmux, "send-keys", "-t", session, "C-j"))
            time.sleep(0.05)
    is_claudecode = _is_claudecode_model()
    time.sleep(0.2 if is_claudecode else 0.05)
    subprocess.check_call(_tmux_cmd(tmux, "send-keys", "-t", session, "C-m"))
    if is_claudecode:
        # ClaudeCode occasionally ignores the first Enter; send an extra one to enqueue input
        time.sleep(0.1)
        subprocess.check_call(_tmux_cmd(tmux, "send-keys", "-t", session, "C-m"))


def resolve_path(path: Path | str) -> Path:
    if isinstance(path, Path):
        return path.expanduser()
    return Path(os.path.expanduser(os.path.expandvars(path))).expanduser()


async def _reply_to_chat(
    chat_id: int,
    text: str,
    *,
    reply_to: Optional[Message],
    disable_notification: bool = False,
    parse_mode: Optional[str] = None,
    reply_markup: Optional[Any] = None,
) -> Optional[Message]:
    """Send a message to a chat, reusing the reply context when available."""

    if reply_to is not None:
        return await reply_to.answer(
            text,
            parse_mode=parse_mode,
            disable_notification=disable_notification,
            reply_markup=reply_markup,
        )

    bot = current_bot()

    async def _send() -> None:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            disable_notification=disable_notification,
            reply_markup=reply_markup,
        )

    try:
        await _send_with_retry(_send)
    except TelegramBadRequest:
        raise
    return None


async def _send_session_ack(
    chat_id: int,
    session_path: Path,
    *,
    reply_to: Optional[Message],
) -> None:
    model_label = (ACTIVE_MODEL or "Model").strip() or "Model"
    session_id = session_path.stem if session_path else "unknown"
    prompt_message = (
        f"💭 {model_label} is Thinking... Listening for model output...\n"
        f"sessionId : {session_id}"
    )
    ack_message = await _reply_to_chat(
        chat_id,
        prompt_message,
        reply_to=reply_to,
        disable_notification=True,
    )
    if ENABLE_PLAN_PROGRESS:
        CHAT_PLAN_MESSAGES.pop(chat_id, None)
        CHAT_PLAN_TEXT.pop(chat_id, None)
        CHAT_PLAN_COMPLETION.pop(chat_id, None)
    worker_log.info(
        "[session-map] chat=%s ack sent",
        chat_id,
        extra={
            **_session_extra(path=session_path),
            "ack_text": prompt_message,
        },
    )


async def _dispatch_prompt_to_model(
    chat_id: int,
    prompt: str,
    *,
    reply_to: Optional[Message],
    ack_immediately: bool = True,
) -> tuple[bool, Optional[Path]]:
    """Handle session binding, acknowledgement, and watcher setup after pushing a prompt."""

    if _is_menu_control_message(prompt):
        worker_log.warning(
            "Rejected menu control prompt before tmux dispatch",
            extra={
                "chat": chat_id,
                **_session_extra(),
                "token": _normalize_choice_token(prompt),
            },
        )
        await _reply_to_chat(
            chat_id,
            "Skip/Cancel inputs are ignored outside interactive menus. Please send an actual prompt.",
            reply_to=reply_to,
        )
        return False, None

    prev_watcher = CHAT_WATCHERS.pop(chat_id, None)
    if prev_watcher is not None:
        if not prev_watcher.done():
            prev_watcher.cancel()
            worker_log.info(
                "[session-map] chat=%s cancel previous watcher",
                chat_id,
                extra=_session_extra(),
            )
            try:
                await prev_watcher
            except asyncio.CancelledError:
                worker_log.info(
                    "[session-map] chat=%s previous watcher cancelled",
                    chat_id,
                    extra=_session_extra(),
                )
            except Exception as exc:  # noqa: BLE001
                worker_log.warning(
                    "[session-map] chat=%s previous watcher exited with error: %s",
                    chat_id,
                    exc,
                    extra=_session_extra(),
                )
        else:
            worker_log.debug(
                "[session-map] chat=%s previous watcher already done",
                chat_id,
                extra=_session_extra(),
            )
    session_path: Optional[Path] = None
    existing = CHAT_SESSION_MAP.get(chat_id)
    if existing:
        candidate = Path(existing)
        if candidate.exists():
            session_path = candidate
        else:
            CHAT_SESSION_MAP.pop(chat_id, None)
            _reset_delivered_hashes(chat_id, existing)
            _reset_delivered_offsets(chat_id, existing)
    else:
        _reset_delivered_hashes(chat_id)
        _reset_delivered_offsets(chat_id)

    pointer_path: Optional[Path] = None
    if CODEX_SESSION_FILE_PATH:
        pointer_path = resolve_path(CODEX_SESSION_FILE_PATH)

    lock_session = _read_session_lock_path()
    lock_required = _is_session_lock_enforced()
    if lock_session is not None:
        if session_path is None:
            worker_log.info(
                "[session-map] chat=%s use lock session %s",
                chat_id,
                lock_session,
                extra=_session_extra(path=lock_session),
            )
        elif session_path != lock_session:
            worker_log.info(
                "[session-map] chat=%s override session with lock %s",
                chat_id,
                lock_session,
                extra=_session_extra(path=lock_session),
            )
        _sync_pointer_with_lock(pointer_path, lock_session)
        session_path = lock_session
    else:
        if session_path is not None:
            worker_log.info(
                "[session-map] chat=%s reuse session %s",
                chat_id,
                session_path,
                extra=_session_extra(path=session_path),
            )
        elif lock_required:
            await _reply_to_chat(
                chat_id,
                _session_lock_missing_message(),
                reply_to=reply_to,
            )
            worker_log.error(
                "[session-lock] Session lock required but missing",
                extra={"chat": chat_id, "lock_file": SESSION_LOCK_FILE_PATH or "-"},
            )
            return False, None
        else:
            if pointer_path is not None and session_path is None:
                session_path = _read_pointer_path(pointer_path)
                if session_path is not None:
                    worker_log.info(
                        "[session-map] chat=%s pointer -> %s",
                        chat_id,
                        session_path,
                        extra=_session_extra(path=session_path),
                    )

            target_cwd = CODEX_WORKDIR if CODEX_WORKDIR else None
            if pointer_path is not None:
                current_cwd = _read_session_meta_cwd(session_path) if session_path else None
                if session_path is None or (target_cwd and current_cwd != target_cwd):
                    latest = _find_latest_rollout_for_cwd(pointer_path, target_cwd)
                    if latest is not None:
                        try:
                            _store_session_offset(str(latest), latest.stat().st_size)
                        except FileNotFoundError:
                            _store_session_offset(str(latest), 0)
                        _update_pointer(pointer_path, latest)
                        session_path = latest
                        worker_log.info(
                            "[session-map] chat=%s (lock disabled) switch to cwd-matched %s",
                            chat_id,
                            session_path,
                            extra=_session_extra(path=session_path),
                        )
                if _is_claudecode_model():
                    fallback = _find_latest_claudecode_rollout(pointer_path)
                    if fallback is not None and fallback != session_path:
                        _update_pointer(pointer_path, fallback)
                        session_path = fallback
                        worker_log.info(
                            "[session-map] chat=%s (lock disabled) fallback to ClaudeCode session %s",
                            chat_id,
                            session_path,
                            extra=_session_extra(path=session_path),
                        )

    needs_session_wait = session_path is None
    if needs_session_wait and pointer_path is None:
        await _reply_to_chat(
            chat_id,
            f"No {MODEL_DISPLAY_LABEL} session log detected yet. Please try again shortly.",
            reply_to=reply_to,
        )
        return False, None

    try:
        tmux_send_line(TMUX_SESSION, prompt)
    except subprocess.CalledProcessError as exc:
        await _reply_to_chat(
            chat_id,
            f"tmux error: {exc}",
            reply_to=reply_to,
        )
        return False, None

    if needs_session_wait:
        session_path = await _await_session_path(pointer_path, target_cwd)
        if session_path is None and pointer_path is not None and _is_claudecode_model():
            session_path = _find_latest_claudecode_rollout(pointer_path)
        if session_path is None:
            await _reply_to_chat(
                chat_id,
                f"No {MODEL_DISPLAY_LABEL} session log detected yet. Please try again shortly.",
                reply_to=reply_to,
            )
            return False, None
        if pointer_path is not None:
            _update_pointer(pointer_path, session_path)
            if _is_claudecode_model():
                worker_log.info(
                    "[session-map] chat=%s update ClaudeCode pointer -> %s",
                    chat_id,
                    session_path,
                    extra=_session_extra(path=session_path),
                )
        worker_log.info(
            "[session-map] chat=%s bind fresh session %s",
            chat_id,
            session_path,
            extra=_session_extra(path=session_path),
        )

    assert session_path is not None
    session_key = str(session_path)
    if session_key not in SESSION_OFFSETS:
        _init_session_offset(session_path)
        worker_log.info(
            "[session-map] init offset for %s -> %s",
            session_key,
            SESSION_OFFSETS[session_key],
            extra=_session_extra(key=session_key),
        )

    CHAT_SESSION_MAP[chat_id] = session_key
    _clear_last_message(chat_id)
    _reset_compact_tracking(chat_id)
    CHAT_FAILURE_NOTICES.pop(chat_id, None)
    worker_log.info(
        "[session-map] chat=%s bound to %s",
        chat_id,
        session_key,
        extra=_session_extra(key=session_key),
    )

    if ack_immediately:
        await _send_session_ack(chat_id, session_path, reply_to=reply_to)

    if SESSION_POLL_TIMEOUT > 0:
        start_time = time.monotonic()
        while time.monotonic() - start_time < SESSION_POLL_TIMEOUT:
            delivered = await _deliver_pending_messages(chat_id, session_path)
            if delivered:
                return True, session_path
            await asyncio.sleep(0.3)

    # Interrupt any previous long-polling task if it exists
    await _interrupt_long_poll(chat_id)

    watcher_task = asyncio.create_task(
        _watch_and_notify(
            chat_id,
            session_path,
            max_wait=WATCH_MAX_WAIT,
            interval=WATCH_INTERVAL,
        )
    )
    CHAT_WATCHERS[chat_id] = watcher_task
    return True, session_path


async def _push_task_to_model(
    task: TaskRecord,
    *,
    chat_id: int,
    reply_to: Optional[Message],
    supplement: Optional[str],
    actor: Optional[str],
    is_bug_report: bool = False,
) -> tuple[bool, str, Optional[Path]]:
    """Push task details to the model with optional supplemental description.

    Args:
        task: Task record being pushed.
        chat_id: Telegram chat identifier.
        reply_to: Original message to reply to, if any.
        supplement: Additional description to append.
        actor: Human operator triggering the push.
        is_bug_report: When True, treat the push as a defect report.
    """

    history_text, history_count = await _build_history_context_for_model(task.id)
    notes = await TASK_SERVICE.list_notes(task.id)
    prompt = _build_model_push_payload(
        task,
        supplement=supplement,
        history=history_text,
        notes=notes,
        is_bug_report=is_bug_report,
    )
    success, session_path = await _dispatch_prompt_to_model(
        chat_id,
        prompt,
        reply_to=reply_to,
        ack_immediately=False,
    )
    has_supplement = bool((supplement or "").strip())
    result_status = "success" if success else "failed"
    payload: dict[str, Any] = {
        "result": result_status,
        "has_supplement": has_supplement,
        "history_items": history_count,
        "history_chars": len(history_text),
        "prompt_chars": len(prompt),
        "model": ACTIVE_MODEL or "",
    }
    if has_supplement:
        payload["supplement"] = supplement or ""

    await _log_task_action(
        task.id,
        action="push_model",
        actor=actor,
        new_value=(supplement or "") if has_supplement else None,
        payload=payload,
    )
    if not success:
        worker_log.warning(
            "Failed to push task to the model: no Codex session established",
            extra={"task_id": task.id},
        )
    else:
        worker_log.info(
            "Task description pushed to the model",
            extra={
                "task_id": task.id,
                "status": task.status,
                "has_supplement": str(has_supplement),
            },
        )
    return success, prompt, session_path


def _extract_executable(cmd: str) -> Optional[str]:
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return None
    if not parts:
        return None
    return parts[0]


def _detect_environment_issues() -> tuple[list[str], Optional[Path]]:
    issues: list[str] = []
    workdir_raw = (os.environ.get("MODEL_WORKDIR") or CODEX_WORKDIR or "").strip()
    workdir_path: Optional[Path] = None
    if not workdir_raw:
        issues.append("MODEL_WORKDIR is not configured")
    else:
        candidate = resolve_path(workdir_raw)
        if not candidate.exists():
            issues.append(f"Working directory does not exist: {workdir_raw}")
        elif not candidate.is_dir():
            issues.append(f"Working directory is not a folder: {workdir_raw}")
        else:
            workdir_path = candidate

    try:
        tmux_bin()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        issues.append("tmux not detected (install via `brew install tmux`)")

    model_cmd = os.environ.get("MODEL_CMD")
    if not model_cmd and (ACTIVE_MODEL or "").lower() == "codex":
        model_cmd = os.environ.get("CODEX_CMD") or "codex"
    if model_cmd:
        executable = _extract_executable(model_cmd)
        if executable and shutil.which(executable) is None:
            issues.append(f"Model CLI executable not found: {executable}")

    return issues, workdir_path


def _format_env_issue_message() -> str:
    if not ENV_ISSUES:
        return ""
    bullet_lines = []
    for issue in ENV_ISSUES:
        if "\n" in issue:
            first, *rest = issue.splitlines()
            bullet_lines.append(f"- {first}")
            bullet_lines.extend([f"  {line}" for line in rest])
        else:
            bullet_lines.append(f"- {issue}")
    return "The current worker environment has issues; resolve them before retrying:\n" + "\n".join(bullet_lines)


ENV_ISSUES, PRIMARY_WORKDIR = _detect_environment_issues()
if ENV_ISSUES:
    worker_log.error("Environment self-check failed: %s", "; ".join(ENV_ISSUES))

ROOT_DIR_ENV = os.environ.get("ROOT_DIR")
ROOT_DIR_PATH = Path(ROOT_DIR_ENV).expanduser() if ROOT_DIR_ENV else Path(__file__).resolve().parent
DATA_ROOT_DEFAULT = CONFIG_ROOT_PATH / "data"
DATA_ROOT = Path(os.environ.get("TASKS_DATA_ROOT", str(DATA_ROOT_DEFAULT))).expanduser()
DATA_ROOT.mkdir(parents=True, exist_ok=True)
PROJECT_SLUG = (PROJECT_NAME or "default").replace("/", "-") or "default"
TASK_DB_PATH = DATA_ROOT / f"{PROJECT_SLUG}.db"
TASK_SERVICE = TaskService(TASK_DB_PATH, PROJECT_SLUG)
COMMAND_DB_PATH = DATA_ROOT / f"{PROJECT_SLUG}_commands.db"
COMMAND_PRESET_SERVICE = CommandPresetService(COMMAND_DB_PATH, PROJECT_SLUG)
GLOBAL_COMMAND_DB_PATH = DATA_ROOT / "master_commands.db"
GLOBAL_COMMAND_PRESET_SERVICE = CommandPresetService(GLOBAL_COMMAND_DB_PATH, GLOBAL_COMMAND_PROJECT_SLUG)
COMMAND_LIST_PAGE_SIZE = max(1, min(_env_int("COMMAND_CENTER_PAGE_SIZE", 5), 20))
COMMAND_LIST_CALLBACK = "cmd:list"
COMMAND_DETAIL_CALLBACK = "cmd:detail"
COMMAND_CREATE_CALLBACK = "cmd:create"
COMMAND_EDIT_CALLBACK = "cmd:edit"
COMMAND_RUN_CALLBACK = "cmd:run"
COMMAND_RUN_EXECUTE_CALLBACK = "cmd:run_exec"
COMMAND_DELETE_CALLBACK = "cmd:delete"
COMMAND_DELETE_EXECUTE_CALLBACK = "cmd:delete_exec"
COMMAND_TOGGLE_CONFIRM_CALLBACK = "cmd:toggle_confirm"
COMMAND_SCOPE_TOKENS = {
    COMMAND_SCOPE_GLOBAL: "g",
    COMMAND_SCOPE_PROJECT: "p",
}
COMMAND_SCOPE_ICONS = {
    COMMAND_SCOPE_GLOBAL: "🌐",
    COMMAND_SCOPE_PROJECT: "📁",
}
COMMAND_SCOPE_LABELS = {
    COMMAND_SCOPE_GLOBAL: "Master（全局）",
    COMMAND_SCOPE_PROJECT: "当前项目",
}


def _command_scope_token(scope: str) -> str:
    """Return the compact token used in callback data for the given scope."""

    return COMMAND_SCOPE_TOKENS.get(scope, COMMAND_SCOPE_PROJECT)


def _command_scope_from_token(token: str) -> str:
    """Resolve callback data scope tokens back to logical scope strings."""

    for scope, scope_token in COMMAND_SCOPE_TOKENS.items():
        if token == scope_token:
            return scope
    return COMMAND_SCOPE_PROJECT


def _command_scope_icon(scope: str) -> str:
    """Return an emoji prefix describing the command ownership."""

    return COMMAND_SCOPE_ICONS.get(scope, "📁")


def _command_scope_label(scope: str) -> str:
    """Return a human-readable label for the given scope."""

    return COMMAND_SCOPE_LABELS.get(scope, "当前项目")


def _command_scope_editable(scope: str) -> bool:
    """Global commands are read-only from worker sessions."""

    return scope == COMMAND_SCOPE_PROJECT


def _command_service_for_scope(scope: str) -> CommandPresetService:
    """Map command scope to the backing persistence service."""

    if scope == COMMAND_SCOPE_GLOBAL:
        return GLOBAL_COMMAND_PRESET_SERVICE
    return COMMAND_PRESET_SERVICE


async def _collect_scoped_command_presets() -> list[ScopedCommandPreset]:
    """Return all command presets across global + project scopes sorted by update time."""

    global_presets = await GLOBAL_COMMAND_PRESET_SERVICE.list_all_presets()
    project_presets = await COMMAND_PRESET_SERVICE.list_all_presets()
    combined: list[ScopedCommandPreset] = []
    combined.extend(ScopedCommandPreset(COMMAND_SCOPE_GLOBAL, record) for record in global_presets)
    combined.extend(ScopedCommandPreset(COMMAND_SCOPE_PROJECT, record) for record in project_presets)
    combined.sort(
        key=lambda item: (item.record.updated_at, item.record.id),
        reverse=True,
    )
    return combined


async def _command_paged_presets(page: int, page_size: int) -> tuple[list[ScopedCommandPreset], int, int]:
    """Return a page of scoped command presets along with total stats."""

    combined = await _collect_scoped_command_presets()
    total = len(combined)
    safe_page = max(page, 1)
    total_pages = max(1, math.ceil(total / page_size)) if total else 1
    start = (safe_page - 1) * page_size
    end = start + page_size
    page_items = combined[start:end] if start < total else []
    return page_items, total, total_pages


async def _get_scoped_command(scope: str, preset_id: int) -> Optional[CommandPresetRecord]:
    """Fetch a command preset record from the requested scope."""

    service = _command_service_for_scope(scope)
    return await service.get_preset(preset_id)

ATTACHMENT_STORAGE_ROOT = (DATA_ROOT / "telegram").expanduser()
ATTACHMENT_STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
_ATTACHMENT_TOTAL_MB = max(_env_int("TELEGRAM_ATTACHMENT_MAX_TOTAL_MB", 512), 16)
ATTACHMENT_TOTAL_LIMIT_BYTES = _ATTACHMENT_TOTAL_MB * 1024 * 1024
MEDIA_GROUP_AGGREGATION_DELAY = max(_env_float("TELEGRAM_MEDIA_GROUP_DELAY", 0.8), 0.1)


@dataclass
class TelegramSavedAttachment:
    """Record metadata about a saved attachment to guide model consumption."""

    kind: str
    display_name: str
    mime_type: str
    absolute_path: Path
    relative_path: str


@dataclass
class PendingMediaGroupState:
    """Temporary cache for aggregating Telegram media group messages."""

    chat_id: int
    origin_message: Message
    attachment_dir: Path
    attachments: list[TelegramSavedAttachment]
    captions: list[str]
    finalize_task: Optional[asyncio.Task] = None


MEDIA_GROUP_STATE: dict[str, PendingMediaGroupState] = {}
MEDIA_GROUP_LOCK = asyncio.Lock()

ATTACHMENT_USAGE_HINT = (
    "Review attachments as needed: use Codex `view_image` or ClaudeCode file references for images; "
    "open text/log files via @<path>; request additional handling if required."
)

_FS_SAFE_PATTERN = re.compile(r"[^A-Za-z0-9._-]")


def _attachment_directory_prefix_for_display(relative_path: str) -> Optional[str]:
    """Derive a directory prefix for display so the model can locate attachments."""

    path_str = (relative_path or "").strip()
    if not path_str:
        return None

    try:
        parent = Path(path_str).parent
    except Exception:
        return None

    parent_str = parent.as_posix()
    if parent_str in {"", "."}:
        if path_str.startswith("./"):
            parent_str = "./"
        elif path_str.startswith("/"):
            parent_str = "/"
        else:
            return None
    else:
        if path_str.startswith("./") and not parent_str.startswith(("./", "/")):
            parent_str = f"./{parent_str}"

    if not parent_str.endswith("/"):
        parent_str = f"{parent_str}/"

    return parent_str


def _sanitize_fs_component(value: str, fallback: str) -> str:
    """Clean path components to remove special characters and prevent traversal."""

    stripped = (value or "").strip()
    cleaned = _FS_SAFE_PATTERN.sub("_", stripped)
    cleaned = cleaned.strip("._")
    return cleaned or fallback


def _format_relative_path(path: Path) -> str:
    """Convert an absolute path into a model-friendly relative representation."""

    try:
        rel = path.relative_to(ROOT_DIR_PATH)
        rel_str = rel.as_posix()
        if not rel_str.startswith("."):
            return f"./{rel_str}"
        return rel_str
    except ValueError:
        return path.resolve().as_posix()


def _directory_size(path: Path) -> int:
    """Calculate the total size of a directory in bytes."""

    total = 0
    if not path.exists():
        return 0
    for entry in path.rglob("*"):
        try:
            if entry.is_file():
                total += entry.stat().st_size
        except FileNotFoundError:
            continue
    return total


def _cleanup_attachment_storage() -> None:
    """Keep attachment storage within configured limits to avoid disk exhaustion."""

    if ATTACHMENT_TOTAL_LIMIT_BYTES <= 0:
        return
    total = _directory_size(ATTACHMENT_STORAGE_ROOT)
    if total <= ATTACHMENT_TOTAL_LIMIT_BYTES:
        return
    candidates = sorted(
        (p for p in ATTACHMENT_STORAGE_ROOT.iterdir() if p.is_dir()),
        key=lambda item: item.stat().st_mtime,
    )
    for folder in candidates:
        try:
            shutil.rmtree(folder, ignore_errors=True)
        except Exception as exc:  # noqa: BLE001
            worker_log.warning(
                "Failed to clean old attachment directory: %s",
                exc,
                extra=_session_extra(path=folder),
            )
        if _directory_size(ATTACHMENT_STORAGE_ROOT) <= ATTACHMENT_TOTAL_LIMIT_BYTES:
            break


def _guess_extension(mime_type: Optional[str], fallback: str = ".bin") -> str:
    """Infer a file extension from the MIME type."""

    if mime_type:
        guessed = mimetypes.guess_extension(mime_type, strict=False)
        if guessed:
            return guessed
    return fallback


def _attachment_dir_for_message(message: Message, media_group_id: Optional[str] = None) -> Path:
    """Create an attachment directory based on project slug and message date."""

    # media_group_id retained for legacy callers; currently archives by date only.
    _ = media_group_id

    # Prefer the project slug, fall back to the bot username or a generic prefix.
    project_identifier = PROJECT_SLUG or ""
    sanitized_project = _sanitize_fs_component(project_identifier, "project")
    if sanitized_project == "project":
        bot_username = getattr(message.bot, "username", None)
        sanitized_project = _sanitize_fs_component(bot_username or "bot", "bot")

    # Format the message timestamp (UTC) as a date to group attachments per day.
    event_time = message.date or datetime.now(UTC)
    try:
        event_time = event_time.astimezone(UTC)
    except Exception:
        event_time = datetime.now(UTC)
    date_component = event_time.strftime("%Y-%m-%d")

    target = ATTACHMENT_STORAGE_ROOT / sanitized_project / date_component
    target.mkdir(parents=True, exist_ok=True)
    return target


async def _download_telegram_file(
    message: Message,
    *,
    file_id: str,
    file_name_hint: str,
    mime_type: Optional[str],
    target_dir: Path,
) -> Path:
    """Download a file from Telegram and return the local path."""

    bot = message.bot or current_bot()
    telegram_file = await bot.get_file(file_id)
    stem = _sanitize_fs_component(Path(file_name_hint).stem, "file")
    extension = Path(file_name_hint).suffix or _guess_extension(mime_type, ".bin")
    if not extension.startswith("."):
        extension = f".{extension}"
    filename = f"{stem}{extension}"
    destination = target_dir / filename
    counter = 1
    while destination.exists():
        destination = target_dir / f"{stem}_{counter}{extension}"
        counter += 1
    await bot.download_file(telegram_file.file_path, destination=destination)
    return destination


async def _collect_saved_attachments(message: Message, target_dir: Path) -> list[TelegramSavedAttachment]:
    """Download all attachments from a message and return their saved metadata."""

    saved: list[TelegramSavedAttachment] = []

    if message.photo:
        photo = message.photo[-1]
        path = await _download_telegram_file(
            message,
            file_id=photo.file_id,
            file_name_hint=f"photo_{photo.file_unique_id}.jpg",
            mime_type="image/jpeg",
            target_dir=target_dir,
        )
        saved.append(
            TelegramSavedAttachment(
                kind="photo",
                display_name=path.name,
                mime_type="image/jpeg",
                absolute_path=path,
                relative_path=_format_relative_path(path),
            )
        )

    document = message.document
    if document:
        file_name = document.file_name or f"document_{document.file_unique_id}"
        path = await _download_telegram_file(
            message,
            file_id=document.file_id,
            file_name_hint=file_name,
            mime_type=document.mime_type or "application/octet-stream",
            target_dir=target_dir,
        )
        saved.append(
            TelegramSavedAttachment(
                kind="document",
                display_name=file_name,
                mime_type=document.mime_type or "application/octet-stream",
                absolute_path=path,
                relative_path=_format_relative_path(path),
            )
        )

    video = message.video
    if video:
        file_name = video.file_name or f"video_{video.file_unique_id}"
        path = await _download_telegram_file(
            message,
            file_id=video.file_id,
            file_name_hint=file_name,
            mime_type=video.mime_type or "video/mp4",
            target_dir=target_dir,
        )
        saved.append(
            TelegramSavedAttachment(
                kind="video",
                display_name=file_name,
                mime_type=video.mime_type or "video/mp4",
                absolute_path=path,
                relative_path=_format_relative_path(path),
            )
        )

    audio = message.audio
    if audio:
        file_name = audio.file_name or f"audio_{audio.file_unique_id}"
        path = await _download_telegram_file(
            message,
            file_id=audio.file_id,
            file_name_hint=file_name,
            mime_type=audio.mime_type or "audio/mpeg",
            target_dir=target_dir,
        )
        saved.append(
            TelegramSavedAttachment(
                kind="audio",
                display_name=file_name,
                mime_type=audio.mime_type or "audio/mpeg",
                absolute_path=path,
                relative_path=_format_relative_path(path),
            )
        )

    voice = message.voice
    if voice:
        file_name = f"voice_{voice.file_unique_id}.ogg"
        path = await _download_telegram_file(
            message,
            file_id=voice.file_id,
            file_name_hint=file_name,
            mime_type=voice.mime_type or "audio/ogg",
            target_dir=target_dir,
        )
        saved.append(
            TelegramSavedAttachment(
                kind="voice",
                display_name=file_name,
                mime_type=voice.mime_type or "audio/ogg",
                absolute_path=path,
                relative_path=_format_relative_path(path),
            )
        )

    animation = message.animation
    if animation:
        file_name = animation.file_name or f"animation_{animation.file_unique_id}"
        path = await _download_telegram_file(
            message,
            file_id=animation.file_id,
            file_name_hint=file_name,
            mime_type=animation.mime_type or "video/mp4",
            target_dir=target_dir,
        )
        saved.append(
            TelegramSavedAttachment(
                kind="animation",
                display_name=file_name,
                mime_type=animation.mime_type or "video/mp4",
                absolute_path=path,
                relative_path=_format_relative_path(path),
            )
        )

    video_note = message.video_note
    if video_note:
        file_name = f"video_note_{video_note.file_unique_id}.mp4"
        path = await _download_telegram_file(
            message,
            file_id=video_note.file_id,
            file_name_hint=file_name,
            mime_type=video_note.mime_type or "video/mp4",
            target_dir=target_dir,
        )
        saved.append(
            TelegramSavedAttachment(
                kind="video_note",
                display_name=file_name,
                mime_type=video_note.mime_type or "video/mp4",
                absolute_path=path,
                relative_path=_format_relative_path(path),
            )
        )

    if saved:
        _cleanup_attachment_storage()
    return saved


def _build_prompt_with_attachments(
    text_part: Optional[str],
    attachments: Sequence[TelegramSavedAttachment],
) -> str:
    """Combine text with attachment descriptions into a model-friendly prompt."""

    sections: list[str] = []
    base_text = (text_part or "").strip()
    if base_text:
        sections.append(base_text)
    if attachments:
        directory_hint: Optional[str] = None
        for item in attachments:
            directory_hint = _attachment_directory_prefix_for_display(item.relative_path)
            if directory_hint:
                break
        if directory_hint:
            lines = [f"Attachment list (files located under project workspace {directory_hint}):"]
        else:
            lines = ["Attachment list (files reside within the project workspace):"]
        for idx, item in enumerate(attachments, 1):
            lines.append(
                f"{idx}. {item.display_name} ({item.mime_type}) → {item.relative_path}"
            )
        lines.append("")
        lines.append(ATTACHMENT_USAGE_HINT)
        sections.append("\n".join(lines))
    if not sections:
        fallback = [
            "Received a message containing only attachments with no additional description.",
            "Review the listed attachments directly and provide observations or conclusions.",
        ]
        sections.append("\n".join(fallback))
    return "\n\n".join(sections).strip()


async def _finalize_media_group_after_delay(media_group_id: str) -> None:
    """Aggregate media group messages after a short delay to ensure all items arrive."""

    try:
        await asyncio.sleep(MEDIA_GROUP_AGGREGATION_DELAY)
    except asyncio.CancelledError:
        return

    async with MEDIA_GROUP_LOCK:
        state = MEDIA_GROUP_STATE.pop(media_group_id, None)

    if state is None:
        return

    text_block = "\n".join(state.captions).strip()
    prompt = _build_prompt_with_attachments(text_block, state.attachments)
    try:
        await _handle_prompt_dispatch(state.origin_message, prompt)
    except Exception as exc:  # noqa: BLE001
        worker_log.exception(
            "Failed to push media group message to model: %s",
            exc,
            extra=_session_extra(media_group=media_group_id),
        )


async def _enqueue_media_group_message(message: Message, text_part: Optional[str]) -> None:
    """Accumulate messages in a media group and dispatch them after a short delay."""

    media_group_id = message.media_group_id
    if not media_group_id:
        return

    async with MEDIA_GROUP_LOCK:
        state = MEDIA_GROUP_STATE.get(media_group_id)
        if state is None:
            attachment_dir = _attachment_dir_for_message(message, media_group_id=media_group_id)
            state = PendingMediaGroupState(
                chat_id=message.chat.id,
                origin_message=message,
                attachment_dir=attachment_dir,
                attachments=[],
                captions=[],
            )
            MEDIA_GROUP_STATE[media_group_id] = state
        else:
            attachment_dir = state.attachment_dir

    attachments = await _collect_saved_attachments(message, attachment_dir)
    caption = (text_part or "").strip()

    async with MEDIA_GROUP_LOCK:
        state = MEDIA_GROUP_STATE.get(media_group_id)
        if state is None:
            # Recreate the state if cleaned during processing to avoid losing entries.
            state = PendingMediaGroupState(
                chat_id=message.chat.id,
                origin_message=message,
                attachment_dir=attachment_dir,
                attachments=[],
                captions=[],
            )
            MEDIA_GROUP_STATE[media_group_id] = state
        state.attachments.extend(attachments)
        if caption:
            state.captions.append(caption)
        # Use the earliest message as reply target for Telegram context.
        if state.origin_message.message_id > message.message_id:
            state.origin_message = message
        if state.finalize_task and not state.finalize_task.done():
            state.finalize_task.cancel()
        state.finalize_task = asyncio.create_task(_finalize_media_group_after_delay(media_group_id))


async def _handle_prompt_dispatch(message: Message, prompt: str) -> None:
    """Wrapper routine that pushes prompts to the model."""

    if _is_menu_control_message(prompt):
        worker_log.info(
            "Dropped menu control prompt during dispatch",
            extra={
                **_session_extra(),
                "chat": getattr(message.chat, "id", None),
                "token": _normalize_choice_token(prompt),
            },
        )
        return

    if ENV_ISSUES:
        message_text = _format_env_issue_message()
        worker_log.warning(
            "Rejecting message due to environment issues: %s",
            message_text,
            extra={**_session_extra(), "chat": message.chat.id},
        )
        await message.answer(message_text)
        return

    bot = current_bot()
    await bot.send_chat_action(message.chat.id, "typing")

    if MODE == "A":
        if not AGENT_CMD:
            await message.answer("AGENT_CMD is not configured in .env")
            return
        rc, out = run_subprocess_capture(AGENT_CMD, input_text=prompt)
        out = out or ""
        out = out + ("" if rc == 0 else f"\n(exit={rc})")
        await reply_large_text(message.chat.id, out)
        return

    await _dispatch_prompt_to_model(message.chat.id, prompt, reply_to=message)

BOT_COMMANDS: list[tuple[str, str]] = [
    ("help", "View all commands"),
    ("tasks", "Task command list"),
    ("task_new", "Create a task"),
    ("task_list", "Show task list"),
    ("task_show", "View task details"),
    ("task_update", "Update task fields"),
    ("task_note", "Add task note"),
]

COMMAND_KEYWORDS: set[str] = {command for command, _ in BOT_COMMANDS}
COMMAND_KEYWORDS.update({"task_child", "task_children", "task_delete"})

def _button_text_variants(env_key: str, fallback: Sequence[str]) -> tuple[str, ...]:
    """Return button label candidates sourced from env or fallback list."""

    raw = (os.environ.get(env_key) or "").strip()
    if not raw:
        return tuple(fallback)
    variants = []
    for segment in raw.split("|"):
        cleaned = segment.strip()
        if cleaned and cleaned not in variants:
            variants.append(cleaned)
    return tuple(variants or fallback)


WORKER_MENU_BUTTON_TEXT_VARIANTS = _button_text_variants(
    "WORKER_MENU_BUTTON_TEXTS",
    ("📋 Task List", "📋 任务列表"),
)
WORKER_CREATE_TASK_BUTTON_TEXT_VARIANTS = _button_text_variants(
    "WORKER_CREATE_TASK_BUTTON_TEXTS",
    ("➕ Create Task", "➕ 创建任务"),
)
WORKER_COMMAND_BUTTON_TEXT_VARIANTS = _button_text_variants(
    "WORKER_COMMAND_BUTTON_TEXTS",
    ("⚙️ Command Center", "⚙️ 命令管理"),
)

WORKER_MENU_BUTTON_TEXT = WORKER_MENU_BUTTON_TEXT_VARIANTS[0]
WORKER_CREATE_TASK_BUTTON_TEXT = WORKER_CREATE_TASK_BUTTON_TEXT_VARIANTS[0]
WORKER_COMMAND_BUTTON_TEXT = WORKER_COMMAND_BUTTON_TEXT_VARIANTS[0]
WORKER_MENU_BUTTON_TEXT_SET = set(WORKER_MENU_BUTTON_TEXT_VARIANTS)
WORKER_CREATE_TASK_BUTTON_TEXT_SET = set(WORKER_CREATE_TASK_BUTTON_TEXT_VARIANTS)
WORKER_COMMAND_BUTTON_TEXT_SET = set(WORKER_COMMAND_BUTTON_TEXT_VARIANTS)
WORKER_MENU_CONTROL_TEXT_SET = (
    WORKER_MENU_BUTTON_TEXT_SET
    | WORKER_CREATE_TASK_BUTTON_TEXT_SET
    | WORKER_COMMAND_BUTTON_TEXT_SET
)
WORKER_KEYBOARD_BROADCAST_TEXT = os.environ.get(
    "WORKER_KEYBOARD_BROADCAST_TEXT",
    "Keyboard refreshed. Use the menu below to view tasks, create tasks, or manage commands.",
).strip() or "Keyboard refreshed. Use the menu below to view tasks, create tasks, or manage commands."

TASK_ID_VALID_PATTERN = re.compile(r"^TASK_[A-Z0-9_]+$")
TASK_ID_USAGE_TIP = "Invalid task ID format. Use patterns like TASK_0001."


def _build_worker_main_keyboard() -> ReplyKeyboardMarkup:
    """Return the persistent worker keyboard containing task shortcuts."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=WORKER_MENU_BUTTON_TEXT),
                KeyboardButton(text=WORKER_CREATE_TASK_BUTTON_TEXT),
            ],
            [
                KeyboardButton(text=WORKER_COMMAND_BUTTON_TEXT),
            ],
        ],
        resize_keyboard=True,
    )


def _format_command_preview(command: str) -> str:
    """Return a one-line preview for list buttons."""

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


async def _build_command_list_view(page: int) -> tuple[str, InlineKeyboardMarkup]:
    """Build the command list text and inline keyboard (global + project)."""

    presets, total, total_pages = await _command_paged_presets(page, COMMAND_LIST_PAGE_SIZE)
    current_page = min(max(1, page), total_pages)
    lines = [
        "*命令管理*",
        f"共 {total} 条（全局 + 本项目），页码 {current_page}/{total_pages}，每页 {COMMAND_LIST_PAGE_SIZE} 条。",
    ]
    if not presets:
        lines.append("尚未配置命令，点击“➕ 新建命令”开始。")
    else:
        lines.append("")
        lines.append("当前命令：")
        for scoped in presets:
            icon = _command_scope_icon(scoped.scope)
            lines.append(f"- {icon} {_escape_markdown_text(scoped.record.title)}")
    rows: list[list[InlineKeyboardButton]] = []
    for scoped in presets:
        preview = _format_command_preview(scoped.record.command)
        prefix = _command_scope_icon(scoped.scope)
        label = f"{prefix} {scoped.record.title}"
        if preview:
            label = f"{label} · {preview}"
        scope_token = _command_scope_token(scoped.scope)
        rows.append(
            [
                InlineKeyboardButton(
                    text=label[:60],
                    callback_data=f"{COMMAND_DETAIL_CALLBACK}:{scope_token}:{scoped.record.id}:{current_page}",
                )
            ]
        )

    nav_row: list[InlineKeyboardButton] = []
    if current_page > 1:
        nav_row.append(
            InlineKeyboardButton(
                text="⬅️ 上一页",
                callback_data=f"{COMMAND_LIST_CALLBACK}:{current_page-1}",
            )
        )
    if current_page < total_pages:
        nav_row.append(
            InlineKeyboardButton(
                text="下一页 ➡️",
                callback_data=f"{COMMAND_LIST_CALLBACK}:{current_page+1}",
            )
        )
    if nav_row:
        rows.append(nav_row)

    rows.append(
        [
            InlineKeyboardButton(
                text="➕ 新建命令",
                callback_data=f"{COMMAND_CREATE_CALLBACK}:{current_page}",
            ),
            InlineKeyboardButton(
                text="🔄 刷新",
                callback_data=f"{COMMAND_LIST_CALLBACK}:{current_page}",
            ),
        ]
    )
    text = "\n".join(lines)
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    return text, markup


def _build_command_detail_view(
    preset: CommandPresetRecord,
    origin_page: int,
    scope: str,
) -> tuple[str, InlineKeyboardMarkup]:
    """Build the detail view for a preset."""

    scope_label = _command_scope_label(scope)
    lines = [
        f"*命令：{_escape_markdown_text(preset.title)}*",
        f"ID: `{preset.id}`",
        f"命令范围: {scope_label}",
        (
            f"工作目录: `{_escape_markdown_text(preset.workdir)}`"
            if preset.workdir
            else "工作目录: 继承当前终端会话"
        ),
        f"执行前确认: {'需要' if preset.require_confirmation else '无需'}",
        "",
        "*命令内容*",
    ]
    block_text, _ = _wrap_text_in_code_block(preset.command)
    lines.append(block_text)
    text = "\n".join(lines)
    confirm_label = "🔐 确认：开" if preset.require_confirmation else "🔓 确认：关"
    scope_token = _command_scope_token(scope)
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="▶️ 执行",
                    callback_data=f"{COMMAND_RUN_CALLBACK}:{scope_token}:{preset.id}:{origin_page}",
                ),
                InlineKeyboardButton(
                    text=confirm_label,
                    callback_data=f"{COMMAND_TOGGLE_CONFIRM_CALLBACK}:{scope_token}:{preset.id}:{origin_page}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✏️ 编辑",
                    callback_data=f"{COMMAND_EDIT_CALLBACK}:{scope_token}:{preset.id}:{origin_page}",
                ),
                InlineKeyboardButton(
                    text="🗑️ 删除",
                    callback_data=f"{COMMAND_DELETE_CALLBACK}:{scope_token}:{preset.id}:{origin_page}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ 返回列表",
                    callback_data=f"{COMMAND_LIST_CALLBACK}:{origin_page}",
                ),
            ],
        ]
    )
    return text, markup


def _command_confirm_label(mode: str) -> str:
    """Return the confirm button label for the wizard."""

    return "✅ 创建命令" if mode == "create" else "✅ 保存命令"


def _build_command_confirm_keyboard(mode: str) -> ReplyKeyboardMarkup:
    """Return the confirmation keyboard for the wizard."""

    rows = [
        [KeyboardButton(text=_command_confirm_label(mode))],
        [KeyboardButton(text="❌ Cancel")],
    ]
    _number_reply_buttons(rows)
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


def _parse_confirmation_choice(value: Optional[str]) -> Optional[bool]:
    """Parse yes/no style input into a boolean."""

    token = _normalize_choice_token(value).lower()
    if not token:
        return None
    true_tokens = {"yes", "y", "true", "1", "需要", "要", "on"}
    false_tokens = {"no", "n", "false", "0", "无需", "不需要", "off"}
    if token in true_tokens:
        return True
    if token in false_tokens:
        return False
    return None


def _format_command_summary_text(
    *,
    name: str,
    command_text: str,
    workdir: Optional[str],
    require_confirmation: bool,
) -> str:
    """Render the wizard summary preview."""

    lines = [
        "*命令预览*",
        f"名称: {_escape_markdown_text(name)}",
        (
            f"工作目录: `{_escape_markdown_text(workdir)}`"
            if workdir
            else "工作目录: 继承当前终端会话"
        ),
        f"执行前确认: {'需要' if require_confirmation else '无需'}",
        "",
        "*命令内容*",
    ]
    block_text, _ = _wrap_text_in_code_block(command_text)
    lines.append(block_text)
    return "\n".join(lines)


def _combine_command_prompt(
    *,
    command_text: str,
    workdir: Optional[str],
) -> str:
    """Construct the multi-line prompt sent to the CLI."""

    segments: list[str] = []
    if workdir:
        segments.append(f"cd {shlex.quote(workdir)}")
    body = (command_text or "").strip()
    if body:
        segments.append(body)
    return "\n".join(segment.strip() for segment in segments if segment.strip())


async def _render_command_list_message(
    *,
    target_message: Optional[Message],
    fallback_message: Message,
    page: int,
) -> None:
    """Render the command list either by editing the existing message or replying."""

    text, markup = await _build_command_list_view(page)
    if target_message and await _try_edit_message(target_message, text, reply_markup=markup):
        return
    await _answer_with_markdown(fallback_message, text, reply_markup=markup)


async def _render_command_detail_message(
    *,
    target_message: Optional[Message],
    fallback_message: Message,
    preset: CommandPresetRecord,
    origin_page: int,
    scope: str,
) -> None:
    """Render the command detail view in-place when possible."""

    text, markup = _build_command_detail_view(preset, origin_page, scope)
    if target_message and await _try_edit_message(target_message, text, reply_markup=markup):
        return
    await _answer_with_markdown(fallback_message, text, reply_markup=markup)


async def _dispatch_command_preset(message: Message, preset: CommandPresetRecord) -> None:
    """Send the configured command to the CLI pipeline."""

    prompt = _combine_command_prompt(command_text=preset.command, workdir=preset.workdir)
    if not prompt:
        await message.answer("命令内容为空，无法执行。", reply_markup=_build_worker_main_keyboard())
        return
    notice = f"已派发命令 `{_escape_markdown_text(preset.title)}`，请留意终端输出。"
    await _answer_with_markdown(message, notice)
    await _handle_prompt_dispatch(message, prompt)


async def _cancel_command_wizard(message: Message, state: FSMContext) -> None:
    """Reset the wizard state and notify the user."""

    await state.clear()
    await message.answer("命令配置已取消。", reply_markup=_build_worker_main_keyboard())


def _resolve_worker_target_chat_ids() -> List[int]:
    """Collect chat IDs that should receive menus, preferring state file records."""
    targets: set[int] = set()

    def _append(value: Optional[int]) -> None:
        if value is None:
            return
        targets.add(value)

    for env_name in ("WORKER_CHAT_ID", "ALLOWED_CHAT_ID"):
        raw = os.environ.get(env_name)
        if raw:
            stripped = raw.strip()
            if stripped.isdigit():
                _append(int(stripped))

    state_file = os.environ.get("STATE_FILE")
    if state_file:
        path = Path(state_file).expanduser()
        try:
            raw_state = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            worker_log.debug("STATE_FILE not found; skipping menu source", extra=_session_extra(key="state_file_missing"))
        except json.JSONDecodeError as exc:
            worker_log.warning("STATE_FILE parse failed: %s", exc, extra=_session_extra(key="state_file_invalid"))
        else:
            if isinstance(raw_state, dict):
                entry = raw_state.get(PROJECT_SLUG) or raw_state.get(PROJECT_NAME)
                if isinstance(entry, dict):
                    chat_val = entry.get("chat_id")
                    if isinstance(chat_val, int):
                        _append(chat_val)
                    elif isinstance(chat_val, str) and chat_val.isdigit():
                        _append(int(chat_val))

    config_path_env = os.environ.get("MASTER_PROJECTS_PATH")
    config_path = Path(config_path_env).expanduser() if config_path_env else CONFIG_DIR_PATH / "projects.json"
    try:
        configs_raw = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        worker_log.debug("Project configuration %s not found; skipping allowed_chat_id", config_path, extra=_session_extra(key="projects_missing"))
    except json.JSONDecodeError as exc:
        worker_log.warning("Failed to parse project configuration: %s", exc, extra=_session_extra(key="projects_invalid"))
    else:
        if isinstance(configs_raw, list):
            for item in configs_raw:
                if not isinstance(item, dict):
                    continue
                slug = str(item.get("project_slug") or "").strip()
                bot_name = str(item.get("bot_name") or "").strip()
                if slug != PROJECT_SLUG and bot_name != PROJECT_NAME:
                    continue
                allowed_val = item.get("allowed_chat_id")
                if isinstance(allowed_val, int):
                    _append(allowed_val)
                elif isinstance(allowed_val, str) and allowed_val.strip().isdigit():
                    _append(int(allowed_val.strip()))

    return sorted(targets)


def _auto_record_chat_id(chat_id: int) -> None:
    """Auto-record the chat_id into the state file on first contact.

    The write happens only when all conditions hold:
    1. ``STATE_FILE`` environment variable is configured.
    2. The state file already exists.
    3. The current project has no ``chat_id`` entry in the state.
    """
    state_file_env = os.environ.get("STATE_FILE")
    if not state_file_env:
        return

    state_path = Path(state_file_env).expanduser()
    if not state_path.exists():
        worker_log.debug(
            "STATE_FILE missing; skip auto-recording chat_id",
            extra={**_session_extra(), "path": str(state_path)},
        )
        return

    # Use a file lock to ensure concurrency safety
    lock_path = state_path.with_suffix(state_path.suffix + ".lock")
    import fcntl

    try:
        with open(lock_path, "w", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)

            try:
                # Read the current state
                raw_state = json.loads(state_path.read_text(encoding="utf-8"))
                if not isinstance(raw_state, dict):
                    worker_log.warning(
                        "STATE_FILE has unexpected format; skip auto-recording",
                        extra=_session_extra(),
                    )
                    return

                # Check the chat_id entry for the current project
                project_key = PROJECT_SLUG or PROJECT_NAME
                if not project_key:
                    worker_log.warning(
                        "Both PROJECT_SLUG and PROJECT_NAME are unset; skipping auto-record",
                        extra=_session_extra(),
                    )
                    return

                project_state = raw_state.get(project_key)
                if not isinstance(project_state, dict):
                    # Project entry missing; create a new one
                    raw_state[project_key] = {
                        "chat_id": chat_id,
                        "model": ACTIVE_MODEL or "codex",
                        "status": "running",
                    }
                    need_write = True
                elif project_state.get("chat_id") is None:
                    # chat_id missing; update it
                    project_state["chat_id"] = chat_id
                    need_write = True
                else:
                    # chat_id already present; nothing to do
                    need_write = False

                if need_write:
                    # Write the updated state back to disk
                    tmp_path = state_path.with_suffix(state_path.suffix + ".tmp")
                    tmp_path.write_text(
                        json.dumps(raw_state, ensure_ascii=False, indent=4),
                        encoding="utf-8",
                    )
                    tmp_path.replace(state_path)
                    worker_log.info(
                        "Recorded chat_id=%s into state file",
                        chat_id,
                        extra={**_session_extra(), "project": project_key},
                    )
                else:
                    worker_log.debug(
                        "chat_id already present; skipping update",
                        extra={**_session_extra(), "existing_chat_id": project_state.get("chat_id")},
                    )

            except json.JSONDecodeError as exc:
                worker_log.error(
                    "STATE_FILE parse failed; skipping auto-record: %s",
                    exc,
                    extra=_session_extra(),
                )
            except Exception as exc:
                worker_log.error(
                    "Failed to auto-record chat_id: %s",
                    exc,
                    extra={**_session_extra(), "chat": chat_id},
                )
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    except Exception as exc:
        worker_log.error(
            "Failed to acquire file lock: %s",
            exc,
            extra=_session_extra(),
        )
    finally:
        # Remove the lock file
        try:
            if lock_path.exists():
                lock_path.unlink()
        except Exception:
            pass


async def _broadcast_worker_keyboard(bot: Bot) -> None:
    """Broadcast the worker keyboard at startup to keep Telegram menus in sync."""
    targets = _resolve_worker_target_chat_ids()
    if not targets:
        worker_log.info("No chat targets available; skipping menu broadcast", extra=_session_extra())
        return
    for chat_id in targets:
        keyboard_markup = _build_worker_main_keyboard()
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=WORKER_KEYBOARD_BROADCAST_TEXT,
                reply_markup=keyboard_markup,
            )
        except TelegramForbiddenError as exc:
            worker_log.warning("Keyboard push rejected: %s", exc, extra={**_session_extra(), "chat": chat_id})
            continue
        except TelegramBadRequest as exc:
            worker_log.warning("Keyboard push failed: %s", exc, extra={**_session_extra(), "chat": chat_id})
        except (TelegramRetryAfter, TelegramNetworkError) as exc:
            worker_log.error("Keyboard push hit network issues: %s", exc, extra={**_session_extra(), "chat": chat_id})
            await _notify_send_failure_message(chat_id)
            continue
        except Exception as exc:
            worker_log.error("Unexpected error while pushing keyboard: %s", exc, extra={**_session_extra(), "chat": chat_id})
        try:
            text, inline_markup = await _build_task_list_view(status=None, page=1, limit=DEFAULT_PAGE_SIZE)
        except Exception as exc:
            worker_log.error(
                "Failed to build task list: %s",
                exc,
                extra={**_session_extra(), "chat": chat_id},
            )
            continue

        parse_mode = _parse_mode_value()
        prepared, fallback_payload = _prepare_model_payload_variants(text)

        async def _send_formatted(payload: str) -> None:
            await bot.send_message(
                chat_id=chat_id,
                text=payload,
                parse_mode=parse_mode,
                reply_markup=inline_markup,
            )

        async def _send_raw(payload: str) -> None:
            await bot.send_message(
                chat_id=chat_id,
                text=payload,
                parse_mode=None,
                reply_markup=inline_markup,
            )

        try:
            delivered = await _send_with_markdown_guard(
                prepared,
                _send_formatted,
                raw_sender=_send_raw,
                fallback_payload=fallback_payload,
            )
        except TelegramForbiddenError as exc:
            worker_log.warning("Task list push rejected: %s", exc, extra={**_session_extra(), "chat": chat_id})
        except TelegramBadRequest as exc:
            worker_log.warning("Task list push failed: %s", exc, extra={**_session_extra(), "chat": chat_id})
        except (TelegramRetryAfter, TelegramNetworkError) as exc:
            worker_log.error("Task list push hit network issues: %s", exc, extra={**_session_extra(), "chat": chat_id})
            await _notify_send_failure_message(chat_id)
        except Exception as exc:
            worker_log.error("Unexpected error while pushing task list: %s", exc, extra={**_session_extra(), "chat": chat_id})
        else:
            worker_log.info(
                "Task list sent to chat_id=%s",
                chat_id,
                extra={**_session_extra(), "length": str(len(delivered))},
            )

STATUS_LABELS = {
    "research": "🔍 Researching",
    "test": "🧪 Testing",
    "done": "✅ Completed",
}

NOTE_LABELS = {
    "research": "Research",
    "test": "Test",
    "bug": "Bug",
    "misc": "Misc",
}

TASK_TYPE_LABELS = {
    "requirement": "Requirement",
    "defect": "Defect",
    "task": "Task",
    "risk": "Risk",
}

TASK_TYPE_EMOJIS = {
    "requirement": "📌",
    "defect": "🐞",
    "task": "🛠️",
    "risk": "⚠️",
}

HISTORY_FIELD_LABELS = {
    "title": "Title",
    "status": "Status",
    "priority": "Priority",
    "description": "Description",
    "due_date": "Due Date",
    "task_type": "Type",
    "type": "Type",
    "tags": "Tags",
    "assignee": "Assignee",
    "parent_id": "Parent Task",
    "root_id": "Root Task",
    "archived": "Archive State",
    "create": "Create Task",
}

_TASK_TYPE_ALIAS: dict[str, str] = {}
for _code, _label in TASK_TYPE_LABELS.items():
    _TASK_TYPE_ALIAS[_code] = _code
    _TASK_TYPE_ALIAS[_code.lower()] = _code
    _TASK_TYPE_ALIAS[_label] = _code
    _TASK_TYPE_ALIAS[_label.lower()] = _code
_TASK_TYPE_ALIAS.update(
    {
        "req": "requirement",
        "feature": "requirement",
        "story": "requirement",
        "need": "requirement",
        "bug": "defect",
        "issue": "defect",
        "todo": "task",
        "improvement": "task",
        "optimization": "task",
        "risk": "risk",
    }
)

_STATUS_ALIAS_MAP: dict[str, str] = {key.lower(): value for key, value in STATUS_ALIASES.items()}

SKIP_TEXT = "Skip"
TASK_LIST_CREATE_CALLBACK = "task:list_create"
TASK_LIST_SEARCH_CALLBACK = "task:list_search"
TASK_LIST_SEARCH_PAGE_CALLBACK = "task:list_search_page"
TASK_LIST_RETURN_CALLBACK = "task:list_return"
TASK_DETAIL_BACK_CALLBACK = "task:detail_back"
TASK_HISTORY_PAGE_CALLBACK = "task:history_page"
TASK_HISTORY_BACK_CALLBACK = "task:history_back"
TASK_DESC_INPUT_CALLBACK = "task:desc_input"
TASK_DESC_CLEAR_CALLBACK = "task:desc_clear"
TASK_DESC_CONFIRM_CALLBACK = "task:desc_confirm"
TASK_DESC_RETRY_CALLBACK = "task:desc_retry"
TASK_DESC_CANCEL_CALLBACK = "task:desc_cancel"
TASK_DESC_CLEAR_TEXT = "🗑️ Clear description"
TASK_DESC_CANCEL_TEXT = "❌ Cancel"
TASK_DESC_REPROMPT_TEXT = "✏️ Reopen input prompt"
TASK_DESC_CONFIRM_TEXT = "✅ Confirm update"
TASK_DESC_RETRY_TEXT = "✏️ Re-enter"

DESCRIPTION_MAX_LENGTH = 3000
SEARCH_KEYWORD_MIN_LENGTH = 2
SEARCH_KEYWORD_MAX_LENGTH = 100
RESEARCH_DESIGN_STATUSES = {"research"}

HISTORY_EVENT_FIELD_CHANGE = "field_change"
HISTORY_EVENT_TASK_ACTION = "task_action"
HISTORY_EVENT_MODEL_REPLY = "model_reply"
HISTORY_EVENT_MODEL_SUMMARY = "model_summary"
HISTORY_DISPLAY_VALUE_LIMIT = 200
HISTORY_MODEL_REPLY_LIMIT = 1200
HISTORY_MODEL_SUMMARY_LIMIT = 1600
MODEL_REPLY_PAYLOAD_LIMIT = 4000
MODEL_SUMMARY_PAYLOAD_LIMIT = 4000
MODEL_HISTORY_MAX_ITEMS = 50
MODEL_HISTORY_MAX_CHARS = 4096
TASK_HISTORY_PAGE_SIZE = 6
HISTORY_TRUNCATION_NOTICE = "⚠️ Some records on this page were truncated by Telegram limits. Export history for the complete view."
HISTORY_TRUNCATION_NOTICE_SHORT = "⚠️ Page truncated"

_NUMBER_PREFIX_RE = re.compile(r"^\d+\.\s")


def _format_numbered_label(index: int, label: str) -> str:
    text = label or ""
    if _NUMBER_PREFIX_RE.match(text):
        return text
    return f"{index}. {text}" if text else f"{index}."


def _number_inline_buttons(rows: list[list[InlineKeyboardButton]], *, start: int = 1) -> None:
    """Prefix FSM inline buttons with numbers for easier selection."""
    counter = start
    for row in rows:
        for button in row:
            button.text = _format_numbered_label(counter, button.text or "")
            counter += 1


def _number_reply_buttons(rows: list[list[KeyboardButton]], *, start: int = 1) -> None:
    """Prefix FSM reply buttons with numbers for easier input."""
    counter = start
    for row in rows:
        for button in row:
            button.text = _format_numbered_label(counter, button.text or "")
            counter += 1


def _strip_number_prefix(value: Optional[str]) -> str:
    if not value:
        return ""
    return _NUMBER_PREFIX_RE.sub("", value, count=1).strip()


def _normalize_choice_token(value: Optional[str]) -> str:
    """Normalize button input by stripping numbering and whitespace."""

    if value is None:
        return ""
    stripped = _strip_number_prefix(value)
    return stripped.strip()


def _is_skip_message(value: Optional[str]) -> bool:
    """Return True when the user chose to skip."""

    token = _normalize_choice_token(value).lower()
    return token in {SKIP_TEXT.lower(), "skip"}


def _is_cancel_message(value: Optional[str]) -> bool:
    """Return True when the user requested cancellation."""

    token = _normalize_choice_token(value)
    if not token:
        return False
    lowered = token.lower()
    cancel_tokens = {"cancel", "quit", "exit"}
    # Support buttons that include emojis to avoid repeated cancellation clicks.
    cancel_tokens.add(_normalize_choice_token(TASK_DESC_CANCEL_TEXT).lower())
    return lowered in cancel_tokens


def _is_menu_control_message(value: Optional[str]) -> bool:
    """Return True when the payload represents a generic Skip/Cancel menu action."""

    if _is_skip_message(value) or _is_cancel_message(value):
        return True
    token = _normalize_choice_token(value)
    if not token:
        return False
    return token in WORKER_MENU_CONTROL_TEXT_SET


_MARKDOWN_ESCAPE_RE = re.compile(r"([_*\[\]()~`>#+=|{}.!])")
TASK_REFERENCE_PATTERN = re.compile(r"/?TASK[_]?\d{4,}")


def _escape_markdown_text(value: Optional[str]) -> str:
    if value is None:
        return ""
    text = str(value)
    if not text:
        return ""
    text = text.replace("\\", "\\\\")
    return _MARKDOWN_ESCAPE_RE.sub(r"\\\1", text)


def _resolve_reply_choice(
    value: Optional[str],
    *,
    options: Sequence[str],
) -> str:
    trimmed = (value or "").strip()
    if not trimmed:
        return ""
    stripped = _strip_number_prefix(trimmed)
    for candidate in (trimmed, stripped):
        if candidate in options:
            return candidate
    for candidate in (trimmed, stripped):
        if candidate.isdigit():
            index = int(candidate) - 1
            if 0 <= index < len(options):
                return options[index]
    return stripped


def _status_display_order() -> tuple[str, ...]:
    """Return the display order of statuses, matching task status definitions."""

    return tuple(TASK_STATUSES)


STATUS_DISPLAY_ORDER: tuple[str, ...] = _status_display_order()
STATUS_FILTER_OPTIONS: tuple[Optional[str], ...] = (None, *STATUS_DISPLAY_ORDER)

VIBE_PHASE_BODY = """## Vibe Stage — File modifications forbidden | Network access allowed | Custom scan scope (trigger words: vibe, enter vibe stage)

 Based on the task and background above, you are a professional full-stack engineer. Use as many specialist agents as
 needed and produce research conclusions: outline implementation approaches, pros/cons, and decision options; then,
 according to the user's decisions, execute those decisions or resolve the issues they encounter. Only after receiving
 the user's explicit instruction that file modifications may begin may you enter the implementation stage, then complete
 all tasks one by one with nothing omitted. After implementation/development, perform self-testing.
 Important constraints:

 - Both response content and thinking must always be in english. In the CLI, present data as formatted Markdown; **no
   Markdown tables**. For flowcharts, use plain text drawings. In Markdown, put code/flows and other necessary content in
   fenced code blocks.
 - Read the project end-to-end first: clarify deployment architecture, system architecture, code style, and common
   components; ask before proceeding when unsure.
 - Analyze thoroughly; discuss requirements and edge cases; list key decision points that require my confirmation;
   clarify uncertainties promptly.
 - When using the Task tool you **must label**: RESEARCH ONLY - NO FILE MODIFICATIONS.
 - You may call any needed tools/sub-agents/MCPs for research; if missing locally, search the web for docs and install
   them.
 - For development/design, specify dependencies, database tables and fields, pseudocode, and impact scope; consider
   production-grade security, performance, and high availability.
 - Prepare plans: propose at least two options, compare pros/cons, and recommend the best.
 - When a user decision/confirmation is required, provide numbered decision items with options A/B/C/D to ease reply.
 - Before coding, run existing related tests and keep the results in memory for post-change self-tests.
 - When coding, ensure performance, robustness, readability, and maintainability; classes, functions, and key lines *
   *must** be commented.
 - After coding, design and run sufficient tests based on the changes, covering normal, boundary, and exceptional cases;
   execute at least 10 distinct inputs with expected outputs.
 - Run all relevant unit and integration tests; if no framework support exists, manually simulate key scenarios to
   validate functionality.
 - Compile a checklist for this session to avoid omissions in subsequent tasks; finally verify all items are completed."""

MODEL_PUSH_CONFIG: dict[str, dict[str, Any]] = {
    "research": {
        "include_task_info": True,
        "body": VIBE_PHASE_BODY,
    },
    "test": {
        "include_task_info": True,
        "body": VIBE_PHASE_BODY,
    },
    "done": {
        "include_task_info": False,
        "body": "/compact",
    },
}

MODEL_PUSH_ELIGIBLE_STATUSES: set[str] = set(MODEL_PUSH_CONFIG)
MODEL_PUSH_SUPPLEMENT_STATUSES: set[str] = {
    "research",
    "test",
}


@dataclass(slots=True)
class _PushSupplementLock:
    """Track active push-to-model supplement prompts to prevent duplicates."""

    origin_message_id: Optional[int]
    created_at: float


PUSH_SUPPLEMENT_LOCK_TTL_SECONDS = _env_int("PUSH_SUPPLEMENT_LOCK_TTL", 300)
PUSH_SUPPLEMENT_LOCKS: dict[tuple[int, str], _PushSupplementLock] = {}


def _prune_push_supplement_locks(*, now: Optional[float] = None) -> None:
    """Remove expired supplement locks to avoid leaking memory."""

    if not PUSH_SUPPLEMENT_LOCKS:
        return
    current_time = now if now is not None else time.monotonic()
    expired_keys = [
        key
        for key, lock in PUSH_SUPPLEMENT_LOCKS.items()
        if current_time - lock.created_at >= PUSH_SUPPLEMENT_LOCK_TTL_SECONDS
    ]
    for key in expired_keys:
        PUSH_SUPPLEMENT_LOCKS.pop(key, None)


def _acquire_push_supplement_lock(
    chat_id: int,
    task_id: str,
    *,
    origin_message_id: Optional[int],
) -> bool:
    """Ensure only one supplement prompt per chat/task is active at a time."""

    _prune_push_supplement_locks()
    key = (chat_id, task_id)
    if key in PUSH_SUPPLEMENT_LOCKS:
        return False
    PUSH_SUPPLEMENT_LOCKS[key] = _PushSupplementLock(
        origin_message_id=origin_message_id,
        created_at=time.monotonic(),
    )
    return True


def _release_push_supplement_lock(chat_id: Optional[int], task_id: Optional[str]) -> None:
    """Allow future prompts once the current flow is completed."""

    if chat_id is None or not task_id:
        return
    PUSH_SUPPLEMENT_LOCKS.pop((chat_id, task_id), None)


PUSH_MODEL_SUPPLEMENT_IN_PROGRESS_TEXT = (
    "A supplementary description prompt is already active. Please respond or tap Skip/Cancel."
)

SUMMARY_COMMAND_PREFIX = "/task_summary_request_"
SUMMARY_COMMAND_ALIASES: tuple[str, ...] = (
    "/task_summary_request_",
    "/tasksummaryrequest",
)


LEGACY_BUG_HISTORY_HEADERS: tuple[str, ...] = (
    "Bug History (latest 3 entries)",
    "defectRecords (last 3)",
)


def _strip_legacy_bug_header(text: str) -> str:
    """Remove legacy bug headers from history templates to avoid repetition."""

    if not text:
        return ""
    cleaned_lines: list[str] = []
    for line in text.splitlines():
        token = line.strip()
        if token and any(token.lower().startswith(header.lower()) for header in LEGACY_BUG_HISTORY_HEADERS):
            # Handle legacy formats such as "Bug History (latest 3 entries) -" or colon variants
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip()


def _build_model_push_payload(
    task: TaskRecord,
    supplement: Optional[str] = None,
    history: Optional[str] = None,
    notes: Optional[Sequence[TaskNoteRecord]] = None,
    is_bug_report: bool = False,
) -> str:
    """Construct the payload to push into tmux based on task status.

    Args:
        task: Task record to push.
        supplement: Additional description supplied by the user.
        history: Formatted history text.
        notes: Task note list.
        is_bug_report: When True, prepend the bug prefix before the prompt.
    """

    config = MODEL_PUSH_CONFIG.get(task.status)
    if config is None:
        raise ValueError(f"Status {task.status!r} has no push template configured")

    body = config.get("body", "")
    include_task = bool(config.get("include_task_info"))
    body = (body or "").strip()
    history_block = (history or "").strip()
    status = task.status

    if status in {"research", "test"}:
        body = ""

    if "{history}" in body:
        replacement = history_block or "(No task execution history available)"
        body = body.replace("{history}", replacement).strip()
        history_block = ""

    supplement_text = (supplement or "").strip()
    segments: list[str] = []

    notes = notes or ()
    regular_notes: list[str] = []

    for note in notes:
        content = note.content or ""
        if not content.strip():
            continue
        summarized = _summarize_note_text(content)
        if note.note_type == "bug":
            # Skip bug notes to avoid duplicating task execution history in prompts
            continue
        regular_notes.append(summarized)

    task_code_plain = f"/{task.id}" if task.id else "-"

    if include_task and status in {"research", "test"}:
        phase_line = VIBE_PHASE_PROMPT
        # For defect pushes, prepend the bug prefix ahead of the phase prompt
        if is_bug_report:
            phase_line = f"{BUG_REPORT_PREFIX}\n{phase_line}"
        title = (task.title or "").strip() or "-"
        description = (task.description or "").strip() or "-"
        supplement_value = supplement_text or "-"
        note_text = "；".join(regular_notes) if regular_notes else "-"

        lines: list[str] = [
            phase_line,
            f"Task Title: {title}",
            f"Task Code: {task_code_plain}",
            f"Task Description: {description}",
            f"Task Notes: {note_text}",
            f"Supplementary Description: {supplement_value}",
            "",
        ]
        history_intro = "Task execution history for traceability:"
        if history_block:
            lines.append(history_intro)
            lines.extend(history_block.splitlines())
        else:
            lines.append(f"{history_intro} -")
        return _strip_legacy_bug_header("\n".join(lines))
    else:
        # Preserve legacy behaviour for other statuses (e.g., completed)
        info_lines: list[str] = []
        if include_task:
            title = (task.title or "-").strip() or "-"
            description = (task.description or "").strip() or "None"
            supplement_value = supplement_text or "-"
            info_lines.extend(
                [
                    f"Task Title: {title}",
                    f"Task Code: {task_code_plain}",
                    f"Task Description: {description}",
                    f"Supplementary Description: {supplement_value}",
                ]
            )
        elif supplement_text:
            info_lines.append(f"Supplementary Description: {supplement_text}")

        if history_block:
            if info_lines and info_lines[-1].strip():
                info_lines.append("")
            info_lines.append("Task Execution History:")
            info_lines.append(history_block)

        if info_lines:
            info_segment = "\n".join(info_lines)
            if info_segment.strip():
                segments.append(info_segment)

    if body:
        segments.append(body)

    tail_prompt = ""
    if status in {"research", "test"}:
        tail_prompt = VIBE_PHASE_PROMPT

    result = "\n\n".join(segment for segment in segments if segment)
    if tail_prompt:
        if result:
            result = f"{result}\n{tail_prompt}"
        else:
            result = tail_prompt
    return _strip_legacy_bug_header(result or body)


try:
    SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
except ZoneInfoNotFoundError:
    SHANGHAI_TZ = None


def _normalize_task_id(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    token_raw = value.strip()
    if not token_raw:
        return None
    token = token_raw[1:] if token_raw.startswith("/") else token_raw
    candidate = token.split()[0]
    if "@" in candidate:
        candidate = candidate.split("@", 1)[0]
    if candidate.lower() in COMMAND_KEYWORDS:
        return None
    normalized = TaskService._convert_task_id_token(candidate.upper())
    if not normalized or not normalized.startswith("TASK_"):
        return None
    if not TASK_ID_VALID_PATTERN.fullmatch(normalized):
        return None
    return normalized


def _format_task_command(task_id: str) -> str:
    """Return a clickable task command string honoring the current parse_mode."""

    command = f"/{task_id}"
    if _IS_MARKDOWN and not _IS_MARKDOWN_V2:
        return command.replace("_", r"\_")
    return command


def _wrap_text_in_code_block(text: str) -> tuple[str, str]:
    """Wrap text in a Telegram code block and return the rendered value plus parse_mode."""

    if MODEL_OUTPUT_PARSE_MODE == ParseMode.HTML:
        escaped = html.escape(text, quote=False)
        return f"<pre><code>{escaped}</code></pre>", ParseMode.HTML.value
    if MODEL_OUTPUT_PARSE_MODE == ParseMode.MARKDOWN_V2:
        # Remove existing MarkdownV2 escapes to avoid double escaping
        cleaned = _unescape_if_already_escaped(text)
        # Only escape backticks and backslashes inside code blocks
        escaped = cleaned.replace("\\", "\\\\").replace("`", "\\`")
        return f"```\n{escaped}\n```", ParseMode.MARKDOWN_V2.value
    # Default to Telegram Markdown to keep code block highlighting enabled
    return f"```\n{text}\n```", ParseMode.MARKDOWN.value


async def _reply_task_detail_message(message: Message, task_id: str) -> None:
    try:
        detail_text, markup = await _render_task_detail(task_id)
    except ValueError:
        await _answer_with_markdown(message, f"Task {task_id} does not exist")
        return
    await _answer_with_markdown(message, detail_text, reply_markup=markup)


def _format_local_time(value: Optional[str]) -> str:
    if not value:
        return "-"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if SHANGHAI_TZ is None:
        return dt.strftime("%Y-%m-%d %H:%M")
    try:
        return dt.astimezone(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return dt.strftime("%Y-%m-%d %H:%M")


def _canonical_status_token(value: Optional[str], *, quiet: bool = False) -> Optional[str]:
    if value is None:
        return None
    token = value.strip().lower()
    mapped = _STATUS_ALIAS_MAP.get(token, token)
    if mapped not in TASK_STATUSES:
        if not quiet:
            worker_log.warning("Encountered unknown task status: %s", value)
        return token
    if mapped != token and not quiet:
        worker_log.info("Task status alias auto-converted: %s -> %s", token, mapped)
    return mapped


def _format_status(status: str) -> str:
    canonical = _canonical_status_token(status)
    if canonical and canonical in STATUS_LABELS:
        return STATUS_LABELS[canonical]
    return status


def _status_icon(status: Optional[str]) -> str:
    """Return the emoji icon associated with a status for compact displays."""

    if not status:
        return ""
    canonical = _canonical_status_token(status, quiet=True)
    if not canonical:
        return ""
    label = STATUS_LABELS.get(canonical)
    if not label:
        return ""
    first_token = label.split(" ", 1)[0]
    if not first_token:
        return ""
    # Skip labels without actual emoji characters
    if first_token[0].isalnum():
        return ""
    return first_token


def _strip_task_type_emoji(value: str) -> str:
    """Remove leading task-type emoji while preserving the remaining text."""

    trimmed = value.strip()
    for emoji in TASK_TYPE_EMOJIS.values():
        if trimmed.startswith(emoji):
            return trimmed[len(emoji):].strip()
    return trimmed


def _format_task_type(task_type: Optional[str]) -> str:
    if not task_type:
        return "⚪ Not Set"
    label = TASK_TYPE_LABELS.get(task_type, task_type)
    icon = TASK_TYPE_EMOJIS.get(task_type)
    if icon:
        return f"{icon} {label}"
    return label


def _format_note_type(note_type: str) -> str:
    return NOTE_LABELS.get(note_type, note_type)


def _format_priority(priority: int) -> str:
    priority = max(1, min(priority, 5))
    return f"P{priority}"


def _status_filter_label(value: Optional[str]) -> str:
    if value is None:
        return "⭐ All"
    canonical = _canonical_status_token(value)
    if canonical and canonical in STATUS_LABELS:
        return STATUS_LABELS[canonical]
    return value


def _build_status_filter_row(current_status: Optional[str], limit: int) -> list[list[InlineKeyboardButton]]:
    """Build the task list status filter buttons, wrapping rows dynamically."""

    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    options = list(STATUS_FILTER_OPTIONS)
    row_capacity = 3
    if len(options) <= 4:
        row_capacity = max(len(options), 1)
    for option in options:
        base_label = _status_filter_label(option)
        label = f"✔️ {base_label}" if option == current_status else base_label
        token = option or "-"
        row.append(
            InlineKeyboardButton(
                text=label,
                callback_data=f"task:list_page:{token}:1:{limit}",
            )
        )
        if len(row) == row_capacity:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows



def _format_task_list_entry(task: TaskRecord) -> str:
    indent = "  " * max(task.depth, 0)
    title_raw = (task.title or "").strip()
    # Intelligent cleanup for pre-escaped text
    if not title_raw:
        title = "-"
    elif _IS_MARKDOWN_V2:
        # Protect code blocks while removing pre-escapes
        title = _unescape_if_already_escaped(title_raw)
    else:
        title = _escape_markdown_text(title_raw)
    type_icon = TASK_TYPE_EMOJIS.get(task.task_type)
    if not type_icon:
        type_icon = "⚪"
    return f"{indent}- {type_icon} {title}"


def _compose_task_button_label(task: TaskRecord, *, max_length: int = 60) -> str:
    """Generate task list button labels with status/type icons aligned to the left."""

    title_raw = (task.title or "").strip()
    title = title_raw if title_raw else "-"
    type_icon = TASK_TYPE_EMOJIS.get(task.task_type) or "⚪"
    status_icon = _status_icon(task.status)

    # Prefix order: status icon first, then type icon, so users see progress first.
    prefix_parts: list[str] = []
    if status_icon:
        prefix_parts.append(status_icon)
    if type_icon:
        prefix_parts.append(type_icon)
    prefix = " ".join(prefix_parts)
    if prefix:
        prefix = f"{prefix} "

    available = max_length - len(prefix)
    if available <= 0:
        truncated_title = "…"
    else:
        if len(title) > available:
            if available <= 1:
                truncated_title = "…"
            else:
                truncated_title = title[: available - 1] + "…"
        else:
            truncated_title = title

    label = f"{prefix}{truncated_title}" if prefix else truncated_title
    if len(label) > max_length:
        label = label[: max_length - 1] + "…"
    return label


def _format_task_detail(
        task: TaskRecord,
        *,
        notes: Sequence[TaskNoteRecord],
    ) -> str:
    # Intelligent handling of pre-escaped text:
    # - MarkdownV2: remove pre-escapes first, then let _prepare_model_payload handle it.
    # - Other modes: escape manually.
    title_raw = (task.title or "").strip()
    if _IS_MARKDOWN_V2:
        # Remove pre-escapes while guarding code blocks
        title_text = _unescape_if_already_escaped(title_raw) if title_raw else "-"
    else:
        title_text = _escape_markdown_text(title_raw) if title_raw else "-"

    task_id_text = _format_task_command(task.id)
    lines: list[str] = [
        f"📝 Title: {title_text}",
        f"🏷️ Task ID: {task_id_text}",
        f"⚙️ Status: {_format_status(task.status)}",
        f"🚦 Priority: {_format_priority(task.priority)}",
        f"📂 Type: {_format_task_type(task.task_type)}",
    ]

    # Description field pre-escape handling
    description_raw = task.description or "None"
    if _IS_MARKDOWN_V2:
        # Remove pre-escapes while guarding code blocks
        description_text = _unescape_if_already_escaped(description_raw)
    else:
        description_text = _escape_markdown_text(description_raw)

    lines.append(f"🖊️ Description: {description_text}")
    lines.append(f"📅 Created At: {_format_local_time(task.created_at)}")
    lines.append(f"🔁 Updated At: {_format_local_time(task.updated_at)}")

    # Intelligent cleanup for parent task identifier
    if task.parent_id:
        if _IS_MARKDOWN_V2:
            # Remove pre-escapes while guarding code blocks
            parent_text = _unescape_if_already_escaped(task.parent_id)
        else:
            parent_text = _escape_markdown_text(task.parent_id)
        lines.append(f"👪 Parent Task: {parent_text}")

    return "\n".join(lines)


def _parse_history_payload(payload_raw: Optional[str]) -> dict[str, Any]:
    if not payload_raw:
        return {}
    try:
        data = json.loads(payload_raw)
    except json.JSONDecodeError:
        worker_log.warning("Failed to parse history payload: %s", payload_raw, extra=_session_extra())
        return {}
    if isinstance(data, dict):
        return data
    worker_log.warning("History payload has unexpected type: %s", type(data), extra=_session_extra())
    return {}


def _trim_history_value(value: Optional[str], limit: int = HISTORY_DISPLAY_VALUE_LIMIT) -> str:
    if value is None:
        return "-"
    text = normalize_newlines(str(value)).strip()
    if not text:
        return "-"
    if len(text) > limit:
        return text[:limit] + "…"
    return text


def _history_field_label(field: Optional[str]) -> str:
    """Return the human-readable label for a history field."""

    token = (field or "").strip().lower()
    if not token:
        return "Field"
    return HISTORY_FIELD_LABELS.get(token, token)


def _format_history_value(field: Optional[str], value: Optional[str]) -> str:
    """Convert a history field value into a readable string."""

    text = _trim_history_value(value)
    if text == "-":
        return text
    token = (field or "").strip().lower()
    if token == "status":
        canonical = _canonical_status_token(text, quiet=True)
        if canonical and canonical in STATUS_LABELS:
            return STATUS_LABELS[canonical]
        return text
    if token in {"task_type", "type"}:
        normalized = _TASK_TYPE_ALIAS.get(text, text)
        label = TASK_TYPE_LABELS.get(normalized)
        return label if label else text
    if token == "archived":
        lowered = text.lower()
        if lowered in {"true", "1", "yes"}:
            return "Archived"
        if lowered in {"false", "0", "no"}:
            return "Not Archived"
    return text


def _format_history_timestamp(value: Optional[str]) -> str:
    """Render history timestamps as "MM-DD HH:MM" to minimise wrapping."""

    if not value:
        return "-"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return _format_local_time(value)
    if SHANGHAI_TZ is not None:
        try:
            dt = dt.astimezone(SHANGHAI_TZ)
        except ValueError:
            return dt.strftime("%m-%d %H:%M")
    return dt.strftime("%m-%d %H:%M")


def _format_history_summary(item: TaskHistoryRecord) -> str:
    """Generate the headline summary emphasising button semantics."""

    event_type = (item.event_type or HISTORY_EVENT_FIELD_CHANGE).strip() or HISTORY_EVENT_FIELD_CHANGE
    payload = _parse_history_payload(item.payload)
    if event_type == HISTORY_EVENT_FIELD_CHANGE:
        field = (item.field or "").strip().lower()
        if field == "create":
            return "Create Task"
        return f"Update {_history_field_label(field)}"
    if event_type == HISTORY_EVENT_TASK_ACTION:
        action = payload.get("action") if isinstance(payload, dict) else None
        if action == "add_note":
            note_type = payload.get("note_type", "misc") if isinstance(payload, dict) else "misc"
            if note_type and note_type != "misc":
                return f"Add Note ({_format_note_type(note_type)})"
            return "Add Note"
        if action == "push_model":
            return "Push to Model"
        if action == "bug_report":
            return "Report Bug"
        if action == "summary_request":
            return "Generate Model Summary"
        if action == "model_session":
            return "Record Model Session"
        label = action or (item.field or "Task Action")
        return f"Perform Action: {label}"
    if event_type == HISTORY_EVENT_MODEL_REPLY:
        return "Model Reply"
    if event_type == HISTORY_EVENT_MODEL_SUMMARY:
        return "Model Summary"
    fallback = item.field or event_type
    return _history_field_label(fallback)


def _format_history_description(item: TaskHistoryRecord) -> str:
    event_type = (item.event_type or HISTORY_EVENT_FIELD_CHANGE).strip() or HISTORY_EVENT_FIELD_CHANGE
    payload = _parse_history_payload(item.payload)
    if event_type == HISTORY_EVENT_FIELD_CHANGE:
        field = (item.field or "").strip().lower()
        label = _history_field_label(field)
        if field == "create":
            title_text = _format_history_value("title", item.new_value)
            return f"Title: \"{title_text}\"" if title_text != "-" else "Title: -"
        old_text = _format_history_value(field, item.old_value)
        new_text = _format_history_value(field, item.new_value)
        if old_text == "-" and new_text != "-":
            return f"{label}: {new_text}"
        return f"{label}: {old_text} -> {new_text}"
    if event_type == HISTORY_EVENT_TASK_ACTION:
        action = payload.get("action")
        if action == "add_note":
            note_type = payload.get("note_type", "misc")
            content_text = _trim_history_value(item.new_value)
            lines: list[str] = []
            if note_type and note_type != "misc":
                lines.append(f"Type: {_format_note_type(note_type)}")
            lines.append(f"Content: {content_text}")
            return "\n".join(lines)
        if action == "push_model":
            details: list[str] = []
            supplement_text: Optional[str] = None
            result = payload.get("result") or "success"
            details.append(f"Result: {result}")
            model_name = payload.get("model")
            if model_name:
                details.append(f"Model: {model_name}")
            history_items = payload.get("history_items")
            if isinstance(history_items, int) and history_items > 0:
                details.append(f"Events Included: {history_items}")
            supplement_raw = payload.get("supplement")
            if supplement_raw is None and payload.get("has_supplement"):
                supplement_raw = item.new_value
            if supplement_raw is not None:
                supplement_text = _trim_history_value(str(supplement_raw))
            detail_text = "; ".join(details) if details else "Triggered"
            if supplement_text and supplement_text != "-":
                return f"{detail_text}\nSupplementary Description: {supplement_text}"
            if payload.get("has_supplement") and (item.new_value or "").strip():
                supplement_fallback = _trim_history_value(item.new_value)
                if supplement_fallback != "-":
                    return f"{detail_text}\nSupplementary Description: {supplement_fallback}"
            return detail_text
        if action == "bug_report":
            has_logs = bool(payload.get("has_logs"))
            has_repro = bool(payload.get("has_reproduction"))
            note_preview = _trim_history_value(item.new_value)
            details = ["Bug Description: " + (note_preview or "-")]
            details.append(f"Contains Reproduction: {'Yes' if has_repro else 'No'}")
            details.append(f"Contains Logs: {'Yes' if has_logs else 'No'}")
            return "\n".join(details)
        if action == "summary_request":
            request_id = payload.get("request_id") or (item.new_value or "-")
            model_name = payload.get("model")
            lines = [f"Summary Request ID: {request_id}"]
            if model_name:
                lines.append(f"Target Model: {model_name}")
            return "\n".join(lines)
        if action == "model_session":
            session = payload.get("session")
            return f"Model Session: {session or '-'}"
        label = action or (item.field or "Action")
        return f"{label}: {_trim_history_value(item.new_value)}"
    if event_type == HISTORY_EVENT_MODEL_REPLY:
        model_name = payload.get("model") or payload.get("source") or ""
        content = payload.get("content") or item.new_value
        text = _trim_history_value(content, limit=HISTORY_MODEL_REPLY_LIMIT)
        prefix = f"{model_name} Reply" if model_name else "Model Reply"
        return f"{prefix}: {text}"
    if event_type == HISTORY_EVENT_MODEL_SUMMARY:
        payload_content = payload.get("content") if isinstance(payload, dict) else None
        content = payload_content or item.new_value
        text = _trim_history_value(content, limit=HISTORY_MODEL_SUMMARY_LIMIT)
        return f"Summary: {text}"
    fallback_field = item.field or event_type
    return f"{fallback_field}: {_trim_history_value(item.new_value)}"


def _format_history_line(item: TaskHistoryRecord) -> str:
    """Render the history entry as Markdown with summary and indented details."""

    timestamp = _format_history_timestamp(item.created_at)
    summary = _format_history_summary(item)
    description = _format_history_description(item)
    detail_lines = [
        line.strip()
        for line in description.splitlines()
        if line.strip()
    ]
    # Markdown lists start with "- " and indent detail lines; MarkdownV2 uses * for bold
    formatted = [f"- *{summary}* {timestamp}"]
    for detail in detail_lines:
        formatted.append(f"  - {detail}")
    formatted.append("")  # Add a blank line to separate history entries
    return "\n".join(formatted)


def _format_history_line_for_model(item: TaskHistoryRecord) -> str:
    timestamp = _format_local_time(item.created_at)
    summary = _format_history_summary(item)
    description = _format_history_description(item).replace("\n", " / ")
    if description:
        return f"{timestamp} | {summary} | {description}"
    return f"{timestamp} | {summary}"


def _trim_history_lines_for_limit(lines: list[str], limit: int) -> list[str]:
    if not lines:
        return lines
    joined = "\n".join(lines)
    while len(joined) > limit and lines:
        lines.pop(0)
        joined = "\n".join(lines)
    return lines


async def _build_history_context_for_model(task_id: str) -> tuple[str, int]:
    history = await TASK_SERVICE.list_history(task_id)
    if not history:
        return "", 0
    selected = history[-MODEL_HISTORY_MAX_ITEMS:]
    lines = [_format_history_line_for_model(item) for item in selected]
    trimmed_lines = _trim_history_lines_for_limit(lines, MODEL_HISTORY_MAX_CHARS)
    return "\n".join(trimmed_lines), len(trimmed_lines)


SKIPPED_TASK_HISTORY_ACTIONS: set[str] = {"push_model", "summary_request"}


async def _log_task_action(
    task_id: str,
    *,
    action: str,
    actor: Optional[str],
    field: str = "",
    old_value: Optional[str] = None,
    new_value: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    created_at: Optional[str] = None,
) -> None:
    """Encapsulate task event writing and record logs when exceptions occur to avoid interrupting the main process."""

    action_token = (action or "").strip()
    if action_token in SKIPPED_TASK_HISTORY_ACTIONS:
        worker_log.debug(
            "Skipped logging task action in history: task_id=%s action=%s",
            task_id,
            action_token,
            extra=_session_extra(),
        )
        return
    data_payload: Optional[Dict[str, Any]]
    if payload is None:
        data_payload = {"action": action_token}
    else:
        data_payload = {"action": action_token, **payload}
    try:
        await TASK_SERVICE.log_task_event(
            task_id,
            event_type=HISTORY_EVENT_TASK_ACTION,
            actor=actor,
            field=field,
            old_value=old_value,
            new_value=new_value,
            payload=data_payload,
            created_at=created_at,
        )
    except ValueError as exc:
        worker_log.warning(
            "Task event writing failed: %s",
            exc,
            extra={"task_id": task_id, **_session_extra()},
        )


async def _auto_push_after_bug_report(task: TaskRecord, *, message: Message, actor: Optional[str]) -> None:
    """After the defect report is completed, try to automatically push the model and maintain the same prompt format as manual push."""

    chat_id = message.chat.id
    if task.status not in MODEL_PUSH_ELIGIBLE_STATUSES:
        await _reply_to_chat(
            chat_id,
            "The defect has been recorded. The current status does not support automatic push to the model. If synchronization is required, please adjust the task status and push manually.",
            reply_to=message,
            reply_markup=_build_worker_main_keyboard(),
        )
        return
    try:
        success, prompt, session_path = await _push_task_to_model(
            task,
            chat_id=chat_id,
            reply_to=message,
            supplement=None,
            actor=actor,
            is_bug_report=True,
        )
    except ValueError as exc:
        worker_log.error(
            "Automatic push to model failed: template missing",
            exc_info=exc,
            extra={"task_id": task.id, "status": task.status},
        )
        await _reply_to_chat(
            chat_id,
            "The defect has been recorded, but the push template is missing. Please manually try pushing to the model again later.",
            reply_to=message,
            reply_markup=_build_worker_main_keyboard(),
        )
        return
    if not success:
        await _reply_to_chat(
            chat_id,
            "The defect has been logged and the model is currently not ready. Please push it again manually later.",
            reply_to=message,
            reply_markup=_build_worker_main_keyboard(),
        )
        return
    preview_block, preview_parse_mode = _wrap_text_in_code_block(prompt)
    await _reply_to_chat(
        chat_id,
        f"Pushed to model:\n{preview_block}",
        reply_to=message,
        parse_mode=preview_parse_mode,
        reply_markup=_build_worker_main_keyboard(),
    )
    if session_path is not None:
        await _send_session_ack(chat_id, session_path, reply_to=message)


def _build_status_buttons(task_id: str, current_status: str) -> list[list[InlineKeyboardButton]]:
    buttons: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for status in STATUS_DISPLAY_ORDER:
        text = _format_status(status)
        if status == current_status:
            text = f"{text} (current)"
        row.append(
            InlineKeyboardButton(
                text=text,
                callback_data=f"task:status:{task_id}:{status}",
            )
        )
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return buttons


def _build_task_actions(task: TaskRecord) -> InlineKeyboardMarkup:
    keyboard: list[list[InlineKeyboardButton]] = []
    keyboard.extend(_build_status_buttons(task.id, task.status))
    keyboard.append(
        [
            InlineKeyboardButton(
                text="✏️ Edit field",
                callback_data=f"task:edit:{task.id}",
            ),
            InlineKeyboardButton(
                text="🗂️ Archive tasks" if not task.archived else "♻️ recovery task",
                callback_data=f"task:toggle_archive:{task.id}",
            ),
        ]
    )
    keyboard.append(
        [
            InlineKeyboardButton(
                text="🚨 Report a defect",
                callback_data=f"task:bug_report:{task.id}",
            ),
            InlineKeyboardButton(
                text="🕘 View history",
                callback_data=f"task:history:{task.id}",
            ),
        ]
    )
    if task.status in MODEL_PUSH_ELIGIBLE_STATUSES:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text="🚀 push to model",
                    callback_data=f"task:push_model:{task.id}",
                )
            ]
        )
    keyboard.append(
        [
            InlineKeyboardButton(
                text="⬅️ Return to task list",
                callback_data=TASK_DETAIL_BACK_CALLBACK,
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def _build_task_desc_confirm_keyboard() -> ReplyKeyboardMarkup:
    """Menu button for task description confirmation phase."""

    rows = [
        [KeyboardButton(text=TASK_DESC_CONFIRM_TEXT)],
        [KeyboardButton(text=TASK_DESC_RETRY_TEXT), KeyboardButton(text=TASK_DESC_CANCEL_TEXT)],
    ]
    _number_reply_buttons(rows)
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


def _build_task_desc_input_keyboard() -> ReplyKeyboardMarkup:
    """Menu button for the task description input stage."""

    rows = [
        [KeyboardButton(text=TASK_DESC_CLEAR_TEXT), KeyboardButton(text=TASK_DESC_REPROMPT_TEXT)],
        [KeyboardButton(text=TASK_DESC_CANCEL_TEXT)],
    ]
    _number_reply_buttons(rows)
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=False)


def _build_task_desc_cancel_keyboard() -> ReplyKeyboardMarkup:
    """Return the keyboard used when only the cancel option should be shown."""

    rows = [[KeyboardButton(text=TASK_DESC_CANCEL_TEXT)]]
    _number_reply_buttons(rows)
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


def _build_task_desc_confirm_text(preview_segment: str) -> str:
    """Return the confirmation prompt for the task description wizard."""

    return (
        "Please confirm the updated task description:\n"
        f"{preview_segment}\n\n"
        "1. Tap \"✅ Confirm update\" to save now.\n"
        "2. Tap \"✏️ Re-enter\" to revise the description.\n"
        "3. Tap \"❌ Cancel\" to abort this edit."
    )


async def _prompt_task_description_input(
    target: Optional[Message],
    *,
    current_description: str,
) -> None:
    """Show the existing description and prompt the user for the next action."""

    if target is None:
        # Telegram If the original message has been deleted, simply ignore it to avoid interruption of the process.
        return
    preview = (current_description or "").strip()
    preview_segment = preview or "(The current description is empty; saving will keep it blank.)"
    await target.answer(
        "The current description is shown below. You can edit it directly, and the menu offers quick clear and cancel actions.",
        reply_markup=_build_task_desc_input_keyboard(),
    )
    preview_block, preview_parse_mode = _wrap_text_in_code_block(preview_segment)
    try:
        await target.answer(
            preview_block,
            parse_mode=preview_parse_mode,
        )
    except TelegramBadRequest:
        await target.answer(preview_segment)
    await target.answer(
        "Send the new task description or choose an action from the menu.",
    )


async def _begin_task_desc_edit_flow(
    *,
    state: FSMContext,
    task: TaskRecord,
    actor: str,
    origin_message: Optional[Message],
) -> None:
    """Unified initialization task description editing FSM, compatible with callback and command entry."""

    if origin_message is None:
        return
    await state.clear()
    await state.update_data(
        task_id=task.id,
        actor=actor,
        current_description=task.description or "",
    )
    await state.set_state(TaskDescriptionStates.waiting_content)
    await _prompt_task_description_input(
        origin_message,
        current_description=task.description or "",
    )


def _extract_command_args(text: Optional[str]) -> str:
    if not text:
        return ""
    stripped = text.strip()
    if not stripped:
        return ""
    if " " not in stripped:
        return ""
    return stripped.split(" ", 1)[1].strip()


async def _answer_with_markdown(
    message: Message,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | ReplyKeyboardMarkup | None = None,
) -> Optional[Message]:
    prepared, fallback_payload = _prepare_model_payload_variants(text)
    sent_message: Optional[Message] = None

    async def _send(payload: str) -> None:
        nonlocal sent_message
        sent_message = await message.answer(
            payload,
            parse_mode=_parse_mode_value(),
            reply_markup=reply_markup,
        )

    async def _send_raw(payload: str) -> None:
        nonlocal sent_message
        sent_message = await message.answer(
            payload,
            parse_mode=None,
            reply_markup=reply_markup,
        )

    try:
        await _send_with_markdown_guard(
            prepared,
            _send,
            raw_sender=_send_raw,
            fallback_payload=fallback_payload,
        )
    except TelegramBadRequest as exc:
        worker_log.warning(
            "Failed to send message: %s",
            exc,
            extra={"chat": getattr(message.chat, "id", None)},
        )
        return None
    return sent_message


async def _edit_message_with_markdown(
    callback: CallbackQuery,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    prepared, fallback_payload = _prepare_model_payload_variants(text)

    async def _send(payload: str) -> None:
        await callback.message.edit_text(
            payload,
            parse_mode=_parse_mode_value(),
            reply_markup=reply_markup,
        )

    async def _send_raw(payload: str) -> None:
        await callback.message.edit_text(
            payload,
            parse_mode=None,
            reply_markup=reply_markup,
        )

    await _send_with_markdown_guard(
        prepared,
        _send,
        raw_sender=_send_raw,
        fallback_payload=fallback_payload,
    )


async def _try_edit_message(
    message: Optional[Message],
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> bool:
    if message is None:
        return False
    prepared, fallback_payload = _prepare_model_payload_variants(text)

    async def _send(payload: str) -> None:
        await message.edit_text(
            payload,
            parse_mode=_parse_mode_value(),
            reply_markup=reply_markup,
        )

    async def _send_raw(payload: str) -> None:
        await message.edit_text(
            payload,
            parse_mode=None,
            reply_markup=reply_markup,
        )

    try:
        await _send_with_markdown_guard(
            prepared,
            _send,
            raw_sender=_send_raw,
            fallback_payload=fallback_payload,
        )
        return True
    except TelegramBadRequest as exc:
        worker_log.info(
            "Failed to edit task list message, new message will be displayed instead",
            extra={"reason": _extract_bad_request_message(exc)},
        )
    return False


def _build_priority_keyboard() -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=str(i)) for i in range(1, 6)],
        [KeyboardButton(text=SKIP_TEXT)],
    ]
    _number_reply_buttons(rows)
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


def _build_task_type_keyboard() -> ReplyKeyboardMarkup:
    rows: list[list[KeyboardButton]] = []
    current_row: list[KeyboardButton] = []
    for task_type in TASK_TYPES:
        current_row.append(KeyboardButton(text=_format_task_type(task_type)))
        if len(current_row) == 2:
            rows.append(current_row)
            current_row = []
    if current_row:
        rows.append(current_row)
    rows.append([KeyboardButton(text="Cancel")])
    _number_reply_buttons(rows)
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


def _build_description_keyboard() -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=SKIP_TEXT)],
        [KeyboardButton(text="Cancel")],
    ]
    _number_reply_buttons(rows)
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


def _build_confirm_keyboard() -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="✅ Confirm creation")],
        [KeyboardButton(text="❌ Cancel")],
    ]
    _number_reply_buttons(rows)
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


def _build_bug_confirm_keyboard() -> ReplyKeyboardMarkup:
    """Defect submission process confirmation keyboard."""

    rows = [
        [KeyboardButton(text="✅ Confirm submission")],
        [KeyboardButton(text="❌ Cancel")],
    ]
    _number_reply_buttons(rows)
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


def _collect_message_payload(message: Message) -> str:
    """Extract the text character and attachment information in the Cancel message to facilitate writing the defect record."""

    parts: list[str] = []
    text = _normalize_choice_token(message.text or message.caption)
    if text:
        parts.append(text)
    if message.photo:
        file_id = message.photo[-1].file_id
        parts.append(f"[picture:{file_id}]")
    if message.document:
        doc = message.document
        name = doc.file_name or doc.file_id
        parts.append(f"[document:{name}]")
    if message.voice:
        parts.append(f"[voice:{message.voice.file_id}]")
    if message.video:
        parts.append(f"[video:{message.video.file_id}]")
    return "\n".join(parts).strip()


def _summarize_note_text(value: str) -> str:
    """Compress note content, maintain main information and control length."""

    cleaned = normalize_newlines(value or "").strip()
    return cleaned.replace("\n", " / ")


def _build_bug_report_intro(task: TaskRecord) -> str:
    """Generate defect report opening prompts."""

    # Directly splice the command text to ensure that Markdown-escaped backslashes do not appear in the prompt.
    task_code = f"/{task.id}" if task.id else "-"
    title = task.title or "-"
    return (
        f"Working on task {task_code} ({title}) to document defects.\n"
        "First describe the observed defect (required), including what happened and what you expected instead."
    )


def _build_bug_repro_prompt() -> str:
    """Generate tips for reproducibility steps."""

    lines = [
        "If there are steps to reproduce, list them in order, for example:",
        "1. Open the relevant page...",
        "2. Perform the necessary action...",
        'If you have no steps to add, send "Skip". Send "Cancel" at any time to exit the process.',
    ]
    return "\n".join(lines)


def _build_bug_log_prompt() -> str:
    """Generate log information prompts."""

    lines = [
        "Please provide error logs, screenshots, or relevant attachments.",
        'If you have nothing to attach, send "Skip" or send "Cancel" to exit the process.',
    ]
    return "\n".join(lines)


def _build_bug_preview_text(
    *,
    task: TaskRecord,
    description: str,
    reproduction: str,
    logs: str,
    reporter: str,
) -> str:
    """Build defect preview text to facilitate user confirmation."""

    # Preview information is for plain text messages, use the task command directly to avoid extra backslashes.
    task_code = f"/{task.id}" if task.id else "-"
    parts = [
        f"Task code: {task_code}",
        f"Defect description: {description or '-'}",
        f"Reproduction steps: {reproduction or '-'}",
        f"Log information: {logs or '-'}",
        f"Reporter: {reporter}",
    ]
    return "\n".join(parts)


def _build_summary_prompt(
    task: TaskRecord,
    *,
    request_id: str,
    history_text: str,
    notes: Sequence[TaskNoteRecord],
) -> str:
    """Construct the model summary prompt word, which is required to carry the request identifier."""

    # The summary prompt words are sent to the model in plain text format and do not require Markdown escaping.
    task_code = f"/{task.id}" if task.id else "-"
    title = task.title or "-"
    status_label = STATUS_LABELS.get(task.status, task.status)
    note_lines: list[str] = []
    if notes:
        note_lines.append("Summary of remarks:")
        for note in notes[-5:]:
            label = NOTE_LABELS.get(note.note_type or "", note.note_type or "Remark")
            content = _summarize_note_text(note.content or "")
            timestamp = _format_local_time(note.created_at)
            note_lines.append(f"- [{label}] {timestamp} — {content or '-'}")
    else:
        note_lines.append("Summary of remarks:")
    history_lines = ["History:"]
    if history_text.strip():
        history_lines.extend(history_text.splitlines())
    else:
        history_lines.append("-")
    instructions = [
        "Entering the summary stage...",
        f"Task code: {task_code}",
        f"SUMMARY_REQUEST_ID::{request_id}; keep this identifier unchanged.",
        "",
        f"Task title: {title}",
        f"Task stage: {status_label}",
        f"Priority: {task.priority}",
        "",
        f"Please use the details below for task {task_code} to prepare the processing summary.",
        "Output requirements:",
        "- The first line must contain SUMMARY_REQUEST_ID::{request_id} as is.",
        "- Summarize the task goals, recent actions, current state, and pending items.",
        "- Use a concise paragraph or list format that teammates can read directly.",
        "- Call out unresolved defects or testing issues explicitly.",
        "",
    ]
    instructions.extend(note_lines)
    instructions.append("")
    instructions.extend(history_lines)
    instructions.append("")
    instructions.append("Please add next step suggestions at the end of the output.")
    return "\n".join(instructions)


def _build_push_supplement_prompt() -> str:
    lines = [
        "Please enter a supplementary task description. Explain the task background and the expected result.",
        'If you have nothing to add, tap "Skip" or send an empty message. Send "Cancel" to end the process.',
    ]
    return "\n".join(lines)





async def _prompt_model_supplement_input(message: Message) -> None:
    await message.answer(
        _build_push_supplement_prompt(),
        reply_markup=_build_description_keyboard(),
    )


def _build_task_search_prompt() -> str:
    lines = [
        "Please enter task search keywords (at least 2 characters). Fuzzy matching of title and description is supported.",
        'Send "Skip" or send "Cancel" to return to the task list.',
    ]
    return "\n".join(lines)





async def _prompt_task_search_keyword(message: Message) -> None:
    await message.answer(
        _build_task_search_prompt(),
        reply_markup=_build_description_keyboard(),
    )


def _build_edit_field_keyboard() -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="title"), KeyboardButton(text="priority")],
        [KeyboardButton(text="type"), KeyboardButton(text="describe")],
        [KeyboardButton(text="state")],
        [KeyboardButton(text="Cancel")],
    ]
    _number_reply_buttons(rows)
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


async def _load_task_context(
    task_id: str,
    *,
    include_history: bool = False,
) -> tuple[TaskRecord, Sequence[TaskNoteRecord], Sequence[TaskHistoryRecord]]:
    task = await TASK_SERVICE.get_task(task_id)
    if task is None:
        raise ValueError("Task does not exist")
    notes = await TASK_SERVICE.list_notes(task_id)
    history: Sequence[TaskHistoryRecord]
    if include_history:
        history = await TASK_SERVICE.list_history(task_id)
    else:
        history = ()
    return task, notes, history


async def _render_task_detail(task_id: str) -> tuple[str, InlineKeyboardMarkup]:
    task, notes, _ = await _load_task_context(task_id)
    detail_text = _format_task_detail(task, notes=notes)
    return detail_text, _build_task_actions(task)


@dataclass(slots=True)
class _HistoryViewPage:
    """Text slices required for history pagination rendering."""

    lines: list[str]
    notice: str
    truncated: bool


def _build_truncated_history_entry(item: TaskHistoryRecord) -> str:
    """Generate truncated prompt text for a single piece of history, retaining summary time information."""

    timestamp = _format_history_timestamp(item.created_at)
    summary = _format_history_summary(item)
    return "\n".join(
        [
            f"- *{summary}* {timestamp}",
            "  - ⚠️ This record is long and only a summary is shown.",
        ]
    )


def _select_truncation_variant(
    entry_text: str,
    *,
    notice: str,
    body_limit: int,
) -> tuple[str, str]:
    """Choose to truncate text and prompts within length limits."""

    variants = [
        (entry_text, notice),
        ("- ⚠️ The history record is too long and has been simplified for display.", notice),
        ("- ⚠️ The history record is too long and has been simplified for display.", HISTORY_TRUNCATION_NOTICE_SHORT),
        ("- ⚠️ Truncated", HISTORY_TRUNCATION_NOTICE_SHORT),
    ]
    for candidate_text, candidate_notice in variants:
        combined = "\n\n".join([candidate_text, candidate_notice])
        if len(_prepare_model_payload(combined)) <= body_limit:
            return candidate_text, candidate_notice
    # In the worst case, only a very short prompt is returned to avoid triggering a long error again.
    fallback_text = "- ⚠️ The history record is Truncated, please export and view the detailed content."
    return fallback_text, HISTORY_TRUNCATION_NOTICE_SHORT


def _build_task_history_view(
    task: TaskRecord,
    history: Sequence[TaskHistoryRecord],
    *,
    page: int,
) -> tuple[str, InlineKeyboardMarkup, int, int]:
    """Construct paginated view content and inline buttons based on task history."""

    limited = list(history[-MODEL_HISTORY_MAX_ITEMS:])
    total_items = len(limited)
    if total_items == 0:
        raise ValueError("No event record yet")

    # History records will be displayed wrapped in code blocks, using plain text format without Markdown escaping.
    title_text = normalize_newlines(task.title or "").strip() or "-"
    title_display = title_text

    digit_width = len(str(max(total_items, 1)))
    placeholder_page = "9" * digit_width
    header_placeholder = "\n".join(
        [
            f"Task {task.id} event history (latest {total_items} entries)",
            f"Title: {title_display}",
            f"Page: {placeholder_page} / {placeholder_page}",
        ]
    )
    header_reserved = len(_prepare_model_payload(header_placeholder))
    # Reserve two additional breaks to separate the main text and header to ensure that the total length does not exceed 4096.
    body_limit = max(1, TELEGRAM_MESSAGE_LIMIT - header_reserved - 2)

    page_size = max(1, TASK_HISTORY_PAGE_SIZE)
    formatted_entries = [_format_history_line(item).rstrip("\n") for item in limited]
    pages: list[_HistoryViewPage] = []
    index = 0
    while index < total_items:
        current_lines: list[str] = []
        truncated = False
        notice_text = ""
        while index < total_items and len(current_lines) < page_size:
            candidate_lines = [*current_lines, formatted_entries[index]]
            candidate_body = "\n\n".join(candidate_lines)
            if len(_prepare_model_payload(candidate_body)) <= body_limit:
                current_lines = candidate_lines
                index += 1
                continue
            break
        if not current_lines:
            # A single record exceeds the limit and needs to be downgraded for display and a truncation prompt added.
            entry = limited[index]
            entry_text = _build_truncated_history_entry(entry)
            truncated_text, notice_text = _select_truncation_variant(
                entry_text,
                notice=HISTORY_TRUNCATION_NOTICE,
                body_limit=body_limit,
            )
            current_lines = [truncated_text]
            truncated = True
            index += 1
        pages.append(_HistoryViewPage(lines=current_lines, notice=notice_text, truncated=truncated))

    total_pages = len(pages)
    normalized_page = page if 1 <= page <= total_pages else total_pages
    selected = pages[normalized_page - 1]
    body_segments = list(selected.lines)
    notice_text = selected.notice
    if selected.truncated and not notice_text:
        # When failing to put in a default prompt, keep at least a brief message.
        notice_text = HISTORY_TRUNCATION_NOTICE_SHORT
    if notice_text:
        body_segments.append(notice_text)
    body_text = "\n\n".join(body_segments).strip()

    header_text = "\n".join(
        [
            f"Task {task.id} event history (latest {total_items} entries)",
            f"Title: {title_display}",
            f"Page: {normalized_page} / {total_pages}",
        ]
    )
    text = f"{header_text}\n\n{body_text}" if body_text else header_text
    prepared = _prepare_model_payload(text)
    if len(prepared) > TELEGRAM_MESSAGE_LIMIT:
        worker_log.warning(
            "History view still exceeds Telegram limit, use safety tips",
            extra={"task_id": task.id, "page": str(normalized_page), "length": str(len(prepared))},
        )
        text = "\n".join(
            [
                f"Task {task.id} event history (latest {total_items} entries)",
                f"Title: {title_display}",
                f"Page: {normalized_page} / {total_pages}",
                "",
                "⚠️ The history content exceeds Telegram's length limit. Please export or filter and try again.",
            ]
        )

    nav_row: list[InlineKeyboardButton] = []
    if normalized_page > 1:
        nav_row.append(
            InlineKeyboardButton(
                text="⬅️ Previous page",
                callback_data=f"{TASK_HISTORY_PAGE_CALLBACK}:{task.id}:{normalized_page - 1}",
            )
        )
    if normalized_page < total_pages:
        nav_row.append(
            InlineKeyboardButton(
                text="Next page ➡️",
                callback_data=f"{TASK_HISTORY_PAGE_CALLBACK}:{task.id}:{normalized_page + 1}",
            )
        )

    keyboard_rows: list[list[InlineKeyboardButton]] = []
    if nav_row:
        keyboard_rows.append(nav_row)
    keyboard_rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ Return to Task details",
                callback_data=f"{TASK_HISTORY_BACK_CALLBACK}:{task.id}",
            )
        ]
    )

    return text, InlineKeyboardMarkup(inline_keyboard=keyboard_rows), normalized_page, total_pages


async def _render_task_history(
    task_id: str,
    page: int,
) -> tuple[str, InlineKeyboardMarkup, int, int]:
    """Render the history view of the specified Task and return content, button and page code information."""

    task, _notes, history_records = await _load_task_context(task_id, include_history=True)
    trimmed = list(history_records[-MODEL_HISTORY_MAX_ITEMS:])
    if not trimmed:
        raise ValueError("No event record yet")
    return _build_task_history_view(task, trimmed, page=page)


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


ANSI_ESCAPE_RE = re.compile(r"\x1B[@-_][0-?]*[ -/]*[@-~]")


def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", text)


NOISE_PATTERNS = (
    "Working(",
    "Deciding whether to run command",
    "⌃J newline",
    "⌃T transcript",
    "⌃C quit",
    "tokens used",
    "Press Enter to confirm",
    "Select Approval Mode",
    "Find and fix a bug in @filename",
    "Write tests for @filename",
)


def postprocess_tmux_output(raw: str) -> str:
    text = normalize_newlines(raw)
    text = text.replace("\x08", "")
    text = strip_ansi(text)
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped in {"%", '"'}:
            continue
        if any(pattern in stripped for pattern in NOISE_PATTERNS):
            continue
        if stripped.startswith("▌"):
            stripped = stripped.lstrip("▌ ")
            if not stripped:
                continue
        lines.append(stripped)
    return "\n".join(lines).strip()


def _session_id_from_path(path: Optional[Path]) -> str:
    """Convert the session path to an ID used by the log."""
    if path is None:
        return "-"
    stem = path.stem
    return stem or path.name or "-"


def _session_extra(*, path: Optional[Path] = None, key: Optional[str] = None) -> Dict[str, str]:
    if key and path is None:
        try:
            path = Path(key)
        except Exception:
            return {"session": key or "-"}
    return {"session": _session_id_from_path(path)}


def _initialize_known_rollouts() -> None:
    if CODEX_SESSION_FILE_PATH:
        KNOWN_ROLLOUTS.add(str(resolve_path(CODEX_SESSION_FILE_PATH)))


def tmux_capture_since(log_path: Path | str, start_pos: int, idle: float = 2.0, timeout: float = 120.0) -> str:
    # Start reading from the log document offset until there is no new addition or timeout in the continuous idle Second
    start = time.time()
    p = resolve_path(log_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # Wait for the log document to appear
    for _ in range(50):
        if p.exists(): break
        time.sleep(0.1)
    buf = []
    last = time.time()
    with open(p, "r", encoding="utf-8", errors="ignore") as f:
        f.seek(start_pos)
        while True:
            chunk = f.read()
            if chunk:
                buf.append(chunk)
                last = time.time()
            else:
                time.sleep(0.2)
            if time.time() - last >= idle:
                break
            if time.time() - start > timeout:
                break
    return "".join(buf)


SESSION_OFFSETS: Dict[str, int] = {}
CHAT_SESSION_MAP: Dict[int, str] = {}
CHAT_WATCHERS: Dict[int, asyncio.Task] = {}
CHAT_LAST_MESSAGE: Dict[int, Dict[str, str]] = {}
CHAT_FAILURE_NOTICES: Dict[int, float] = {}
CHAT_PLAN_MESSAGES: Dict[int, int] = {}
CHAT_PLAN_TEXT: Dict[int, str] = {}
CHAT_PLAN_COMPLETION: Dict[int, bool] = {}
CHAT_DELIVERED_HASHES: Dict[int, Dict[str, set[str]]] = {}
CHAT_DELIVERED_OFFSETS: Dict[int, Dict[str, set[int]]] = {}
CHAT_REPLY_COUNT: Dict[int, Dict[str, int]] = {}
CHAT_COMPACT_STATE: Dict[int, Dict[str, Dict[str, Any]]] = {}
# Long polling state: used for delayed polling mechanism
CHAT_LONG_POLL_STATE: Dict[int, Dict[str, Any]] = {}
CHAT_LONG_POLL_LOCK: Optional[asyncio.Lock] = None  # Initialized after event loop starts
SUMMARY_REQUEST_TIMEOUT_SECONDS = 300.0


@dataclass(slots=True)
class PendingSummary:
    """Record the model summary request to be dropped into the database."""

    task_id: str
    request_id: str
    actor: Optional[str]
    session_key: str
    session_path: Path
    created_at: float
    buffer: str = ""


PENDING_SUMMARIES: Dict[str, PendingSummary] = {}

# --- TaskView context cache ---
TaskViewKind = Literal["list", "search", "detail", "history"]


@dataclass
class TaskViewState:
    """Cache the rendering parameters of the Task view and support message editing navigation."""

    kind: TaskViewKind
    data: Dict[str, Any]


TASK_VIEW_STACK: Dict[int, Dict[int, List[TaskViewState]]] = {}


def _task_view_stack(chat_id: int) -> Dict[int, List[TaskViewState]]:
    """Get the view stack mapping of the specified chat."""

    return TASK_VIEW_STACK.setdefault(chat_id, {})


def _push_task_view(chat_id: int, message_id: int, state: TaskViewState) -> None:
    """Push in a new view state, which is used to enter details and other scenes."""

    stack = _task_view_stack(chat_id).setdefault(message_id, [])
    stack.append(state)


def _replace_task_view(chat_id: int, message_id: int, state: TaskViewState) -> None:
    """Replace the top view of the stack, commonly used in list sub-page or refresh operations."""

    stack = _task_view_stack(chat_id).setdefault(message_id, [])
    if stack:
        stack[-1] = state
    else:
        stack.append(state)


def _peek_task_view(chat_id: int, message_id: int) -> Optional[TaskViewState]:
    """View the top view of the current stack."""

    stack = TASK_VIEW_STACK.get(chat_id, {}).get(message_id)
    if not stack:
        return None
    return stack[-1]


def _pop_task_view(chat_id: int, message_id: int) -> Optional[TaskViewState]:
    """Pop up the top view of the stack and clear the empty stack if necessary."""

    chat_views = TASK_VIEW_STACK.get(chat_id)
    if not chat_views:
        return None
    stack = chat_views.get(message_id)
    if not stack:
        return None
    state = stack.pop()
    if not stack:
        chat_views.pop(message_id, None)
    if not chat_views:
        TASK_VIEW_STACK.pop(chat_id, None)
    return state


def _clear_task_view(chat_id: int, message_id: Optional[int] = None) -> None:
    """Clean cache to prevent memory leaks or context pollution."""

    if message_id is None:
        TASK_VIEW_STACK.pop(chat_id, None)
        return
    chat_views = TASK_VIEW_STACK.get(chat_id)
    if not chat_views:
        return
    chat_views.pop(message_id, None)
    if not chat_views:
        TASK_VIEW_STACK.pop(chat_id, None)


def _init_task_view_context(message: Optional[Message], state: TaskViewState) -> None:
    """Initializes the view stack (newly sent list or search view) for the specified message."""

    if message is None:
        return
    chat = getattr(message, "chat", None)
    if chat is None:
        return
    chat_id = chat.id
    message_id = message.message_id
    _clear_task_view(chat_id, message_id)
    _push_task_view(chat_id, message_id, state)


def _set_task_view_context(message: Optional[Message], state: TaskViewState) -> None:
    """Update the stack top view of existing messages, keeping the existing history."""

    if message is None:
        return
    chat = getattr(message, "chat", None)
    if chat is None:
        return
    _replace_task_view(chat.id, message.message_id, state)


def _push_detail_view(message: Optional[Message], task_id: str) -> None:
    """Push the detail view into the view stack for easy rollback."""

    if message is None:
        return
    chat = getattr(message, "chat", None)
    if chat is None:
        return
    _push_task_view(
        chat.id,
        message.message_id,
        TaskViewState(kind="detail", data={"task_id": task_id}),
    )


def _pop_detail_view(message: Optional[Message]) -> Optional[TaskViewState]:
    """The details view pops up and the removed state is returned."""

    if message is None:
        return None
    chat = getattr(message, "chat", None)
    if chat is None:
        return None
    state = _pop_task_view(chat.id, message.message_id)
    if state and state.kind != "detail":
        # The top of the stack is not a detail, indicating that the context is abnormal and should be put back to avoid damaging the structure.
        _push_task_view(chat.id, message.message_id, state)
        return None
    return state


async def _render_task_view_from_state(state: TaskViewState) -> tuple[str, InlineKeyboardMarkup]:
    """Re-render the corresponding Task interface based on the view state."""

    if state.kind == "list":
        status = state.data.get("status")
        page = int(state.data.get("page", 1) or 1)
        limit = int(state.data.get("limit", DEFAULT_PAGE_SIZE) or DEFAULT_PAGE_SIZE)
        return await _build_task_list_view(status=status, page=page, limit=limit)
    if state.kind == "search":
        keyword = state.data.get("keyword", "")
        page = int(state.data.get("page", 1) or 1)
        limit = int(state.data.get("limit", DEFAULT_PAGE_SIZE) or DEFAULT_PAGE_SIZE)
        origin_status = state.data.get("origin_status")
        origin_page = int(state.data.get("origin_page", 1) or 1)
        return await _build_task_search_view(
            keyword,
            page=page,
            limit=limit,
            origin_status=origin_status,
            origin_page=origin_page,
        )
    if state.kind == "detail":
        task_id = state.data.get("task_id")
        if not task_id:
            raise ValueError("TaskDetails missing task_id")
        return await _render_task_detail(task_id)
    if state.kind == "history":
        task_id = state.data.get("task_id")
        if not task_id:
            raise ValueError("TaskHistory is missing tasks_id")
        page = int(state.data.get("page", 1) or 1)
        text, markup, _, _ = await _render_task_history(task_id, page)
        return text, markup
    raise ValueError(f"Unknown task view type: {state.kind}")


def _make_list_view_state(*, status: Optional[str], page: int, limit: int) -> TaskViewState:
    """Constructs the context of the list view."""

    return TaskViewState(
        kind="list",
        data={
            "status": status,
            "page": page,
            "limit": limit,
        },
    )


def _make_search_view_state(
    *,
    keyword: str,
    page: int,
    limit: int,
    origin_status: Optional[str],
    origin_page: int,
) -> TaskViewState:
    """Constructs the context of the search view."""

    return TaskViewState(
        kind="search",
        data={
            "keyword": keyword,
            "page": page,
            "limit": limit,
            "origin_status": origin_status,
            "origin_page": origin_page,
        },
    )


def _make_history_view_state(*, task_id: str, page: int) -> TaskViewState:
    """Construct the context of the history view."""

    return TaskViewState(
        kind="history",
        data={
            "task_id": task_id,
            "page": page,
        },
    )

ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-9;?]*[ -/]*[@-~]")


def _get_last_message(chat_id: int, session_key: str) -> Optional[str]:
    sessions = CHAT_LAST_MESSAGE.get(chat_id)
    if not sessions:
        return None
    return sessions.get(session_key)


def _set_last_message(chat_id: int, session_key: str, text: str) -> None:
    CHAT_LAST_MESSAGE.setdefault(chat_id, {})[session_key] = text


def _clear_last_message(chat_id: int, session_key: Optional[str] = None) -> None:
    if session_key is None:
        CHAT_LAST_MESSAGE.pop(chat_id, None)
        return
    sessions = CHAT_LAST_MESSAGE.get(chat_id)
    if not sessions:
        return
    sessions.pop(session_key, None)
    if not sessions:
        CHAT_LAST_MESSAGE.pop(chat_id, None)


def _reset_delivered_hashes(chat_id: int, session_key: Optional[str] = None) -> None:
    if session_key is None:
        removed = CHAT_DELIVERED_HASHES.pop(chat_id, None)
        if removed:
            worker_log.info(
                "Clear a chat's sent message hash",
                extra={"chat": chat_id},
            )
        return
    sessions = CHAT_DELIVERED_HASHES.get(chat_id)
    if not sessions:
        return
    if session_key in sessions:
        sessions.pop(session_key, None)
        worker_log.info(
            "Clear the session's sent message hash",
            extra={
                "chat": chat_id,
                **_session_extra(key=session_key),
            },
        )
    if not sessions:
        CHAT_DELIVERED_HASHES.pop(chat_id, None)


def _get_delivered_hashes(chat_id: int, session_key: str) -> set[str]:
    return CHAT_DELIVERED_HASHES.setdefault(chat_id, {}).setdefault(session_key, set())


def _reset_compact_tracking(chat_id: int, session_key: Optional[str] = None) -> None:
    """Clean up and automatically compress related states to avoid historical counting from affecting subsequent judgments."""

    if session_key is None:
        CHAT_REPLY_COUNT.pop(chat_id, None)
        CHAT_COMPACT_STATE.pop(chat_id, None)
        return

    reply_sessions = CHAT_REPLY_COUNT.get(chat_id)
    if reply_sessions is not None:
        reply_sessions.pop(session_key, None)
        if not reply_sessions:
            CHAT_REPLY_COUNT.pop(chat_id, None)

    compact_sessions = CHAT_COMPACT_STATE.get(chat_id)
    if compact_sessions is not None:
        compact_sessions.pop(session_key, None)
        if not compact_sessions:
            CHAT_COMPACT_STATE.pop(chat_id, None)


def _increment_reply_count(chat_id: int, session_key: str) -> int:
    sessions = CHAT_REPLY_COUNT.setdefault(chat_id, {})
    sessions[session_key] = sessions.get(session_key, 0) + 1
    return sessions[session_key]


def _cleanup_expired_summaries() -> None:
    """Remove unfinished digest requests that have timed out."""

    if not PENDING_SUMMARIES:
        return
    now = time.monotonic()
    expired = [
        key
        for key, pending in PENDING_SUMMARIES.items()
        if now - pending.created_at > SUMMARY_REQUEST_TIMEOUT_SECONDS
    ]
    for key in expired:
        PENDING_SUMMARIES.pop(key, None)
        worker_log.info(
            "Digest request timeout cleared",
            extra={"session": key},
        )


def _extract_task_ids_from_text(text: str) -> list[str]:
    """Extract standard Task numbers from model text."""

    if not text:
        return []
    matches = TASK_REFERENCE_PATTERN.findall(text)
    normalized: list[str] = []
    for token in matches:
        normalized_id = _normalize_task_id(token)
        if normalized_id and normalized_id not in normalized:
            normalized.append(normalized_id)
    return normalized


async def _log_model_reply_event(
    task_id: str,
    *,
    content: str,
    session_path: Path,
    event_offset: int,
) -> None:
    """Model replies are no longer persisted to history."""

    worker_log.debug(
        "Skipping history write for model reply",
        extra={"task_id": task_id, "session": str(session_path)},
    )


def _sanitize_summary_text(text: Optional[str]) -> str:
    """Normalize escaped underscores while retaining user formatting."""

    if not text:
        return ""
    return text.replace("\\_", "_")


async def _maybe_finalize_summary(
    session_key: str,
    *,
    content: str,
    event_offset: int,
    session_path: Path,
) -> None:
    """Detect and log the summary returned by the model."""

    pending = PENDING_SUMMARIES.get(session_key)
    if not pending:
        return
    sanitized_buffer = _sanitize_summary_text(pending.buffer or "")
    sanitized_content = _sanitize_summary_text(content)
    combined_text = (
        f"{sanitized_buffer}\n{sanitized_content}"
        if sanitized_buffer
        else sanitized_content
    )
    normalized_combined = unicodedata.normalize("NFKC", combined_text)
    tag_pattern = re.compile(
        rf"SUMMARY_REQUEST_ID\s*(?:[:：]{{1,2}})?\s*{re.escape(pending.request_id)}",
        re.IGNORECASE,
    )
    if not tag_pattern.search(normalized_combined):
        pending.buffer = combined_text
        return
    summary_text = combined_text
    trimmed = _trim_history_value(summary_text, limit=HISTORY_DISPLAY_VALUE_LIMIT)
    payload = {
        "request_id": pending.request_id,
        "model": ACTIVE_MODEL or "",
        "session": str(session_path),
        "offset": event_offset,
    }
    if summary_text:
        payload["content"] = summary_text[:MODEL_SUMMARY_PAYLOAD_LIMIT]
    try:
        await TASK_SERVICE.log_task_event(
            pending.task_id,
            event_type="model_summary",
            actor=pending.actor,
            new_value=trimmed,
            payload=payload,
        )
    except ValueError:
        worker_log.warning(
            "Summary writing fails: Task does not exist",
            extra={"task_id": pending.task_id, **_session_extra(path=session_path)},
        )
    finally:
        PENDING_SUMMARIES.pop(session_key, None)


async def _handle_model_response(
    *,
    chat_id: int,
    session_key: str,
    session_path: Path,
    event_offset: int,
    content: str,
) -> None:
    """Unify the persistence model output and process the summary dropout."""

    _cleanup_expired_summaries()
    await _maybe_finalize_summary(
        session_key,
        content=content,
        event_offset=event_offset,
        session_path=session_path,
    )
    # History is recorded only when the summary request is discarded; regular model replies are no longer written to task_history.
    return


def _set_reply_count(chat_id: int, session_key: str, value: int) -> None:
    sessions = CHAT_REPLY_COUNT.setdefault(chat_id, {})
    sessions[session_key] = max(value, 0)


def _get_compact_state(chat_id: int, session_key: str) -> Dict[str, Any]:
    sessions = CHAT_COMPACT_STATE.setdefault(chat_id, {})
    state = sessions.get(session_key)
    if state is None:
        state = {"pending": False, "triggered_at": 0.0}
        sessions[session_key] = state
    return state


def _is_compact_pending(chat_id: int, session_key: str) -> bool:
    return bool(_get_compact_state(chat_id, session_key).get("pending"))


def _mark_compact_pending(chat_id: int, session_key: str) -> None:
    state = _get_compact_state(chat_id, session_key)
    state["pending"] = True
    state["triggered_at"] = time.monotonic()


def _clear_compact_pending(chat_id: int, session_key: str) -> float:
    state = _get_compact_state(chat_id, session_key)
    started = float(state.get("triggered_at") or 0.0)
    state["pending"] = False
    state["triggered_at"] = 0.0
    return started


async def _send_plain_notice(chat_id: int, text: str) -> None:
    """Send prompt messages to users without requiring Markdown format."""

    bot = current_bot()

    async def _do() -> None:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode=None)

    await _send_with_retry(_do)


async def _maybe_trigger_auto_compact(chat_id: int, session_key: str, count: int) -> None:
    """Trigger /compact automatically once the reply threshold is reached and notify the user."""

    if AUTO_COMPACT_THRESHOLD <= 0:
        return
    if count < AUTO_COMPACT_THRESHOLD:
        return
    if _is_compact_pending(chat_id, session_key):
        return

    notice = (
        f"The model has responded {count} times in a row. Ready to automate /compact; please wait."
    )
    await _send_plain_notice(chat_id, notice)

    try:
        tmux_send_line(TMUX_SESSION, "/compact")
    except subprocess.CalledProcessError as exc:
        worker_log.error(
            "Automatic /compact trigger failed: %s",
            exc,
            extra={
                "chat": chat_id,
                **_session_extra(key=session_key),
            },
        )
        failure_text = f"Automatic /compact execution failed: {exc}"
        await _send_plain_notice(chat_id, failure_text)
        fallback = max(AUTO_COMPACT_THRESHOLD - 1, 0)
        _set_reply_count(chat_id, session_key, fallback)
        return

    _set_reply_count(chat_id, session_key, 0)
    _mark_compact_pending(chat_id, session_key)

    worker_log.info(
        "Sent automatically /compact",
        extra={
            "chat": chat_id,
            **_session_extra(key=session_key),
            "threshold": str(AUTO_COMPACT_THRESHOLD),
        },
    )

    await _send_plain_notice(chat_id, "Waiting for sorting results. /compact has been sent to the model.")


async def _post_delivery_compact_checks(chat_id: int, session_key: str) -> None:
    """Perform counting and automatic compression checks after model messages are sent successfully."""

    if _is_compact_pending(chat_id, session_key):
        started = _clear_compact_pending(chat_id, session_key)
        elapsed = 0.0
        if started > 0:
            elapsed = max(time.monotonic() - started, 0.0)
        duration_hint = f" (elapsed time ~{elapsed:.1f}s)" if elapsed > 0 else ""
        await _send_plain_notice(
            chat_id,
            f"Automatic /compact execution Completed{duration_hint}.",
        )
        _set_reply_count(chat_id, session_key, 0)

    if AUTO_COMPACT_THRESHOLD <= 0:
        return

    new_count = _increment_reply_count(chat_id, session_key)
    await _maybe_trigger_auto_compact(chat_id, session_key, new_count)


def _reset_delivered_offsets(chat_id: int, session_key: Optional[str] = None) -> None:
    if session_key is None:
        removed = CHAT_DELIVERED_OFFSETS.pop(chat_id, None)
        if removed:
            worker_log.info(
                "Clear the chat's processed event offset",
                extra={"chat": chat_id},
            )
        _reset_compact_tracking(chat_id)
        return
    sessions = CHAT_DELIVERED_OFFSETS.get(chat_id)
    if not sessions:
        return
    if session_key in sessions:
        sessions.pop(session_key, None)
        worker_log.info(
            "Clear the session's processed event offset",
            extra={
                "chat": chat_id,
                **_session_extra(key=session_key),
            },
        )
    if not sessions:
        CHAT_DELIVERED_OFFSETS.pop(chat_id, None)
    _reset_compact_tracking(chat_id, session_key)


def _get_delivered_offsets(chat_id: int, session_key: str) -> set[int]:
    return CHAT_DELIVERED_OFFSETS.setdefault(chat_id, {}).setdefault(session_key, set())


async def _deliver_pending_messages(
    chat_id: int,
    session_path: Path,
    *,
    add_completion_header: bool = True
) -> bool:
    """Send pending model messages.

    Args:
        chat_id: Telegram Chat ID
        session_path: Session document path
        add_completion_header: Whether to add an explicit completion header (True for fast polling phase, False for delayed polling)
    """
    session_key = str(session_path)
    previous_offset = SESSION_OFFSETS.get(session_key, 0)
    new_offset, events = _read_session_events(session_path)
    delivered_response = False
    last_sent = _get_last_message(chat_id, session_key)
    delivered_hashes = _get_delivered_hashes(chat_id, session_key)
    delivered_offsets = _get_delivered_offsets(chat_id, session_key)
    last_committed_offset = previous_offset

    if not events:
        _store_session_offset(session_key, max(previous_offset, new_offset))
        return False

    worker_log.info(
        "Detected model event to be sent",
        extra={
            **_session_extra(path=session_path),
            "chat": chat_id,
            "events": str(len(events)),
            "offset_before": str(previous_offset),
            "offset_after": str(new_offset),
        },
    )

    for deliverable in events:
        event_offset = deliverable.offset
        text_to_send = (deliverable.text or "").rstrip("\n")
        if event_offset in delivered_offsets:
            worker_log.info(
                "Skip handled model events",
                extra={
                    **_session_extra(path=session_path),
                    "chat": chat_id,
                    "offset": str(event_offset),
                },
            )
            last_committed_offset = event_offset
            _store_session_offset(session_key, event_offset)
            continue
        if not text_to_send:
            last_committed_offset = event_offset
            _store_session_offset(session_key, event_offset)
            continue
        if deliverable.kind == DELIVERABLE_KIND_PLAN:
            if ENABLE_PLAN_PROGRESS:
                plan_completed = False
                if deliverable.metadata and "plan_completed" in deliverable.metadata:
                    plan_completed = bool(deliverable.metadata.get("plan_completed"))
                worker_log.info(
                    "Update plan progress",
                    extra={
                        **_session_extra(path=session_path),
                        "chat": chat_id,
                        "offset": str(event_offset),
                        "plan_completed": str(plan_completed),
                    },
                )
                await _update_plan_progress(
                    chat_id,
                    text_to_send,
                    plan_completed=plan_completed,
                )
                # The planned event may exist in the same batch and continue to follow the model output. The local state is refreshed here to avoid misjudgment.
                plan_active = ENABLE_PLAN_PROGRESS and (chat_id in CHAT_PLAN_TEXT)
                plan_completed_flag = bool(CHAT_PLAN_COMPLETION.get(chat_id))
            delivered_offsets.add(event_offset)
            last_committed_offset = event_offset
            _store_session_offset(session_key, event_offset)
            continue
        if deliverable.kind != DELIVERABLE_KIND_MESSAGE:
            delivered_offsets.add(event_offset)
            last_committed_offset = event_offset
            _store_session_offset(session_key, event_offset)
            continue
        # Determine the Where to add completion prefix based on the polling phase
        formatted_text = _prepend_completion_header(text_to_send) if add_completion_header else text_to_send
        hash_variants = _hash_delivery_variants(formatted_text)
        if delivered_hashes.intersection(hash_variants):
            worker_log.info(
                "Skip duplicate model output",
                extra={
                    **_session_extra(path=session_path),
                    "chat": chat_id,
                    "offset": str(event_offset),
                },
            )
            delivered_offsets.add(event_offset)
            last_committed_offset = event_offset
            _store_session_offset(session_key, event_offset)
            continue
        worker_log.info(
            "Prepare to send model output",
            extra={
                **_session_extra(path=session_path),
                "chat": chat_id,
                "offset": str(event_offset),
                "length": str(len(formatted_text)),
            },
        )
        try:
            delivered_payload = await reply_large_text(chat_id, formatted_text)
        except TelegramBadRequest as exc:
            _store_session_offset(session_key, previous_offset)
            _clear_last_message(chat_id, session_key)
            worker_log.error(
                "Send message fail (request is invalid): %s",
                exc,
                extra={
                    **_session_extra(path=session_path),
                    "chat": chat_id,
                    "offset": event_offset,
                },
            )
            await _notify_send_failure_message(chat_id)
            return False
        except (TelegramNetworkError, TelegramRetryAfter) as exc:
            _store_session_offset(session_key, last_committed_offset)
            _clear_last_message(chat_id, session_key)
            worker_log.warning(
                "Send message fails and will try again: %s",
                exc,
                extra={
                    **_session_extra(path=session_path),
                    "chat": chat_id,
                    "offset": last_committed_offset,
                },
            )
            await _notify_send_failure_message(chat_id)
            return False
        else:
            delivered_response = True
            last_sent = delivered_payload
            _set_last_message(chat_id, session_key, delivered_payload or formatted_text)
            payload_variant = delivered_payload or formatted_text
            delivered_hashes.update(hash_variants)
            delivered_hashes.update(_hash_delivery_variants(payload_variant))
            delivered_offsets.add(event_offset)
            CHAT_FAILURE_NOTICES.pop(chat_id, None)
            last_committed_offset = event_offset
            _store_session_offset(session_key, event_offset)
            worker_log.info(
                "Model output sent successfully",
                extra={
                    **_session_extra(path=session_path),
                    "chat": chat_id,
                    "offset": str(event_offset),
                    "length": str(len(formatted_text)),
                },
            )
            if session_path is not None:
                await _handle_model_response(
                    chat_id=chat_id,
                    session_key=session_key,
                    session_path=session_path,
                    event_offset=event_offset,
                    content=delivered_payload or formatted_text,
                )
            await _post_delivery_compact_checks(chat_id, session_key)
            if not ENABLE_PLAN_PROGRESS:
                CHAT_PLAN_TEXT.pop(chat_id, None)
                CHAT_PLAN_MESSAGES.pop(chat_id, None)
                CHAT_PLAN_COMPLETION.pop(chat_id, None)

    plan_active = ENABLE_PLAN_PROGRESS and (chat_id in CHAT_PLAN_TEXT)
    plan_completed_flag = bool(CHAT_PLAN_COMPLETION.get(chat_id))
    final_response_sent = session_key in (CHAT_LAST_MESSAGE.get(chat_id) or {})

    if ENABLE_PLAN_PROGRESS and plan_active and plan_completed_flag and final_response_sent:
        await _finalize_plan_progress(chat_id)
        plan_active = False
        plan_completed_flag = False

    if not delivered_response:
        worker_log.info(
            "No sendable model output found in this round",
            extra={
                **_session_extra(path=session_path),
                "chat": chat_id,
                "offset": str(last_committed_offset),
            },
        )
        _store_session_offset(session_key, max(last_committed_offset, new_offset))

    if delivered_response:
        # The message is actually sent, and returning True indicates that this call was sent successfully.
        # This ensures that the deferred polling mechanism is triggered correctly
        if ENABLE_PLAN_PROGRESS and plan_active:
            worker_log.info(
                "Model output has been sent, but plans are still being updated",
                extra={
                    **_session_extra(path=session_path),
                    "chat": chat_id,
                },
            )
            return False
        else:
            worker_log.info(
                "Model output sent and scheduled completed",
                extra={
                    **_session_extra(path=session_path),
                    "chat": chat_id,
                },
            )
        return True

    if ENABLE_PLAN_PROGRESS and not plan_active and final_response_sent:
        worker_log.info(
            "Historical responses already exist and are confirmed to be completed after the plan is closed.",
            extra={
                **_session_extra(path=session_path),
                "chat": chat_id,
            },
        )
        return True

    return False


async def _ensure_session_watcher(chat_id: int) -> Optional[Path]:
    """Make sure the specified chat is bound to the Codex session and starts listening."""

    pointer_path: Optional[Path] = None
    if CODEX_SESSION_FILE_PATH:
        pointer_path = resolve_path(CODEX_SESSION_FILE_PATH)

    session_path: Optional[Path] = None
    previous_key = CHAT_SESSION_MAP.get(chat_id)
    if previous_key:
        candidate = resolve_path(previous_key)
        if candidate.exists():
            session_path = candidate
        else:
            worker_log.warning(
                "[session-map] chat=%s The recorded session document does not exist and is ready to be relocated.",
                chat_id,
                extra={"session": previous_key},
            )

    target_cwd = CODEX_WORKDIR or None

    lock_session = _read_session_lock_path()
    lock_required = _is_session_lock_enforced()
    if lock_session is not None:
        if session_path is None or session_path != lock_session:
            worker_log.info(
                "[session-map] chat=%s use lock session %s",
                chat_id,
                lock_session,
                extra=_session_extra(path=lock_session),
            )
        _sync_pointer_with_lock(pointer_path, lock_session)
        session_path = lock_session
    else:
        if lock_required:
            worker_log.error(
                "[session-lock] Session lock required but missing during watcher ensure",
                extra={"chat": chat_id, "lock_file": SESSION_LOCK_FILE_PATH or "-"},
            )
            return None
        if session_path is None and pointer_path is not None:
            session_path = _read_pointer_path(pointer_path)
            if session_path is not None:
                worker_log.info(
                    "[session-map] chat=%s pointer -> %s",
                    chat_id,
                    session_path,
                    extra=_session_extra(path=session_path),
                )
        if session_path is None and pointer_path is not None:
            latest = _find_latest_rollout_for_cwd(pointer_path, target_cwd)
            if latest is not None:
                session_path = latest
                _update_pointer(pointer_path, latest)
                worker_log.info(
                    "[session-map] chat=%s locate latest rollout %s",
                    chat_id,
                    session_path,
                    extra=_session_extra(path=session_path),
                )

        if pointer_path is not None and _is_claudecode_model():
            fallback = _find_latest_claudecode_rollout(pointer_path)
            if fallback is not None and fallback != session_path:
                session_path = fallback
                _update_pointer(pointer_path, session_path)
                worker_log.info(
                    "[session-map] chat=%s resume ClaudeCode session %s",
                    chat_id,
                    session_path,
                    extra=_session_extra(path=session_path),
                )

        if session_path is None and pointer_path is not None:
            session_path = await _await_session_path(pointer_path, target_cwd)
            if session_path is not None:
                _update_pointer(pointer_path, session_path)
                worker_log.info(
                    "[session-map] chat=%s bind fresh session %s",
                    chat_id,
                    session_path,
                    extra=_session_extra(path=session_path),
                )
        if session_path is None and pointer_path is not None and _is_claudecode_model():
            fallback = _find_latest_claudecode_rollout(pointer_path)
            if fallback is not None:
                session_path = fallback
                _update_pointer(pointer_path, session_path)
                worker_log.info(
                    "[session-map] chat=%s fallback bind ClaudeCode session %s",
                    chat_id,
                    session_path,
                    extra=_session_extra(path=session_path),
                )

    if session_path is None:
        worker_log.warning(
            "[session-map] chat=%s Unable to determine Codex session",
            chat_id,
        )
        return None

    session_key = str(session_path)
    if session_key not in SESSION_OFFSETS:
        _init_session_offset(session_path)
        worker_log.info(
            "[session-map] init offset for %s -> %s",
            session_key,
            SESSION_OFFSETS[session_key],
            extra=_session_extra(key=session_key),
        )

    if previous_key != session_key:
        _clear_last_message(chat_id)
        _reset_compact_tracking(chat_id)
        CHAT_FAILURE_NOTICES.pop(chat_id, None)

    CHAT_SESSION_MAP[chat_id] = session_key

    try:
        delivered = await _deliver_pending_messages(chat_id, session_path)
        if delivered:
            worker_log.info(
                "[session-map] chat=%s Pending output has been sent immediately",
                chat_id,
                extra=_session_extra(path=session_path),
            )
            return session_path
    except Exception as exc:  # noqa: BLE001
        worker_log.warning(
            "Check Codex event fail after push: %s",
            exc,
            extra={"chat": chat_id, **_session_extra(path=session_path)},
        )

    watcher = CHAT_WATCHERS.get(chat_id)
    if watcher is not None and not watcher.done():
        return session_path
    if watcher is not None and watcher.done():
        CHAT_WATCHERS.pop(chat_id, None)

    # Interrupt old deferred polling if present
    await _interrupt_long_poll(chat_id)

    CHAT_WATCHERS[chat_id] = asyncio.create_task(
        _watch_and_notify(
            chat_id,
            session_path,
            max_wait=WATCH_MAX_WAIT,
            interval=WATCH_INTERVAL,
        )
    )
    return session_path


async def _update_plan_progress(chat_id: int, plan_text: str, *, plan_completed: bool) -> bool:
    if not ENABLE_PLAN_PROGRESS:
        return False
    CHAT_PLAN_COMPLETION[chat_id] = plan_completed
    if CHAT_PLAN_TEXT.get(chat_id) == plan_text:
        worker_log.debug(
            "The content of the plan progress has not changed and the update will be skipped.",
            extra={"chat": chat_id},
        )
        return True

    bot = current_bot()
    message_id = CHAT_PLAN_MESSAGES.get(chat_id)
    parse_mode = _plan_parse_mode_value()

    if message_id is None:
        sent_message: Optional[Message] = None

        async def _send_plan_payload(payload: str) -> None:
            nonlocal sent_message

            async def _do() -> None:
                nonlocal sent_message
                sent_message = await bot.send_message(
                    chat_id=chat_id,
                    text=payload,
                    parse_mode=parse_mode,
                    disable_notification=True,
                )

            await _send_with_retry(_do)

        async def _send_plan_payload_raw(payload: str) -> None:
            nonlocal sent_message

            async def _do() -> None:
                nonlocal sent_message
                sent_message = await bot.send_message(
                    chat_id=chat_id,
                    text=payload,
                    parse_mode=None,
                    disable_notification=True,
                )

            await _send_with_retry(_do)

        try:
            await _send_with_markdown_guard(
                plan_text,
                _send_plan_payload,
                raw_sender=_send_plan_payload_raw,
            )
        except TelegramBadRequest as exc:
            worker_log.warning(
                "The plan progress will fail and updates will be stopped.: %s",
                exc,
                extra={"chat": chat_id},
            )
            return False
        except (TelegramNetworkError, TelegramRetryAfter) as exc:
            worker_log.warning(
                "Plan progress sending encountered network exception: %s",
                exc,
                extra={"chat": chat_id},
            )
            return False

        if sent_message is None:
            return False

        message_id = sent_message.message_id
        CHAT_PLAN_MESSAGES[chat_id] = message_id
        worker_log.info(
            "Plan progress message sent",
            extra={
                "chat": chat_id,
                "message_id": message_id,
                "length": len(plan_text),
            },
        )
    else:
        async def _edit_payload(payload: str) -> None:

            async def _do() -> None:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=payload,
                    parse_mode=parse_mode,
                )

            await _send_with_retry(_do)

        async def _edit_payload_raw(payload: str) -> None:

            async def _do() -> None:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=payload,
                    parse_mode=None,
                )

            await _send_with_retry(_do)

        try:
            await _send_with_markdown_guard(
                plan_text,
                _edit_payload,
                raw_sender=_edit_payload_raw,
            )
        except TelegramBadRequest as exc:
            CHAT_PLAN_TEXT.pop(chat_id, None)
            removed_id = CHAT_PLAN_MESSAGES.pop(chat_id, None)
            worker_log.warning(
                "Planned progress editing fails and updates will be stopped.: %s",
                exc,
                extra={"chat": chat_id, "message_id": removed_id},
            )
            return False
        except (TelegramNetworkError, TelegramRetryAfter) as exc:
            worker_log.warning(
                "Plan progress editor encountered network exception: %s",
                exc,
                extra={"chat": chat_id, "message_id": message_id},
            )
            return False
        worker_log.info(
            "Program progress message edited",
            extra={
                "chat": chat_id,
                "message_id": message_id,
                "length": len(plan_text),
            },
        )

    CHAT_PLAN_TEXT[chat_id] = plan_text
    return True


async def _finalize_plan_progress(chat_id: int) -> None:
    CHAT_PLAN_TEXT.pop(chat_id, None)
    CHAT_PLAN_MESSAGES.pop(chat_id, None)
    CHAT_PLAN_COMPLETION.pop(chat_id, None)




async def _interrupt_long_poll(chat_id: int) -> None:
    """
    Interrupts delayed polling for the specified chat.

    Called when the user sends a new message, ensuring that the old deferred polling is terminated,
    Make way for the new listening task.

    Thread safety: use asyncio.Lock Protect state access.
    """
    if CHAT_LONG_POLL_LOCK is None:
        state = CHAT_LONG_POLL_STATE.get(chat_id)
        if state is not None:
            state["interrupted"] = True
            worker_log.info(
                "Mark deferred polling as pending",
                extra={"chat": chat_id},
            )
        return

    async with CHAT_LONG_POLL_LOCK:
        state = CHAT_LONG_POLL_STATE.get(chat_id)
        if state is not None:
            state["interrupted"] = True
            worker_log.info(
                "Mark deferred polling as pending",
                extra={"chat": chat_id},
            )


async def _watch_and_notify(chat_id: int, session_path: Path,
                            max_wait: float, interval: float):
    """
    Listen to the session document and forward messages to the user.

    Two-stage polling strategy:
    - Phase 1 (fast polling): check every 0.3 seconds until the first delivery succeeds.
    - Phase 2 (slow polling): check every 3 seconds (up to 600 attempts, about 30 minutes) to capture long-running output.

    Reliability: a try/finally block guarantees cleanup even when errors occur.
    Interrupt behaviour: when a new Telegram message arrives, the interrupted flag stops further polling.
    """
    start = time.monotonic()
    first_delivery_done = False
    current_interval = interval  # Initially the fast polling interval (0.3 Second)
    long_poll_rounds = 0
    long_poll_max_rounds = 600  # 30 minute / 3 Second = 600 times
    long_poll_interval = 3.0  # 3 Second

    try:
        while True:
            # Check if interrupted by new message (protected with lock)
            if CHAT_LONG_POLL_LOCK is not None:
                async with CHAT_LONG_POLL_LOCK:
                    state = CHAT_LONG_POLL_STATE.get(chat_id)
                    if state is not None and state.get("interrupted", False):
                        worker_log.info(
                            "Delayed polling interrupted by new messages",
                            extra={
                                **_session_extra(path=session_path),
                                "chat": chat_id,
                                "round": long_poll_rounds,
                            },
                        )
                        return

            await asyncio.sleep(current_interval)

            # Check timeout (only during fast polling phase)
            if not first_delivery_done and max_wait > 0 and time.monotonic() - start > max_wait:
                worker_log.warning(
                    "[session-map] chat=%s Codex output has not been obtained for a long time, so polling is stopped.",
                    chat_id,
                    extra=_session_extra(path=session_path),
                )
                return

            if not session_path.exists():
                continue

            try:
                # The prefix is added during the fast polling phase and not added during the delayed polling phase.
                delivered = await _deliver_pending_messages(
                    chat_id,
                    session_path,
                    add_completion_header=not first_delivery_done
                )
            except Exception as exc:
                worker_log.error(
                    "An unexpected exception occurred while sending the message",
                    exc_info=exc,
                    extra={
                        **_session_extra(path=session_path),
                        "chat": chat_id,
                    },
                )
                delivered = False

            # The first transmission is successful and switches to delayed polling mode.
            if delivered and not first_delivery_done:
                first_delivery_done = True
                current_interval = long_poll_interval
                if CHAT_LONG_POLL_LOCK is not None:
                    async with CHAT_LONG_POLL_LOCK:
                        CHAT_LONG_POLL_STATE[chat_id] = {
                            "active": True,
                            "round": 0,
                            "max_rounds": long_poll_max_rounds,
                            "interrupted": False,
                        }
                else:
                    CHAT_LONG_POLL_STATE[chat_id] = {
                        "active": True,
                        "round": 0,
                        "max_rounds": long_poll_max_rounds,
                        "interrupted": False,
                    }
                worker_log.info(
                    "The first transmission is successful and delayed polling mode is started.",
                    extra={
                        **_session_extra(path=session_path),
                        "chat": chat_id,
                        "interval": long_poll_interval,
                        "max_rounds": long_poll_max_rounds,
                    },
                )
                continue

            # Delayed polling phase
            if first_delivery_done:
                if delivered:
                    # New message received, reset polling count
                    long_poll_rounds = 0
                    if CHAT_LONG_POLL_LOCK is not None:
                        async with CHAT_LONG_POLL_LOCK:
                            state = CHAT_LONG_POLL_STATE.get(chat_id)
                            if state is not None:
                                state["round"] = 0
                    else:
                        state = CHAT_LONG_POLL_STATE.get(chat_id)
                        if state is not None:
                            state["round"] = 0
                    worker_log.info(
                        "New message received in delayed polling, reset count",
                        extra={
                            **_session_extra(path=session_path),
                            "chat": chat_id,
                        },
                    )
                else:
                    # No new messages, increase polling count
                    long_poll_rounds += 1
                    if CHAT_LONG_POLL_LOCK is not None:
                        async with CHAT_LONG_POLL_LOCK:
                            state = CHAT_LONG_POLL_STATE.get(chat_id)
                            if state is not None:
                                state["round"] = long_poll_rounds
                    else:
                        state = CHAT_LONG_POLL_STATE.get(chat_id)
                        if state is not None:
                            state["round"] = long_poll_rounds

                    if long_poll_rounds >= long_poll_max_rounds:
                        worker_log.info(
                            "Delayed polling reaches the maximum number of times and stops listening.",
                            extra={
                                **_session_extra(path=session_path),
                                "chat": chat_id,
                                "total_rounds": long_poll_rounds,
                            },
                        )
                        return

                    worker_log.debug(
                        "No new messages in delayed polling",
                        extra={
                            **_session_extra(path=session_path),
                            "chat": chat_id,
                            "round": f"{long_poll_rounds}/{long_poll_max_rounds}",
                        },
                    )
                continue

            # Fast polling phase: if message has been sent, exit
            if delivered:
                return

    finally:
        # Make sure to clean up the deferred polling state anyway
        if CHAT_LONG_POLL_LOCK is not None:
            async with CHAT_LONG_POLL_LOCK:
                if chat_id in CHAT_LONG_POLL_STATE:
                    CHAT_LONG_POLL_STATE.pop(chat_id, None)
                    worker_log.debug(
                        "The listening task exits and the delayed polling state has been cleared",
                        extra={"chat": chat_id},
                    )
        else:
            if chat_id in CHAT_LONG_POLL_STATE:
                CHAT_LONG_POLL_STATE.pop(chat_id, None)
                worker_log.debug(
                    "The listening task exits and the delayed polling state has been cleared",
                    extra={"chat": chat_id},
                )


_SESSION_LOCK_CACHE_MTIME: Optional[float] = None
_SESSION_LOCK_CACHE_VALUE: Optional[Path] = None


def _session_lock_file() -> Optional[Path]:
    if not SESSION_LOCK_FILE_PATH:
        return None
    return resolve_path(SESSION_LOCK_FILE_PATH)


def _is_session_lock_enforced() -> bool:
    """Return True when the worker must rely on the captured session lock."""

    return SESSION_LOCK_REQUIRED and bool(SESSION_LOCK_FILE_PATH)


def _session_lock_missing_message() -> str:
    """Build a human-readable error when the session lock is missing."""

    target = SESSION_LOCK_FILE_PATH or "session_lock.json"
    return (
        "当前 worker 未检测到会话锁，无法定位本项目独占的模型会话。\n"
        f"缺失的锁文件：{target}\n"
        "请在对应项目目录重新执行 scripts/run_bot.sh（或等效启动脚本）以捕获新的 tmux 会话。"
    )


def _read_session_lock_path() -> Optional[Path]:
    """Read the persisted session lock and return the rollout path when valid."""

    lock_file = _session_lock_file()
    if lock_file is None:
        return None
    global _SESSION_LOCK_CACHE_MTIME, _SESSION_LOCK_CACHE_VALUE

    try:
        stat = lock_file.stat()
    except FileNotFoundError:
        _SESSION_LOCK_CACHE_MTIME = None
        _SESSION_LOCK_CACHE_VALUE = None
        return None

    mtime = stat.st_mtime
    if _SESSION_LOCK_CACHE_MTIME == mtime and _SESSION_LOCK_CACHE_VALUE is not None:
        return _SESSION_LOCK_CACHE_VALUE

    try:
        raw = lock_file.read_text(encoding="utf-8")
    except OSError:
        _SESSION_LOCK_CACHE_MTIME = mtime
        _SESSION_LOCK_CACHE_VALUE = None
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        worker_log.warning(
            "[session-lock] Invalid JSON payload",
            extra={"lock": str(lock_file)},
        )
        _SESSION_LOCK_CACHE_MTIME = mtime
        _SESSION_LOCK_CACHE_VALUE = None
        return None

    session_raw = payload.get("session_path")
    if not isinstance(session_raw, str) or not session_raw.strip():
        _SESSION_LOCK_CACHE_MTIME = mtime
        _SESSION_LOCK_CACHE_VALUE = None
        return None

    rollout = resolve_path(session_raw.strip())
    if not rollout.exists():
        worker_log.warning(
            "[session-lock] Recorded session file is missing",
            extra={"session": str(rollout)},
        )
        _SESSION_LOCK_CACHE_MTIME = mtime
        _SESSION_LOCK_CACHE_VALUE = None
        return None

    tmux_name = payload.get("tmux_session")
    if tmux_name and isinstance(tmux_name, str) and tmux_name.strip() and tmux_name.strip() != TMUX_SESSION:
        worker_log.info(
            "[session-lock] tmux mismatch, ignoring lock",
            extra={"lock_session": tmux_name.strip(), "tmux": TMUX_SESSION},
        )
        _SESSION_LOCK_CACHE_MTIME = mtime
        _SESSION_LOCK_CACHE_VALUE = None
        return None

    _SESSION_LOCK_CACHE_MTIME = mtime
    _SESSION_LOCK_CACHE_VALUE = rollout
    return rollout


def _sync_pointer_with_lock(pointer: Optional[Path], lock_path: Path) -> None:
    """Ensure pointer.txt matches the locked session path."""

    if pointer is None:
        return
    target = str(lock_path)
    try:
        current = pointer.read_text(encoding="utf-8").strip()
    except OSError:
        current = ""
    if current == target:
        return
    _update_pointer(pointer, lock_path)


def _read_pointer_path(pointer: Path) -> Optional[Path]:
    try:
        raw = pointer.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    if not raw:
        return None
    rollout = resolve_path(raw)
    return rollout if rollout.exists() else None


def _read_session_meta_cwd(path: Path) -> Optional[str]:
    try:
        with path.open(encoding="utf-8", errors="ignore") as fh:
            first_line = fh.readline()
    except OSError:
        return None
    if not first_line:
        return None
    try:
        data = json.loads(first_line)
    except json.JSONDecodeError:
        return None
    payload = data.get("payload") or {}
    return payload.get("cwd")


def _find_latest_claudecode_rollout(pointer: Path) -> Optional[Path]:
    """ClaudeCode-specific helper: pick the latest session document when CWD metadata is missing.

    Note: agent-*.jsonl files represent sidechain sessions (isSidechain=true) and must be ignored.
    """

    pointer_target = _read_pointer_path(pointer)
    candidates: List[Path] = []
    if pointer_target is not None:
        # If pointer points to agent document, skip
        if not pointer_target.name.startswith("agent-"):
            candidates.append(pointer_target)

    search_roots: List[Path] = []
    if MODEL_SESSION_ROOT:
        search_roots.append(resolve_path(MODEL_SESSION_ROOT))
    if pointer_target is not None:
        search_roots.append(pointer_target.parent)
    search_roots.append(pointer.parent)
    search_roots.append(pointer.parent / "sessions")

    seen_roots: set[str] = set()
    pattern = f"**/{MODEL_SESSION_GLOB}"
    for root in search_roots:
        try:
            real_root = root.resolve()
        except OSError:
            real_root = root
        key = str(real_root)
        if key in seen_roots:
            continue
        seen_roots.add(key)
        if not real_root.exists():
            continue
        for rollout in real_root.glob(pattern):
            if rollout.is_file():
                # exclude agent-*.jsonl document
                if not rollout.name.startswith("agent-"):
                    candidates.append(rollout)

    latest_path: Optional[Path] = None
    latest_mtime = -1.0
    seen_files: set[str] = set()
    for rollout in candidates:
        try:
            real_rollout = rollout.resolve()
        except OSError:
            real_rollout = rollout
        key = str(real_rollout)
        if key in seen_files:
            continue
        seen_files.add(key)
        try:
            mtime = real_rollout.stat().st_mtime
        except OSError:
            continue
        if mtime > latest_mtime:
            latest_mtime = mtime
            latest_path = Path(real_rollout)

    # Log the session document found
    if latest_path:
        worker_log.info(
            "ClaudeCode Find the latest session document (exclude agent-* document)",
            extra={"session_file": str(latest_path), "mtime": latest_mtime}
        )
    else:
        worker_log.warning(
            "ClaudeCode No valid session document found (exclude agent-* document)",
            extra={"search_roots": [str(r) for r in search_roots]}
        )

    return latest_path


def _find_latest_rollout_for_cwd(pointer: Path, target_cwd: Optional[str]) -> Optional[Path]:
    """Find the latest session document in the target CWD exist candidate directory."""

    roots: List[Path] = []
    for candidate in (CODEX_SESSIONS_ROOT, MODEL_SESSION_ROOT):
        if candidate:
            roots.append(resolve_path(candidate))

    pointer_target = _read_pointer_path(pointer)
    if pointer_target is not None:
        roots.append(pointer_target.parent)
        for parent in pointer_target.parents:
            if parent.name == "sessions":
                roots.append(parent)
                break

    roots.append(pointer.parent / "sessions")

    latest_path: Optional[Path] = None
    latest_mtime = -1.0
    seen: set[str] = set()

    for root in roots:
        try:
            real_root = root.resolve()
        except OSError:
            real_root = root
        key = str(real_root)
        if key in seen:
            continue
        seen.add(key)
        if not real_root.exists():
            continue

        pattern = f"**/{MODEL_SESSION_GLOB}"
        for rollout in real_root.glob(pattern):
            if not rollout.is_file():
                continue
            try:
                resolved = str(rollout.resolve())
            except OSError:
                resolved = str(rollout)
            try:
                mtime = rollout.stat().st_mtime
            except OSError:
                continue
            if mtime <= latest_mtime:
                continue
            if target_cwd:
                cwd = _read_session_meta_cwd(rollout)
                if cwd != target_cwd:
                    continue
            latest_mtime = mtime
            latest_path = rollout

    return latest_path


async def _await_session_path(
    pointer: Optional[Path], target_cwd: Optional[str], poll: float = 0.5
) -> Optional[Path]:
    if pointer:
        candidate = _read_pointer_path(pointer)
        if candidate is not None:
            return candidate
    await asyncio.sleep(poll)
    if pointer:
        candidate = _read_pointer_path(pointer)
        if candidate is not None:
            return candidate
        return _find_latest_rollout_for_cwd(pointer, target_cwd)
    return None


def _update_pointer(pointer: Path, rollout: Path) -> None:
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(str(rollout), encoding="utf-8")


def _format_plan_update(arguments: Any, *, event_timestamp: Optional[str]) -> Optional[Tuple[str, bool]]:
    if not isinstance(arguments, str):
        return None
    try:
        data = json.loads(arguments)
    except (TypeError, json.JSONDecodeError):
        return None

    plan_items = data.get("plan")
    if not isinstance(plan_items, list):
        return None

    explanation = data.get("explanation")
    lines: List[str] = []
    if isinstance(explanation, str) and explanation.strip():
        lines.append(explanation.strip())

    steps: List[str] = []
    all_completed = True
    for idx, item in enumerate(plan_items, 1):
        if not isinstance(item, dict):
            continue
        step = item.get("step")
        if not isinstance(step, str) or not step.strip():
            continue
        status_raw = str(item.get("status", "")).strip().lower()
        status_icon = PLAN_STATUS_LABELS.get(status_raw, status_raw or "-")
        steps.append(f"{status_icon} {idx}. {step.strip()}")
        if status_raw != "completed":
            all_completed = False

    if not steps:
        return None

    header = "current task execution plan:"
    body_parts = [header]
    if lines:
        body_parts.extend(lines)
    body_parts.extend(steps)
    text = "\n".join(body_parts)
    if event_timestamp:
        tz_name = os.environ.get("LOG_TIMEZONE", "Asia/Shanghai").strip() or "Asia/Shanghai"
        formatted_ts: Optional[str] = None
        try:
            normalized = event_timestamp.replace("Z", "+00:00")
            dt = datetime.fromisoformat(normalized)
            try:
                target_tz = ZoneInfo(tz_name)
            except ZoneInfoNotFoundError:
                target_tz = ZoneInfo("Asia/Shanghai")
            formatted_ts = dt.astimezone(target_tz).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            formatted_ts = None
        suffix = formatted_ts or event_timestamp
        text = f"{text}\n\nState updating; last updated: {suffix}"
    return text, all_completed


def _extract_codex_payload(data: dict, *, event_timestamp: Optional[str]) -> Optional[Tuple[str, str, Optional[Dict[str, Any]]]]:
    event_type = data.get("type")

    if event_type == "agent_message":
        message = data.get("message")
        if isinstance(message, str) and message.strip():
            return DELIVERABLE_KIND_MESSAGE, message, None

    if event_type == "event_msg":
        payload = data.get("payload") or {}
        if payload.get("type") == "agent_message":
            message = payload.get("message")
            if isinstance(message, str) and message.strip():
                return DELIVERABLE_KIND_MESSAGE, message, None
        return None

    if event_type != "response_item":
        return None

    payload = data.get("payload") or {}
    payload_type = payload.get("type")

    if payload_type in {"message", "assistant_message"}:
        content = payload.get("content")
        if isinstance(content, list):
            fragments = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") in {"output_text", "text", "markdown"}:
                    text = item.get("text") or item.get("markdown")
                    if text:
                        fragments.append(text)
            if fragments:
                return DELIVERABLE_KIND_MESSAGE, "\n".join(fragments), None
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return DELIVERABLE_KIND_MESSAGE, message, None
        text = payload.get("text")
        if isinstance(text, str) and text.strip():
            return DELIVERABLE_KIND_MESSAGE, text, None

    if payload_type == "function_call" and payload.get("name") == "update_plan":
        plan_result = _format_plan_update(payload.get("arguments"), event_timestamp=event_timestamp)
        if plan_result:
            plan_text, plan_completed = plan_result
            extra: Dict[str, Any] = {"plan_completed": plan_completed}
            call_id = payload.get("call_id")
            if call_id:
                extra["call_id"] = call_id
            return DELIVERABLE_KIND_PLAN, plan_text, extra

    if payload.get("event") == "final":
        delta = payload.get("delta")
        if isinstance(delta, str) and delta.strip():
            return DELIVERABLE_KIND_MESSAGE, delta, None

    return None


def _extract_claudecode_payload(
    data: dict, *, event_timestamp: Optional[str]
) -> Optional[Tuple[str, str, Optional[Dict[str, Any]]]]:
    # Claude Code isSidechain will be output at startup=true Welcome message, such events are simply ignored
    sidechain_flag = data.get("isSidechain")
    if isinstance(sidechain_flag, bool) and sidechain_flag:
        return None

    event_type = data.get("type")

    if event_type == "assistant":
        message = data.get("message")
        if isinstance(message, dict):
            fragments: List[str] = []
            content = message.get("content")
            if isinstance(content, list):
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    item_type = item.get("type")
                    if item_type != "text":
                        continue
                    text_value = item.get("text")
                    if isinstance(text_value, str) and text_value.strip():
                        fragments.append(text_value)
                if fragments:
                    combined = "\n\n".join(fragments)
                    metadata: Optional[Dict[str, Any]] = None
                    message_id = message.get("id")
                    if isinstance(message_id, str) and message_id:
                        metadata = {"message_id": message_id}
                    return DELIVERABLE_KIND_MESSAGE, combined, metadata
            fallback_text = message.get("text")
            if isinstance(fallback_text, str) and fallback_text.strip():
                metadata: Optional[Dict[str, Any]] = None
                message_id = message.get("id")
                if isinstance(message_id, str) and message_id:
                    metadata = {"message_id": message_id}
                return DELIVERABLE_KIND_MESSAGE, fallback_text, metadata
        return None

    return _extract_codex_payload(data, event_timestamp=event_timestamp)


def _extract_deliverable_payload(data: dict, *, event_timestamp: Optional[str]) -> Optional[Tuple[str, str, Optional[Dict[str, Any]]]]:
    if _is_claudecode_model():
        return _extract_claudecode_payload(data, event_timestamp=event_timestamp)
    return _extract_codex_payload(data, event_timestamp=event_timestamp)


def _read_session_events(path: Path) -> Tuple[int, List[SessionDeliverable]]:
    key = str(path)
    offset = SESSION_OFFSETS.get(key)
    if offset is None:
        _init_session_offset(path)
        offset = SESSION_OFFSETS.get(key, 0)
    events: List[SessionDeliverable] = []
    new_offset = offset

    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            fh.seek(offset)
            while True:
                line = fh.readline()
                if not line:
                    break
                new_offset = fh.tell()
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event_timestamp = event.get("timestamp")
                if not isinstance(event_timestamp, str):
                    event_timestamp = None
                candidate = _extract_deliverable_payload(event, event_timestamp=event_timestamp)
                if candidate:
                    kind, text, extra = candidate
                    events.append(
                        SessionDeliverable(
                            offset=new_offset,
                            kind=kind,
                            text=text,
                            timestamp=event_timestamp,
                            metadata=extra,
                        )
                    )
    except FileNotFoundError:
        return offset, []

    return new_offset, events


# --- Processor ---

@router.message(Command("help"))
async def on_help_command(message: Message) -> None:
    text = (
        "*Command overview*\n"
        "- /help — Show this help list\n"
        "- /tasks — List task-management shortcuts\n"
        "- /task_new — Create a task (interactive or parameterised)\n"
        "- /task_list — List tasks (supports status/limit/offset)\n"
        "- /task_show — Display task details\n"
        "- /task_update — Quickly update task fields\n"
        "- /task_note — Add a task note\n"
        "- /task_delete — Archive or restore a task\n"
        "- Sub-tasks are retired; use /task_new to create standalone tasks.\n\n"
        "Tip: Most operations offer buttons and guided dialogues, so you rarely need to remember the parameters."
    )
    await _answer_with_markdown(message, text)


@router.message(Command("tasks"))
async def on_tasks_help(message: Message) -> None:
    text = (
        "*Task management commands*\n"
        "- /task_new title | type=need — Create a task\n"
        "- /task_list [status=test] [limit=10] [offset=0] — List tasks\n"
        "- /task_show TASK_0001 — View task details\n"
        "- /task_update TASK_0001 status=test | priority=2 | type=defect — Update fields\n"
        "- /task_note TASK_0001 Note content | type=research — Add a task note\n"
        "- /task_delete TASK_0001 — Archive a task (run again to restore)\n"
        "- Sub-tasks are retired; use /task_new for new items.\n\n"
        "Recommendation: after `/task_new` or `/task_show`, use the inline buttons to continue."
    )
    await _answer_with_markdown(message, text)


def _normalize_status(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    token = _canonical_status_token(value, quiet=True)
    return token if token in TASK_STATUSES else None


def _normalize_task_type(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    raw = _strip_number_prefix((value or "").strip())
    if not raw:
        return None
    cleaned = _strip_task_type_emoji(raw)
    if not cleaned:
        return None
    token = cleaned.lower()
    if token in TASK_TYPES:
        return token
    if cleaned in TASK_TYPE_LABELS.values():
        for code, label in TASK_TYPE_LABELS.items():
            if cleaned == label:
                return code
    alias = _TASK_TYPE_ALIAS.get(cleaned) or _TASK_TYPE_ALIAS.get(token)
    if alias in TASK_TYPES:
        return alias
    return None

def _actor_from_message(message: Message) -> str:
    if message.from_user and message.from_user.full_name:
        return f"{message.from_user.full_name}#{message.from_user.id}"
    return str(message.from_user.id if message.from_user else message.chat.id)


def _actor_from_callback(callback: CallbackQuery) -> str:
    user = callback.from_user
    if user and user.full_name:
        return f"{user.full_name}#{user.id}"
    if user:
        return str(user.id)
    if callback.message and callback.message.chat:
        return str(callback.message.chat.id)
    return "unknown"


async def _build_task_list_view(
    *,
    status: Optional[str],
    page: int,
    limit: int,
) -> tuple[str, InlineKeyboardMarkup]:
    exclude_statuses: Optional[Sequence[str]] = None if status else ("done",)
    tasks, total_pages = await TASK_SERVICE.paginate(
        status=status,
        page=page,
        page_size=limit,
        exclude_statuses=exclude_statuses,
    )
    total = await TASK_SERVICE.count_tasks(
        status=status,
        include_archived=False,
        exclude_statuses=exclude_statuses,
    )
    display_pages = total_pages or 1
    current_page_display = min(page, display_pages)
    lines = [
        "*task list*",
        f"Filter state: {_format_status(status) if status else 'all'}",
    ]
    if not tasks:
        lines.append("If there is no matching task, use the status buttons above to switch categories.")
    lines.append(
        f"Paging info: page {current_page_display}/{display_pages}, {limit} items per page, total {total}"
    )
    text = "\n".join(lines)

    rows: list[list[InlineKeyboardButton]] = []
    rows.extend(_build_status_filter_row(status, limit))
    for task in tasks:
        label = _compose_task_button_label(task)
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"task:detail:{task.id}",
                )
            ]
        )

    status_token = status or "-"
    nav_row: list[InlineKeyboardButton] = []
    if page > 1:
        nav_row.append(
            InlineKeyboardButton(
                text="⬅️ Previous page",
                callback_data=f"task:list_page:{status_token}:{page-1}:{limit}",
            )
        )
    if total_pages and page < total_pages:
        nav_row.append(
            InlineKeyboardButton(
                text="Next page ➡️",
                callback_data=f"task:list_page:{status_token}:{page+1}:{limit}",
            )
        )
    if nav_row:
        rows.append(nav_row)

    rows.append(
        [
            InlineKeyboardButton(
                text="🔍 search tasks",
                callback_data=f"{TASK_LIST_SEARCH_CALLBACK}:{status_token}:{page}:{limit}",
            ),
            InlineKeyboardButton(
                text="➕ Create tasks",
                callback_data=TASK_LIST_CREATE_CALLBACK,
            ),
        ]
    )

    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    return text, markup


async def _build_task_search_view(
    keyword: str,
    *,
    page: int,
    limit: int,
    origin_status: Optional[str],
    origin_page: int,
) -> tuple[str, InlineKeyboardMarkup]:
    tasks, total_pages, total = await TASK_SERVICE.search_tasks(
        keyword,
        page=page,
        page_size=limit,
    )
    display_pages = total_pages or 1
    current_page_display = min(page, display_pages)
    sanitized_keyword = keyword.replace("\n", " ").strip()
    if not sanitized_keyword:
        sanitized_keyword = "-"
    # Fix: avoid double escaping
    if _IS_MARKDOWN_V2:
        escaped_keyword = sanitized_keyword
    else:
        escaped_keyword = _escape_markdown_text(sanitized_keyword)
    lines = [
        "*Task search results*",
        f"Search keywords: {escaped_keyword}",
        "Search scope: title, describe",
        f"Paging info: page {current_page_display}/{display_pages}, {limit} items per page, total {total}",
    ]
    if not tasks:
        lines.append("No matching Task found, please adjust the keywords or search again.")

    rows: list[list[InlineKeyboardButton]] = []
    for task in tasks:
        label = _compose_task_button_label(task)
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"task:detail:{task.id}",
                )
            ]
        )

    encoded_keyword = quote(keyword, safe="")
    origin_status_token = origin_status or "-"

    nav_row: list[InlineKeyboardButton] = []
    if page > 1:
        nav_row.append(
            InlineKeyboardButton(
                text="⬅️ Previous page",
                callback_data=(
                    f"{TASK_LIST_SEARCH_PAGE_CALLBACK}:{encoded_keyword}:"
                    f"{origin_status_token}:{origin_page}:{page-1}:{limit}"
                ),
            )
        )
    if total_pages and page < total_pages:
        nav_row.append(
            InlineKeyboardButton(
                text="Next page ➡️",
                callback_data=(
                    f"{TASK_LIST_SEARCH_PAGE_CALLBACK}:{encoded_keyword}:"
                    f"{origin_status_token}:{origin_page}:{page+1}:{limit}"
                ),
            )
        )
    if nav_row:
        rows.append(nav_row)

    rows.append(
        [
            InlineKeyboardButton(
                text="🔁 Search again",
                callback_data=f"{TASK_LIST_SEARCH_CALLBACK}:{origin_status_token}:{origin_page}:{limit}",
            ),
            InlineKeyboardButton(
                text="📋 Return to list",
                callback_data=f"{TASK_LIST_RETURN_CALLBACK}:{origin_status_token}:{origin_page}:{limit}",
            ),
        ]
    )

    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    text = "\n".join(lines)
    return text, markup


async def _handle_task_list_request(message: Message) -> None:
    raw_text = (message.text or "").strip()
    args = _extract_command_args(raw_text) if raw_text.startswith("/") else ""
    _, extra = parse_structured_text(args)
    status = _normalize_status(extra.get("status"))
    try:
        limit = int(extra.get("limit", DEFAULT_PAGE_SIZE))
    except ValueError:
        limit = DEFAULT_PAGE_SIZE
    limit = max(1, min(limit, 50))
    try:
        page = int(extra.get("page", "1"))
    except ValueError:
        page = 1
    page = max(page, 1)

    text, markup = await _build_task_list_view(status=status, page=page, limit=limit)
    sent = await _answer_with_markdown(message, text, reply_markup=markup)
    if sent is not None:
        _init_task_view_context(
            sent,
            _make_list_view_state(status=status, page=page, limit=limit),
        )


@router.message(Command("task_list"))
async def on_task_list(message: Message) -> None:
    await _handle_task_list_request(message)


@router.message(F.text.in_(WORKER_MENU_BUTTON_TEXT_SET))
async def on_task_list_button(message: Message) -> None:
    await _handle_task_list_request(message)


async def _dispatch_task_new_command(source_message: Message, actor: Optional[User]) -> None:
    """Simulate a /task_new message so the existing command logic can be reused."""
    if actor is None:
        raise ValueError("Valid task creation user information is missing")
    bot_instance = current_bot()
    command_text = "/task_new"
    try:
        now = datetime.now(tz=ZoneInfo("UTC"))
    except ZoneInfoNotFoundError:
        now = datetime.now(UTC)
    entities = [
        MessageEntity(type="bot_command", offset=0, length=len(command_text)),
    ]
    synthetic_message = source_message.model_copy(
        update={
            "message_id": source_message.message_id + 1,
            "date": now,
            "edit_date": None,
            "text": command_text,
            "from_user": actor,
            "entities": entities,
        }
    )
    update = Update.model_construct(
        update_id=int(time.time() * 1000),
        message=synthetic_message,
    )
    await dp.feed_update(bot_instance, update)


@router.message(F.text.in_(WORKER_CREATE_TASK_BUTTON_TEXT_SET))
async def on_task_create_button(message: Message, state: FSMContext) -> None:
    await state.clear()
    try:
        await _dispatch_task_new_command(message, message.from_user)
    except ValueError:
        await message.answer("Unable to initiate Task creation, please try again or use /task_new Order.")


@router.message(F.text.in_(WORKER_COMMAND_BUTTON_TEXT_SET))
async def on_command_center_button(message: Message, state: FSMContext) -> None:
    await state.clear()
    text, markup = await _build_command_list_view(page=1)
    await _answer_with_markdown(message, text, reply_markup=markup)


@router.callback_query(F.data.startswith(f"{COMMAND_LIST_CALLBACK}:"))
async def on_command_list_callback(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer("原始消息不存在", show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("分页参数错误", show_alert=True)
        return
    try:
        page = int(parts[-1])
    except ValueError:
        page = 1
    await _render_command_list_message(
        target_message=callback.message,
        fallback_message=callback.message,
        page=page,
    )
    await callback.answer()


@router.callback_query(F.data.startswith(f"{COMMAND_DETAIL_CALLBACK}:"))
async def on_command_detail_callback(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer("原始消息不存在", show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) != 5:
        await callback.answer("参数错误", show_alert=True)
        return
    try:
        scope = _command_scope_from_token(parts[2])
        preset_id = int(parts[3])
        page = int(parts[4])
    except ValueError:
        await callback.answer("参数错误", show_alert=True)
        return
    preset = await _get_scoped_command(scope, preset_id)
    if preset is None:
        await callback.answer("命令不存在，已回到列表。", show_alert=True)
        await _render_command_list_message(
            target_message=callback.message,
            fallback_message=callback.message,
            page=page,
        )
        return
    await _render_command_detail_message(
        target_message=callback.message,
        fallback_message=callback.message,
        preset=preset,
        origin_page=page,
        scope=scope,
    )
    await callback.answer()


@router.callback_query(F.data.startswith(f"{COMMAND_CREATE_CALLBACK}:"))
async def on_command_create_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer("无法启动命令配置：缺少消息上下文", show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("参数错误", show_alert=True)
        return
    try:
        page = int(parts[2])
    except ValueError:
        page = 1
    await state.clear()
    await state.update_data(mode="create", origin_page=page)
    await state.set_state(CommandPresetStates.waiting_name)
    await callback.answer()
    await callback.message.answer("请输入命令名称：", reply_markup=_build_description_keyboard())


@router.callback_query(F.data.startswith(f"{COMMAND_EDIT_CALLBACK}:"))
async def on_command_edit_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer("无法进入编辑：缺少消息上下文", show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) != 5:
        await callback.answer("参数错误", show_alert=True)
        return
    try:
        scope = _command_scope_from_token(parts[2])
        preset_id = int(parts[3])
        page = int(parts[4])
    except ValueError:
        await callback.answer("参数错误", show_alert=True)
        return
    if not _command_scope_editable(scope):
        await callback.answer("全局命令请在 Master 中管理。", show_alert=True)
        return
    preset = await _get_scoped_command(scope, preset_id)
    if preset is None:
        await callback.answer("命令不存在", show_alert=True)
        return
    await state.clear()
    await state.update_data(
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
        reply_markup=_build_description_keyboard(),
    )


@router.callback_query(F.data.startswith(f"{COMMAND_TOGGLE_CONFIRM_CALLBACK}:"))
async def on_command_toggle_confirm(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer("原始消息不存在", show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) != 5:
        await callback.answer("参数错误", show_alert=True)
        return
    try:
        scope = _command_scope_from_token(parts[2])
        preset_id = int(parts[3])
        page = int(parts[4])
    except ValueError:
        await callback.answer("参数错误", show_alert=True)
        return
    if not _command_scope_editable(scope):
        await callback.answer("全局命令需在 Master 中调整执行前确认。", show_alert=True)
        return
    service = _command_service_for_scope(scope)
    preset = await service.toggle_confirmation(preset_id)
    if preset is None:
        await callback.answer("命令不存在", show_alert=True)
        return
    await _render_command_detail_message(
        target_message=callback.message,
        fallback_message=callback.message,
        preset=preset,
        origin_page=page,
        scope=scope,
    )
    await callback.answer("执行前确认状态已切换")


@router.callback_query(F.data.startswith(f"{COMMAND_RUN_CALLBACK}:"))
async def on_command_run_callback(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer("原始消息不存在", show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) != 5:
        await callback.answer("参数错误", show_alert=True)
        return
    try:
        scope = _command_scope_from_token(parts[2])
        preset_id = int(parts[3])
        page = int(parts[4])
    except ValueError:
        await callback.answer("参数错误", show_alert=True)
        return
    preset = await _get_scoped_command(scope, preset_id)
    if preset is None:
        await callback.answer("命令不存在", show_alert=True)
        return
    if preset.require_confirmation:
        confirm_markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ 确认执行",
                        callback_data=f"{COMMAND_RUN_EXECUTE_CALLBACK}:{_command_scope_token(scope)}:{preset.id}:{page}",
                    ),
                    InlineKeyboardButton(
                        text="⬅️ 返回详情",
                        callback_data=f"{COMMAND_DETAIL_CALLBACK}:{_command_scope_token(scope)}:{preset.id}:{page}",
                    ),
                ]
            ]
        )
        prompt_text = f"确认执行命令 `{_escape_markdown_text(preset.title)}` 吗？"
        await _answer_with_markdown(callback.message, prompt_text, reply_markup=confirm_markup)
        await callback.answer("请确认执行")
        return
    await callback.answer("命令已派发")
    await _dispatch_command_preset(callback.message, preset)


@router.callback_query(F.data.startswith(f"{COMMAND_RUN_EXECUTE_CALLBACK}:"))
async def on_command_run_execute(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer("原始消息不存在", show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) != 5:
        await callback.answer("参数错误", show_alert=True)
        return
    try:
        scope = _command_scope_from_token(parts[2])
        preset_id = int(parts[3])
    except ValueError:
        await callback.answer("参数错误", show_alert=True)
        return
    preset = await _get_scoped_command(scope, preset_id)
    if preset is None:
        await callback.answer("命令不存在", show_alert=True)
        return
    await callback.answer("命令已派发")
    await _dispatch_command_preset(callback.message, preset)


@router.callback_query(F.data.startswith(f"{COMMAND_DELETE_CALLBACK}:"))
async def on_command_delete_callback(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer("原始消息不存在", show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) != 5:
        await callback.answer("参数错误", show_alert=True)
        return
    try:
        scope = _command_scope_from_token(parts[2])
        preset_id = int(parts[3])
        page = int(parts[4])
    except ValueError:
        await callback.answer("参数错误", show_alert=True)
        return
    if not _command_scope_editable(scope):
        await callback.answer("全局命令删除需在 Master 中执行。", show_alert=True)
        return
    preset = await _get_scoped_command(scope, preset_id)
    if preset is None:
        await callback.answer("命令不存在", show_alert=True)
        return
    confirm_markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ 确认删除",
                    callback_data=f"{COMMAND_DELETE_EXECUTE_CALLBACK}:{_command_scope_token(scope)}:{preset.id}:{page}",
                ),
                InlineKeyboardButton(
                    text="⬅️ 返回详情",
                    callback_data=f"{COMMAND_DETAIL_CALLBACK}:{_command_scope_token(scope)}:{preset.id}:{page}",
                ),
            ]
        ]
    )
    text = f"确认删除命令 `{_escape_markdown_text(preset.title)}` 吗？"
    await _answer_with_markdown(callback.message, text, reply_markup=confirm_markup)
    await callback.answer("请确认删除")


@router.callback_query(F.data.startswith(f"{COMMAND_DELETE_EXECUTE_CALLBACK}:"))
async def on_command_delete_execute(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer("原始消息不存在", show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) != 5:
        await callback.answer("参数错误", show_alert=True)
        return
    try:
        scope = _command_scope_from_token(parts[2])
        preset_id = int(parts[3])
        page = int(parts[4])
    except ValueError:
        await callback.answer("参数错误", show_alert=True)
        return
    if not _command_scope_editable(scope):
        await callback.answer("全局命令请在 Master 中删除。", show_alert=True)
        return
    service = _command_service_for_scope(scope)
    deleted = await service.delete_preset(preset_id)
    if not deleted:
        await callback.answer("命令不存在", show_alert=True)
        return
    await callback.answer("命令已删除")
    await callback.message.answer("命令已删除，返回列表。")
    await _render_command_list_message(
        target_message=callback.message,
        fallback_message=callback.message,
        page=page,
    )


@router.message(CommandPresetStates.waiting_name)
async def on_command_wizard_name(message: Message, state: FSMContext) -> None:
    raw_text = message.text or ""
    if _is_cancel_message(raw_text):
        await _cancel_command_wizard(message, state)
        return
    data = await state.get_data()
    if mode == "edit" and _is_skip_message(raw_text):
        existing = data.get("name")
        if not existing:
            await message.answer("原命令名称缺失，请重新输入：", reply_markup=_build_description_keyboard())
            return
        name = existing
    else:
        trimmed = raw_text.strip()
        if not trimmed:
            await message.answer("命令名称不能为空，请重新输入：", reply_markup=_build_description_keyboard())
            return
        name = trimmed
    await state.update_data(name=name)
    await state.set_state(CommandPresetStates.waiting_command)
    prompt = (
        "请输入命令内容，将作为完整文本注入 CLI："
        if mode == "create"
        else "请输入新的命令内容，或发送 Skip 保持不变："
    )
    await message.answer(prompt, reply_markup=_build_description_keyboard())


@router.message(CommandPresetStates.waiting_command)
async def on_command_wizard_command(message: Message, state: FSMContext) -> None:
    raw_text = message.text or ""
    if _is_cancel_message(raw_text):
        await _cancel_command_wizard(message, state)
        return
    data = await state.get_data()
    mode = data.get("mode", "create")
    if mode == "edit" and _is_skip_message(raw_text):
        existing = data.get("command_text")
        if not existing:
            await message.answer("原命令内容缺失，请重新输入：", reply_markup=_build_description_keyboard())
            return
        command_text = existing
    else:
        if not raw_text.strip():
            await message.answer("命令内容不能为空，请重新输入：", reply_markup=_build_description_keyboard())
            return
        command_text = raw_text
    await state.update_data(command_text=command_text)
    await state.set_state(CommandPresetStates.waiting_workdir)
    current_dir = data.get("workdir")
    if current_dir:
        prompt = (
            f"当前工作目录：{current_dir}\n"
            "请输入新的目录（绝对路径），或发送 Skip 继承当前终端："
        )
    else:
        prompt = "请输入命令执行目录（绝对路径），或发送 Skip 继承当前终端："
    await message.answer(prompt, reply_markup=_build_description_keyboard())


@router.message(CommandPresetStates.waiting_workdir)
async def on_command_wizard_workdir(message: Message, state: FSMContext) -> None:
    raw_text = message.text or ""
    if _is_cancel_message(raw_text):
        await _cancel_command_wizard(message, state)
        return
    data = await state.get_data()
    mode = data.get("mode", "create")
    if _is_skip_message(raw_text):
        workdir = data.get("workdir") if mode == "edit" else None
    else:
        trimmed = raw_text.strip()
        workdir = trimmed or None
    await state.update_data(workdir=workdir)
    await state.set_state(CommandPresetStates.waiting_confirmation_flag)
    current_flag = data.get("require_confirmation")
    current_label = "需要" if current_flag else "无需"
    prompt = (
        f"当前设置：{current_label if current_flag is not None else '默认需要'}。\n"
        "执行前是否需要确认？回复 yes/no、1/0，或发送 Skip 保持当前设置："
    )
    await message.answer(prompt, reply_markup=_build_description_keyboard())


@router.message(CommandPresetStates.waiting_confirmation_flag)
async def on_command_wizard_confirmation(message: Message, state: FSMContext) -> None:
    raw_text = message.text or ""
    if _is_cancel_message(raw_text):
        await _cancel_command_wizard(message, state)
        return
    data = await state.get_data()
    mode = data.get("mode", "create")
    if _is_skip_message(raw_text):
        require_confirmation = bool(data.get("require_confirmation", True))
    else:
        parsed = _parse_confirmation_choice(raw_text)
        if parsed is None:
            await message.answer(
                "请输入 yes/no、1/0，或发送 Skip 保持当前设置：",
                reply_markup=_build_description_keyboard(),
            )
            return
        require_confirmation = parsed
    name = data.get("name")
    command_text = data.get("command_text")
    if not name or not command_text:
        await message.answer("命令信息不完整，请从头重新开始配置。", reply_markup=_build_worker_main_keyboard())
        await state.clear()
        return
    workdir = data.get("workdir")
    await state.update_data(require_confirmation=require_confirmation)
    await state.set_state(CommandPresetStates.waiting_final_confirmation)
    summary = _format_command_summary_text(
        name=name,
        command_text=command_text,
        workdir=workdir,
        require_confirmation=require_confirmation,
    )
    await _answer_with_markdown(message, summary, reply_markup=_build_command_confirm_keyboard(mode))


@router.message(CommandPresetStates.waiting_final_confirmation)
async def on_command_wizard_confirm(message: Message, state: FSMContext) -> None:
    raw_text = message.text or ""
    if _is_cancel_message(raw_text):
        await _cancel_command_wizard(message, state)
        return
    data = await state.get_data()
    mode = data.get("mode", "create")
    confirm_label = _command_confirm_label(mode)
    if _normalize_choice_token(raw_text) != _normalize_choice_token(confirm_label):
        await message.answer(
            "请输入确认选项，或发送 Cancel 退出：",
            reply_markup=_build_command_confirm_keyboard(mode),
        )
        return
    name = data.get("name")
    command_text = data.get("command_text")
    require_confirmation = bool(data.get("require_confirmation", True))
    workdir = data.get("workdir")
    origin_page = int(data.get("origin_page") or 1)
    if not name or not command_text:
        await message.answer("命令数据缺失，流程已终止。", reply_markup=_build_worker_main_keyboard())
        await state.clear()
        return
    if mode == "edit":
        preset_id = data.get("preset_id")
        if not preset_id:
            await message.answer("缺少命令 ID，无法保存。", reply_markup=_build_worker_main_keyboard())
            await state.clear()
            return
        record = await COMMAND_PRESET_SERVICE.update_preset(
            preset_id,
            title=name,
            command=command_text,
            workdir=workdir,
            require_confirmation=require_confirmation,
        )
        if record is None:
            await message.answer("命令不存在或已删除。", reply_markup=_build_worker_main_keyboard())
            await state.clear()
            return
    else:
        record = await COMMAND_PRESET_SERVICE.create_preset(
            title=name,
            command=command_text,
            workdir=workdir,
            require_confirmation=require_confirmation,
        )
    await state.clear()
    await message.answer("命令已保存。", reply_markup=_build_worker_main_keyboard())
    detail_text, detail_markup = _build_command_detail_view(record, origin_page, COMMAND_SCOPE_PROJECT)
    await _answer_with_markdown(message, detail_text, reply_markup=detail_markup)


@router.callback_query(F.data.startswith("task:list_page:"))
async def on_task_list_page(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 5:
        await callback.answer("Callback data exception", show_alert=True)
        return
    _, _, status_token, page_raw, limit_raw = parts
    if callback.message is None:
        await callback.answer("Unable to locate original message", show_alert=True)
        return
    status = None if status_token == "-" else _normalize_status(status_token)
    try:
        page = int(page_raw)
        limit = int(limit_raw)
    except ValueError:
        await callback.answer("Paging parameter error", show_alert=True)
        return
    page = max(page, 1)
    limit = max(1, min(limit, 50))
    text, markup = await _build_task_list_view(status=status, page=page, limit=limit)
    state = _make_list_view_state(status=status, page=page, limit=limit)
    if await _try_edit_message(callback.message, text, reply_markup=markup):
        _set_task_view_context(callback.message, state)
    else:
        origin = callback.message
        origin_chat = getattr(origin, "chat", None)
        if origin and origin_chat:
            _clear_task_view(origin_chat.id, origin.message_id)
        sent = await _answer_with_markdown(origin or callback.message, text, reply_markup=markup)
        if sent is not None:
            _init_task_view_context(sent, state)
    await callback.answer()


@router.callback_query(F.data.startswith(f"{TASK_LIST_SEARCH_CALLBACK}:"))
async def on_task_list_search(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    if len(parts) != 5:
        await callback.answer("Callback data exception", show_alert=True)
        return
    _, _, status_token, page_raw, limit_raw = parts
    status = None if status_token == "-" else _normalize_status(status_token)
    try:
        page = max(int(page_raw), 1)
        limit = max(1, min(int(limit_raw), 50))
    except ValueError:
        await callback.answer("Parameter error", show_alert=True)
        return
    await state.clear()
    await state.update_data(
        origin_status=status,
        origin_status_token=status_token,
        origin_page=page,
        limit=limit,
        origin_message=callback.message,
    )
    await state.set_state(TaskListSearchStates.waiting_keyword)
    await callback.answer("Please enter search keywords")
    if callback.message:
        await _prompt_task_search_keyword(callback.message)


@router.callback_query(F.data.startswith(f"{TASK_LIST_SEARCH_PAGE_CALLBACK}:"))
async def on_task_list_search_page(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 7:
        await callback.answer("Callback data exception", show_alert=True)
        return
    _, _, encoded_keyword, origin_status_token, origin_page_raw, target_page_raw, limit_raw = parts
    if callback.message is None:
        await callback.answer("Unable to locate original message", show_alert=True)
        return
    keyword = unquote(encoded_keyword)
    origin_status = None if origin_status_token == "-" else _normalize_status(origin_status_token)
    try:
        origin_page = max(int(origin_page_raw), 1)
        page = max(int(target_page_raw), 1)
        limit = max(1, min(int(limit_raw), 50))
    except ValueError:
        await callback.answer("Parameter error", show_alert=True)
        return
    text, markup = await _build_task_search_view(
        keyword,
        page=page,
        limit=limit,
        origin_status=origin_status,
        origin_page=origin_page,
    )
    view_state = _make_search_view_state(
        keyword=keyword,
        page=page,
        limit=limit,
        origin_status=origin_status,
        origin_page=origin_page,
    )
    if await _try_edit_message(callback.message, text, reply_markup=markup):
        _set_task_view_context(callback.message, view_state)
    else:
        origin = callback.message
        origin_chat = getattr(origin, "chat", None)
        if origin and origin_chat:
            _clear_task_view(origin_chat.id, origin.message_id)
        sent = await _answer_with_markdown(origin or callback.message, text, reply_markup=markup)
        if sent is not None:
            _init_task_view_context(sent, view_state)
    await callback.answer()


@router.callback_query(F.data.startswith(f"{TASK_LIST_RETURN_CALLBACK}:"))
async def on_task_list_return(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    if len(parts) != 5:
        await callback.answer("Callback data exception", show_alert=True)
        return
    _, _, status_token, page_raw, limit_raw = parts
    if callback.message is None:
        await callback.answer("Unable to locate original message", show_alert=True)
        return
    status = None if status_token == "-" else _normalize_status(status_token)
    try:
        page = max(int(page_raw), 1)
        limit = max(1, min(int(limit_raw), 50))
    except ValueError:
        await callback.answer("Parameter error", show_alert=True)
        return
    await state.clear()
    text, markup = await _build_task_list_view(status=status, page=page, limit=limit)
    view_state = _make_list_view_state(status=status, page=page, limit=limit)
    if await _try_edit_message(callback.message, text, reply_markup=markup):
        _set_task_view_context(callback.message, view_state)
    else:
        origin = callback.message
        origin_chat = getattr(origin, "chat", None)
        if origin and origin_chat:
            _clear_task_view(origin_chat.id, origin.message_id)
        sent = await _answer_with_markdown(origin or callback.message, text, reply_markup=markup)
        if sent is not None:
            _init_task_view_context(sent, view_state)
    await callback.answer("Task list has been returned")


@router.callback_query(F.data == TASK_LIST_CREATE_CALLBACK)
async def on_task_list_create(callback: CallbackQuery) -> None:
    message = callback.message
    user = callback.from_user
    if message is None or user is None:
        await callback.answer("Unable to locate session", show_alert=True)
        return
    await callback.answer()
    await _dispatch_task_new_command(message, user)


@router.message(TaskListSearchStates.waiting_keyword)
async def on_task_list_search_keyword(message: Message, state: FSMContext) -> None:
    raw_text = message.text or ""
    trimmed = raw_text.strip()
    options = [SKIP_TEXT, "Cancel"]
    resolved = _resolve_reply_choice(raw_text, options=options)
    data = await state.get_data()
    origin_status = data.get("origin_status")
    origin_page = int(data.get("origin_page", 1) or 1)
    limit = int(data.get("limit", DEFAULT_PAGE_SIZE) or DEFAULT_PAGE_SIZE)
    limit = max(1, min(limit, 50))
    origin_message = data.get("origin_message")

    async def _restore_list() -> None:
        text, markup = await _build_task_list_view(status=origin_status, page=origin_page, limit=limit)
        list_state = _make_list_view_state(status=origin_status, page=origin_page, limit=limit)
        if await _try_edit_message(origin_message, text, reply_markup=markup):
            _set_task_view_context(origin_message, list_state)
            return
        origin_chat = getattr(origin_message, "chat", None)
        if origin_message and origin_chat:
            _clear_task_view(origin_chat.id, origin_message.message_id)
        sent = await _answer_with_markdown(message, text, reply_markup=markup)
        if sent is not None:
            _init_task_view_context(sent, list_state)

    if resolved == "Cancel" or resolved == SKIP_TEXT or not trimmed:
        await state.clear()
        await _restore_list()
        await message.answer("Task list has been returned.", reply_markup=_build_worker_main_keyboard())
        return

    if len(trimmed) < SEARCH_KEYWORD_MIN_LENGTH:
        await message.answer(
            f"Keyword length must be at least {SEARCH_KEYWORD_MIN_LENGTH} characters, please re-enter:",
            reply_markup=_build_description_keyboard(),
        )
        return
    if len(trimmed) > SEARCH_KEYWORD_MAX_LENGTH:
        await message.answer(
            f"Keyword length cannot exceed {SEARCH_KEYWORD_MAX_LENGTH} characters, please re-enter:",
            reply_markup=_build_description_keyboard(),
        )
        return

    search_text, search_markup = await _build_task_search_view(
        trimmed,
        page=1,
        limit=limit,
        origin_status=origin_status,
        origin_page=origin_page,
    )
    await state.clear()
    search_state = _make_search_view_state(
        keyword=trimmed,
        page=1,
        limit=limit,
        origin_status=origin_status,
        origin_page=origin_page,
    )
    if await _try_edit_message(origin_message, search_text, reply_markup=search_markup):
        _set_task_view_context(origin_message, search_state)
    else:
        origin_chat = getattr(origin_message, "chat", None)
        if origin_message and origin_chat:
            _clear_task_view(origin_chat.id, origin_message.message_id)
        sent = await _answer_with_markdown(message, search_text, reply_markup=search_markup)
        if sent is not None:
            _init_task_view_context(sent, search_state)
    await message.answer("Search completed.", reply_markup=_build_worker_main_keyboard())


@router.message(Command("task_show"))
async def on_task_show(message: Message) -> None:
    args = _extract_command_args(message.text)
    if not args:
        await _answer_with_markdown(message, "usage: /task_show TASK_0001")
        return
    task_id = _normalize_task_id(args)
    if not task_id:
        await _answer_with_markdown(message, TASK_ID_USAGE_TIP)
        return
    await _reply_task_detail_message(message, task_id)


@router.message(F.text.regexp(r"^/TASK_[A-Z0-9_]+(?:@[\w_]+)?(?:\s|$)"))
async def on_task_quick_command(message: Message) -> None:
    """Handle direct use /TASK_XXXX Quick query Order called."""
    raw_text = (message.text or "").strip()
    if not raw_text:
        await _answer_with_markdown(message, TASK_ID_USAGE_TIP)
        return
    first_token = raw_text.split()[0]
    task_id = _normalize_task_id(first_token)
    if not task_id:
        await _answer_with_markdown(message, TASK_ID_USAGE_TIP)
        return
    await _reply_task_detail_message(message, task_id)


@router.message(Command("task_children"))
async def on_task_children(message: Message) -> None:
    await _answer_with_markdown(
        message,
        "The sub-task function has been offline, and historical sub-tasks have been automatically archived. Please use /task_new Create independent tasks to split work.",
    )


@router.message(Command("task_new"))
async def on_task_new(message: Message, state: FSMContext) -> None:
    args = _extract_command_args(message.text)
    if args:
        title, extra = parse_structured_text(args)
        title = title.strip()
        if not title:
            await _answer_with_markdown(
                message,
                "Please provide a task title, for example: /task_new Fix login | type=need",
            )
            return
        if "priority" in extra:
            await _answer_with_markdown(
                message,
                "The `priority` parameter is no longer supported. Use `/task_new title | type=need` instead.",
            )
            return
        status = _normalize_status(extra.get("status")) or TASK_STATUSES[0]
        task_type = _normalize_task_type(extra.get("type"))
        if task_type is None:
            await _answer_with_markdown(
                message,
                "Task type is missing or invalid. Use type=need/defect/optimization/risk.",
            )
            return
        description = extra.get("description")
        actor = _actor_from_message(message)
        task = await TASK_SERVICE.create_root_task(
            title=title,
            status=status,
            priority=DEFAULT_PRIORITY,
            task_type=task_type,
            tags=(),
            due_date=None,
            description=description,
            actor=actor,
        )
        detail_text, markup = await _render_task_detail(task.id)
        await _answer_with_markdown(message, f"Task created:\n{detail_text}", reply_markup=markup)
        return

    await state.clear()
    await state.update_data(
        actor=_actor_from_message(message),
        priority=DEFAULT_PRIORITY,
    )
    await state.set_state(TaskCreateStates.waiting_title)
    await message.answer("Please enter the task title:")


@router.message(TaskCreateStates.waiting_title)
async def on_task_create_title(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if not title:
        await message.answer("The title cannot be empty. Please try again:")
        return
    await state.update_data(title=title)
    await state.set_state(TaskCreateStates.waiting_type)
    await message.answer(
        "Please select a task type (need / defect / optimization / risk):",
        reply_markup=_build_task_type_keyboard(),
    )


@router.message(TaskCreateStates.waiting_type)
async def on_task_create_type(message: Message, state: FSMContext) -> None:
    options = [_format_task_type(task_type) for task_type in TASK_TYPES]
    options.append("Cancel")
    resolved = _resolve_reply_choice(message.text, options=options)
    candidate = resolved or (message.text or "").strip()
    if resolved == "Cancel" or candidate == "Cancel":
        await state.clear()
        await message.answer("Task creation cancelled.", reply_markup=_build_worker_main_keyboard())
        return
    task_type = _normalize_task_type(candidate)
    if task_type is None:
        await message.answer(
            "Invalid task type. Use the keyboard or enter need/defect/optimization/risk:",
            reply_markup=_build_task_type_keyboard(),
        )
        return
    await state.update_data(task_type=task_type)
    await state.set_state(TaskCreateStates.waiting_description)
    await message.answer(
        (
            "Please enter the task description. Describe the business background and the expected result.\n"
            'If you have nothing to add, tap "Skip" or send an empty message. Send "Cancel" to terminate.'
        ),
        reply_markup=_build_description_keyboard(),
    )


@router.message(TaskCreateStates.waiting_description)
async def on_task_create_description(message: Message, state: FSMContext) -> None:
    raw_text = message.text or ""
    trimmed = raw_text.strip()
    options = [SKIP_TEXT, "Cancel"]
    resolved = _resolve_reply_choice(raw_text, options=options)
    if resolved == "Cancel":
        await state.clear()
        await message.answer("Task creation cancelled.", reply_markup=_build_worker_main_keyboard())
        return
    if trimmed and resolved != SKIP_TEXT and len(trimmed) > DESCRIPTION_MAX_LENGTH:
        await message.answer(
            f"The task description cannot exceed {DESCRIPTION_MAX_LENGTH} characters. Please try again:",
            reply_markup=_build_description_keyboard(),
        )
        return
    description: str = ""
    if trimmed and resolved != SKIP_TEXT:
        description = raw_text.strip()
    await state.update_data(description=description)
    await state.set_state(TaskCreateStates.waiting_confirm)
    data = await state.get_data()
    task_type_code = data.get("task_type")
    summary_lines = [
        "Please confirm the task information:",
        f"Title: {data.get('title')}",
        f"Type: {_format_task_type(task_type_code)}",
    ]
    priority_text = _format_priority(int(data.get("priority", DEFAULT_PRIORITY)))
    summary_lines.append(f"Priority: {priority_text} (default)")
    if description:
        summary_lines.append("Description:")
        summary_lines.append(description)
    else:
        summary_lines.append("Description: None yet (add details later via /task_desc).")
    await message.answer("\n".join(summary_lines), reply_markup=_build_worker_main_keyboard())
    await message.answer("Do you want to create this task?", reply_markup=_build_confirm_keyboard())


@router.message(TaskCreateStates.waiting_confirm)
async def on_task_create_confirm(message: Message, state: FSMContext) -> None:
    options = ["✅ Confirm creation", "❌ Cancel"]
    resolved = _resolve_reply_choice(message.text, options=options)
    stripped = _strip_number_prefix((message.text or "").strip()).lower()
    if resolved == options[1] or stripped in {"cancel"}:
        await state.clear()
        await message.answer("Task creation cancelled.", reply_markup=ReplyKeyboardRemove())
        await message.answer("Returned to main menu.", reply_markup=_build_worker_main_keyboard())
        return
    if resolved != options[0] and stripped not in {"confirm", "confirm creation"}:
        await message.answer(
            "Please select \"✅ Confirm creation\" or \"❌ Cancel\". Enter the number directly or tap the keyboard button:",
            reply_markup=_build_confirm_keyboard(),
        )
        return
    data = await state.get_data()
    title = data.get("title")
    if not title:
        await state.clear()
        await message.answer(
            "Creation data missing. Please run /task_new again.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await message.answer("SessionReturned to main menu.", reply_markup=_build_worker_main_keyboard())
        return
    priority_raw = data.get("priority")
    if not isinstance(priority_raw, int):
        parent_priority_value = data.get("parent_priority", DEFAULT_PRIORITY)
        priority_raw = parent_priority_value if isinstance(parent_priority_value, int) else DEFAULT_PRIORITY
    priority = int(priority_raw)
    task_type = data.get("task_type")
    if task_type is None:
        await state.clear()
        await message.answer(
            "Task type missing. Please run /task_new again.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await message.answer("SessionReturned to main menu.", reply_markup=_build_worker_main_keyboard())
        return
    actor = data.get("actor") or _actor_from_message(message)
    task = await TASK_SERVICE.create_root_task(
        title=title,
        status=TASK_STATUSES[0],
        priority=priority,
        task_type=task_type,
        tags=(),
        due_date=None,
        description=data.get("description"),
        actor=actor,
    )
    await state.clear()
    detail_text, markup = await _render_task_detail(task.id)
    await message.answer("Task created.", reply_markup=_build_worker_main_keyboard())
    await _answer_with_markdown(message, f"Task created:\n{detail_text}", reply_markup=markup)


@router.message(Command("task_child"))
async def on_task_child(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _answer_with_markdown(
        message,
        "The sub-task function has been offline, and historical sub-tasks have been automatically archived. Please use /task_new Create new Task.",
    )


@router.callback_query(
    F.data.in_(
        {
            "task:create_confirm",
            "task:create_cancel",
            "task:child_confirm",
            "task:child_cancel",
        }
    )
)
async def on_outdated_confirm_callback(callback: CallbackQuery) -> None:
    await callback.answer("Sub-task support has been removed and related buttons have expired. Please use /task_new to create a task.", show_alert=True)


@router.callback_query(F.data.startswith("task:desc_edit:"))
async def on_task_desc_edit(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Callback parameter error.", show_alert=True)
        return
    _, _, task_id = parts
    task = await TASK_SERVICE.get_task(task_id)
    if task is None:
        await callback.answer("Task does not exist", show_alert=True)
        return
    origin_message = callback.message
    if origin_message is None:
        await callback.answer("The message no longer exists, please start editing again.", show_alert=True)
        return
    await callback.answer()
    await _begin_task_desc_edit_flow(
        state=state,
        task=task,
        actor=_actor_from_message(origin_message),
        origin_message=origin_message,
    )


@router.message(TaskDescriptionStates.waiting_content)
async def on_task_desc_input(message: Message, state: FSMContext) -> None:
    """Handle text or menu input while the user edits the task description."""

    data = await state.get_data()
    task_id = data.get("task_id")
    if not task_id:
        await state.clear()
        await message.answer("The session has expired, please operate again.", reply_markup=_build_worker_main_keyboard())
        return

    token = _normalize_choice_token(message.text or "")
    if _is_cancel_message(token):
        await state.clear()
        await message.answer("Task description editing cancelled.", reply_markup=_build_worker_main_keyboard())
        return

    if token == _normalize_choice_token(TASK_DESC_CLEAR_TEXT):
        await state.update_data(
            new_description="",
            actor=_actor_from_message(message),
        )
        await state.set_state(TaskDescriptionStates.waiting_confirm)
        await _answer_with_markdown(
            message,
            _build_task_desc_confirm_text("(The new description is empty; the task description will be cleared.)"),
            reply_markup=_build_task_desc_confirm_keyboard(),
        )
        return

    if token == _normalize_choice_token(TASK_DESC_REPROMPT_TEXT):
        await _prompt_task_description_input(
            message,
            current_description=data.get("current_description", ""),
        )
        return

    trimmed = (message.text or "").strip()
    if len(trimmed) > DESCRIPTION_MAX_LENGTH:
        await message.answer(
            f"The task description cannot exceed {DESCRIPTION_MAX_LENGTH} characters. Please try again:",
            reply_markup=_build_task_desc_input_keyboard(),
        )
        await _prompt_task_description_input(
            message,
            current_description=data.get("current_description", ""),
        )
        return

    preview_segment = trimmed if trimmed else "(The new description is empty; the task description will be cleared.)"
    await state.update_data(
        new_description=trimmed,
        actor=_actor_from_message(message),
    )
    await state.set_state(TaskDescriptionStates.waiting_confirm)
    await _answer_with_markdown(
        message,
        _build_task_desc_confirm_text(preview_segment),
        reply_markup=_build_task_desc_confirm_keyboard(),
    )


@router.message(TaskDescriptionStates.waiting_confirm)
async def on_task_desc_confirm_stage_text(message: Message, state: FSMContext) -> None:
    """Handle the confirmation stage for task description edits (buttons or free text)."""

    data = await state.get_data()
    task_id = data.get("task_id")
    if not task_id:
        await state.clear()
        await message.answer("The session has expired, please operate again.", reply_markup=_build_worker_main_keyboard())
        return

    # Use _resolve_reply_choice() to parse user input (numbers, button labels, or free text).
    options = [TASK_DESC_CONFIRM_TEXT, TASK_DESC_RETRY_TEXT, TASK_DESC_CANCEL_TEXT]
    resolved = _resolve_reply_choice(message.text, options=options)
    stripped = _strip_number_prefix((message.text or "").strip()).lower()

    # Handle cancel requests.
    if resolved == options[2] or _is_cancel_message(resolved) or stripped in {"cancel"}:
        await state.clear()
        await message.answer("Task description editing cancelled.", reply_markup=_build_worker_main_keyboard())
        return

    # Handle re-entry of descriptions.
    if resolved == options[1] or stripped in {"re-enter", "reenter"}:
        task = await TASK_SERVICE.get_task(task_id)
        if task is None:
            await state.clear()
            await message.answer("Task not found. The editing process has ended.", reply_markup=_build_worker_main_keyboard())
            return
        await state.update_data(
            new_description=None,
            current_description=task.description or "",
        )
        await state.set_state(TaskDescriptionStates.waiting_content)
        await message.answer(
            "Returned to the description input stage. Please enter a new task description.",
            reply_markup=_build_task_desc_input_keyboard(),
        )
        await _prompt_task_description_input(
            message,
            current_description=task.description or "",
        )
        return

    # Process confirmed updates.
    if resolved == options[0] or stripped in {"confirm", "confirm update", "confirmrenew"}:
        new_description = data.get("new_description")
        if new_description is None:
            await state.set_state(TaskDescriptionStates.waiting_content)
            await message.answer(
                "The draft description has expired. Please enter it again:",
                reply_markup=_build_task_desc_input_keyboard(),
            )
            await _prompt_task_description_input(
                message,
                current_description=data.get("current_description", ""),
            )
            return
        actor = data.get("actor") or _actor_from_message(message)
        try:
            updated = await TASK_SERVICE.update_task(
                task_id,
                actor=actor,
                description=new_description,
            )
        except ValueError as exc:
            await state.clear()
            await message.answer(str(exc), reply_markup=_build_worker_main_keyboard())
            return
        await state.clear()
        await message.answer("Task description updated. Refreshing task details…", reply_markup=_build_worker_main_keyboard())
        detail_text, markup = await _render_task_detail(updated.id)
        await _answer_with_markdown(
            message,
            f"Task description updated:\n{detail_text}",
            reply_markup=markup,
        )
        return

    # Invalid input, prompt user
    await message.answer(
        "Currently in the confirmation stage. Choose \"✅ Confirm update\", \"✏️ Re-enter\", or \"❌ Cancel\" by entering the number or pressing a keyboard button:",
        reply_markup=_build_task_desc_confirm_keyboard(),
    )


@router.callback_query(F.data.startswith("task:desc_"))
async def on_task_desc_legacy_callback(callback: CallbackQuery, state: FSMContext) -> None:
    """Compatible with legacy inline buttons, prompting users to use menu buttons instead."""

    await callback.answer(
        "Task description editing is now available from the menu. Please use the menu options.",
        show_alert=True,
    )
    current_state = await state.get_state()
    data = await state.get_data()
    if callback.message is None:
        return
    if current_state == TaskDescriptionStates.waiting_content.state:
        await _prompt_task_description_input(
            callback.message,
            current_description=data.get("current_description", ""),
        )
        return
    if current_state == TaskDescriptionStates.waiting_confirm.state:
        preview_segment = data.get("new_description") or "(The new description is empty; the task description will be cleared.)"
        await _answer_with_markdown(
            callback.message,
            _build_task_desc_confirm_text(preview_segment),
            reply_markup=_build_task_desc_confirm_keyboard(),
        )


@router.callback_query(F.data.startswith("task:push_model:"))
async def on_task_push_model(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Callback parameter error.", show_alert=True)
        return
    _, _, task_id = parts
    current_state = await state.get_state()
    existing_context: Dict[str, Any] = {}
    if current_state == TaskPushStates.waiting_supplement.state:
        existing_context = await state.get_data()
    task = await TASK_SERVICE.get_task(task_id)
    if task is None:
        await callback.answer("Task does not exist", show_alert=True)
        return
    if task.status not in MODEL_PUSH_ELIGIBLE_STATUSES:
        await callback.answer("The current status does not support pushing to the model yet.", show_alert=True)
        return
    actor = _actor_from_callback(callback)
    chat_id = callback.message.chat.id if callback.message else callback.from_user.id
    if task.status in MODEL_PUSH_SUPPLEMENT_STATUSES:
        origin_message = callback.message
        origin_message_id = origin_message.message_id if origin_message else None
        if (
            current_state == TaskPushStates.waiting_supplement.state
            and existing_context.get("task_id") == task_id
            and existing_context.get("origin_message_id") == origin_message_id
        ):
            await callback.answer(PUSH_MODEL_SUPPLEMENT_IN_PROGRESS_TEXT)
            return
        if not _acquire_push_supplement_lock(chat_id, task_id, origin_message_id=origin_message_id):
            await callback.answer(PUSH_MODEL_SUPPLEMENT_IN_PROGRESS_TEXT)
            return
        await state.clear()
        try:
            await state.update_data(
                task_id=task_id,
                origin_message=origin_message,
                origin_message_id=origin_message_id,
                chat_id=chat_id,
                actor=actor,
            )
            await state.set_state(TaskPushStates.waiting_supplement)
        except Exception:
            _release_push_supplement_lock(chat_id, task_id)
            raise
        await callback.answer("Please add a task description, or choose Skip/Cancel.")
        if callback.message:
            await _prompt_model_supplement_input(callback.message)
        return
    await state.clear()
    try:
        success, prompt, session_path = await _push_task_to_model(
            task,
            chat_id=chat_id,
            reply_to=callback.message,
            supplement=None,
            actor=actor,
        )
    except ValueError as exc:
        worker_log.error(
            "Missing push template: %s",
            exc,
            extra={"task_id": task_id, "status": task.status},
        )
        await callback.answer("Push fail: Missing template configuration", show_alert=True)
        return
    if not success:
        await callback.answer("Push fail: model is not ready", show_alert=True)
        return
    await callback.answer("has been pushed to model")
    preview_block, preview_parse_mode = _wrap_text_in_code_block(prompt)
    await _reply_to_chat(
        chat_id,
        f"Pushed to model:\n{preview_block}",
        reply_to=callback.message,
        parse_mode=preview_parse_mode,
    )
    if session_path is not None:
        await _send_session_ack(chat_id, session_path, reply_to=callback.message)


@router.callback_query(F.data.startswith("task:push_model_skip:"))
async def on_task_push_model_skip(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Callback parameter error.", show_alert=True)
        return
    _, _, task_id = parts
    data = await state.get_data()
    stored_id = data.get("task_id")
    if stored_id and stored_id != task_id:
        task_id = stored_id
    lock_task_id = task_id
    task = await TASK_SERVICE.get_task(task_id)
    if task is None:
        await state.clear()
        _release_push_supplement_lock(data.get("chat_id") or (callback.message.chat.id if callback.message else callback.from_user.id), lock_task_id)
        await callback.answer("Task does not exist", show_alert=True)
        return
    actor = _actor_from_callback(callback)
    chat_id = data.get("chat_id") or (callback.message.chat.id if callback.message else callback.from_user.id)
    lock_chat_id = chat_id
    origin_message = data.get("origin_message") or callback.message
    try:
        success, prompt, session_path = await _push_task_to_model(
            task,
            chat_id=chat_id,
            reply_to=origin_message,
            supplement=None,
            actor=actor,
        )
    except ValueError as exc:
        await state.clear()
        _release_push_supplement_lock(lock_chat_id, lock_task_id)
        worker_log.error(
            "Missing push template: %s",
            exc,
            extra={"task_id": task_id, "status": task.status},
        )
        await callback.answer("Push fail: Missing template configuration", show_alert=True)
        return
    await state.clear()
    _release_push_supplement_lock(lock_chat_id, lock_task_id)
    if not success:
        await callback.answer("Push fail: model is not ready", show_alert=True)
        return
    await callback.answer("has been pushed to model")
    preview_block, preview_parse_mode = _wrap_text_in_code_block(prompt)
    await _reply_to_chat(
        chat_id,
        f"Pushed to model:\n{preview_block}",
        reply_to=origin_message,
        parse_mode=preview_parse_mode,
    )
    if session_path is not None:
        await _send_session_ack(chat_id, session_path, reply_to=origin_message)


@router.callback_query(F.data.startswith("task:push_model_fill:"))
async def on_task_push_model_fill(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Callback parameter error.", show_alert=True)
        return
    _, _, task_id = parts
    current_state = await state.get_state()
    existing_context: Dict[str, Any] = {}
    if current_state == TaskPushStates.waiting_supplement.state:
        existing_context = await state.get_data()
    task = await TASK_SERVICE.get_task(task_id)
    if task is None:
        await state.clear()
        await callback.answer("Task does not exist", show_alert=True)
        return
    actor = _actor_from_callback(callback)
    origin_message = callback.message
    origin_message_id = origin_message.message_id if origin_message else None
    if (
        current_state == TaskPushStates.waiting_supplement.state
        and existing_context.get("task_id") == task_id
        and existing_context.get("origin_message_id") == origin_message_id
    ):
        await callback.answer(PUSH_MODEL_SUPPLEMENT_IN_PROGRESS_TEXT)
        return
    chat_id = origin_message.chat.id if origin_message else callback.from_user.id
    if not _acquire_push_supplement_lock(chat_id, task_id, origin_message_id=origin_message_id):
        await callback.answer(PUSH_MODEL_SUPPLEMENT_IN_PROGRESS_TEXT)
        return
    await state.clear()
    try:
        await state.update_data(
            task_id=task_id,
            origin_message=origin_message,
            origin_message_id=origin_message_id,
            chat_id=chat_id,
            actor=actor,
        )
        await state.set_state(TaskPushStates.waiting_supplement)
    except Exception:
        _release_push_supplement_lock(chat_id, task_id)
        raise
    await callback.answer()
    if callback.message:
        await _prompt_model_supplement_input(callback.message)


@router.message(TaskPushStates.waiting_supplement)
async def on_task_push_model_supplement(message: Message, state: FSMContext) -> None:
    raw_text = message.text or ""
    trimmed = raw_text.strip()
    options = [SKIP_TEXT, "Cancel"]
    resolved = _resolve_reply_choice(raw_text, options=options)
    data = await state.get_data()
    lock_chat_id = data.get("chat_id") or message.chat.id
    lock_task_id = data.get("task_id")
    if resolved == "Cancel" or trimmed == "Cancel":
        await state.clear()
        _release_push_supplement_lock(lock_chat_id, lock_task_id)
        await message.answer("Push to the model cancelled.", reply_markup=_build_worker_main_keyboard())
        raise SkipHandler()
    task_id = lock_task_id
    if not task_id:
        await state.clear()
        _release_push_supplement_lock(lock_chat_id, lock_task_id)
        await message.answer("The push session has expired, please click the button again.", reply_markup=_build_worker_main_keyboard())
        raise SkipHandler()
    task = await TASK_SERVICE.get_task(task_id)
    if task is None:
        await state.clear()
        _release_push_supplement_lock(lock_chat_id, lock_task_id)
        await message.answer("Task not found. Push cancelled.", reply_markup=_build_worker_main_keyboard())
        raise SkipHandler()
    supplement: Optional[str] = None
    if trimmed and resolved != SKIP_TEXT:
        if len(trimmed) > DESCRIPTION_MAX_LENGTH:
            await message.answer(
                f"Supplementary task description cannot exceed {DESCRIPTION_MAX_LENGTH} characters. Please re-enter:",
                reply_markup=_build_description_keyboard(),
            )
            raise SkipHandler()
        supplement = raw_text.strip()
    chat_id = data.get("chat_id") or message.chat.id
    origin_message = data.get("origin_message")
    actor = data.get("actor") or _actor_from_message(message)
    try:
        success, prompt, session_path = await _push_task_to_model(
            task,
            chat_id=chat_id,
            reply_to=origin_message,
            supplement=supplement,
            actor=actor,
        )
    except ValueError as exc:
        await state.clear()
        _release_push_supplement_lock(lock_chat_id, lock_task_id)
        worker_log.error(
            "Missing push template: %s",
            exc,
            extra={"task_id": task_id, "status": task.status if task else None},
        )
        await message.answer("Push failed: missing template configuration.", reply_markup=_build_worker_main_keyboard())
        raise SkipHandler()
    await state.clear()
    _release_push_supplement_lock(lock_chat_id, lock_task_id)
    if not success:
        await message.answer("Push failed: model is not ready. Please retry shortly.", reply_markup=_build_worker_main_keyboard())
        raise SkipHandler()
    preview_block, preview_parse_mode = _wrap_text_in_code_block(prompt)
    await _reply_to_chat(
        chat_id,
        f"Pushed to model:\n{preview_block}",
        reply_to=origin_message,
        parse_mode=preview_parse_mode,
        reply_markup=_build_worker_main_keyboard(),
    )
    if session_path is not None:
        await _send_session_ack(chat_id, session_path, reply_to=origin_message)
    raise SkipHandler()


@router.callback_query(F.data.startswith("task:history:"))
async def on_task_history(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Callback parameter error.", show_alert=True)
        return
    _, _, task_id = parts
    message = callback.message
    if message is None:
        await callback.answer("Unable to locate original message", show_alert=True)
        return
    try:
        text, markup, page, total_pages = await _render_task_history(task_id, page=0)
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    history_state = _make_history_view_state(task_id=task_id, page=page)
    code_text, parse_mode = _wrap_text_in_code_block(text)
    try:
        sent = await message.answer(
            code_text,
            parse_mode=parse_mode,
            reply_markup=markup,
        )
    except TelegramBadRequest as exc:
        worker_log.warning(
            "TaskEvent history sending failed: %s",
            exc,
            extra={"task_id": task_id},
        )
        await callback.answer("History send fail", show_alert=True)
        return
    _init_task_view_context(sent, history_state)
    await callback.answer("Displayed history")
    worker_log.info(
        "TaskEvent history is displayed via code block messages",
        extra={
            "task_id": task_id,
            "page": str(page),
            "pages": str(total_pages),
        },
    )


@router.callback_query(F.data.startswith(f"{TASK_HISTORY_PAGE_CALLBACK}:"))
async def on_task_history_page(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Callback parameter error.", show_alert=True)
        return
    _, _, task_id, page_raw = parts
    try:
        requested_page = int(page_raw)
    except ValueError:
        await callback.answer("Invalid page number", show_alert=True)
        return
    message = callback.message
    if message is None:
        await callback.answer("Unable to locate original message", show_alert=True)
        return
    try:
        text, markup, page, total_pages = await _render_task_history(task_id, requested_page)
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    history_state = _make_history_view_state(task_id=task_id, page=page)
    code_text, parse_mode = _wrap_text_in_code_block(text)
    try:
        sent = await message.answer(
            code_text,
            parse_mode=parse_mode,
            reply_markup=markup,
        )
    except TelegramBadRequest as exc:
        worker_log.info(
            "History page sending failure: %s",
            exc,
            extra={"task_id": task_id, "page": requested_page},
        )
        await callback.answer("Switch to fail, please try again later", show_alert=True)
        return
    chat = getattr(message, "chat", None)
    if chat is not None:
        _clear_task_view(chat.id, message.message_id)
    _init_task_view_context(sent, history_state)
    await callback.answer(f"Shown {page}/{total_pages} Page")


@router.callback_query(F.data.startswith(f"{TASK_HISTORY_BACK_CALLBACK}:"))
async def on_task_history_back(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Callback parameter error.", show_alert=True)
        return
    _, _, task_id = parts
    message = callback.message
    if message is None:
        await callback.answer("Unable to locate original message", show_alert=True)
        return
    try:
        text, markup = await _render_task_detail(task_id)
    except ValueError:
        await callback.answer("Task does not exist", show_alert=True)
        return
    detail_state = TaskViewState(kind="detail", data={"task_id": task_id})
    chat = getattr(message, "chat", None)
    if chat is not None:
        _clear_task_view(chat.id, message.message_id)
    sent = await _answer_with_markdown(message, text, reply_markup=markup)
    if sent is not None:
        _init_task_view_context(sent, detail_state)
        await callback.answer("Returned to task details")
        return
    await callback.answer("Returns fail, please try again later", show_alert=True)


class TaskSummaryRequestError(Exception):
    """Business exceptions in the summary generation process."""


async def _request_task_summary(
    task: TaskRecord,
    *,
    actor: Optional[str],
    chat_id: int,
    reply_to: Optional[Message],
) -> tuple[str, bool]:
    """Request a summary from the model, updating the task status when required."""

    status_changed = False
    current_task = task
    if current_task.status != "test":
        try:
            updated = await TASK_SERVICE.update_task(
                current_task.id,
                actor=actor,
                status="test",
            )
        except ValueError as exc:
            raise TaskSummaryRequestError(f"Failed to update task status: {exc}") from exc
        else:
            current_task = updated
            status_changed = True

    history_text, history_count = await _build_history_context_for_model(current_task.id)
    notes = await TASK_SERVICE.list_notes(current_task.id)
    request_id = uuid.uuid4().hex
    prompt = _build_summary_prompt(
        current_task,
        request_id=request_id,
        history_text=history_text,
        notes=notes,
    )

    success, session_path = await _dispatch_prompt_to_model(
        chat_id,
        prompt,
        reply_to=reply_to,
        ack_immediately=False,
    )
    if not success:
        raise TaskSummaryRequestError("The model is not ready and summary generation fails.")

    actor_label = actor
    if session_path is not None:
        session_key = str(session_path)
        PENDING_SUMMARIES[session_key] = PendingSummary(
            task_id=current_task.id,
            request_id=request_id,
            actor=actor_label,
            session_key=session_key,
            session_path=session_path,
            created_at=time.monotonic(),
        )

    payload: dict[str, Any] = {
        "request_id": request_id,
        "model": ACTIVE_MODEL or "",
        "status_auto_updated": status_changed,
    }
    if history_count:
        payload["history_items"] = history_count

    await _log_task_action(
        current_task.id,
        action="summary_request",
        actor=actor_label,
        new_value=request_id,
        payload=payload,
    )

    return request_id, status_changed


@router.message(Command("task_note"))
async def on_task_note(message: Message, state: FSMContext) -> None:
    args = _extract_command_args(message.text)
    if args:
        body, extra = parse_structured_text(args)
        parts = body.split(" ", 1)
        task_id = parts[0].strip() if parts and parts[0].strip() else extra.get("id")
        if not task_id:
            await _answer_with_markdown(
                message,
                "Please provide a task ID (example: /task_note TASK_0001 content).",
            )
            return
        normalized_task_id = _normalize_task_id(task_id)
        if not normalized_task_id:
            await _answer_with_markdown(message, TASK_ID_USAGE_TIP)
            return
        content = parts[1].strip() if len(parts) > 1 else extra.get("content", "").strip()
        if not content:
            await _answer_with_markdown(message, "The remark content cannot be empty.")
            return
        note_type_raw = extra.get("type", "").strip().lower()
        note_type = note_type_raw if note_type_raw in NOTE_TYPES else "misc"
        await TASK_SERVICE.add_note(
            normalized_task_id,
            note_type=note_type,
            content=content,
            actor=_actor_from_message(message),
        )
        detail_text, markup = await _render_task_detail(normalized_task_id)
        await _answer_with_markdown(message, f"Remark added:\n{detail_text}", reply_markup=markup)
        return

    await state.clear()
    await state.set_state(TaskNoteStates.waiting_task_id)
    await message.answer("Please enter the task ID:")


@router.message(TaskNoteStates.waiting_task_id)
async def on_note_task_id(message: Message, state: FSMContext) -> None:
    task_id_raw = (message.text or "").strip()
    if not task_id_raw:
        await message.answer("The task ID cannot be empty. Please try again:")
        return
    task_id = _normalize_task_id(task_id_raw)
    if not task_id:
        await message.answer(TASK_ID_USAGE_TIP)
        return
    task = await TASK_SERVICE.get_task(task_id)
    if task is None:
        await message.answer("Task not found. Please enter a valid ID:")
        return
    await state.update_data(task_id=task_id)
    await state.set_state(TaskNoteStates.waiting_content)
    await message.answer("Please enter the remark content:")


@router.message(TaskNoteStates.waiting_content)
async def on_note_content(message: Message, state: FSMContext) -> None:
    content = (message.text or "").strip()
    if not content:
        await message.answer("The remark content cannot be empty. Please try again:")
        return
    data = await state.get_data()
    task_id = data.get("task_id")
    if not task_id:
        await state.clear()
        await message.answer("Missing context. Please run /task_note again.")
        return
    await TASK_SERVICE.add_note(
        task_id,
        note_type="misc",
        content=content,
        actor=_actor_from_message(message),
    )
    await state.clear()
    detail_text, markup = await _render_task_detail(task_id)
    await _answer_with_markdown(message, f"Remark added:\n{detail_text}", reply_markup=markup)


@router.message(Command("task_update"))
async def on_task_update(message: Message) -> None:
    args = _extract_command_args(message.text)
    if not args:
        await _answer_with_markdown(
            message,
            "usage: /task_update TASK_0001 status=test | priority=2 | description=Research content",
        )
        return
    body, extra = parse_structured_text(args)
    parts = body.split(" ", 1)
    task_id = parts[0].strip() if parts and parts[0].strip() else extra.get("id")
    if not task_id:
        await _answer_with_markdown(message, "Please provide a task ID.")
        return
    normalized_task_id = _normalize_task_id(task_id)
    if not normalized_task_id:
        await _answer_with_markdown(message, TASK_ID_USAGE_TIP)
        return
    title = extra.get("title")
    if title is None and len(parts) > 1:
        title = parts[1].strip()
    status = _normalize_status(extra.get("status"))
    priority = None
    if "priority" in extra:
        try:
            priority = int(extra["priority"])
        except ValueError:
            await _answer_with_markdown(message, "Priority must be a number between 1 and 5.")
            return
        priority = max(1, min(priority, 5))
    description = extra.get("description")
    if description is not None and len(description) > DESCRIPTION_MAX_LENGTH:
        await _answer_with_markdown(
            message,
            f"The task description cannot exceed {DESCRIPTION_MAX_LENGTH} characters.",
        )
        return
    task_type = None
    if "type" in extra:
        task_type = _normalize_task_type(extra.get("type"))
        if task_type is None:
            await _answer_with_markdown(
                message,
                "Invalid task type; use type=need/defect/optimization/risk.",
            )
            return
    updates = {
        "title": title,
        "status": status,
        "priority": priority,
        "task_type": task_type,
        "description": description,
    }
    if all(value is None for value in updates.values()):
        await _answer_with_markdown(message, "Please provide the field and value to update, e.g., status=test")
        return
    actor = _actor_from_message(message)
    try:
        updated = await TASK_SERVICE.update_task(
            normalized_task_id,
            actor=actor,
            title=updates["title"],
            status=updates["status"],
            priority=updates["priority"],
            task_type=updates["task_type"],
            description=updates["description"],
        )
    except ValueError as exc:
        await _answer_with_markdown(message, str(exc))
        return
    detail_text, markup = await _render_task_detail(updated.id)
    await _answer_with_markdown(message, f"Task updated:\n{detail_text}", reply_markup=markup)


@router.message(Command("task_delete"))
async def on_task_delete(message: Message) -> None:
    args = _extract_command_args(message.text)
    if not args:
        await _answer_with_markdown(message, "usage: /task_delete TASK_0001 [restore=yes]")
        return
    parts = args.split()
    task_id_raw = parts[0].strip()
    task_id = _normalize_task_id(task_id_raw)
    if not task_id:
        await _answer_with_markdown(message, TASK_ID_USAGE_TIP)
        return
    extra = parse_simple_kv(" ".join(parts[1:])) if len(parts) > 1 else {}
    restore = extra.get("restore", "no").strip().lower() in {"yes", "1", "true"}
    try:
        updated = await TASK_SERVICE.update_task(
            task_id,
            actor=_actor_from_message(message),
            archived=not restore,
        )
    except ValueError as exc:
        await _answer_with_markdown(message, str(exc))
        return
    action = "Restored" if restore else "Archived"
    detail_text, markup = await _render_task_detail(updated.id)
    await _answer_with_markdown(message, f"Task {action}:\n{detail_text}", reply_markup=markup)


@router.callback_query(F.data.startswith("task:status:"))
async def on_status_callback(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Callback parameter error.", show_alert=True)
        return
    _, _, task_id, status_value = parts
    status = _normalize_status(status_value)
    if status is None:
        await callback.answer("Invalid state", show_alert=True)
        return
    try:
        updated = await TASK_SERVICE.update_task(
            task_id,
            actor=_actor_from_message(callback.message),
            status=status,
        )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    detail_text, markup = await _render_task_detail(updated.id)
    message = callback.message
    if message is None:
        await callback.answer("Unable to locate original message", show_alert=True)
        return
    detail_state = TaskViewState(kind="detail", data={"task_id": updated.id})
    if await _try_edit_message(message, detail_text, reply_markup=markup):
        _set_task_view_context(message, detail_state)
        await callback.answer("Status updated")
        return
    sent = await _answer_with_markdown(message, detail_text, reply_markup=markup)
    if sent is not None:
        _init_task_view_context(sent, detail_state)
        await callback.answer("Status updated")
        return
    await callback.answer("Status updated but message refresh failed", show_alert=True)


@router.callback_query(F.data.startswith("task:summary:"))
async def on_task_summary_request(callback: CallbackQuery) -> None:
    """Request the model to generate a Task summary."""

    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Callback parameter error.", show_alert=True)
        return
    _, _, task_id = parts
    task = await TASK_SERVICE.get_task(task_id)
    if task is None:
        await callback.answer("Task does not exist", show_alert=True)
        return
    chat_id = callback.message.chat.id if callback.message else callback.from_user.id
    actor = _actor_from_callback(callback)
    try:
        _, status_changed = await _request_task_summary(
            task,
            actor=actor,
            chat_id=chat_id,
            reply_to=callback.message,
        )
    except TaskSummaryRequestError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("Model generation summary requested")
    if callback.message:
        lines = ["Sent to modelSummary request, please await response."]
        if status_changed:
            lines.append("Task status automatically adjusted to 'test'.")
        await callback.message.answer(
            "\n".join(lines),
            reply_markup=_build_worker_main_keyboard(),
        )


@router.message(
    F.text.lower().startswith("/task_summary_request_")
    | F.text.lower().startswith("/tasksummaryrequest")
)
async def on_task_summary_command(message: Message) -> None:
    """Imperative triggering of Task summary generation."""

    raw_text = (message.text or "").strip()
    if not raw_text:
        await message.answer("Please provide a task ID, for example: /task_summary_request_TASK_0001")
        return
    token = raw_text.split()[0]
    command_part, _, _bot = token.partition("@")
    lowered = command_part.lower()
    prefix = next(
        (alias for alias in SUMMARY_COMMAND_ALIASES if lowered.startswith(alias)),
        None,
    )
    if prefix is None:
        await message.answer("Please provide a task ID, for example: /task_summary_request_TASK_0001")
        return
    task_segment = command_part[len(prefix) :].strip()
    if not task_segment:
        await message.answer("Please provide a task ID, for example: /task_summary_request_TASK_0001")
        return
    normalized_task_id = _normalize_task_id(task_segment)
    if not normalized_task_id:
        await message.answer(TASK_ID_USAGE_TIP)
        return
    task = await TASK_SERVICE.get_task(normalized_task_id)
    if task is None:
        await message.answer("Task does not exist", reply_markup=_build_worker_main_keyboard())
        return
    actor = _actor_from_message(message)
    chat_id = message.chat.id
    try:
        _, status_changed = await _request_task_summary(
            task,
            actor=actor,
            chat_id=chat_id,
            reply_to=message,
        )
    except TaskSummaryRequestError as exc:
        await message.answer(str(exc), reply_markup=_build_worker_main_keyboard())
        return
    lines = ["Sent to modelSummary request, please await response."]
    if status_changed:
        lines.append("Task status automatically adjusted to 'test'.")
    await message.answer("\n".join(lines), reply_markup=_build_worker_main_keyboard())


@router.callback_query(F.data.startswith("task:bug_report:"))
async def on_task_bug_report(callback: CallbackQuery, state: FSMContext) -> None:
    """Enter the defect reporting process."""

    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Callback parameter error.", show_alert=True)
        return
    _, _, task_id = parts
    task = await TASK_SERVICE.get_task(task_id)
    if task is None:
        await callback.answer("Task does not exist", show_alert=True)
        return
    await state.clear()
    reporter = _actor_from_callback(callback)
    await state.update_data(
        task_id=task.id,
        reporter=reporter,
        description="",
        reproduction="",
        logs="",
    )
    await state.set_state(TaskBugReportStates.waiting_description)
    await callback.answer("please describeefect")
    if callback.message:
        await callback.message.answer(
            _build_bug_report_intro(task),
            reply_markup=_build_description_keyboard(),
        )


@router.message(TaskBugReportStates.waiting_description)
async def on_task_bug_description(message: Message, state: FSMContext) -> None:
    """Handle defectdescribe input."""

    if _is_cancel_message(message.text):
        await state.clear()
        await message.answer("Bug report cancelled.", reply_markup=_build_worker_main_keyboard())
        return
    content = _collect_message_payload(message)
    if not content:
        await message.answer(
            "defectdescribeCannot be empty, please Re-enter:",
            reply_markup=_build_description_keyboard(),
        )
        return
    await state.update_data(
        description=content,
        reporter=_actor_from_message(message),
    )
    await state.set_state(TaskBugReportStates.waiting_reproduction)
    await message.answer(_build_bug_repro_prompt(), reply_markup=_build_description_keyboard())


@router.message(TaskBugReportStates.waiting_reproduction)
async def on_task_bug_reproduction(message: Message, state: FSMContext) -> None:
    """Handle replication step input."""

    if _is_cancel_message(message.text):
        await state.clear()
        await message.answer("Bug report cancelled.", reply_markup=_build_worker_main_keyboard())
        return
    options = [SKIP_TEXT, "Cancel"]
    resolved = _resolve_reply_choice(message.text or "", options=options)
    reproduction = ""
    if resolved not in {SKIP_TEXT, "Cancel"}:
        reproduction = _collect_message_payload(message)
    await state.update_data(reproduction=reproduction)
    await state.set_state(TaskBugReportStates.waiting_logs)
    await message.answer(_build_bug_log_prompt(), reply_markup=_build_description_keyboard())


@router.message(TaskBugReportStates.waiting_logs)
async def on_task_bug_logs(message: Message, state: FSMContext) -> None:
    """Process log information input."""

    if _is_cancel_message(message.text):
        await state.clear()
        await message.answer("Bug report cancelled.", reply_markup=_build_worker_main_keyboard())
        return
    options = [SKIP_TEXT, "Cancel"]
    resolved = _resolve_reply_choice(message.text or "", options=options)
    logs = ""
    if resolved not in {SKIP_TEXT, "Cancel"}:
        logs = _collect_message_payload(message)
    data = await state.get_data()
    task_id = data.get("task_id")
    if not task_id:
        await state.clear()
        await message.answer("Task information is missing; the process has been cancelled.", reply_markup=_build_worker_main_keyboard())
        return
    task = await TASK_SERVICE.get_task(task_id)
    if task is None:
        await state.clear()
        await message.answer("Task does not exist; the bug report has been cancelled.", reply_markup=_build_worker_main_keyboard())
        return
    description = data.get("description", "")
    reproduction = data.get("reproduction", "")
    reporter = data.get("reporter") or _actor_from_message(message)
    await state.update_data(logs=logs)
    preview = _build_bug_preview_text(
        task=task,
        description=description,
        reproduction=reproduction,
        logs=logs,
        reporter=reporter,
    )
    await state.set_state(TaskBugReportStates.waiting_confirm)
    await message.answer(
        f"Please confirm the following defect information:\n{preview}",
        reply_markup=_build_bug_confirm_keyboard(),
    )


@router.message(TaskBugReportStates.waiting_confirm)
async def on_task_bug_confirm(message: Message, state: FSMContext) -> None:
    """Confirm the bug report and persist it."""

    if _is_cancel_message(message.text):
        await state.clear()
        await message.answer("Bug report cancelled.", reply_markup=_build_worker_main_keyboard())
        return
    options = ["✅ Confirm submission", "❌ Cancel"]
    resolved = _resolve_reply_choice(message.text or "", options=options)
    if resolved == options[1]:
        await state.clear()
        await message.answer("Bug report cancelled.", reply_markup=_build_worker_main_keyboard())
        return
    stripped = _strip_number_prefix((message.text or "").strip()).lower()
    if resolved != options[0] and stripped not in {"confirm submission"}:
        await message.answer(
            "Please reply with \"✅ Confirm submission\" or type \"Cancel\".",
            reply_markup=_build_bug_confirm_keyboard(),
        )
        return
    data = await state.get_data()
    task_id = data.get("task_id")
    if not task_id:
        await state.clear()
        await message.answer("Task information is missing; the process has been cancelled.", reply_markup=_build_worker_main_keyboard())
        return
    task = await TASK_SERVICE.get_task(task_id)
    if task is None:
        await state.clear()
        await message.answer("Task does not exist; the bug report has been cancelled.", reply_markup=_build_worker_main_keyboard())
        return
    description = data.get("description", "")
    reproduction = data.get("reproduction", "")
    logs = data.get("logs", "")
    reporter = data.get("reporter") or _actor_from_message(message)
    payload = {
        "action": "bug_report",
        "description_length": len(description),
        "has_reproduction": bool(reproduction.strip()),
        "has_logs": bool(logs.strip()),
        "description": description,
        "reproduction": reproduction,
        "logs": logs,
        "reporter": reporter,
    }
    await _log_task_action(
        task.id,
        action="bug_report",
        actor=reporter,
        new_value=description[:HISTORY_DISPLAY_VALUE_LIMIT],
        payload=payload,
    )
    await state.clear()
    await _auto_push_after_bug_report(task, message=message, actor=reporter)


@router.callback_query(F.data.startswith("task:add_note:"))
async def on_add_note_callback(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Callback parameter error.", show_alert=True)
        return
    _, _, task_id = parts
    await state.clear()
    await state.update_data(task_id=task_id)
    await state.set_state(TaskNoteStates.waiting_content)
    await callback.answer("Please enter the remark content.")
    await callback.message.answer("Please enter the remark content:")


@router.callback_query(F.data.startswith("task:add_child:"))
async def on_add_child_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("The sub-task feature has been retired.", show_alert=True)
    if callback.message:
        await callback.message.answer(
            "The sub-task feature has been retired and historical items archived automatically. Use /task_new to create a new task.",
            reply_markup=_build_worker_main_keyboard(),
        )


@router.callback_query(F.data.startswith("task:list_children:"))
async def on_list_children_callback(callback: CallbackQuery) -> None:
    await callback.answer("The sub-task function has been offline", show_alert=True)
    if callback.message:
        await callback.message.answer(
            "The sub-task function has been offline, and historical sub-tasks have been automatically archived. Please use /task_new Create new Task.",
            reply_markup=_build_worker_main_keyboard(),
        )


@router.callback_query(F.data.startswith("task:detail:"))
async def on_task_detail_callback(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Callback parameter error.", show_alert=True)
        return
    _, _, task_id = parts
    message = callback.message
    if message is None:
        await callback.answer("Unable to locate the original message.", show_alert=True)
        return
    try:
        detail_text, markup = await _render_task_detail(task_id)
    except ValueError:
        await callback.answer("Task does not exist.", show_alert=True)
        return
    await callback.answer()
    detail_state = TaskViewState(kind="detail", data={"task_id": task_id})
    chat = getattr(message, "chat", None)
    base_state = _peek_task_view(chat.id, message.message_id) if chat else None
    if base_state is None:
        sent = await _answer_with_markdown(message, detail_text, reply_markup=markup)
        if sent is not None:
            _init_task_view_context(sent, detail_state)
        else:
            # Fix: Give user feedback when sending a message fails
            await message.answer(
                f"⚠️ Failed to display task details—there may be unsupported characters.\nTask ID: {task_id}\nPlease contact the administrator to inspect the task data."
            )
        return
    if await _try_edit_message(message, detail_text, reply_markup=markup):
        _push_detail_view(message, task_id)
        return
    sent = await _answer_with_markdown(message, detail_text, reply_markup=markup)
    if sent is not None:
        _init_task_view_context(sent, detail_state)
    else:
        # Fix: Give user feedback when sending a message fails
        await message.answer(
            f"⚠️ Failed to display task details—there may be unsupported characters.\nTask ID: {task_id}\nPlease contact the administrator to inspect the task data."
        )


async def _fallback_task_detail_back(callback: CallbackQuery) -> None:
    """Fallback to the legacy /task_list trigger when the view stack is missing."""

    message = callback.message
    user = callback.from_user
    if message is None or user is None:
        await callback.answer("Unable to locate the session.", show_alert=True)
        return
    await callback.answer()
    bot = current_bot()
    command_text = "/task_list"
    try:
        now = datetime.now(tz=ZoneInfo("UTC"))
    except ZoneInfoNotFoundError:
        now = datetime.now(UTC)
    entities = [
        MessageEntity(type="bot_command", offset=0, length=len(command_text)),
    ]
    synthetic_message = message.model_copy(
        update={
            "message_id": message.message_id + 1,
            "date": now,
            "edit_date": None,
            "text": command_text,
            "from_user": user,
            "entities": entities,
        }
    )
    update = Update.model_construct(
        update_id=int(time.time() * 1000),
        message=synthetic_message,
    )
    await dp.feed_update(bot, update)


@router.callback_query(F.data == TASK_DETAIL_BACK_CALLBACK)
async def on_task_detail_back(callback: CallbackQuery) -> None:
    message = callback.message
    if message is None:
        await callback.answer("Unable to locate session", show_alert=True)
        return
    popped = _pop_detail_view(message)
    if popped is None:
        await _fallback_task_detail_back(callback)
        return
    chat = getattr(message, "chat", None)
    if chat is None:
        await _fallback_task_detail_back(callback)
        return
    prev_state = _peek_task_view(chat.id, message.message_id)
    if prev_state is None:
        await _fallback_task_detail_back(callback)
        return
    try:
        text, markup = await _render_task_view_from_state(prev_state)
    except Exception as exc:  # pragma: no cover - Going into hiding under extreme circumstances
        worker_log.warning(
            "Recovering task view failed: %s",
            exc,
            extra={"chat": message.chat.id, "message": message.message_id},
        )
        await _fallback_task_detail_back(callback)
        return
    if await _try_edit_message(message, text, reply_markup=markup):
        await callback.answer("Task list has been returned.")
        return
    _clear_task_view(chat.id, message.message_id)
    sent = await _answer_with_markdown(message, text, reply_markup=markup)
    if sent is not None:
        cloned_state = TaskViewState(kind=prev_state.kind, data=dict(prev_state.data))
        _init_task_view_context(sent, cloned_state)
        await callback.answer("Task list has been returned.")
        return
    await _fallback_task_detail_back(callback)


@router.callback_query(F.data.startswith("task:toggle_archive:"))
async def on_toggle_archive(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Callback parameter error.", show_alert=True)
        return
    _, _, task_id = parts
    task = await TASK_SERVICE.get_task(task_id)
    if task is None:
        await callback.answer("Task does not exist.", show_alert=True)
        return
    updated = await TASK_SERVICE.update_task(
        task_id,
        actor=_actor_from_message(callback.message),
        archived=not task.archived,
    )
    detail_text, markup = await _render_task_detail(updated.id)
    message = callback.message
    if message is None:
        await callback.answer("Unable to locate the original message.", show_alert=True)
        return
    detail_state = TaskViewState(kind="detail", data={"task_id": updated.id})
    if await _try_edit_message(message, detail_text, reply_markup=markup):
        _set_task_view_context(message, detail_state)
        await callback.answer("Task status switched.")
        return
    sent = await _answer_with_markdown(message, detail_text, reply_markup=markup)
    if sent is not None:
        _init_task_view_context(sent, detail_state)
        await callback.answer("Task status switched.")
        return
    await callback.answer("Status switched but refreshing the message failed.", show_alert=True)


@router.callback_query(F.data.startswith("task:refresh:"))
async def on_refresh_callback(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Callback parameter error.", show_alert=True)
        return
    _, _, task_id = parts
    message = callback.message
    if message is None:
        await callback.answer("Unable to locate original message", show_alert=True)
        return
    try:
        detail_text, markup = await _render_task_detail(task_id)
    except ValueError:
        await callback.answer("Task does not exist", show_alert=True)
        return
    detail_state = TaskViewState(kind="detail", data={"task_id": task_id})
    if await _try_edit_message(message, detail_text, reply_markup=markup):
        _set_task_view_context(message, detail_state)
        await callback.answer("Refreshed")
        return
    sent = await _answer_with_markdown(message, detail_text, reply_markup=markup)
    if sent is not None:
        _init_task_view_context(sent, detail_state)
        await callback.answer("Refreshed")
        return
    await callback.answer("refreshfail", show_alert=True)


@router.callback_query(F.data.startswith("task:edit:"))
async def on_edit_callback(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Callback parameter error.", show_alert=True)
        return
    _, _, task_id = parts
    task = await TASK_SERVICE.get_task(task_id)
    if task is None:
        await callback.answer("Task does not exist", show_alert=True)
        return
    await state.clear()
    await state.update_data(task_id=task_id, actor=_actor_from_message(callback.message))
    await state.set_state(TaskEditStates.waiting_field_choice)
    await callback.answer("Please select the Character segment to be edited")
    await callback.message.answer("Please select the Character segment that needs to be modified:", reply_markup=_build_edit_field_keyboard())


@router.message(TaskEditStates.waiting_field_choice)
async def on_edit_field_choice(message: Message, state: FSMContext) -> None:
    options = ["title", "priority", "type", "describe", "state", "Cancel"]
    resolved = _resolve_reply_choice(message.text, options=options)
    choice = resolved or (message.text or "").strip()
    mapping = {
        "title": "title",
        "priority": "priority",
        "type": "task_type",
        "describe": "description",
    }
    if choice == "Cancel":
        await state.clear()
        await message.answer("Edited by Cancel", reply_markup=_build_worker_main_keyboard())
        return
    field = mapping.get(choice)
    if choice == "state":
        await state.clear()
        await message.answer("Please use the state button in the useTask details to switch.", reply_markup=_build_worker_main_keyboard())
        return
    if field is None:
        await message.answer("This Character segment is not supported yet, please select again:", reply_markup=_build_edit_field_keyboard())
        return
    if field == "description":
        data = await state.get_data()
        task_id = data.get("task_id")
        if not task_id:
            await state.clear()
            await message.answer("Task information missing. Update cancelled.", reply_markup=_build_worker_main_keyboard())
            return
        task = await TASK_SERVICE.get_task(task_id)
        if task is None:
            await state.clear()
            await message.answer("Task not found. Update cancelled.", reply_markup=_build_worker_main_keyboard())
            return
        actor = data.get("actor") or _actor_from_message(message)
        await _begin_task_desc_edit_flow(
            state=state,
            task=task,
            actor=actor,
            origin_message=message,
        )
        return
    await state.update_data(field=field)
    await state.set_state(TaskEditStates.waiting_new_value)
    if field == "priority":
        await message.answer("Please enter a new priority (1-5):", reply_markup=_build_priority_keyboard())
    elif field == "task_type":
        await message.answer(
            "Please select a new task type (need / defect / optimization / risk):",
            reply_markup=_build_task_type_keyboard(),
        )
    else:
        await message.answer("Please enter the new value:", reply_markup=_build_worker_main_keyboard())


@router.message(TaskEditStates.waiting_new_value)
async def on_edit_new_value(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    task_id = data.get("task_id")
    field = data.get("field")
    if not task_id or not field:
        await state.clear()
        await message.answer("Input data missing. Update cancelled.", reply_markup=_build_worker_main_keyboard())
        return
    raw_text = message.text or ""
    text = raw_text.strip()
    resolved_task_type: Optional[str] = None
    if field == "task_type":
        task_type_options = [_format_task_type(task_type) for task_type in TASK_TYPES]
        task_type_options.append("Cancel")
        resolved_task_type = _resolve_reply_choice(raw_text, options=task_type_options)
        if resolved_task_type == "Cancel":
            await state.clear()
            await message.answer("Edit cancelled.", reply_markup=_build_worker_main_keyboard())
            return
    elif text == "Cancel":
        await state.clear()
        await message.answer("Edit cancelled.", reply_markup=_build_worker_main_keyboard())
        return

    update_kwargs: dict[str, Any] = {}
    if field == "priority":
        priority_options = [str(i) for i in range(1, 6)]
        priority_options.append(SKIP_TEXT)
        resolved_priority = _resolve_reply_choice(raw_text, options=priority_options)
        if resolved_priority == SKIP_TEXT:
            await message.answer(
                "Priority must be a number from 1 to 5. Please try again:",
                reply_markup=_build_priority_keyboard(),
            )
            return
        candidate = resolved_priority or text
        try:
            value = int(candidate)
        except ValueError:
            await message.answer(
                "Priority must be a number from 1 to 5. Please try again:",
                reply_markup=_build_priority_keyboard(),
            )
            return
        value = max(1, min(value, 5))
        update_kwargs["priority"] = value
    elif field == "description":
        if len(text) > DESCRIPTION_MAX_LENGTH:
            await message.answer(
                f"The task description cannot exceed {DESCRIPTION_MAX_LENGTH} characters. Please try again:",
                reply_markup=_build_worker_main_keyboard(),
            )
            return
        update_kwargs["description"] = text
    elif field == "task_type":
        candidate = resolved_task_type or text
        task_type = _normalize_task_type(candidate)
        if task_type is None:
            await message.answer(
                "Invalid task type. Enter need/defect/optimization/risk or use the keyboard:",
                reply_markup=_build_task_type_keyboard(),
            )
            return
        update_kwargs["task_type"] = task_type
    else:
        if not text:
            await message.answer(
                "The title cannot be empty. Please enter a non-empty value:",
                reply_markup=_build_worker_main_keyboard(),
            )
            return
        update_kwargs["title"] = text
    await state.clear()
    try:
        updated = await TASK_SERVICE.update_task(
            task_id,
            actor=_actor_from_message(message),
            title=update_kwargs.get("title"),
            priority=update_kwargs.get("priority"),
            task_type=update_kwargs.get("task_type"),
            description=update_kwargs.get("description"),
        )
    except ValueError as exc:
        await message.answer(str(exc), reply_markup=_build_worker_main_keyboard())
        return
    detail_text, markup = await _render_task_detail(updated.id)
    await _answer_with_markdown(message, f"Task updated:\n{detail_text}", reply_markup=markup)


@router.message(
    F.photo | F.document | F.video | F.audio | F.voice | F.animation | F.video_note
)
async def on_media_message(message: Message) -> None:
    """Process ordinary messages with attachments, download the attachments and splice prompt words."""

    _auto_record_chat_id(message.chat.id)
    text_part = (message.caption or message.text or "").strip()

    if message.media_group_id:
        await _enqueue_media_group_message(message, text_part)
        return

    attachment_dir = _attachment_dir_for_message(message)
    attachments = await _collect_saved_attachments(message, attachment_dir)
    if not attachments and not text_part:
        await message.answer("No processable attachment or text Charactercontent was detected.")
        return
    prompt = _build_prompt_with_attachments(text_part, attachments)
    await _handle_prompt_dispatch(message, prompt)


@router.message(CommandStart())
async def on_start(m: Message):
    # Automatically record chat when first received message_id to state document
    _auto_record_chat_id(m.chat.id)

    await m.answer(
        (
            f"Hello, {m.from_user.full_name}！\n"
            "Just send a question to talk to the model,\n"
            "Or useTask function to organize need and execution records. \n\n"
            "The main menu is ready, I wish you a happy use!"
        ),
        reply_markup=_build_worker_main_keyboard(),
    )
    worker_log.info("Received /start, chat_id=%s", m.chat.id, extra=_session_extra())
    if ENV_ISSUES:
        await m.answer(_format_env_issue_message())

@router.message(F.text)
async def on_text(m: Message, state: FSMContext | None = None):
    # Automatically record chat when first received message_id to state document
    _auto_record_chat_id(m.chat.id)

    current_state: Optional[str] = None
    if state is not None:
        current_state = await state.get_state()

    prompt = (m.text or "").strip()
    if not prompt:
        return await m.answer("Please enter a non-empty prompt word")
    task_id_candidate = _normalize_task_id(prompt)
    if task_id_candidate:
        await _reply_task_detail_message(m, task_id_candidate)
        return
    if prompt.startswith("/"):
        return
    if current_state:
        worker_log.debug(
            "Suppressed model dispatch due to active wizard state",
            extra={**_session_extra(), "chat": m.chat.id, "state": current_state},
        )
        return
    if _is_menu_control_message(prompt):
        worker_log.info(
            "Suppressed stray menu control input without active wizard",
            extra={
                **_session_extra(),
                "chat": getattr(m.chat, "id", None),
                "token": _normalize_choice_token(prompt),
            },
        )
        return
    await _handle_prompt_dispatch(m, prompt)


async def ensure_telegram_connectivity(bot: Bot, timeout: float = 30.0):
    """Verify Telegram connectivity before starting to facilitate quick location of agents/network problems"""
    try:
        if hasattr(asyncio, "timeout"):
            async with asyncio.timeout(timeout):
                me = await bot.get_me()
        else:
            me = await asyncio.wait_for(bot.get_me(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise RuntimeError(f"exist {timeout} SecondFailed to successfully shake hands with Telegram") from exc
    except TelegramNetworkError as exc:
        raise RuntimeError("Telegram Network request failed, please check the proxy or network policy") from exc
    except ClientError as exc:
        raise RuntimeError("Unable to connect to proxy or Telegram, please check proxy configuration") from exc
    else:
        worker_log.info(
            "Telegram The connection is OK, Bot=%s (id=%s)",
            me.username,
            me.id,
            extra=_session_extra(),
        )
        return me


async def _ensure_bot_commands(bot: Bot) -> None:
    commands = [BotCommand(command=cmd, description=desc) for cmd, desc in BOT_COMMANDS]
    scopes: list[tuple[Optional[object], str]] = [
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
            worker_log.warning(
                "Set Bot command fail: %s",
                exc,
                extra={**_session_extra(), "scope": label},
            )
        else:
            worker_log.info(
                "Bot Commands synchronized",
                extra={**_session_extra(), "scope": label},
            )


async def _ensure_worker_menu_button(bot: Bot) -> None:
    """Ensure the worker-side chat menu button text points to the task list."""
    try:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonCommands(text=WORKER_MENU_BUTTON_TEXT),
        )
    except TelegramBadRequest as exc:
        worker_log.warning(
            "Setting chat menu fail: %s",
            exc,
            extra=_session_extra(),
        )
    else:
        worker_log.info(
            "Chat menu synced",
            extra={**_session_extra(), "text": WORKER_MENU_BUTTON_TEXT},
        )

async def main():
    global _bot, CHAT_LONG_POLL_LOCK
    # Initialize long polling lock
    CHAT_LONG_POLL_LOCK = asyncio.Lock()
    _bot = build_bot()
    try:
        await ensure_telegram_connectivity(_bot)
    except Exception as exc:
        worker_log.error("Telegram Connectivity check failed: %s", exc, extra=_session_extra())
        if _bot:
            await _bot.session.close()
        raise SystemExit(1)
    try:
        await TASK_SERVICE.initialize()
    except Exception as exc:
        worker_log.error("TaskDatabase initialization fail: %s", exc, extra=_session_extra())
        if _bot:
            await _bot.session.close()
        raise SystemExit(1)
    try:
        await COMMAND_PRESET_SERVICE.initialize()
    except Exception as exc:
        worker_log.error("Command preset initialization fail: %s", exc, extra=_session_extra())
        if _bot:
            await _bot.session.close()
        raise SystemExit(1)
    try:
        await GLOBAL_COMMAND_PRESET_SERVICE.initialize()
    except Exception as exc:
        worker_log.error("Global command preset initialization fail: %s", exc, extra=_session_extra())
        if _bot:
            await _bot.session.close()
        raise SystemExit(1)
    await _ensure_bot_commands(_bot)
    await _ensure_worker_menu_button(_bot)
    await _broadcast_worker_keyboard(_bot)

    try:
        await dp.start_polling(_bot)
    finally:
        if _bot:
            await _bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
