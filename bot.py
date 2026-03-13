# bot.py — Telegram 提示词 → Mac 执行 → 回推 (aiogram 3.x)
# 说明：
# - 使用长轮询，不需要公网端口；
# - MODE=A: 直接以子进程方式调用你的 agent/codex CLI/HTTP（此处给出 CLI 示例）；
# - MODE=B: 将提示词注入 tmux 会话（如 vibe），依靠 pipe-pane 写入的日志抽取本次输出；
# - 安全：仅允许 ALLOWED_CHAT_ID（私聊你的 chat_id）；BOT_TOKEN 从 .env 读取；不要把 token 写进代码。

from __future__ import annotations

import asyncio, os, sys, time, uuid, shlex, subprocess, socket, re, json, shutil, hashlib, html, mimetypes
try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python<3.11 兼容兜底
    import tomli as tomllib  # type: ignore[no-redef]
from contextlib import suppress
from datetime import datetime, timezone
try:
    from datetime import UTC
except ImportError:  # pragma: no cover - 仅 Python3.10 及更早版本会触发
    UTC = timezone.utc  # Python<3.11 没有 datetime.UTC，用 timezone.utc 兜底
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple, List, Callable, Awaitable, Literal, Mapping
from dataclasses import dataclass, field
from urllib.parse import urlparse, quote, unquote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.filters.command import CommandObject
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State
from aiogram.fsm.storage.memory import MemoryStorage
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
    FSInputFile,
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
from codex_trust import ensure_codex_project_trust
from tasks import TaskHistoryRecord, TaskNoteRecord, TaskRecord, TaskAttachmentRecord, TaskService
from tasks.commands import parse_simple_kv, parse_structured_text
from tasks.models import shanghai_now_iso
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
    TaskBatchPushStates,
    TaskCreateStates,
    TaskDefectReportStates,
    TaskDescriptionStates,
    TaskAttachmentStates,
    TaskEditStates,
    TaskListSearchStates,
    TaskNoteStates,
    TaskPushStates,
    ModelQuickReplyStates,
)
from command_center import (
    CommandCreateStates,
    CommandEditStates,
    WxPreviewStates,
    CommandDefinition,
    CommandHistoryRecord,
    CommandService,
    CommandAliasConflictError,
    CommandAlreadyExistsError,
    CommandNotFoundError,
    CommandHistoryNotFoundError,
    GLOBAL_COMMAND_PROJECT_SLUG,
    GLOBAL_COMMAND_SCOPE,
    resolve_global_command_db,
)
from command_center.prompts import build_field_prompt_text
from parallel_runtime import (
    BranchRef,
    CommonBranchRef,
    DEFAULT_PARALLEL_BRANCH_PREFIX,
    ParallelCommitResult,
    ParallelMergeResult,
    ParallelRepoRecord,
    ParallelSessionRecord,
    ParallelSessionStore,
    RepoBranchSelection,
    RepoOperationResult,
    build_parallel_branch_name,
    collect_common_branch_refs,
    commit_parallel_repos,
    delete_parallel_workspace,
    discover_git_repos,
    filter_common_branch_repo_options,
    get_current_branch_state,
    list_branch_refs,
    merge_parallel_repos,
    normalize_parallel_branch_prefix,
    prepare_parallel_workspace,
)

# Python 3.10 才支持 dataclass slots，这里动态传参以兼容旧版本。
_DATACLASS_SLOT_KW = {"slots": True} if sys.version_info >= (3, 10) else {}
# --- 简单 .env 加载 ---
def load_env(p: str = ".env"):
    """从指定路径加载 dotenv 格式的键值对到进程环境变量。"""

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

# --- 日志 & 上下文 ---
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
    """解析配置根目录，优先读取显式环境变量并兼容 XDG 约定。"""

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

def _env_int(name: str, default: int) -> int:
    """读取整型环境变量，解析失败时回退默认值。"""

    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        worker_log.warning("环境变量 %s=%r 解析为整数失败，已使用默认值 %s", name, raw, default)
        return default


def _env_float(name: str, default: float) -> float:
    """读取浮点型环境变量，解析失败时回退默认值。"""

    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw.strip())
    except ValueError:
        worker_log.warning("环境变量 %s=%r 解析为浮点数失败，已使用默认值 %s", name, raw, default)
        return default


def _env_bool(name: str, default: bool) -> bool:
    """读取布尔型环境变量，兼容多种写法。"""

    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
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

# 阶段提示统一追加 agents.md 信息，确保推送记录要求一致。
AGENTS_PHASE_SUFFIX = "，最后列出当前所触发的 agents.md 的阶段、任务名称、任务编码（例：/TASK_0001）。以下是需要执行的任务描述以及其对应的执行历史摘要："
# 推送到模型的阶段提示（vibe 与测试），合并统一后缀确保输出一致。
VIBE_PHASE_PROMPT = f"进入vibe阶段{AGENTS_PHASE_SUFFIX}"
TEST_PHASE_PROMPT = f"进入测试阶段{AGENTS_PHASE_SUFFIX}"
# 报告缺陷时的专用前缀，插入在统一提示语之前
BUG_REPORT_PREFIX = "报告一个缺陷，详见底部最新的缺陷描述。\n"

# 推送到模型模式（PLAN / YOLO）
PUSH_MODE_PLAN = "PLAN"
PUSH_MODE_YOLO = "YOLO"
PUSH_TARGET_CURRENT = "现有 CLI 会话处理"
PUSH_TARGET_PARALLEL = "新建分支 + 新 CLI 并行处理"
PUSH_SEND_MODE_IMMEDIATE = "immediate"
PUSH_SEND_MODE_QUEUED = "queued"
PUSH_SEND_MODE_IMMEDIATE_LABEL = "立即发送"
PUSH_SEND_MODE_QUEUED_LABEL = "排队发送"

_parse_mode_env = (os.environ.get("TELEGRAM_PARSE_MODE") or "Markdown").strip()
_parse_mode_key = _parse_mode_env.replace("-", "").replace("_", "").lower()
MODEL_OUTPUT_PARSE_MODE: Optional[ParseMode]
if _parse_mode_key in _PARSE_MODE_CANDIDATES:
    MODEL_OUTPUT_PARSE_MODE = _PARSE_MODE_CANDIDATES[_parse_mode_key]
    if MODEL_OUTPUT_PARSE_MODE is None:
        worker_log.info("模型输出将按纯文本发送")
    else:
        mode_value = (
            MODEL_OUTPUT_PARSE_MODE.value
            if isinstance(MODEL_OUTPUT_PARSE_MODE, ParseMode)
            else str(MODEL_OUTPUT_PARSE_MODE)
        )
        worker_log.info("模型输出 parse_mode：%s", mode_value)
else:
    MODEL_OUTPUT_PARSE_MODE = ParseMode.MARKDOWN_V2
    worker_log.warning(
        "未识别的 TELEGRAM_PARSE_MODE=%s，回退为 MarkdownV2",
        _parse_mode_env,
    )

_plan_parse_mode_env = (os.environ.get("PLAN_PROGRESS_PARSE_MODE") or "").strip()
_plan_parse_mode_key = _plan_parse_mode_env.replace("-", "").replace("_", "").lower()
PLAN_PROGRESS_PARSE_MODE: Optional[ParseMode]
if not _plan_parse_mode_key:
    PLAN_PROGRESS_PARSE_MODE = None
    worker_log.info("计划进度消息默认按纯文本发送")
elif _plan_parse_mode_key in _PARSE_MODE_CANDIDATES:
    PLAN_PROGRESS_PARSE_MODE = _PARSE_MODE_CANDIDATES[_plan_parse_mode_key]
    if PLAN_PROGRESS_PARSE_MODE is None:
        worker_log.info("计划进度消息将按纯文本发送")
    else:
        mode_value = (
            PLAN_PROGRESS_PARSE_MODE.value
            if isinstance(PLAN_PROGRESS_PARSE_MODE, ParseMode)
            else str(PLAN_PROGRESS_PARSE_MODE)
        )
        worker_log.info("计划进度消息 parse_mode：%s", mode_value)
else:
    PLAN_PROGRESS_PARSE_MODE = None
    worker_log.warning(
        "未识别的 PLAN_PROGRESS_PARSE_MODE=%s，计划进度消息将按纯文本发送",
        _plan_parse_mode_env,
    )

_IS_MARKDOWN_V2 = MODEL_OUTPUT_PARSE_MODE == ParseMode.MARKDOWN_V2
_IS_MARKDOWN = MODEL_OUTPUT_PARSE_MODE == ParseMode.MARKDOWN


def _parse_mode_value() -> Optional[str]:
    """返回模型输出使用的 Telegram parse_mode 值。"""

    if MODEL_OUTPUT_PARSE_MODE is None:
        return None
    return MODEL_OUTPUT_PARSE_MODE.value if isinstance(MODEL_OUTPUT_PARSE_MODE, ParseMode) else str(MODEL_OUTPUT_PARSE_MODE)


def _plan_parse_mode_value() -> Optional[str]:
    """返回计划进度消息使用的 Telegram parse_mode 值。"""

    if PLAN_PROGRESS_PARSE_MODE is None:
        return None
    return (
        PLAN_PROGRESS_PARSE_MODE.value
        if isinstance(PLAN_PROGRESS_PARSE_MODE, ParseMode)
        else str(PLAN_PROGRESS_PARSE_MODE)
    )

# --- 配置 ---
BOT_TOKEN = os.environ.get("BOT_TOKEN") or ""
if not BOT_TOKEN:
    worker_log.error("BOT_TOKEN 未配置，程序退出")
    sys.exit(1)

MODE = os.environ.get("MODE", "B").upper()                      # A 或 B

# 模式A（CLI）
AGENT_CMD = os.environ.get("AGENT_CMD", "")  # 例如: codex --project /path/to/proj --prompt -
# 可扩展 HTTP：AGENT_HTTP=http://127.0.0.1:7001/api/run

# 模式B（tmux）
TMUX_SESSION = os.environ.get("TMUX_SESSION", "vibe")
TMUX_LOG = os.environ.get("TMUX_LOG", str(Path(__file__).resolve().parent / "vibe.out.log"))
IDLE_SECONDS = float(os.environ.get("IDLE_SECONDS", "3"))
MAX_RETURN_CHARS = int(os.environ.get("MAX_RETURN_CHARS", "200000"))  # 超大文本转附件
TELEGRAM_PROXY = os.environ.get("TELEGRAM_PROXY", "").strip()        # 可选代理 URL
CODEX_WORKDIR = os.environ.get("CODEX_WORKDIR", "").strip()
CODEX_SESSION_FILE_PATH = os.environ.get("CODEX_SESSION_FILE_PATH", "").strip()
CODEX_CONFIG_PATH = Path(os.environ.get("CODEX_CONFIG_PATH", str(Path.home() / ".codex" / "config.toml"))).expanduser()
SESSION_ACTIVE_ID_FILE = os.environ.get("SESSION_ACTIVE_ID_FILE", "").strip()
CODEX_SESSIONS_ROOT = os.environ.get("CODEX_SESSIONS_ROOT", "").strip()
MODEL_SESSION_ROOT = os.environ.get("MODEL_SESSION_ROOT", "").strip()
MODEL_SESSION_GLOB = os.environ.get("MODEL_SESSION_GLOB", "rollout-*.jsonl").strip() or "rollout-*.jsonl"
SESSION_POLL_TIMEOUT = float(os.environ.get("SESSION_POLL_TIMEOUT", "2"))
WATCH_MAX_WAIT = float(os.environ.get("WATCH_MAX_WAIT", "0"))
WATCH_INTERVAL = float(os.environ.get("WATCH_INTERVAL", "2"))
# Telegram 消息补偿轮询：每条入站消息触发一次，按 1/3/10/30/90 分钟检测是否有遗漏输出。
MESSAGE_RECOVERY_POLL_DELAYS_SECONDS: tuple[float, ...] = (60.0, 180.0, 600.0, 1800.0, 5400.0)
SEND_RETRY_ATTEMPTS = int(os.environ.get("SEND_RETRY_ATTEMPTS", "3"))
TMUX_SNAPSHOT_LINES = _env_int("TMUX_SNAPSHOT_LINES", 5)
TMUX_SNAPSHOT_MAX_LINES = _env_int("TMUX_SNAPSHOT_MAX_LINES", 500)
TMUX_SNAPSHOT_TIMEOUT_SECONDS = max(_env_float("TMUX_SNAPSHOT_TIMEOUT_SECONDS", 3.0), 0.0)
SEND_RETRY_BASE_DELAY = float(os.environ.get("SEND_RETRY_BASE_DELAY", "0.5"))
SEND_FAILURE_NOTICE_COOLDOWN = float(os.environ.get("SEND_FAILURE_NOTICE_COOLDOWN", "30"))
SESSION_INITIAL_BACKTRACK_BYTES = int(os.environ.get("SESSION_INITIAL_BACKTRACK_BYTES", "16384"))
GEMINI_SESSION_INITIAL_BACKTRACK_MESSAGES = max(_env_int("GEMINI_SESSION_INITIAL_BACKTRACK_MESSAGES", 20), 0)
ENABLE_PLAN_PROGRESS = (os.environ.get("ENABLE_PLAN_PROGRESS", "1").strip().lower() not in {"0", "false", "no", "off"})
AUTO_COMPACT_THRESHOLD = max(_env_int("AUTO_COMPACT_THRESHOLD", 0), 0)
SESSION_BIND_STRICT = _env_bool("SESSION_BIND_STRICT", True)
SESSION_BIND_TIMEOUT_SECONDS = max(_env_float("SESSION_BIND_TIMEOUT_SECONDS", 30.0), 0.0)
SESSION_BIND_POLL_INTERVAL = max(_env_float("SESSION_BIND_POLL_INTERVAL", 0.5), 0.1)
# Codex PLAN 模式切换：在发送提示词前可选发送 /plan 预命令
PLAN_MODE_SWITCH_COMMAND = (os.environ.get("PLAN_MODE_SWITCH_COMMAND") or "/plan").strip() or "/plan"
PLAN_MODE_SWITCH_DELAY_SECONDS = max(_env_float("PLAN_MODE_SWITCH_DELAY_SECONDS", 0.25), 0.0)
PARALLEL_PLAN_READY_TIMEOUT_SECONDS = max(_env_float("PARALLEL_PLAN_READY_TIMEOUT_SECONDS", 6.0), 0.0)
PARALLEL_PLAN_READY_POLL_INTERVAL_SECONDS = max(_env_float("PARALLEL_PLAN_READY_POLL_INTERVAL_SECONDS", 0.2), 0.05)
PARALLEL_PLAN_READY_PROBE_LINES = max(_env_int("PARALLEL_PLAN_READY_PROBE_LINES", 80), 20)
PARALLEL_WORKSPACE_PREPARE_TIMEOUT_SECONDS = max(_env_float("PARALLEL_WORKSPACE_PREPARE_TIMEOUT_SECONDS", 45.0), 0.0)
PARALLEL_PLAN_READY_MARKERS: Tuple[str, ...] = (
    "OpenAI Codex",
    "model:",
    "/model to change",
)
# 直接 Telegram 文本消息默认按 PLAN 推送（先 /plan 再发正文）
ENABLE_AUTO_PLAN_FOR_DIRECT_MESSAGE = _env_bool("ENABLE_AUTO_PLAN_FOR_DIRECT_MESSAGE", True)
# tmux 注入兜底：部分终端偶发“文本已进输入框但未真正发送”，默认延迟补发一次 Enter。
TMUX_SEND_LINE_DOUBLE_ENTER_ENABLED = _env_bool("TMUX_SEND_LINE_DOUBLE_ENTER_ENABLED", True)
CODEX_TRUST_SCOPE_PARALLEL_WORKSPACE = "parallel_workspace"
CODEX_TRUST_SCOPE_PROJECT_WORKDIR = "project_workdir"
CODEX_CONFIG_LOCK = asyncio.Lock()
TMUX_SEND_LINE_DOUBLE_ENTER_DELAY_SECONDS = max(_env_float("TMUX_SEND_LINE_DOUBLE_ENTER_DELAY_SECONDS", 2.0), 0.0)

PLAN_STATUS_LABELS = {
    "completed": "✅",
    "in_progress": "🔄",
    "pending": "⏳",
}

DELIVERABLE_KIND_MESSAGE = "message"
DELIVERABLE_KIND_PLAN = "plan_update"
DELIVERABLE_KIND_REQUEST_INPUT = "request_input"
CODEX_MESSAGE_PHASE_COMMENTARY = "commentary"
CODEX_MESSAGE_PHASE_FINAL_ANSWER = "final_answer"
MODEL_COMPLETION_PREFIX = "✅模型执行完成，响应结果如下："
TELEGRAM_MESSAGE_LIMIT = 4096  # Telegram sendMessage 单条上限
# 长文本粘贴聚合：当用户粘贴内容接近上限时，Telegram 客户端可能拆成多条消息；
# 这里在入站最前置聚合为一次逻辑输入（覆盖普通对话 + 任务/缺陷等 FSM 交互），避免流程被分片打断。
# - 仅当单条文本长度达到阈值或命中“短前缀 + 长日志”模式时触发，降低误合并风险
ENABLE_TEXT_PASTE_AGGREGATION = _env_bool("ENABLE_TEXT_PASTE_AGGREGATION", True)
TEXT_PASTE_NEAR_LIMIT_THRESHOLD = max(_env_int("TEXT_PASTE_NEAR_LIMIT_THRESHOLD", 3500), 0)
TEXT_PASTE_AGGREGATION_DELAY = max(_env_float("TEXT_PASTE_AGGREGATION_DELAY", 0.8), 0.1)
# “短前缀 + 长日志”合并：短前缀（通常很短且以冒号结尾）先进入等待窗口，若窗口内出现长日志分片则合并为一次推送。
TEXT_PASTE_PREFIX_MAX_CHARS = max(_env_int("TEXT_PASTE_PREFIX_MAX_CHARS", 120), 0)
TEXT_PASTE_PREFIX_FOLLOWUP_MIN_CHARS = max(_env_int("TEXT_PASTE_PREFIX_FOLLOWUP_MIN_CHARS", 200), 0)
# 发送到 tmux 的提示词前缀（用户确认版本），用于强制模型遵守 vibego 规约文件
ENFORCED_AGENTS_NOTICE = (
    "【强制规约】你必须先读取 $HOME/.config/vibego/AGENTS.md、当前根目录 AGENTS.md、"
    "以及所有受影响子项目目录下最近的 AGENTS.md 与 AGENTS.evidence.json；如冲突以更近目录为准。\n"
    "本次任务继续走 vibe -> design -> develop；无论 PLAN 还是 YOLO，都必须严格执行 TDD 门禁。\n"
    "如未特殊指定模式，则默认进入 PLAN 模式。\n"
    "以下是用户需求描述："
)
# 模型答案消息底部快捷按钮（仅用于模型输出投递的消息）
MODEL_QUICK_REPLY_ALL_CALLBACK = "model:quick_reply:all"
MODEL_QUICK_REPLY_PARTIAL_CALLBACK = "model:quick_reply:partial"
MODEL_QUICK_REPLY_ALL_TASK_PREFIX = "model:quick_reply:all:"
MODEL_QUICK_REPLY_PARTIAL_TASK_PREFIX = "model:quick_reply:partial:"
MODEL_QUICK_REPLY_ALL_SESSION_PREFIX = "model:quick_reply:all_session:"
MODEL_QUICK_REPLY_PARTIAL_SESSION_PREFIX = "model:quick_reply:partial_session:"
SESSION_COMMIT_CALLBACK_PREFIX = "session:commit:"
# 模型答案消息底部：一键将任务切换到“测试”（不依赖提示词/摘要输出）
MODEL_TASK_TO_TEST_PREFIX = "model:task_to_test:"
PARALLEL_REPLY_CALLBACK_PREFIX = "parallel:reply:"
PARALLEL_COMMIT_CALLBACK_PREFIX = "parallel:commit:"
PARALLEL_MERGE_CALLBACK_PREFIX = "parallel:merge:"
PARALLEL_MERGE_SKIP_CALLBACK_PREFIX = "parallel:merge_skip:"
PARALLEL_DELETE_CALLBACK_PREFIX = "parallel:delete:"
PARALLEL_DELETE_CONFIRM_CALLBACK_PREFIX = "parallel:delete_confirm:"
PARALLEL_DELETE_CANCEL_CALLBACK_PREFIX = "parallel:delete_cancel:"
PARALLEL_BRANCH_SELECT_PREFIX = "parallel:branch_select:"
PARALLEL_BRANCH_PAGE_PREFIX = "parallel:branch_page:"
PARALLEL_BRANCH_CANCEL_PREFIX = "parallel:branch_cancel:"
PARALLEL_BRANCH_CONFIRM_PREFIX = "parallel:branch_confirm:"
PARALLEL_BRANCH_PREFIX_CANCEL_PREFIX = "parallel:branch_prefix_cancel:"
PARALLEL_COMMON_BRANCH_SELECT_PREFIX = "parallel:common_branch_select:"
PARALLEL_COMMON_BRANCH_PAGE_PREFIX = "parallel:common_branch_page:"
PARALLEL_BRANCH_INDIVIDUAL_PREFIX = "parallel:branch_individual:"
PARALLEL_BRANCH_PREPARING_MESSAGE = "正在准备并行副本，请稍候……"
PARALLEL_BRANCH_STARTING_CLI_MESSAGE = "正在启动并行 CLI，请稍候……"
# request_user_input：Telegram 按钮回调前缀（需控制总长度 <= 64 字节）
REQUEST_INPUT_CALLBACK_PREFIX = "rui:"
REQUEST_INPUT_ACTION_OPTION = "opt"
REQUEST_INPUT_ACTION_CUSTOM = "custom"
REQUEST_INPUT_ACTION_PREV = "prev"
REQUEST_INPUT_ACTION_NEXT = "next"
REQUEST_INPUT_ACTION_SUBMIT = "submit"
REQUEST_INPUT_ACTION_CANCEL = "cancel"
REQUEST_INPUT_ACTION_RETRY_SUBMIT = "retry_submit"
REQUEST_INPUT_CUSTOM_OPTION_INDEX = -1
REQUEST_INPUT_CUSTOM_LABEL = "输入自定义决策"
REQUEST_INPUT_SESSION_TTL_SECONDS = max(_env_float("REQUEST_INPUT_SESSION_TTL_SECONDS", 900.0), 60.0)
REQUEST_INPUT_MAX_QUESTIONS = max(_env_int("REQUEST_INPUT_MAX_QUESTIONS", 20), 1)
REQUEST_INPUT_MAX_OPTIONS = max(_env_int("REQUEST_INPUT_MAX_OPTIONS", 10), 1)
REQUEST_INPUT_SUBMIT_AUTO_RETRY_MAX = max(_env_int("REQUEST_INPUT_SUBMIT_AUTO_RETRY_MAX", 1), 0)
ENABLE_REQUEST_USER_INPUT_UI = _env_bool("ENABLE_REQUEST_USER_INPUT_UI", True)
# Plan 结束确认：透传为 Telegram 按钮（最小改造）
PLAN_CONFIRM_CALLBACK_PREFIX = "pcf:"
PLAN_CONFIRM_ACTION_YES = "yes"
PLAN_CONFIRM_ACTION_NO = "no"
PLAN_IMPLEMENT_PROMPT = "Implement the plan."
# 兼容历史提示词：保留旧常量，避免外部引用报错。
PLAN_IMPLEMENT_EXEC_PROMPT = "develop\nImplement the plan."
PLAN_RECOVERY_DEVELOP_PROMPT = "进入开发阶段\ndevelop"
# 兼容历史“重试进入开发”按钮回调（旧消息仍可点击）。
PLAN_DEVELOP_RETRY_CALLBACK_PREFIX = "pdr:"
PLAN_DEVELOP_RETRY_ACTION_RETRY = "retry"
# Plan -> develop 强制切换：先发送 Shift+Tab（tmux 键名 BTab）再发送 develop 提示。
PLAN_EXECUTION_EXIT_PLAN_KEY = (os.environ.get("PLAN_EXECUTION_EXIT_PLAN_KEY") or "BTab").strip() or "BTab"
# 是否在发送 Shift+Tab 前先发送 Escape，避免焦点停留在输入态/菜单态。
PLAN_EXECUTION_EXIT_PLAN_ESC_FIRST = _env_bool("PLAN_EXECUTION_EXIT_PLAN_ESC_FIRST", True)
# 单轮切换中要发送的按键序列（逗号分隔）。默认连续两次 Shift+Tab，提升切换稳定性。
PLAN_EXECUTION_EXIT_PLAN_RETRY_KEYS = tuple(
    key.strip()
    for key in (
        os.environ.get("PLAN_EXECUTION_EXIT_PLAN_RETRY_KEYS")
        or f"{PLAN_EXECUTION_EXIT_PLAN_KEY},{PLAN_EXECUTION_EXIT_PLAN_KEY}"
    ).split(",")
    if key.strip()
)
# 单轮按键序列中，相邻按键的等待时间（秒）。
PLAN_EXECUTION_EXIT_PLAN_RETRY_GAP_SECONDS = max(_env_float("PLAN_EXECUTION_EXIT_PLAN_RETRY_GAP_SECONDS", 0.15), 0.0)
# 最多执行多少轮“退出 Plan”尝试。
PLAN_EXECUTION_EXIT_PLAN_MAX_ROUNDS = max(_env_int("PLAN_EXECUTION_EXIT_PLAN_MAX_ROUNDS", 2), 1)
# 单轮按键序列发送完成后，等待终端模式稳定再探测。
PLAN_EXECUTION_EXIT_PLAN_DELAY_SECONDS = max(_env_float("PLAN_EXECUTION_EXIT_PLAN_DELAY_SECONDS", 0.2), 0.0)
PLAN_EXECUTION_MODE_PROBE_LINES = max(_env_int("PLAN_EXECUTION_MODE_PROBE_LINES", 80), 20)
# “重试进入开发”按钮链路的专用退出按键策略（默认：Escape + 单次 BTab）。
PLAN_DEVELOP_RETRY_EXIT_PLAN_ESC_FIRST = _env_bool("PLAN_DEVELOP_RETRY_EXIT_PLAN_ESC_FIRST", True)
PLAN_DEVELOP_RETRY_EXIT_PLAN_KEYS = tuple(
    key.strip()
    for key in (os.environ.get("PLAN_DEVELOP_RETRY_EXIT_PLAN_KEYS") or PLAN_EXECUTION_EXIT_PLAN_KEY).split(",")
    if key.strip()
)
PLAN_DEVELOP_RETRY_EXIT_PLAN_MAX_ROUNDS = max(_env_int("PLAN_DEVELOP_RETRY_EXIT_PLAN_MAX_ROUNDS", 1), 1)


def _canonical_model_name(raw_model: Optional[str] = None) -> str:
    """标准化模型名称，便于后续按模型分支处理。"""

    source = raw_model
    if source is None:
        source = (os.environ.get("MODEL_NAME") or ACTIVE_MODEL or "codex").strip()
    normalized = source.replace("-", "").replace("_", "").lower()
    return normalized or "codex"


def _model_display_label() -> str:
    """返回当前活跃模型的友好名称。"""

    raw = (os.environ.get("MODEL_NAME") or ACTIVE_MODEL or "codex").strip()
    normalized = _canonical_model_name(raw)
    mapping = {
        "codex": "Codex",
        "claudecode": "ClaudeCode",
        "gemini": "Gemini",
    }
    return mapping.get(normalized, raw or "模型")


MODEL_CANONICAL_NAME = _canonical_model_name()
MODEL_DISPLAY_LABEL = _model_display_label()


def _is_claudecode_model() -> bool:
    """判断当前 worker 是否运行 ClaudeCode 模型。"""

    return MODEL_CANONICAL_NAME == "claudecode"


def _is_gemini_model() -> bool:
    """判断当前 worker 是否运行 Gemini 模型。"""

    return MODEL_CANONICAL_NAME == "gemini"


def _is_codex_model() -> bool:
    """判断当前 worker 是否运行 Codex 模型。"""

    return MODEL_CANONICAL_NAME == "codex"


@dataclass
class SessionDeliverable:
    """描述 JSONL 会话中的单个推送事件。"""

    offset: int
    kind: str
    text: str
    timestamp: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass(**_DATACLASS_SLOT_KW)
class RequestInputOption:
    """request_user_input 单个候选项。"""

    label: str
    description: str = ""


@dataclass(**_DATACLASS_SLOT_KW)
class RequestInputQuestion:
    """request_user_input 单个问题。"""

    question_id: str
    question: str
    header: str = ""
    options: List[RequestInputOption] = field(default_factory=list)


@dataclass(**_DATACLASS_SLOT_KW)
class RequestInputSession:
    """Telegram 侧 request_user_input 交互会话。"""

    token: str
    chat_id: int
    user_id: int
    call_id: str
    session_key: str
    questions: List[RequestInputQuestion]
    current_index: int
    created_at: float
    expires_at: float
    selected_option_indexes: Dict[str, int] = field(default_factory=dict)
    custom_answers: Dict[str, str] = field(default_factory=dict)
    question_message_ids: Dict[str, int] = field(default_factory=dict)
    processed_media_groups: set[str] = field(default_factory=set)
    input_mode_question_id: Optional[str] = None
    parallel_task_id: Optional[str] = None
    parallel_dispatch_context: Optional["ParallelDispatchContext"] = None
    submission_state: str = "idle"
    submit_retry_count: int = 0
    submitted: bool = False
    cancelled: bool = False


@dataclass(**_DATACLASS_SLOT_KW)
class PlanConfirmSession:
    """Plan 收口后的“是否进入开发”确认会话。"""

    token: str
    chat_id: int
    session_key: str
    user_id: Optional[int]
    created_at: float
    parallel_task_id: Optional[str] = None
    parallel_dispatch_context: Optional["ParallelDispatchContext"] = None


ENV_ISSUES: list[str] = []
PRIMARY_WORKDIR: Optional[Path] = None

storage = MemoryStorage()
router = Router()
dp = Dispatcher(storage=storage)
dp.include_router(router)

_bot: Bot | None = None


class TextPasteAggregationMiddleware(BaseMiddleware):
    """全局长文本粘贴聚合中间件。

    目标：
    - Telegram 可能会把超长文本拆成多条消息；这里在入站最前置聚合为一次逻辑输入；
    - 聚合完成后再交由原有 handler / FSM 流程处理，避免“第一段被当成完整输入”导致流程中断。
    """

    async def __call__(self, handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]], event: Any, data: Dict[str, Any]) -> Any:
        # 任意 Telegram Message 入站都触发一次补偿轮询（同 chat 覆盖旧任务）。
        if isinstance(event, Message) and not _is_text_paste_synthetic_message(event):
            await _schedule_message_recovery_poll(event, source="telegram_message")
        if not ENABLE_TEXT_PASTE_AGGREGATION:
            return await handler(event, data)
        if not isinstance(event, Message):
            return await handler(event, data)
        if not event.text:
            return await handler(event, data)
        if _is_text_paste_synthetic_message(event):
            return await handler(event, data)
        if await _maybe_enqueue_text_paste_message(event, event.text):
            # 命中聚合：吞掉当前分片，等待窗口结束后再注入合并后的“合成消息”。
            return None
        return await handler(event, data)


# 注册全局中间件：覆盖所有文本消息（含 FSM 流程中的文本输入）。
router.message.middleware(TextPasteAggregationMiddleware())


def _mask_proxy(url: str) -> str:
    """在 stderr 打印代理信息时隐藏凭据"""
    if "@" not in url:
        return url
    parsed = urlparse(url)
    host = parsed.hostname or "***"
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://***:***@{host}{port}"


def _detect_proxy() -> tuple[Optional[str], Optional[BasicAuth], Optional[str]]:
    """优先使用 TELEGRAM_PROXY，否则回落到常见环境变量"""
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

    worker_log.info("使用代理(%s): %s", source, _mask_proxy(proxy_raw))
    return proxy_raw, auth, source

# 统一以 IPv4 访问 Telegram，避免部分网络环境下 IPv6 连接被丢弃
def build_bot() -> Bot:
    """按照网络环境与代理配置创建 aiogram Bot。"""

    proxy_url, proxy_auth, _ = _detect_proxy()
    session_kwargs = {
        "proxy": proxy_url,
        "timeout": 60,
        "limit": 100,
    }
    if proxy_auth is not None:
        session_kwargs["proxy_auth"] = proxy_auth

    session = AiohttpSession(**session_kwargs)
    # 内部 `_connector_init` 控制 TCPConnector 创建参数，此处强制 IPv4
    session._connector_init.update({  # type: ignore[attr-defined]
        "family": socket.AF_INET,
        "ttl_dns_cache": 60,
    })
    return Bot(token=BOT_TOKEN, session=session)

def current_bot() -> Bot:
    """返回懒加载的全局 Bot 实例。"""

    global _bot
    if _bot is None:
        _bot = build_bot()
    return _bot

# --- 工具函数 ---
async def _send_with_retry(coro_factory, *, attempts: int = SEND_RETRY_ATTEMPTS) -> None:
    """对 Telegram 调用执行有限次重试。"""

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
    """转义 MarkdownV2 特殊字符，保护代码块内容。

    注意：
    - 使用分段处理，保护代码块（```...``` 和 `...`）
    - Text().as_markdown() 会转义所有 MarkdownV2 特殊字符
    - 只移除纯英文单词之间的连字符转义（如 "pre-release"）
    - 保留数字、时间戳等其他情况的连字符转义（如 "2025-10-23"）
    - 代码块内容不被转义，保持原样
    """

    def _escape_segment(segment: str) -> str:
        """转义单个文本段落（非代码块）"""
        escaped = Text(segment).as_markdown()
        # 只移除纯英文字母之间的连字符转义
        escaped = re.sub(r"(?<=[a-zA-Z])\\-(?=[a-zA-Z])", "-", escaped)
        # 移除斜杠的转义
        escaped = escaped.replace("\\/", "/")
        return escaped

    # 分段处理：代码块保持原样，普通文本转义
    pieces: list[str] = []
    last_index = 0

    for match in CODE_SEGMENT_RE.finditer(text):
        # 处理代码块之前的普通文本
        normal_part = text[last_index:match.start()]
        if normal_part:
            pieces.append(_escape_segment(normal_part))

        # 代码块保持原样，不转义
        pieces.append(match.group(0))
        last_index = match.end()

    # 处理最后一段普通文本
    if last_index < len(text):
        remaining = text[last_index:]
        pieces.append(_escape_segment(remaining))

    return "".join(pieces) if pieces else _escape_segment(text)


LEGACY_DOUBLE_BOLD = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
LEGACY_DOUBLE_UNDERLINE = re.compile(r"__(.+?)__", re.DOTALL)
CODE_SEGMENT_RE = re.compile(r"(```.*?```|`[^`]*`)", re.DOTALL)
# Markdown 标题模式（# - ####）
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


_LEGACY_FENCE_LINE_RE = re.compile(r"^(\s*)```.*$")
_LEGACY_STAR_BULLET_RE = re.compile(r"^(\s*)\*\s+")
_TASK_CODE_TOKEN_RE = re.compile(r"(?<![\w`])/?TASK_\d{4,}(?![\w`])", re.IGNORECASE)
_TASK_SUMMARY_REQUEST_TOKEN_RE = re.compile(
    r"(?<![\w`])/?task_summary_request_TASK_\d{4,}(?![\w`])",
    re.IGNORECASE,
)


def _count_unescaped_char(text: str, target: str) -> int:
    """统计未被反斜杠转义的字符数量。"""

    if not text:
        return 0
    count = 0
    idx = 0
    length = len(text)
    while idx < length:
        ch = text[idx]
        if ch == "\\":
            idx += 2
            continue
        if ch == target:
            count += 1
        idx += 1
    return count


def _escape_last_unescaped_char(text: str, target: str) -> str:
    """若未转义的 target 数量为奇数，则转义最后一个 target，避免 Telegram Markdown 解析失败。"""

    if not text:
        return text
    if _count_unescaped_char(text, target) % 2 == 0:
        return text

    idx = len(text) - 1
    while idx >= 0:
        if text[idx] != target:
            idx -= 1
            continue

        # 统计紧邻的反斜杠数量，奇数表示该字符已被转义。
        slash_count = 0
        j = idx - 1
        while j >= 0 and text[j] == "\\":
            slash_count += 1
            j -= 1
        if slash_count % 2 == 0:
            return f"{text[:idx]}\\{target}{text[idx + 1:]}"
        idx -= 1
    return text


def _escape_inline_triple_backticks(line: str) -> str:
    """将行内的 ``` 转义为 \\`\\`\\`，避免被 Telegram 误判为代码块起始。"""

    if "```" not in line:
        return line
    return line.replace("```", r"\`\`\`")


def _escape_token_underscores(token: str) -> str:
    """仅转义 token 内未转义的下划线，避免触发 Telegram Markdown 斜体解析。"""

    if "_" not in token:
        return token
    return re.sub(r"(?<!\\)_", r"\\_", token)


def _escape_tokens_for_legacy_markdown(text: str) -> str:
    """转义容易触发 Telegram Markdown 解析失败的 token（常见为带下划线的任务编码/命令）。"""

    def _escape(match: re.Match[str]) -> str:
        return _escape_token_underscores(match.group(0))

    text = _TASK_SUMMARY_REQUEST_TOKEN_RE.sub(_escape, text)
    text = _TASK_CODE_TOKEN_RE.sub(_escape, text)
    return text


def _transform_outside_inline_code(line: str, transform: Callable[[str], str]) -> str:
    """仅对不在 `...` 行内代码块内的片段应用 transform。"""

    if "`" not in line:
        return transform(line)

    parts: list[str] = []
    buffer: list[str] = []
    in_code = False
    idx = 0
    length = len(line)

    while idx < length:
        ch = line[idx]
        if ch == "\\" and idx + 1 < length:
            buffer.append(line[idx : idx + 2])
            idx += 2
            continue
        if ch == "`":
            segment = "".join(buffer)
            parts.append(transform(segment) if not in_code else segment)
            parts.append("`")
            buffer.clear()
            in_code = not in_code
            idx += 1
            continue
        buffer.append(ch)
        idx += 1

    segment = "".join(buffer)
    parts.append(transform(segment) if not in_code else segment)
    return "".join(parts)


def _sanitize_telegram_markdown_legacy(text: str) -> str:
    """尽量修正 Telegram Markdown(legacy) 易失败的输出，降低降级为纯文本的概率。

    典型失败样例：
    - 文本中出现 “半截代码块 ```” 但未闭合，导致 Telegram 报 can't parse entities。
    - 任务编码/命令如 /TASK_0027 含下划线，可能触发斜体实体解析失败。
    """

    if not text:
        return text

    lines = normalize_newlines(text).splitlines()
    sanitized_lines: list[str] = []
    in_fence = False

    for raw_line in lines:
        line = raw_line
        stripped = line.lstrip()
        is_fence = stripped.startswith("```")

        if is_fence:
            # Telegram Markdown(legacy) 对 ``` 后跟语言标记的兼容性不稳定，统一去掉语言部分。
            match = _LEGACY_FENCE_LINE_RE.match(line)
            if match:
                indent = match.group(1) or ""
                sanitized_lines.append(f"{indent}```")
            else:
                sanitized_lines.append("```")
            in_fence = not in_fence
            continue

        if in_fence:
            sanitized_lines.append(line)
            continue

        # 兼容模型偶尔使用 * item 作为列表符号，避免误触发加粗实体。
        line = _LEGACY_STAR_BULLET_RE.sub(r"\1- ", line)

        # 将行内 ``` 视为普通文本并转义，避免被解析为代码块（常见于“举例说明”）。
        line = _escape_inline_triple_backticks(line)

        # 若存在未闭合的行内代码标记，转义最后一个反引号，避免 can't parse entities。
        line = _escape_last_unescaped_char(line, "`")

        def _fix_plain_segment(segment: str) -> str:
            fixed = _escape_tokens_for_legacy_markdown(segment)
            fixed = _escape_last_unescaped_char(fixed, "*")
            fixed = _escape_last_unescaped_char(fixed, "_")
            return fixed

        # 仅在非 `...` 代码片段中修复 token / 未配对标记，避免污染行内代码内容。
        line = _transform_outside_inline_code(line, _fix_plain_segment)

        sanitized_lines.append(line)

    # 若代码块未闭合，追加闭合标记，避免后续整条消息解析失败。
    if in_fence:
        sanitized_lines.append("```")

    return "\n".join(sanitized_lines)


# MarkdownV2 转义字符模式（用于检测已转义文本）
_ESCAPED_MARKDOWN_PATTERN = re.compile(
    r"\\[_*\[\]()~`>#+=|{}.!:-]"  # 添加了冒号
)

# 已转义的代码块模式（转义的反引号）
_ESCAPED_CODE_BLOCK_PATTERN = re.compile(
    r"(\\\`\\\`\\\`.*?\\\`\\\`\\\`|\\\`[^\\\`]*?\\\`)",
    re.DOTALL
)

def _is_already_escaped(text: str) -> bool:
    """检测文本是否已经包含 MarkdownV2 转义字符。

    通过统计转义字符的出现频率来判断：
    - 如果转义字符数量 >= 文本长度的 3%，认为已被转义（降低阈值）
    - 或者如果有 2 个以上的连续转义模式（如 \*\*），也认为已被转义
    - 或者包含已转义的代码块标记
    """
    if not text:
        return False

    # 检查是否有已转义的代码块标记
    if _ESCAPED_CODE_BLOCK_PATTERN.search(text):
        return True

    matches = _ESCAPED_MARKDOWN_PATTERN.findall(text)
    if not matches:
        return False

    # 对于短文本，放宽检测条件
    if len(text) < 20:
        # 短文本出现任意转义字符即可认定为已转义，防止重复转义
        if len(matches) >= 1:
            return True
    else:
        # 检查转义字符密度（降低到 3%）
        escape_count = len(matches)
        text_length = len(text)
        density = escape_count / text_length

        if density >= 0.03:  # 3% 以上认为已被转义
            return True

    # 检查是否有连续转义模式（如 \#\#\# 或 \*\*）
    consecutive_pattern = re.compile(r"(?:\\[_*\[\]()~`>#+=|{}.!:-]){2,}")
    if consecutive_pattern.search(text):
        return True

    return False


def _unescape_markdown_v2(text: str) -> str:
    """反转义 MarkdownV2 特殊字符。

    将 \*, \_, \#, \[, \], \: 等转义字符还原为原始字符。
    """
    # 移除所有 MarkdownV2 转义的反斜杠
    # 匹配模式：反斜杠 + 特殊字符（添加了冒号）
    return re.sub(r"\\([_*\[\]()~`>#+=|{}.!:-])", r"\1", text)


def _force_unescape_markdown(text: str) -> str:
    """强制移除 MarkdownV2 转义，同时保护代码块语法不被破坏。"""
    if not text:
        return text

    processed = text
    code_blocks: list[str] = []

    def _preserve_code_block(match: re.Match[str]) -> str:
        """临时替换代码块，防止内部字符被错误反转义。"""
        block = match.group(0)
        if block.startswith(r"\`\`\`"):
            # 多行代码块保留内容，只修复边界反引号
            unescaped_block = block.replace(r"\`", "`", 6)
        else:
            # 单行代码块同理处理首尾反引号
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
    """智能检测并清理预转义文本，必要时触发强制反转义。"""
    if not text:
        return text
    if not _is_already_escaped(text):
        return text
    return _force_unescape_markdown(text)


def _clean_user_text(text: Optional[str]) -> str:
    """清理用户输入中可能存在的预转义反斜杠，保持后续渲染一致。"""
    if text is None:
        return ""
    value = str(text)
    if not value:
        return ""
    return _unescape_if_already_escaped(value)


def _prepare_model_payload(text: str) -> str:
    if _IS_MARKDOWN_V2:
        cleaned = _unescape_if_already_escaped(text)
        return _escape_markdown_v2(cleaned)
    if _IS_MARKDOWN:
        normalized = _normalize_legacy_markdown(text)
        return _sanitize_telegram_markdown_legacy(normalized)
    return text


def _prepare_model_payload_variants(text: str) -> tuple[str, Optional[str]]:
    """返回首选与备用内容，默认为单一格式。"""

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
                    "Markdown 优化回退为严格转义版本",
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
            # 保留代码块标记不转义（它们本身就是 Markdown 语法）
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
                "Markdown 解析失败，已对文本转义后重试",
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
            "Markdown 解析仍失败，将以纯文本发送",
            extra={"length": len(text)},
        )
        await raw_sender(text)
        return text


async def _notify_send_failure_message(chat_id: int) -> None:
    """向用户提示消息发送存在网络问题，避免重复刷屏。"""

    now = time.monotonic()
    last_notice = CHAT_FAILURE_NOTICES.get(chat_id)
    if last_notice is not None and (now - last_notice) < SEND_FAILURE_NOTICE_COOLDOWN:
        return

    notice = "发送结果时网络出现异常，系统正在尝试重试，请稍后再试。"
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
    """为模型输出添加完成提示，避免重复拼接。"""

    if text.startswith(MODEL_COMPLETION_PREFIX):
        return text
    if text:
        return f"{MODEL_COMPLETION_PREFIX}\n\n{text}"
    return MODEL_COMPLETION_PREFIX

# pylint: disable=too-many-locals
async def reply_large_text(
    chat_id: int,
    text: str,
    *,
    parse_mode: Optional[str] = None,
    preformatted: bool = False,
    reply_markup: Optional[Any] = None,
    attachment_reply_markup: Optional[Any] = None,
) -> str:
    """向指定会话发送可能较长的文本，必要时退化为附件。

    :param chat_id: Telegram 会话标识。
    :param text: 待发送内容。
    :param parse_mode: 指定消息的 parse_mode，未提供时沿用全局默认值。
    :param preformatted: 标记文本已按 parse_mode 处理，跳过内部转义。
    :param reply_markup: 短消息模式下，附带的键盘（如 InlineKeyboard）。
    :param attachment_reply_markup: 长消息降级为文件时，附带在“文件消息”上的键盘（摘要消息不挂键盘）。
    """
    bot = current_bot()
    parse_mode_value = parse_mode if parse_mode is not None else _parse_mode_value()
    if preformatted:
        prepared = text
        fallback_payload = None
    else:
        prepared, fallback_payload = _prepare_model_payload_variants(text)

    async def _send_formatted_message(payload: str) -> None:
        kwargs: dict[str, Any] = {}
        if reply_markup is not None:
            kwargs["reply_markup"] = reply_markup
        await bot.send_message(chat_id=chat_id, text=payload, parse_mode=parse_mode_value, **kwargs)

    async def _send_formatted_message_without_markup(payload: str) -> None:
        await bot.send_message(chat_id=chat_id, text=payload, parse_mode=parse_mode_value)

    async def _send_raw_message(payload: str) -> None:
        kwargs: dict[str, Any] = {}
        if reply_markup is not None:
            kwargs["reply_markup"] = reply_markup
        await bot.send_message(chat_id=chat_id, text=payload, parse_mode=None, **kwargs)

    async def _send_raw_message_without_markup(payload: str) -> None:
        await bot.send_message(chat_id=chat_id, text=payload, parse_mode=None)

    if len(prepared) <= TELEGRAM_MESSAGE_LIMIT:
        delivered = await _send_with_markdown_guard(
            prepared,
            _send_formatted_message,
            raw_sender=_send_raw_message,
            fallback_payload=fallback_payload,
        )

        worker_log.info(
            "完成单条消息发送",
            extra={
                "chat": chat_id,
                "mode": "single",
                "length": str(len(delivered)),
            },
        )
        return delivered

    attachment_name = f"model-response-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    summary_text = f"{MODEL_COMPLETION_PREFIX}\n\n内容较长，已生成附件 {attachment_name}，请下载查看全文。"

    document = BufferedInputFile(text.encode("utf-8"), filename=attachment_name)

    async def _send_document() -> None:
        kwargs: dict[str, Any] = {}
        attachment_markup = attachment_reply_markup if attachment_reply_markup is not None else reply_markup
        if attachment_markup is not None:
            kwargs["reply_markup"] = attachment_markup
        await bot.send_document(
            chat_id=chat_id,
            document=document,
            caption=summary_text,
            parse_mode=None,
            **kwargs,
        )

    await _send_with_retry(_send_document)

    worker_log.info(
        "长文本已转附件发送",
        extra={
            "chat": chat_id,
            "mode": "attachment",
            "length": str(len(prepared)),
            "attachment_name": attachment_name,
        },
    )

    return summary_text


async def _send_model_push_preview(
    chat_id: int,
    preview_block: str,
    *,
    reply_to: Optional[Message],
    parse_mode: Optional[str],
    reply_markup: Optional[Any],
) -> None:
    """发送推送预览，超长时自动转附件并提示。"""

    text = f"已推送到模型：\n{preview_block}"
    try:
        await _reply_to_chat(
            chat_id,
            text,
            reply_to=reply_to,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )
        return
    except TelegramBadRequest as exc:
        reason = _extract_bad_request_message(exc).lower()
        if "message is too long" not in reason:
            raise
        worker_log.warning(
            "推送预览超出 Telegram 限制，已降级为附件发送",
            extra={"chat": chat_id, "length": str(len(text))},
        )

    await reply_large_text(chat_id, text, parse_mode=parse_mode, preformatted=True)
    if reply_markup:
        await _reply_to_chat(
            chat_id,
            "预览内容较长，已以附件形式发送，请查收。",
            reply_to=reply_to,
            parse_mode=None,
            reply_markup=reply_markup,
        )

def run_subprocess_capture(cmd: str, input_text: str = "") -> Tuple[int, str]:
    # 同步执行 CLI，stdin 喂 prompt，捕获 stdout+stderr
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


def _tmux_submit_line(
    session: str,
    line: str,
    *,
    submit_key: str,
    double_submit: bool,
):
    """向 tmux 注入文本，并使用指定提交键发送。"""

    tmux = tmux_bin()
    subprocess.check_call(_tmux_cmd(tmux, "has-session", "-t", session))
    _tmux_prepare_immediate_submit(tmux, session)
    _tmux_send_text_chunks(tmux, session, line)
    # 首次发送前，给不同模型一个轻量稳定窗口，避免输入事件丢失。
    time.sleep(0.2 if _is_claudecode_model() else 0.05)
    subprocess.check_call(_tmux_cmd(tmux, "send-keys", "-t", session, submit_key))
    if not double_submit or not TMUX_SEND_LINE_DOUBLE_ENTER_ENABLED or submit_key != "C-m":
        return

    # 统一兜底：固定延迟后补发 1 次 Enter，覆盖“输入框有文案但未提交”的黑盒场景。
    if TMUX_SEND_LINE_DOUBLE_ENTER_DELAY_SECONDS > 0:
        time.sleep(TMUX_SEND_LINE_DOUBLE_ENTER_DELAY_SECONDS)
    try:
        subprocess.check_call(_tmux_cmd(tmux, "send-keys", "-t", session, "C-m"))
    except subprocess.CalledProcessError as exc:
        # 兜底补发失败不应覆盖首发结果，只记录告警方便后续定位。
        worker_log.warning(
            "tmux 延迟补发 Enter 失败，已保留首发结果：%s",
            exc,
            extra={
                "tmux_session": session,
                "double_enter_enabled": str(TMUX_SEND_LINE_DOUBLE_ENTER_ENABLED),
                "double_enter_delay_seconds": str(TMUX_SEND_LINE_DOUBLE_ENTER_DELAY_SECONDS),
                "double_enter_fallback_sent": "false",
            },
        )
    else:
        worker_log.debug(
            "tmux 延迟补发 Enter 成功",
            extra={
                "tmux_session": session,
                "double_enter_enabled": str(TMUX_SEND_LINE_DOUBLE_ENTER_ENABLED),
                "double_enter_delay_seconds": str(TMUX_SEND_LINE_DOUBLE_ENTER_DELAY_SECONDS),
                "double_enter_fallback_sent": "true",
            },
        )


def _tmux_prepare_immediate_submit(tmux: str, session: str) -> None:
    """立即发送前的既有预处理：退出菜单态并清理 tmux copy-mode。"""

    # 发送一次 ESC，退出 Codex 可能的菜单或输入模式
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


def _tmux_send_text_chunks(tmux: str, session: str, line: str) -> None:
    """向 tmux 输入文本正文，保留多行输入的既有拆分策略。"""

    chunks = line.split("\n")
    for idx, chunk in enumerate(chunks):
        if chunk:
            subprocess.check_call(_tmux_cmd(tmux, "send-keys", "-t", session, "--", chunk))
        if idx < len(chunks) - 1:
            subprocess.check_call(_tmux_cmd(tmux, "send-keys", "-t", session, "C-j"))
            time.sleep(0.05)


def tmux_send_line(session: str, line: str):
    """立即发送：使用 Enter 提交当前 prompt。"""

    _tmux_submit_line(session, line, submit_key="C-m", double_submit=True)


def tmux_queue_line(session: str, line: str):
    """排队发送：尽量贴近用户手动输入后按 Tab 的行为，仅影响 queued 链路。"""

    tmux = tmux_bin()
    subprocess.check_call(_tmux_cmd(tmux, "has-session", "-t", session))
    # 排队发送不复用“立即发送”的前置 Escape，避免干扰 Codex 当前会话状态。
    _tmux_send_text_chunks(tmux, session, line)
    time.sleep(0.2 if _is_claudecode_model() else 0.05)
    subprocess.check_call(_tmux_cmd(tmux, "send-keys", "-t", session, "Tab"))


_TMUX_SHELL_COMMANDS = {"sh", "bash", "zsh", "fish", "dash", "ksh", "csh", "tcsh"}


def _get_tmux_pane_current_command(session: str) -> Optional[str]:
    """读取 tmux pane 当前前台进程名，用于判断模型 CLI 是否真正启动。"""

    tmux = tmux_bin()
    try:
        current = subprocess.check_output(
            _tmux_cmd(tmux, "display-message", "-p", "-t", session, "#{pane_current_command}"),
            text=True,
        ).strip()
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return current or None


def _is_tmux_shell_command(command: Optional[str]) -> bool:
    """判断当前 tmux 前台进程是否仍是 shell。"""

    normalized = Path((command or "").strip()).name.lower()
    return normalized in _TMUX_SHELL_COMMANDS


def tmux_send_key(session: str, key: str) -> None:
    """向 tmux 会话发送单个按键（如 BTab / Escape）。"""

    tmux = tmux_bin()
    subprocess.check_call(_tmux_cmd(tmux, "has-session", "-t", session))
    subprocess.check_call(_tmux_cmd(tmux, "send-keys", "-t", session, key))


def _capture_tmux_output_for_session(session: str, line_count: int, timeout: float) -> str:
    """抓取指定 tmux 会话尾部输出，供并行 CLI 就绪判断复用。"""

    tmux = tmux_bin()
    try:
        return subprocess.check_output(
            _tmux_cmd(
                tmux,
                "capture-pane",
                "-p",
                "-t",
                session,
                "-S",
                f"-{line_count}",
            ),
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
        return ""


def _is_tmux_session_ready_for_plan_switch(raw_output: str) -> bool:
    """判断新启动的 Codex tmux 会话是否已进入可接收 /plan 的稳定界面。"""

    text = strip_ansi(normalize_newlines(raw_output or ""))
    return any(marker in text for marker in PARALLEL_PLAN_READY_MARKERS)


async def _wait_tmux_session_ready_for_plan_switch(tmux_session: Optional[str]) -> bool:
    """并行 CLI 首次推送前，等待 tmux 内的 Codex UI ready。"""

    if not tmux_session or PARALLEL_PLAN_READY_TIMEOUT_SECONDS <= 0 or not _is_codex_model():
        return True
    deadline = time.monotonic() + PARALLEL_PLAN_READY_TIMEOUT_SECONDS
    probe_timeout = max(WORKER_PLAN_MODE_PROBE_TIMEOUT_SECONDS, 0.1)
    while time.monotonic() < deadline:
        raw_output = await asyncio.to_thread(
            _capture_tmux_output_for_session,
            tmux_session,
            PARALLEL_PLAN_READY_PROBE_LINES,
            probe_timeout,
        )
        if _is_tmux_session_ready_for_plan_switch(raw_output):
            return True
        await asyncio.sleep(PARALLEL_PLAN_READY_POLL_INTERVAL_SECONDS)
    return False


def _capture_tmux_recent_lines(line_count: int, tmux_session: Optional[str] = None) -> str:
    """截取指定 tmux 会话尾部指定行数的原始文本。"""

    tmux = tmux_bin()
    normalized = max(1, min(line_count, TMUX_SNAPSHOT_MAX_LINES))
    start_arg = f"-{normalized}"
    timeout: Optional[float] = None
    if TMUX_SNAPSHOT_TIMEOUT_SECONDS > 0:
        timeout = max(TMUX_SNAPSHOT_TIMEOUT_SECONDS, 0.1)
    return subprocess.check_output(
        _tmux_cmd(
            tmux,
            "capture-pane",
            "-p",
            "-t",
            tmux_session or TMUX_SESSION,
            "-S",
            start_arg,
        ),
        text=True,
        timeout=timeout,
    )


def _extract_terminal_collaboration_mode(raw_output: str) -> Optional[str]:
    """从 tmux 截图文本中提取底部协作模式（plan/default/...）。"""

    text = normalize_newlines(raw_output or "")
    text = strip_ansi(text)
    for raw_line in reversed(text.splitlines()):
        line = (raw_line or "").strip()
        if not line:
            continue
        match = TERMINAL_COLLABORATION_MODE_RE.search(line)
        if match:
            mode = (match.group(1) or "").strip().lower()
            # 仅接受已知模式，避免把普通句子（如 "no mode marker"）误判为模式值。
            if mode in {"plan", "default"}:
                return mode
    return None


def _probe_terminal_collaboration_mode(tmux_session: Optional[str] = None) -> Literal["plan", "non_plan", "unknown"]:
    """探测指定终端协作模式：plan / 非 plan / unknown。"""

    try:
        raw_output = _capture_tmux_recent_lines(PLAN_EXECUTION_MODE_PROBE_LINES, tmux_session=tmux_session)
    except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
        return "unknown"
    mode = _extract_terminal_collaboration_mode(raw_output)
    if not mode:
        return "unknown"
    if mode == "plan":
        return "plan"
    return "non_plan"


async def _probe_plan_execution_terminal_mode(tmux_session: Optional[str] = None) -> Literal["plan", "non_plan", "unknown"]:
    """异步包装终端模式探测，避免阻塞事件循环。"""

    return await asyncio.to_thread(_probe_terminal_collaboration_mode, tmux_session)


def _should_force_exit_plan_ui(*, force_exit_plan_ui: bool, prompt: str) -> bool:
    """判断本次发送前是否需要尝试切出 Plan UI 模式。"""

    if not force_exit_plan_ui:
        return False
    if not _is_codex_model():
        return False
    if (prompt or "").lstrip().startswith("/"):
        return False
    return True


def _build_plan_develop_retry_exit_plan_key_sequence() -> Tuple[str, ...]:
    """构造“重试进入开发”按钮链路的退出按键序列。"""

    key_sequence: list[str] = []
    if PLAN_DEVELOP_RETRY_EXIT_PLAN_ESC_FIRST:
        key_sequence.append("Escape")
    if PLAN_DEVELOP_RETRY_EXIT_PLAN_KEYS:
        key_sequence.extend(PLAN_DEVELOP_RETRY_EXIT_PLAN_KEYS)
    if not key_sequence:
        key_sequence.append(PLAN_EXECUTION_EXIT_PLAN_KEY)
    return tuple(key_sequence)


async def _maybe_force_exit_plan_ui(
    *,
    chat_id: int,
    prompt: str,
    force_exit_plan_ui: bool,
    tmux_session: Optional[str] = None,
    force_exit_plan_ui_key_sequence: Optional[Sequence[str]] = None,
    force_exit_plan_ui_max_rounds: Optional[int] = None,
) -> Literal["plan", "non_plan", "unknown", "skipped"]:
    """在发送 develop 提示词前，尝试通过 Shift+Tab 退出 Plan UI 锁定。"""

    if not _should_force_exit_plan_ui(force_exit_plan_ui=force_exit_plan_ui, prompt=prompt):
        return "skipped"

    target_tmux_session = tmux_session or TMUX_SESSION
    before_mode = await _probe_plan_execution_terminal_mode(target_tmux_session)
    if before_mode == "non_plan":
        worker_log.info(
            "Plan 切换预检：当前已非 Plan 模式，跳过 Shift+Tab",
            extra={"chat": chat_id, "before_mode": before_mode, "tmux_session": target_tmux_session},
        )
        return before_mode

    # 构造“退出 Plan”按键序列：
    # 1) 优先使用调用方显式传入序列（便于链路级策略覆盖）
    # 2) 否则走全局默认：可选 Escape + 一组 Shift+Tab
    key_sequence: list[str] = []
    if force_exit_plan_ui_key_sequence is not None:
        key_sequence = [str(key).strip() for key in force_exit_plan_ui_key_sequence if str(key).strip()]
    else:
        if PLAN_EXECUTION_EXIT_PLAN_ESC_FIRST:
            key_sequence.append("Escape")
        if PLAN_EXECUTION_EXIT_PLAN_RETRY_KEYS:
            key_sequence.extend(PLAN_EXECUTION_EXIT_PLAN_RETRY_KEYS)
        else:
            key_sequence.append(PLAN_EXECUTION_EXIT_PLAN_KEY)
    if not key_sequence:
        key_sequence.append(PLAN_EXECUTION_EXIT_PLAN_KEY)

    max_rounds = PLAN_EXECUTION_EXIT_PLAN_MAX_ROUNDS
    if force_exit_plan_ui_max_rounds is not None:
        try:
            max_rounds = max(int(force_exit_plan_ui_max_rounds), 1)
        except (TypeError, ValueError):
            max_rounds = 1

    current_mode = before_mode
    for round_index in range(1, max_rounds + 1):
        try:
            for key_index, key in enumerate(key_sequence):
                tmux_send_key(target_tmux_session, key)
                if (
                    PLAN_EXECUTION_EXIT_PLAN_RETRY_GAP_SECONDS > 0
                    and key_index < len(key_sequence) - 1
                ):
                    await asyncio.sleep(PLAN_EXECUTION_EXIT_PLAN_RETRY_GAP_SECONDS)
        except subprocess.CalledProcessError as exc:
            worker_log.warning(
                "发送 Plan 退出按键失败，继续发送提示词：%s",
                exc,
                extra={
                    "chat": chat_id,
                    "before_mode": before_mode,
                    "round": str(round_index),
                    "switch_key_sequence": ",".join(key_sequence),
                    "tmux_session": target_tmux_session,
                },
            )
            return current_mode

        if PLAN_EXECUTION_EXIT_PLAN_DELAY_SECONDS > 0:
            await asyncio.sleep(PLAN_EXECUTION_EXIT_PLAN_DELAY_SECONDS)

        current_mode = await _probe_plan_execution_terminal_mode(target_tmux_session)
        worker_log.info(
            "Plan 切换预命令已发送",
            extra={
                "chat": chat_id,
                "before_mode": before_mode,
                "after_mode": current_mode,
                "round": str(round_index),
                "max_rounds": str(max_rounds),
                "switch_key_sequence": ",".join(key_sequence),
                "round_gap": str(PLAN_EXECUTION_EXIT_PLAN_RETRY_GAP_SECONDS),
                "delay": str(PLAN_EXECUTION_EXIT_PLAN_DELAY_SECONDS),
                "tmux_session": target_tmux_session,
            },
        )
        if current_mode == "non_plan":
            return current_mode
        # 单轮后仍是 Plan / unknown，继续下一轮尝试。

    return current_mode


async def _resume_session_watcher_if_needed(chat_id: int, *, reason: str) -> None:
    """在不打断用户会话的前提下尝试恢复 watcher。

    背景：
    - 用户点击“终端实况”通常发生在模型仍在输出时；
    - 若此时 watcher 因异常提前结束，后续推送会看起来“断了”；
    - 用户再次发消息会触发 `_dispatch_prompt_to_model` 重建 watcher。

    这里做一次轻量自愈，尽量避免用户必须再发一条消息才能恢复推送。

    约束：
    - 仅当 watcher 已存在但已结束时才尝试恢复，避免无会话时误启动监听任务；
    - 不主动发送任何消息，仅重建监听任务。
    """

    watcher = CHAT_WATCHERS.get(chat_id)
    if watcher is None:
        return
    if not watcher.done():
        return

    # watcher 已结束，准备清理并尝试恢复
    CHAT_WATCHERS.pop(chat_id, None)

    session_key = CHAT_SESSION_MAP.get(chat_id)
    if not session_key:
        worker_log.debug(
            "[session-map] chat=%s watcher 已退出但未绑定会话，跳过恢复（reason=%s）",
            chat_id,
            reason,
        )
        return

    session_path = resolve_path(session_key)
    if not session_path.exists():
        worker_log.warning(
            "[session-map] chat=%s watcher 已退出但会话文件不存在，跳过恢复（reason=%s）",
            chat_id,
            reason,
            extra=_session_extra(key=session_key),
        )
        return

    if session_key not in SESSION_OFFSETS:
        initial_offset = _initial_session_offset(session_path)
        SESSION_OFFSETS[session_key] = initial_offset
        worker_log.info(
            "[session-map] init offset for %s -> %s",
            session_key,
            SESSION_OFFSETS[session_key],
            extra=_session_extra(key=session_key),
        )

    # 若该 session 已经发送过响应，则恢复时直接进入延迟轮询阶段，避免重复追加“完成前缀”。
    start_in_long_poll = session_key in (CHAT_LAST_MESSAGE.get(chat_id) or {})

    await _interrupt_long_poll(chat_id)
    CHAT_WATCHERS[chat_id] = asyncio.create_task(
        _watch_and_notify(
            chat_id,
            session_path,
            max_wait=WATCH_MAX_WAIT,
            interval=WATCH_INTERVAL,
            start_in_long_poll=start_in_long_poll,
        )
    )
    worker_log.info(
        "[session-map] chat=%s watcher resumed (reason=%s)",
        chat_id,
        reason,
        extra=_session_extra(path=session_path),
    )


def resolve_path(path: Path | str) -> Path:
    if isinstance(path, Path):
        return path.expanduser()
    return Path(os.path.expanduser(os.path.expandvars(path))).expanduser()


def _codex_project_table_header(path: Path) -> str:
    """返回 Codex config.toml 中 projects.<path> 的标准表头。"""

    return f"[projects.{json.dumps(str(path))}]"


def _find_codex_project_table_bounds(text: str, path: Path) -> tuple[int, int] | None:
    """查找 Codex projects.<path> 表的文本边界。"""

    pattern = re.compile(
        rf'^\[projects\.(["\']){re.escape(str(path))}\1\]\s*$',
        re.MULTILINE,
    )
    match = pattern.search(text)
    if match is None:
        return None
    start = match.start()
    next_header = re.compile(r"^\[", re.MULTILINE).search(text, match.end())
    end = next_header.start() if next_header is not None else len(text)
    return start, end


def _read_codex_project_trust_level(config_path: Path, project_path: Path) -> Optional[str]:
    """读取 Codex config.toml 中指定 projects.<path> 的 trust_level。"""

    if not config_path.exists():
        return None
    try:
        raw = config_path.read_text(encoding="utf-8")
        data = tomllib.loads(raw) if raw.strip() else {}
    except (OSError, tomllib.TOMLDecodeError):
        return None
    projects = data.get("projects")
    if not isinstance(projects, dict):
        return None
    project = projects.get(str(project_path))
    if not isinstance(project, dict):
        return None
    trust_level = project.get("trust_level")
    return trust_level if isinstance(trust_level, str) else None


def _upsert_codex_project_trust_level_text(text: str, project_path: Path, trust_level: str) -> str:
    """在 Codex config.toml 文本中新增或更新指定 projects.<path>.trust_level。"""

    bounds = _find_codex_project_table_bounds(text, project_path)
    line = f'trust_level = "{trust_level}"'
    if bounds is None:
        base = text.rstrip()
        suffix = "" if not base else "\n\n"
        return f"{base}{suffix}{_codex_project_table_header(project_path)}\n{line}\n"
    start, end = bounds
    section = text[start:end]
    if re.search(r"^\s*trust_level\s*=", section, re.MULTILINE):
        section = re.sub(
            r'^\s*trust_level\s*=\s*["\'][^"\']*["\']\s*$',
            line,
            section,
            count=1,
            flags=re.MULTILINE,
        )
    else:
        section = section.rstrip("\n") + f"\n{line}\n"
    return text[:start] + section + text[end:]


def _remove_codex_project_table_text(text: str, project_path: Path) -> str:
    """从 Codex config.toml 文本中移除指定 projects.<path> 表。"""

    bounds = _find_codex_project_table_bounds(text, project_path)
    if bounds is None:
        return text
    start, end = bounds
    cleaned = (text[:start] + text[end:]).strip("\n")
    return f"{cleaned}\n" if cleaned else ""


def _write_codex_config_text(config_path: Path, text: str) -> None:
    """原子写回 Codex config.toml。"""

    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = config_path.with_suffix(config_path.suffix + ".tmp")
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(config_path)


async def _ensure_codex_trusted_project_path(
    project_path: Path,
    *,
    scope: str,
    owner_key: str,
) -> None:
    """确保给定目录已写入 Codex trusted 配置，并登记 vibego 托管状态。"""

    normalized = resolve_path(project_path)
    async with CODEX_CONFIG_LOCK:
        ensure_result = ensure_codex_project_trust(normalized, config_path=CODEX_CONFIG_PATH)
        if ensure_result.changed:
            managed_by_vibego = True
            previous_trust_level = ensure_result.previous_trust_level
        else:
            existing = await PARALLEL_SESSION_STORE.get_trusted_path(str(normalized))
            managed_by_vibego = bool(existing.managed_by_vibego) if existing is not None else False
            previous_trust_level = (
                existing.previous_trust_level
                if existing is not None
                else ensure_result.previous_trust_level
            )
        await PARALLEL_SESSION_STORE.upsert_trusted_path(
            path=str(normalized),
            scope=scope,
            owner_key=owner_key,
            previous_trust_level=previous_trust_level,
            managed_by_vibego=managed_by_vibego,
        )


async def _cleanup_codex_trusted_project_path(
    project_path: Path,
    *,
    scope: str,
    owner_key: str,
) -> None:
    """回收 vibego 自管的 Codex trusted 配置，并恢复旧值或删除表。"""

    normalized = resolve_path(project_path)
    record = await PARALLEL_SESSION_STORE.get_trusted_path(str(normalized))
    if record is None or record.scope != scope or record.owner_key != owner_key:
        return
    async with CODEX_CONFIG_LOCK:
        raw = CODEX_CONFIG_PATH.read_text(encoding="utf-8") if CODEX_CONFIG_PATH.exists() else ""
        if record.managed_by_vibego:
            if record.previous_trust_level:
                raw = _upsert_codex_project_trust_level_text(raw, normalized, record.previous_trust_level)
            else:
                raw = _remove_codex_project_table_text(raw, normalized)
            _write_codex_config_text(CODEX_CONFIG_PATH, raw)
        await PARALLEL_SESSION_STORE.delete_trusted_path(str(normalized))


async def _reconcile_codex_trusted_paths() -> None:
    """清理目录已不存在的并行 trusted 条目，防止 config.toml 膨胀。"""

    records = await PARALLEL_SESSION_STORE.list_trusted_paths(scope=CODEX_TRUST_SCOPE_PARALLEL_WORKSPACE)
    for record in records:
        path = resolve_path(record.path)
        session = await PARALLEL_SESSION_STORE.get_session(record.owner_key)
        if path.exists() and session is not None and session.status not in {"deleted", "closed"}:
            continue
        if path.exists():
            continue
        async with CODEX_CONFIG_LOCK:
            raw = CODEX_CONFIG_PATH.read_text(encoding="utf-8") if CODEX_CONFIG_PATH.exists() else ""
            if record.managed_by_vibego:
                if record.previous_trust_level:
                    raw = _upsert_codex_project_trust_level_text(raw, path, record.previous_trust_level)
                else:
                    raw = _remove_codex_project_table_text(raw, path)
                _write_codex_config_text(CODEX_CONFIG_PATH, raw)
            await PARALLEL_SESSION_STORE.delete_trusted_path(str(path))


async def _ensure_primary_workdir_codex_trust() -> None:
    """在 worker 启动时确保主项目工作目录已被 Codex trusted。"""

    if PRIMARY_WORKDIR is None:
        return
    await _ensure_codex_trusted_project_path(
        PRIMARY_WORKDIR,
        scope=CODEX_TRUST_SCOPE_PROJECT_WORKDIR,
        owner_key=PROJECT_SLUG or PRIMARY_WORKDIR.name,
    )


def _is_gemini_session_file(path: Path) -> bool:
    """判断给定会话文件是否为 Gemini CLI 的 session-*.json。"""

    return path.suffix.lower() == ".json"


def _initial_session_offset(session_path: Path) -> int:
    """为会话文件计算初始化偏移。

    - Codex / ClaudeCode：按文件字节偏移回退一小段，避免漏掉刚写入的 JSONL 行；
    - Gemini：会话文件是完整 JSON（非追加写），用 messages 列表长度作为游标，并回退最近 N 条。
    """

    if _is_gemini_session_file(session_path):
        data = _read_gemini_session_json(session_path)
        messages = (data or {}).get("messages")
        total = len(messages) if isinstance(messages, list) else 0
        backtrack = max(GEMINI_SESSION_INITIAL_BACKTRACK_MESSAGES, 0)
        return max(total - backtrack, 0)

    try:
        size = session_path.stat().st_size
    except FileNotFoundError:
        size = 0
    backtrack = max(SESSION_INITIAL_BACKTRACK_BYTES, 0)
    return max(size - backtrack, 0)


async def _reply_to_chat(
    chat_id: int,
    text: str,
    *,
    reply_to: Optional[Message],
    disable_notification: bool = False,
    parse_mode: Optional[str] = None,
    reply_markup: Optional[Any] = None,
) -> Optional[Message]:
    """向聊天发送消息，优先复用原消息上下文。"""

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


async def _answer_callback_safely(
    callback: CallbackQuery,
    text: Optional[str] = None,
    *,
    show_alert: bool = False,
) -> bool:
    """安全确认 Telegram callback，避免长耗时流程结束后 query 过期打断主流程。"""

    try:
        await callback.answer(text, show_alert=show_alert)
        return True
    except (TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter) as exc:
        worker_log.warning(
            "回调答复失败(忽略)：%s",
            exc,
            extra=_session_extra(),
        )
        return False
    except Exception as exc:  # noqa: BLE001
        worker_log.warning(
            "回调答复失败(忽略)：%s",
            exc,
            extra=_session_extra(),
        )
        return False


async def _send_session_ack(
    chat_id: int,
    session_path: Path,
    *,
    reply_to: Optional[Message],
) -> None:
    model_label = (ACTIVE_MODEL or "模型").strip() or "模型"
    session_id = session_path.stem if session_path else "unknown"
    prompt_message = (
        f"💭 {model_label}思考中，正在持续监听模型响应结果中。\n"
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


def _prepend_enforced_agents_notice(raw_prompt: str) -> str:
    """在推送到 tmux 前追加强制规约提示语。

    约束：
    - 仅对非命令类 prompt 生效（以 / 开头的内部命令不注入，避免破坏语义）
    - 避免重复注入同一条提示语
    """

    text = (raw_prompt or "").strip("\n")
    if not text:
        return raw_prompt
    # Plan 收口确认（Yes）与恢复提示要求严格透传固定提示词，不允许追加强制前缀。
    if text in {PLAN_IMPLEMENT_PROMPT, PLAN_IMPLEMENT_EXEC_PROMPT, PLAN_RECOVERY_DEVELOP_PROMPT}:
        return raw_prompt
    # 约定：内部命令（如 /compact）不应被提示语破坏
    if text.lstrip().startswith("/"):
        return raw_prompt
    notice = ENFORCED_AGENTS_NOTICE.strip()
    if not notice:
        return raw_prompt
    if text.lstrip().startswith(notice):
        return raw_prompt
    return f"{notice}\n\n{raw_prompt}"


def _infer_dispatch_mode_from_prompt(prompt: str) -> Optional[str]:
    """根据提示词前缀推断推送模式（用于兼容旧调用方）。"""

    text = (prompt or "").lstrip()
    if text.startswith(f"进入 {PUSH_MODE_PLAN} 模式"):
        return PUSH_MODE_PLAN
    if text.startswith(f"{PUSH_MODE_YOLO} 模式"):
        return PUSH_MODE_YOLO
    return None


def _resolve_dispatch_mode(*, intended_mode: Optional[str], prompt: str) -> Optional[str]:
    """归一化本次推送模式；若调用方未显式传入则尝试从提示词推断。"""

    normalized = (intended_mode or "").strip().upper()
    if normalized in {PUSH_MODE_PLAN, PUSH_MODE_YOLO}:
        return normalized
    return _infer_dispatch_mode_from_prompt(prompt)


def _normalize_push_send_mode(send_mode: Optional[str]) -> str:
    """归一化发送方式；未知值回退为立即发送。"""

    normalized = (send_mode or "").strip().lower()
    if normalized == PUSH_SEND_MODE_QUEUED:
        return PUSH_SEND_MODE_QUEUED
    return PUSH_SEND_MODE_IMMEDIATE


def _push_send_mode_label(send_mode: Optional[str]) -> str:
    """返回发送方式的人类可读文案。"""

    normalized = _normalize_push_send_mode(send_mode)
    if normalized == PUSH_SEND_MODE_QUEUED:
        return PUSH_SEND_MODE_QUEUED_LABEL
    return PUSH_SEND_MODE_IMMEDIATE_LABEL


def _should_send_plan_switch_command(*, intended_mode: Optional[str], prompt: str, send_mode: Optional[str] = None) -> bool:
    """判断本次推送前是否需要先发送 /plan。"""

    if not _is_codex_model():
        return False
    if _normalize_push_send_mode(send_mode) == PUSH_SEND_MODE_QUEUED:
        return False
    resolved_mode = _resolve_dispatch_mode(intended_mode=intended_mode, prompt=prompt)
    if resolved_mode != PUSH_MODE_PLAN:
        return False
    # 内部 slash 命令不插入 /plan，避免干扰语义。
    if (prompt or "").lstrip().startswith("/"):
        return False
    return True


async def _maybe_send_plan_switch_command(
    *,
    chat_id: int,
    intended_mode: Optional[str],
    prompt: str,
    send_mode: Optional[str] = None,
    tmux_session: Optional[str] = None,
) -> bool:
    """按条件先向 tmux 发送 /plan，再短暂等待模式切换稳定。"""

    if not _should_send_plan_switch_command(intended_mode=intended_mode, prompt=prompt, send_mode=send_mode):
        return False
    if tmux_session and tmux_session != TMUX_SESSION:
        ready = await _wait_tmux_session_ready_for_plan_switch(tmux_session)
        if not ready:
            worker_log.warning(
                "并行 tmux 会话在发送 PLAN 预命令前仍未就绪，继续尝试发送 /plan",
                extra={"chat": chat_id, "tmux_session": tmux_session},
            )
    try:
        tmux_send_line(tmux_session or TMUX_SESSION, PLAN_MODE_SWITCH_COMMAND)
    except subprocess.CalledProcessError as exc:
        worker_log.warning(
            "发送 PLAN 预命令失败，继续发送正文：%s",
            exc,
            extra={"chat": chat_id, "mode": PUSH_MODE_PLAN},
        )
        return False
    if PLAN_MODE_SWITCH_DELAY_SECONDS > 0:
        await asyncio.sleep(PLAN_MODE_SWITCH_DELAY_SECONDS)
    worker_log.info(
        "已发送 PLAN 预命令",
        extra={
            "chat": chat_id,
            "mode": PUSH_MODE_PLAN,
            "delay": str(PLAN_MODE_SWITCH_DELAY_SECONDS),
            "command": PLAN_MODE_SWITCH_COMMAND,
        },
    )
    return True


async def _validate_parallel_tmux_ready_for_dispatch(
    *,
    chat_id: int,
    tmux_session: Optional[str],
) -> Optional[str]:
    """并行首次派发前校验 tmux 已进入模型 CLI，而不是停留在 shell。"""

    if not tmux_session:
        return "缺少并行 tmux 会话标识"
    current_command = await asyncio.to_thread(_get_tmux_pane_current_command, tmux_session)
    if _is_tmux_shell_command(current_command):
        worker_log.warning(
            "并行 tmux 首次派发前仍停留在 shell",
            extra={
                "chat": chat_id,
                "tmux_session": tmux_session,
                "pane_current_command": current_command or "-",
            },
        )
        return f"当前终端仍停留在 shell（{current_command or 'unknown'}）"
    return None


async def _dispatch_prompt_to_model(
    chat_id: int,
    prompt: str,
    *,
    reply_to: Optional[Message],
    ack_immediately: bool = True,
    intended_mode: Optional[str] = None,
    send_mode: Optional[str] = None,
    force_exit_plan_ui: bool = False,
    force_exit_plan_ui_key_sequence: Optional[Sequence[str]] = None,
    force_exit_plan_ui_max_rounds: Optional[int] = None,
    dispatch_context: Optional[ParallelDispatchContext] = None,
) -> tuple[bool, Optional[Path]]:
    """统一处理向模型推送提示后的会话绑定、确认与监听。"""

    is_parallel_dispatch = dispatch_context is not None
    parallel_task_id = _normalize_task_id(dispatch_context.task_id) if dispatch_context is not None else None

    if not is_parallel_dispatch:
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
    existing = PARALLEL_TASK_SESSION_MAP.get(parallel_task_id or "") if is_parallel_dispatch else CHAT_SESSION_MAP.get(chat_id)
    if existing:
        candidate = Path(existing)
        if candidate.exists():
            session_path = candidate
        else:
            if is_parallel_dispatch and parallel_task_id:
                PARALLEL_TASK_SESSION_MAP.pop(parallel_task_id, None)
                PARALLEL_SESSION_CONTEXTS.pop(existing, None)
            else:
                CHAT_SESSION_MAP.pop(chat_id, None)
            _reset_delivered_hashes(chat_id, existing)
            _reset_delivered_offsets(chat_id, existing)
    elif not is_parallel_dispatch:
        _reset_delivered_hashes(chat_id)
        _reset_delivered_offsets(chat_id)

    pointer_path: Optional[Path] = None
    pointer_override = dispatch_context.pointer_file if dispatch_context is not None else None
    if pointer_override is not None:
        pointer_path = resolve_path(pointer_override)
    elif CODEX_SESSION_FILE_PATH:
        pointer_path = resolve_path(CODEX_SESSION_FILE_PATH)
    pointer_target = _read_pointer_path(pointer_path) if pointer_path is not None else None
    pointer_switched = False

    if pointer_target is not None:
        if session_path is None:
            session_path = pointer_target
            worker_log.info(
                "[session-map] chat=%s pointer -> %s",
                chat_id,
                session_path,
                extra=_session_extra(path=session_path),
            )
        elif session_path != pointer_target:
            previous_key = existing
            if previous_key:
                _reset_delivered_hashes(chat_id, previous_key)
                _reset_delivered_offsets(chat_id, previous_key)
                SESSION_OFFSETS.pop(previous_key, None)
            elif not is_parallel_dispatch:
                _reset_delivered_hashes(chat_id)
                _reset_delivered_offsets(chat_id)
            session_path = pointer_target
            pointer_switched = True
            worker_log.info(
                "[session-map] chat=%s pointer switched -> %s",
                chat_id,
                session_path,
                extra=_session_extra(path=session_path),
            )
    elif session_path is not None:
        worker_log.info(
            "[session-map] chat=%s reuse session %s",
            chat_id,
            session_path,
            extra=_session_extra(path=session_path),
        )

    # 统一以 MODEL_WORKDIR 作为目标工作目录（Gemini/Codex/ClaudeCode 皆由 run_bot.sh 注入）
    target_cwd_raw = (
        str(dispatch_context.workspace_root)
        if dispatch_context is not None
        else (os.environ.get("MODEL_WORKDIR") or CODEX_WORKDIR or "").strip()
    )
    target_cwd = target_cwd_raw or None
    if pointer_path is not None and not SESSION_BIND_STRICT:
        current_cwd = _read_session_meta_cwd(session_path) if session_path else None
        if session_path is None or (target_cwd and current_cwd != target_cwd):
            latest = (
                _find_latest_gemini_session(pointer_path, target_cwd)
                if _is_gemini_model()
                else _find_latest_rollout_for_cwd(pointer_path, target_cwd)
            )
            if latest is not None:
                SESSION_OFFSETS[str(latest)] = _initial_session_offset(latest)
                _update_pointer(pointer_path, latest)
                session_path = latest
                worker_log.info(
                    "[session-map] chat=%s switch to cwd-matched %s",
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
                    "[session-map] chat=%s fallback to ClaudeCode session %s",
                    chat_id,
                    session_path,
                    extra=_session_extra(path=session_path),
                )

    needs_session_wait = session_path is None
    if session_path is not None:
        # 仅收口当前即将继续执行的会话，避免其他并存会话按钮被误删。
        _drop_plan_confirm_sessions_for_session(chat_id, str(session_path))
    if needs_session_wait and pointer_path is None:
        await _reply_to_chat(
            chat_id,
            f"未检测到 {MODEL_DISPLAY_LABEL} 会话日志，请稍后重试。",
            reply_to=reply_to,
        )
        return False, None

    if is_parallel_dispatch and needs_session_wait:
        ready_issue = await _validate_parallel_tmux_ready_for_dispatch(
            chat_id=chat_id,
            tmux_session=dispatch_context.tmux_session if dispatch_context is not None else None,
        )
        if ready_issue:
            await _reply_to_chat(
                chat_id,
                f"并行 CLI 未启动成功：{ready_issue}",
                reply_to=reply_to,
            )
            return False, None

    await _maybe_send_plan_switch_command(
        chat_id=chat_id,
        intended_mode=intended_mode,
        prompt=prompt,
        send_mode=send_mode,
        tmux_session=dispatch_context.tmux_session if dispatch_context is not None else None,
    )
    await _maybe_force_exit_plan_ui(
        chat_id=chat_id,
        prompt=prompt,
        force_exit_plan_ui=force_exit_plan_ui,
        tmux_session=dispatch_context.tmux_session if dispatch_context is not None else None,
        force_exit_plan_ui_key_sequence=force_exit_plan_ui_key_sequence,
        force_exit_plan_ui_max_rounds=force_exit_plan_ui_max_rounds,
    )

    resolved_send_mode = _normalize_push_send_mode(send_mode)
    if resolved_send_mode == PUSH_SEND_MODE_QUEUED and not _is_codex_model():
        worker_log.warning(
            "非 Codex 模型不支持排队发送，已回退为立即发送",
            extra={"chat": chat_id, "model": MODEL_CANONICAL_NAME},
        )
        resolved_send_mode = PUSH_SEND_MODE_IMMEDIATE

    try:
        dispatch_text = _prepend_enforced_agents_notice(prompt)
        target_session = dispatch_context.tmux_session if dispatch_context is not None else TMUX_SESSION
        if resolved_send_mode == PUSH_SEND_MODE_QUEUED:
            tmux_queue_line(target_session, dispatch_text)
        else:
            tmux_send_line(target_session, dispatch_text)
    except subprocess.CalledProcessError as exc:
        manual_hint = "若终端输入框仍停留未发送，请手动按 Tab 后重试一次推送。" if resolved_send_mode == PUSH_SEND_MODE_QUEUED else "若终端输入框仍停留未发送，请手动按 Enter 后重试一次推送。"
        await _reply_to_chat(
            chat_id,
            (
                f"tmux错误：{exc}\n"
                f"{manual_hint}"
            ),
            reply_to=reply_to,
        )
        return False, None

    if needs_session_wait:
        session_path = await _await_session_path(
            pointer_path,
            target_cwd,
            poll=SESSION_BIND_POLL_INTERVAL,
            strict=SESSION_BIND_STRICT,
            max_wait=SESSION_BIND_TIMEOUT_SECONDS,
        )
        if (
            session_path is None
            and pointer_path is not None
            and _is_claudecode_model()
            and not SESSION_BIND_STRICT
        ):
            session_path = _find_latest_claudecode_rollout(pointer_path)
        allow_strict_fallback = not (is_parallel_dispatch and needs_session_wait)
        if session_path is None and pointer_path is not None and SESSION_BIND_STRICT and allow_strict_fallback:
            # strict 模式兜底：当 pointer 长时间未写入（binder 异常/已退出）时，
            # 直接扫描会话目录定位最新 session，避免用户侧出现“未检测到会话日志”的误报。
            session_path = _fallback_locate_latest_session(pointer_path, target_cwd)
            if session_path is not None:
                worker_log.info(
                    "[session-map] chat=%s strict fallback locate latest session %s",
                    chat_id,
                    session_path,
                    extra=_session_extra(path=session_path),
                )
        if session_path is None:
            details = []
            if pointer_path is not None:
                details.append(f"pointer={pointer_path}")
            if target_cwd:
                details.append(f"cwd={target_cwd}")
            if is_parallel_dispatch and needs_session_wait:
                if details:
                    hint = "；".join(details)
                    message = f"并行 CLI 未生成新的会话日志，请稍后重试。\n（{hint}）"
                else:
                    message = "并行 CLI 未生成新的会话日志，请稍后重试。"
            elif details:
                hint = "；".join(details)
                message = f"未检测到 {MODEL_DISPLAY_LABEL} 会话日志，请稍后重试。\n（{hint}）"
            else:
                message = f"未检测到 {MODEL_DISPLAY_LABEL} 会话日志，请稍后重试。"
            await _reply_to_chat(
                chat_id,
                message,
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
        _drop_plan_confirm_sessions_for_session(chat_id, str(session_path))

    assert session_path is not None
    session_key = str(session_path)
    if session_key not in SESSION_OFFSETS:
        initial_offset = _initial_session_offset(session_path)
        SESSION_OFFSETS[session_key] = initial_offset
        worker_log.info(
            "[session-map] init offset for %s -> %s",
            session_key,
            SESSION_OFFSETS[session_key],
            extra=_session_extra(key=session_key),
        )

    if is_parallel_dispatch:
        assert dispatch_context is not None and parallel_task_id is not None
        _bind_parallel_session_task(session_key, parallel_task_id)
        _bind_parallel_dispatch_context(session_key, dispatch_context)
        _clear_last_message(chat_id, session_key)
        _reset_compact_tracking(chat_id, session_key)
    else:
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

    if ack_immediately or pointer_switched:
        await _send_session_ack(chat_id, session_path, reply_to=reply_to)

    quick_poll_delivered = False
    if SESSION_POLL_TIMEOUT > 0:
        start_time = time.monotonic()
        while time.monotonic() - start_time < SESSION_POLL_TIMEOUT:
            delivered = await _deliver_pending_messages(chat_id, session_path)
            if delivered:
                quick_poll_delivered = True
                break
            await asyncio.sleep(0.3)

    start_in_long_poll = quick_poll_delivered or (session_key in (CHAT_LAST_MESSAGE.get(chat_id) or {}))
    if is_parallel_dispatch:
        assert parallel_task_id is not None
        await _start_parallel_task_watcher(
            parallel_task_id,
            chat_id,
            session_path,
            start_in_long_poll=start_in_long_poll,
        )
    else:
        # 中断旧的延迟轮询（如果存在）
        await _interrupt_long_poll(chat_id)

        # 即时轮询已命中时，恢复 watcher 直接进入延迟轮询，避免重复添加“完成前缀”。
        watcher_task = asyncio.create_task(
            _watch_and_notify(
                chat_id,
                session_path,
                max_wait=WATCH_MAX_WAIT,
                interval=WATCH_INTERVAL,
                start_in_long_poll=start_in_long_poll,
            )
        )
        CHAT_WATCHERS[chat_id] = watcher_task
    return True, session_path


def _fallback_locate_latest_session(pointer_path: Path, target_cwd: Optional[str]) -> Optional[Path]:
    """在 session pointer 未写入时，兜底扫描会话目录定位最新 session 文件。

    主要用于修复以下场景：
    - session_binder 异常退出/未启动，导致 pointer 文件长期为空；
    - bot 处于 strict 绑定模式时，仅等待 pointer 会导致误报“未检测到会话日志”。
    """

    if _is_gemini_model():
        return _find_latest_gemini_session(pointer_path, target_cwd)
    if _is_claudecode_model():
        # ClaudeCode 会话可能缺少 cwd 元数据，按更新时间回退选择最新（并排除 agent-*）。
        return _find_latest_claudecode_rollout(pointer_path)
    return _find_latest_rollout_for_cwd(pointer_path, target_cwd)


def _convert_overlong_task_prompt_to_attachment(
    prompt: str,
    *,
    chat_id: int,
    reply_to: Optional[Message],
) -> str:
    """任务推送兜底：超长提示词自动落盘为附件并改写为附件提示。"""

    if len(prompt) <= TELEGRAM_MESSAGE_LIMIT:
        return prompt
    if reply_to is None:
        worker_log.warning(
            "任务推送提示词超长，但缺少消息上下文，回退原始提示词推送",
            extra={"chat": chat_id, "length": str(len(prompt))},
        )
        return prompt
    try:
        attachment = _persist_text_paste_as_attachment(reply_to, prompt)
        converted = _build_prompt_with_attachments(
            "当前任务推送内容较长，已自动保存为附件（文本），请阅读附件获取全文。",
            [attachment],
        )
    except Exception as exc:  # noqa: BLE001
        worker_log.warning(
            "任务推送超长转附件失败，将回退为原始提示词推送：%s",
            exc,
            extra={"chat": chat_id, "length": str(len(prompt))},
        )
        return prompt
    worker_log.info(
        "任务推送内容过长，已转为附件提示词",
        extra={
            "chat": chat_id,
            "original_length": str(len(prompt)),
            "converted_length": str(len(converted)),
            "attachment_path": attachment.relative_path,
        },
    )
    return converted


async def _push_task_to_model(
    task: TaskRecord,
    *,
    chat_id: int,
    reply_to: Optional[Message],
    supplement: Optional[str],
    actor: Optional[str],
    is_bug_report: bool = False,
    push_mode: Optional[str] = None,
    send_mode: Optional[str] = None,
    dispatch_context: Optional[ParallelDispatchContext] = None,
) -> tuple[bool, str, Optional[Path]]:
    """推送任务信息到模型，并附带补充描述。

    Args:
        task: 任务记录
        chat_id: 聊天 ID
        reply_to: 回复的消息
        supplement: 补充描述
        actor: 操作者
        is_bug_report: 是否为缺陷报告推送
        push_mode: 推送模式（PLAN/YOLO），仅对推送到模型按钮流程生效
        send_mode: 发送方式（立即发送/排队发送）
    """

    history_text, history_count = await _build_history_context_for_model(task.id)
    try:
        notes = await TASK_SERVICE.list_notes(task.id)
    except Exception as exc:  # noqa: BLE001
        worker_log.warning(
            "读取任务备注失败，已回退为空列表：%s",
            exc,
            extra={"task_id": task.id},
        )
        notes = []
    try:
        attachments = await TASK_SERVICE.list_attachments(task.id)
    except Exception as exc:  # noqa: BLE001
        worker_log.warning(
            "读取任务附件失败，已回退为空列表：%s",
            exc,
            extra={"task_id": task.id},
        )
        attachments = []
    # 需求约定：附件按发送顺序展示（时间升序）；服务层默认倒序，这里反转后输出。
    attachments = list(reversed(attachments))
    prompt = _build_model_push_payload(
        task,
        supplement=supplement,
        history=history_text,
        notes=notes,
        attachments=attachments,
        is_bug_report=is_bug_report,
        push_mode=push_mode,
    )
    dispatch_prompt = _convert_overlong_task_prompt_to_attachment(
        prompt,
        chat_id=chat_id,
        reply_to=reply_to,
    )
    _remember_chat_active_user(chat_id, _extract_actor_user_id(actor))
    dispatch_kwargs: dict[str, Any] = {
        "reply_to": reply_to,
        "ack_immediately": False,
        "intended_mode": push_mode,
        "send_mode": send_mode,
    }
    if dispatch_context is not None:
        dispatch_kwargs["dispatch_context"] = dispatch_context
    success, session_path = await _dispatch_prompt_to_model(
        chat_id,
        dispatch_prompt,
        **dispatch_kwargs,
    )
    if success and session_path is not None:
        _bind_session_task(str(session_path), task.id)
        if dispatch_context is not None:
            _bind_parallel_session_task(str(session_path), task.id)
    has_supplement = bool((supplement or "").strip())
    result_status = "success" if success else "failed"
    payload: dict[str, Any] = {
        "result": result_status,
        "has_supplement": has_supplement,
        "history_items": history_count,
        "history_chars": len(history_text),
        "prompt_chars": len(dispatch_prompt),
        "model": ACTIVE_MODEL or "",
        "send_mode": _normalize_push_send_mode(send_mode),
    }
    if dispatch_prompt != prompt:
        payload["original_prompt_chars"] = len(prompt)
    if has_supplement:
        payload["supplement"] = supplement or ""

    if not success:
        worker_log.warning(
            "推送到模型失败：未能建立 Codex 会话",
            extra={"task_id": task.id},
        )
    else:
        worker_log.info(
            "已推送任务描述到模型",
            extra={
                "task_id": task.id,
                "status": task.status,
                "has_supplement": str(has_supplement),
            },
        )
    return success, dispatch_prompt, session_path


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
        issues.append("未配置工作目录 (MODEL_WORKDIR)")
    else:
        candidate = resolve_path(workdir_raw)
        if not candidate.exists():
            issues.append(f"工作目录不存在: {workdir_raw}")
        elif not candidate.is_dir():
            issues.append(f"工作目录不是文件夹: {workdir_raw}")
        else:
            workdir_path = candidate

    try:
        tmux_bin()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        issues.append("未检测到 tmux，可通过 'brew install tmux' 安装")

    model_cmd = os.environ.get("MODEL_CMD")
    if not model_cmd and (ACTIVE_MODEL or "").lower() == "codex":
        model_cmd = os.environ.get("CODEX_CMD") or "codex"
    if model_cmd:
        executable = _extract_executable(model_cmd)
        if executable and shutil.which(executable) is None:
            issues.append(f"无法找到模型 CLI 可执行文件: {executable}")

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
    return "当前 worker 环境存在以下问题，请先处理后再试：\n" + "\n".join(bullet_lines)


ENV_ISSUES, PRIMARY_WORKDIR = _detect_environment_issues()
if ENV_ISSUES:
    worker_log.error("环境自检失败: %s", "; ".join(ENV_ISSUES))

ROOT_DIR_ENV = os.environ.get("ROOT_DIR")
ROOT_DIR_PATH = Path(ROOT_DIR_ENV).expanduser() if ROOT_DIR_ENV else Path(__file__).resolve().parent
DATA_ROOT_DEFAULT = CONFIG_ROOT_PATH / "data"
DATA_ROOT = Path(os.environ.get("TASKS_DATA_ROOT", str(DATA_ROOT_DEFAULT))).expanduser()
DATA_ROOT.mkdir(parents=True, exist_ok=True)
PROJECT_SLUG = (PROJECT_NAME or "default").replace("/", "-") or "default"
TASK_DB_PATH = DATA_ROOT / f"{PROJECT_SLUG}.db"
TASK_SERVICE = TaskService(TASK_DB_PATH, PROJECT_SLUG)
PARALLEL_SESSION_STORE = ParallelSessionStore(TASK_DB_PATH, PROJECT_SLUG)
COMMAND_SERVICE = CommandService(TASK_DB_PATH, PROJECT_SLUG)
# 通用命令独立存放在全局数据库，worker 只读运行并将执行历史标记到自身项目
GLOBAL_COMMAND_DB_PATH = resolve_global_command_db(CONFIG_ROOT_PATH)
GLOBAL_COMMAND_SERVICE = CommandService(
    GLOBAL_COMMAND_DB_PATH,
    GLOBAL_COMMAND_PROJECT_SLUG,
    scope=GLOBAL_COMMAND_SCOPE,
    history_project_slug=PROJECT_SLUG,
)

ATTACHMENT_STORAGE_ROOT = (DATA_ROOT / "telegram").expanduser()
ATTACHMENT_STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
_ATTACHMENT_TOTAL_MB = max(_env_int("TELEGRAM_ATTACHMENT_MAX_TOTAL_MB", 512), 16)
ATTACHMENT_TOTAL_LIMIT_BYTES = _ATTACHMENT_TOTAL_MB * 1024 * 1024
# 普通 Telegram 图文混合/相册偶发会出现后续图片稍晚到达的情况；
# 默认将 quiet window 提升到 1.5 秒，优先保证同一组输入只触发一次推送。
MEDIA_GROUP_AGGREGATION_DELAY = max(_env_float("TELEGRAM_MEDIA_GROUP_DELAY", 1.5), 0.1)


@dataclass
class TelegramSavedAttachment:
    """记录单个附件的落地信息，便于提示模型读取。"""

    kind: str
    display_name: str
    mime_type: str
    absolute_path: Path
    relative_path: str


@dataclass
class PendingMediaGroupState:
    """聚合 Telegram 媒体组的临时缓存。"""

    chat_id: int
    origin_message: Message
    attachment_dir: Path
    attachments: list[TelegramSavedAttachment]
    captions: list[str]
    finalize_task: Optional[asyncio.Task] = None


MEDIA_GROUP_STATE: dict[str, PendingMediaGroupState] = {}
MEDIA_GROUP_LOCK = asyncio.Lock()


@dataclass
class PendingBugMediaGroupState:
    """缺陷/任务流程中用于媒体组聚合的临时缓存。"""

    chat_id: int
    attachment_dir: Path
    attachments: list[TelegramSavedAttachment]
    captions: list[str]
    waiters: list[asyncio.Future]
    finalize_task: Optional[asyncio.Task] = None


BUG_MEDIA_GROUP_STATE: dict[str, PendingBugMediaGroupState] = {}
BUG_MEDIA_GROUP_LOCK = asyncio.Lock()
BUG_MEDIA_GROUP_PROCESSED: set[str] = set()
# 通用附件流程（/task_new、/attach）媒体组仅允许消费一次，避免相册导致重复附件。
GENERIC_MEDIA_GROUP_CONSUMED: set[tuple[int, str]] = set()


@dataclass
class PendingTextPasteState:
    """聚合“长文本粘贴”被 Telegram 拆分的多条消息。"""

    chat_id: int
    origin_message: Message
    # 可能出现“短前缀 + 长日志”的两段输入：短前缀先到，长日志后到。
    # 为避免触发两次推送（两次 ack），这里把短前缀先暂存，待窗口内收到长日志后合并为一次推送。
    prefix_text: Optional[str] = None
    # 记录每一段分片，按 message_id 排序后拼接，降低乱序到达导致的“看似缺失/顺序错乱”风险。
    parts: list[tuple[int, str]] = field(default_factory=list)
    finalize_task: Optional[asyncio.Task] = None


TEXT_PASTE_STATE: dict[int, PendingTextPasteState] = {}
TEXT_PASTE_LOCK = asyncio.Lock()
# 合成消息保护：内部会注入“合成消息”复用既有 handler/FSM（含长文本聚合、回调兜底命令）。
# 这类消息不应再触发“文本聚合/补偿轮询”等入站逻辑，否则会产生误触发与递归。
TEXT_PASTE_SYNTHETIC_GUARD: dict[tuple[int, int], float] = {}
TEXT_PASTE_SYNTHETIC_GUARD_TTL_SECONDS = 60.0
INTERNAL_SYNTHETIC_MESSAGE_ID_OFFSET = 10_000_000

ATTACHMENT_USAGE_HINT = (
    "请按需读取附件：图片可使用 Codex 的 view_image 功能或 Claude Code 的文件引用能力；"
    "文本/日志可直接通过 @<路径> 打开；若需其他处理请说明。"
)


def _mark_text_paste_synthetic_message(chat_id: int, message_id: int) -> None:
    """标记某条 message_id 为“内部合成消息”，在短 TTL 内跳过入站二次处理。"""

    now = time.monotonic()
    TEXT_PASTE_SYNTHETIC_GUARD[(chat_id, message_id)] = now
    # 简单清理：避免守护表无限增长（窗口短，数量不会大）。
    expired_before = now - TEXT_PASTE_SYNTHETIC_GUARD_TTL_SECONDS
    for key, ts in list(TEXT_PASTE_SYNTHETIC_GUARD.items()):
        if ts < expired_before:
            TEXT_PASTE_SYNTHETIC_GUARD.pop(key, None)


def _is_text_paste_synthetic_message(message: Message) -> bool:
    """判断当前消息是否为内部注入的“合成消息”。"""

    try:
        chat_id = int(message.chat.id)
        message_id = int(getattr(message, "message_id", 0) or 0)
    except Exception:
        return False
    ts = TEXT_PASTE_SYNTHETIC_GUARD.get((chat_id, message_id))
    if ts is None:
        return False
    if time.monotonic() - ts > TEXT_PASTE_SYNTHETIC_GUARD_TTL_SECONDS:
        TEXT_PASTE_SYNTHETIC_GUARD.pop((chat_id, message_id), None)
        return False
    return True


def _build_internal_synthetic_message_id(origin_message_id: int) -> int:
    """为内部合成消息生成远离真实 Telegram 序列的 message_id，避免与真实消息冲突。"""

    base = max(int(origin_message_id or 0), 0)
    return base + INTERNAL_SYNTHETIC_MESSAGE_ID_OFFSET

_FS_SAFE_PATTERN = re.compile(r"[^A-Za-z0-9._-]")


def _attachment_directory_prefix_for_display(relative_path: str) -> Optional[str]:
    """根据附件相对路径推导目录前缀，便于提示模型定位。"""

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
    """清理路径片段中的特殊字符，避免越权访问。"""

    stripped = (value or "").strip()
    cleaned = _FS_SAFE_PATTERN.sub("_", stripped)
    cleaned = cleaned.strip("._")
    return cleaned or fallback


def _format_relative_path(path: Path) -> str:
    """将绝对路径转换为模型更易识别的相对路径。"""

    try:
        rel = path.relative_to(ROOT_DIR_PATH)
        rel_str = rel.as_posix()
        if not rel_str.startswith("."):
            return f"./{rel_str}"
        return rel_str
    except ValueError:
        return path.resolve().as_posix()


def _directory_size(path: Path) -> int:
    """计算目录占用的总字节数。"""

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
    """控制附件目录容量，避免磁盘被占满。"""

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
                "清理旧附件目录失败：%s",
                exc,
                extra=_session_extra(path=folder),
            )
        if _directory_size(ATTACHMENT_STORAGE_ROOT) <= ATTACHMENT_TOTAL_LIMIT_BYTES:
            break


def _guess_extension(mime_type: Optional[str], fallback: str = ".bin") -> str:
    """根据 MIME 类型推断扩展名。"""

    if mime_type:
        guessed = mimetypes.guess_extension(mime_type, strict=False)
        if guessed:
            return guessed
    return fallback


def _build_obfuscated_filename(
    file_name_hint: str,
    mime_type: Optional[str],
    *,
    salt: str,
    now: Optional[datetime] = None,
    monotonic_ns: Optional[int] = None,
) -> str:
    """生成混淆后的文件名，避免暴露源文件名。"""

    current = now or datetime.now(UTC)
    timestamp = current.strftime("%Y%m%d_%H%M%S%f")[:-3]  # 精确到毫秒
    monotonic_value = monotonic_ns if monotonic_ns is not None else time.monotonic_ns()
    hasher = hashlib.sha256()
    for part in (salt, file_name_hint, str(monotonic_value)):
        hasher.update(str(part).encode("utf-8", errors="ignore"))

    digest = hasher.hexdigest()[:12]

    raw_suffix = Path(file_name_hint).suffix
    if raw_suffix and not re.fullmatch(r"\.[A-Za-z0-9]+", raw_suffix):
        raw_suffix = ""
    extension = raw_suffix or _guess_extension(mime_type, ".bin")
    if not extension.startswith("."):
        extension = f".{extension}"

    safe_suffix = re.sub(r"[^A-Za-z0-9]", "", extension.lstrip("."))
    extension = f".{safe_suffix or 'bin'}"

    return f"{timestamp}-{digest}{extension}"


def _attachment_dir_for_message(message: Message, media_group_id: Optional[str] = None) -> Path:
    """为当前消息生成附件目录，按项目标识 + 日期归档，便于模型定位。"""

    # media_group_id 参数保留用于兼容旧调用，目前统一归档至日期目录。
    _ = media_group_id

    # 优先使用项目 slug，回退到 bot 名称或通用前缀。
    project_identifier = PROJECT_SLUG or ""
    sanitized_project = _sanitize_fs_component(project_identifier, "project")
    if sanitized_project == "project":
        bot_username = getattr(message.bot, "username", None)
        sanitized_project = _sanitize_fs_component(bot_username or "bot", "bot")

    # 使用消息时间（UTC）格式化日期，确保相同日期的附件集中存放。
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
    """从 Telegram 下载文件并返回本地路径。"""

    bot = message.bot or current_bot()
    telegram_file = await bot.get_file(file_id)
    salt = f"{file_id}:{getattr(message, 'message_id', '')}:{getattr(message.chat, 'id', '')}:{uuid.uuid4().hex}"
    filename = _build_obfuscated_filename(
        file_name_hint,
        mime_type,
        salt=salt,
    )
    destination = target_dir / filename
    counter = 1
    while destination.exists():
        filename = _build_obfuscated_filename(
            file_name_hint,
            mime_type,
            salt=f"{salt}:{counter}",
        )
        destination = target_dir / filename
        counter += 1
    await bot.download_file(telegram_file.file_path, destination=destination)
    return destination


async def _collect_saved_attachments(message: Message, target_dir: Path) -> list[TelegramSavedAttachment]:
    """下载消息中的所有附件，并返回保存记录。"""

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
                display_name=path.name,
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
                display_name=path.name,
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
                display_name=path.name,
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
                display_name=path.name,
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
                display_name=path.name,
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
                display_name=path.name,
                mime_type=video_note.mime_type or "video/mp4",
                absolute_path=path,
                relative_path=_format_relative_path(path),
            )
        )

    if saved:
        _cleanup_attachment_storage()
    return saved


async def _finalize_bug_media_group(media_group_id: str) -> None:
    """在延迟后统一返回媒体组聚合结果，唤醒所有等待者。"""

    try:
        await asyncio.sleep(MEDIA_GROUP_AGGREGATION_DELAY)
    except asyncio.CancelledError:
        return

    async with BUG_MEDIA_GROUP_LOCK:
        state = BUG_MEDIA_GROUP_STATE.pop(media_group_id, None)

    if state is None:
        return

    caption = "\n".join(state.captions).strip()
    attachments = list(state.attachments)
    for waiter in state.waiters:
        if waiter.done():
            continue
        try:
            waiter.set_result((attachments, caption))
        except Exception:
            continue


async def _collect_bug_media_group(
    message: Message,
    attachment_dir: Path,
) -> tuple[list[TelegramSavedAttachment], str]:
    """收集媒体组内的全部附件与合并文本，用于缺陷/任务流程。

    设计要点：
    - 媒体组内的每条消息都会加入同一聚合缓存，等待短暂延迟后一次性返回；
    - 返回的文本为媒体组所有 caption/text 合并结果，附件为整组去重后的列表；
    - 防止同一媒体组被重复处理时遗漏图片或重复绑定。
    """

    media_group_id = message.media_group_id
    text_part = (message.caption or message.text or "").strip()

    if not media_group_id:
        attachments = await _collect_saved_attachments(message, attachment_dir)
        return attachments, text_part

    async with BUG_MEDIA_GROUP_LOCK:
        state = BUG_MEDIA_GROUP_STATE.get(media_group_id)
        if state is None:
            state = PendingBugMediaGroupState(
                chat_id=message.chat.id,
                attachment_dir=attachment_dir,
                attachments=[],
                captions=[],
                waiters=[],
            )
            BUG_MEDIA_GROUP_STATE[media_group_id] = state
        loop = asyncio.get_event_loop()
        waiter: asyncio.Future = loop.create_future()
        state.waiters.append(waiter)

    attachments = await _collect_saved_attachments(message, state.attachment_dir)

    async with BUG_MEDIA_GROUP_LOCK:
        state = BUG_MEDIA_GROUP_STATE.get(media_group_id)
        if state is None:
            # 理论上不会发生，若被清理则直接返回当前消息结果
            return attachments, text_part
        state.attachments.extend(attachments)
        if text_part:
            state.captions.append(text_part)
        if state.finalize_task and not state.finalize_task.done():
            state.finalize_task.cancel()
        state.finalize_task = asyncio.create_task(_finalize_bug_media_group(media_group_id))

    all_attachments, merged_caption = await waiter
    return all_attachments, merged_caption


async def _collect_generic_media_group(
    message: Message,
    attachment_dir: Path,
    *,
    processed: set[str],
) -> tuple[list[TelegramSavedAttachment], str, set[str]]:
    """通用媒体组聚合助手，供任务创建/描述补充等流程使用。"""

    media_group_id = message.media_group_id
    text_part = (message.caption or message.text or "").strip()

    if not media_group_id:
        attachments = await _collect_saved_attachments(message, attachment_dir)
        return attachments, text_part, processed

    async with BUG_MEDIA_GROUP_LOCK:
        state = BUG_MEDIA_GROUP_STATE.get(media_group_id)
        if state is None:
            state = PendingBugMediaGroupState(
                chat_id=message.chat.id,
                attachment_dir=attachment_dir,
                attachments=[],
                captions=[],
                waiters=[],
            )
            BUG_MEDIA_GROUP_STATE[media_group_id] = state
        loop = asyncio.get_event_loop()
        waiter: asyncio.Future = loop.create_future()
        state.waiters.append(waiter)

    attachments = await _collect_saved_attachments(message, state.attachment_dir)

    async with BUG_MEDIA_GROUP_LOCK:
        state = BUG_MEDIA_GROUP_STATE.get(media_group_id)
        if state is None:
            return attachments, text_part, processed
        state.attachments.extend(attachments)
        if text_part:
            state.captions.append(text_part)
        if state.finalize_task and not state.finalize_task.done():
            state.finalize_task.cancel()
        state.finalize_task = asyncio.create_task(_finalize_bug_media_group(media_group_id))

    all_attachments, merged_caption = await waiter
    # 同一媒体组会触发多次 handler（每张图一条消息），这里需要确保整组仅被消费一次。
    # 否则会出现：用户发两张图，任务附件写入四条（每张图各重复一次）。
    async with BUG_MEDIA_GROUP_LOCK:
        consumed_key = (message.chat.id, media_group_id)
        already_consumed = consumed_key in GENERIC_MEDIA_GROUP_CONSUMED
        if not already_consumed:
            GENERIC_MEDIA_GROUP_CONSUMED.add(consumed_key)
    processed.add(media_group_id)
    if already_consumed:
        return [], "", processed
    return all_attachments, merged_caption, processed


def _serialize_saved_attachment(item: TelegramSavedAttachment) -> dict[str, str]:
    """将附件对象转为可持久化在 FSM 中的简易字典。"""

    return {
        "kind": item.kind,
        "display_name": item.display_name,
        "mime_type": item.mime_type,
        "path": item.relative_path,
    }


async def _bind_serialized_attachments(
    task: TaskRecord,
    attachments: Sequence[Mapping[str, str]],
    *,
    actor: str,
) -> list[TaskAttachmentRecord]:
    """将序列化附件绑定到任务并记录事件日志。"""

    bound: list[TaskAttachmentRecord] = []
    # 兜底：按 path 去重，避免媒体组/重放导致同一附件重复写库。
    seen_paths: set[str] = set()
    for item in attachments:
        path = (item.get("path") or "").strip()
        if path:
            if path in seen_paths:
                continue
            seen_paths.add(path)
        record = await TASK_SERVICE.add_attachment(
            task.id,
            display_name=item.get("display_name") or "attachment",
            mime_type=item.get("mime_type") or "application/octet-stream",
            path=path,
            kind=item.get("kind") or "document",
        )
        bound.append(record)
    return bound


def _build_prompt_with_attachments(
    text_part: Optional[str],
    attachments: Sequence[TelegramSavedAttachment],
) -> str:
    """将文字与附件描述拼接成模型可读的提示。"""

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
            lines = [f"附件列表（文件位于项目工作目录（{directory_hint}），可直接读取）："]
        else:
            lines = ["附件列表（文件位于项目工作目录，可直接读取）："]
        for idx, item in enumerate(attachments, 1):
            lines.append(
                f"{idx}. {item.display_name}（{item.mime_type}）→ {item.relative_path}"
            )
        lines.append("")
        lines.append(ATTACHMENT_USAGE_HINT)
        sections.append("\n".join(lines))
    if not sections:
        fallback = [
            "收到一条仅包含附件的消息，没有额外文字说明。",
            "请直接阅读列出的附件并给出观察结果或结论。",
        ]
        sections.append("\n".join(fallback))
    return "\n\n".join(sections).strip()


def _write_text_payload_as_attachment(
    message: Message,
    *,
    text: str,
    target_dir: Path,
    file_name_hint: str = "pasted_log.txt",
    mime_type: str = "text/plain",
) -> Path:
    """将长文本落盘为“本地附件”，用于提示模型按文件读取。

    说明：
    - 这里不调用 Telegram sendDocument，仅把文本写入 vibego 附件目录；
    - 推送给模型的提示词会以“附件列表 → 文件路径”的形式引用该文件；
    - 文件名使用混淆策略，避免泄露用户的原始上下文信息。
    """

    salt = f"text:{getattr(message, 'message_id', '')}:{getattr(message.chat, 'id', '')}:{uuid.uuid4().hex}"
    filename = _build_obfuscated_filename(file_name_hint, mime_type, salt=salt)
    destination = target_dir / filename
    counter = 1
    while destination.exists():
        filename = _build_obfuscated_filename(
            file_name_hint,
            mime_type,
            salt=f"{salt}:{counter}",
        )
        destination = target_dir / filename
        counter += 1
    destination.write_text(text, encoding="utf-8", errors="ignore")
    return destination


def _persist_text_paste_as_attachment(message: Message, text: str) -> TelegramSavedAttachment:
    """把“被拆分的长文本粘贴”保存为本地附件，并返回附件描述对象。"""

    attachment_dir = _attachment_dir_for_message(message)
    path = _write_text_payload_as_attachment(
        message,
        text=text,
        target_dir=attachment_dir,
    )
    _cleanup_attachment_storage()
    return TelegramSavedAttachment(
        kind="document",
        display_name=path.name,
        mime_type="text/plain",
        absolute_path=path,
        relative_path=_format_relative_path(path),
    )


def _build_overlong_text_placeholder(label: str) -> str:
    """为写库字段生成“超长占位文本”，提示用户全文已保存为附件。"""

    cleaned = (label or "").strip()
    if cleaned.endswith(("：", ":")):
        cleaned = cleaned[:-1].strip()
    prefix = f"{cleaned}" if cleaned else "内容"
    return f"⚠️ {prefix}过长，已自动保存为附件（文本），请查看附件获取全文。"


async def _feed_synthetic_text_update(origin_message: Message, *, text: str) -> None:
    """将聚合后的文本以“合成消息”的形式重新注入 dispatcher，让原有流程继续执行。"""

    bot = current_bot()
    raw_text = text or ""
    stripped = raw_text.strip()
    if not stripped:
        return

    try:
        now = datetime.now(tz=ZoneInfo("UTC"))
    except ZoneInfoNotFoundError:
        now = datetime.now(UTC)

    origin_message_id = int(getattr(origin_message, "message_id", 0) or 0)
    # 关键：使用远大于真实 Telegram message_id 的编号，避免与真实消息冲突导致误判为“合成消息”。
    synthetic_message_id = _build_internal_synthetic_message_id(origin_message_id)

    entities: Optional[list[MessageEntity]] = None
    if stripped.startswith("/"):
        command_token = stripped.split()[0]
        entities = [MessageEntity(type="bot_command", offset=0, length=len(command_token))]

    synthetic_message = origin_message.model_copy(
        update={
            "message_id": synthetic_message_id,
            "date": now,
            "edit_date": None,
            "text": stripped,
            "caption": None,
            "entities": entities,
        }
    )
    update = Update.model_construct(
        update_id=int(time.time() * 1000),
        message=synthetic_message,
    )
    _mark_text_paste_synthetic_message(int(origin_message.chat.id), synthetic_message_id)
    await dp.feed_update(bot, update)


async def _finalize_text_paste_after_delay(chat_id: int) -> None:
    """在短暂延迟后合并“长文本粘贴”并注入合成消息。"""

    try:
        await asyncio.sleep(TEXT_PASTE_AGGREGATION_DELAY)
    except asyncio.CancelledError:
        return

    async with TEXT_PASTE_LOCK:
        state = TEXT_PASTE_STATE.pop(chat_id, None)

    if state is None:
        return

    prefix_text = (state.prefix_text or "").strip() or None
    merged = "".join(part for _message_id, part in sorted(state.parts, key=lambda item: item[0]))
    if not merged.strip():
        # 仅有“短前缀”但没有后续日志分片：窗口结束后按普通消息处理，避免吞消息。
        if prefix_text:
            try:
                await _feed_synthetic_text_update(state.origin_message, text=prefix_text)
            except Exception as exc:  # noqa: BLE001
                worker_log.exception(
                    "短前缀聚合回退推送失败：%s",
                    exc,
                    extra={**_session_extra(), "chat": chat_id},
                )
        return

    try:
        combined = f"{prefix_text}\n{merged}" if prefix_text else merged
        await _feed_synthetic_text_update(state.origin_message, text=combined)
    except Exception as exc:  # noqa: BLE001
        worker_log.exception(
            "长文本粘贴聚合注入失败：%s",
            exc,
            extra={**_session_extra(), "chat": chat_id},
        )


def _is_text_paste_prefix_candidate(text: str) -> bool:
    """判断是否为“短前缀”候选，用于合并“短前缀 + 长日志”的两段输入。

    设计目标：
    - 解决“短前缀先到、长日志后到”导致的两次推送（两次 ack）；
    - 尽量降低误合并：仅在非常短、且以冒号结尾的文本上启用等待窗口。
    """

    stripped = (text or "").strip()
    if not stripped:
        return False
    if len(stripped) > TEXT_PASTE_PREFIX_MAX_CHARS:
        return False
    if "\n" in stripped or "\r" in stripped:
        return False
    if not stripped.endswith((":", "：")):
        return False
    # 降低误触发：纯数字/编号（如“1:”）不视为短前缀。
    if re.search(r"[A-Za-z\u4e00-\u9fff]", stripped) is None:
        return False
    return True


_TEXT_PASTE_LOG_PREFIX_PATTERN = re.compile(r"^\s*\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}")


def _looks_like_text_paste_log_fragment(text: str) -> bool:
    """粗略判断文本是否像日志分片（用于降低“短前缀聚合”误触发的概率）。"""

    candidate = (text or "").lstrip()
    if not candidate:
        return False
    if _TEXT_PASTE_LOG_PREFIX_PATTERN.match(candidate):
        return True
    # 常见关键词兜底（避免过拟合某个业务日志格式）
    lowered = candidate.lower()
    return any(token in lowered for token in ("traceid", "error", "exception", "[info]", "[warn]", "[warning]"))


async def _maybe_enqueue_text_paste_message(message: Message, text_part: str) -> bool:
    """尝试将当前文本加入“长文本粘贴聚合”队列。

    触发规则：
    - 若当前 chat 已在聚合中：无条件追加（用户粘贴的后续分片可能较短）
    - 若当前 chat 未聚合：
      - 单条文本接近 Telegram 上限时启动聚合
      - 或者文本是“短前缀”（如“见如下日志：”）时先进入短暂等待窗口，用于合并后续长日志
    """

    if not ENABLE_TEXT_PASTE_AGGREGATION:
        return False
    if TEXT_PASTE_NEAR_LIMIT_THRESHOLD <= 0:
        return False
    if not text_part:
        return False

    chat_id = message.chat.id
    message_id = int(getattr(message, "message_id", 0) or 0)
    prefix_to_flush: Optional[str] = None
    prefix_message: Optional[Message] = None

    async with TEXT_PASTE_LOCK:
        state = TEXT_PASTE_STATE.get(chat_id)
        if state is None:
            state = PendingTextPasteState(
                chat_id=chat_id,
                origin_message=message,
            )
            stripped = (text_part or "").strip()
            if len(text_part) >= TEXT_PASTE_NEAR_LIMIT_THRESHOLD:
                state.parts.append((message_id, text_part))
                TEXT_PASTE_STATE[chat_id] = state
            elif _is_text_paste_prefix_candidate(stripped):
                # 短前缀先缓存，等待窗口内出现的长日志分片；窗口结束仍未出现则回退为普通推送。
                state.prefix_text = stripped
                TEXT_PASTE_STATE[chat_id] = state
            else:
                return False
        else:
            # 若聚合由“短前缀”触发，且尚未收到任何日志分片，则对下一条消息做一次兜底判断：
            # - 若下一条仍然很短（且不接近上限），大概率不是日志粘贴分片，立即回退推送短前缀，避免误合并。
            if (state.prefix_text or "").strip() and not state.parts:
                is_followup_long_enough = (
                    len(text_part) >= TEXT_PASTE_NEAR_LIMIT_THRESHOLD
                    or "\n" in text_part
                    or "\r" in text_part
                    or len(text_part) >= TEXT_PASTE_PREFIX_FOLLOWUP_MIN_CHARS
                    or _looks_like_text_paste_log_fragment(text_part)
                )
                if not is_followup_long_enough:
                    prefix_to_flush = (state.prefix_text or "").strip() or None
                    prefix_message = state.origin_message
                    # 清理当前聚合状态，避免 finalize 误触发。
                    if state.finalize_task and not state.finalize_task.done():
                        state.finalize_task.cancel()
                    TEXT_PASTE_STATE.pop(chat_id, None)
                    state = None

            if state is not None:
                state.parts.append((message_id, text_part))

        if state is not None:
            # 使用最早的一条消息作为回复对象，避免引用后续分片导致上下文不连贯。
            if getattr(state.origin_message, "message_id", 0) > message_id:
                state.origin_message = message

            if state.finalize_task and not state.finalize_task.done():
                state.finalize_task.cancel()
            state.finalize_task = asyncio.create_task(_finalize_text_paste_after_delay(chat_id))

    if prefix_to_flush and prefix_message is not None:
        await _feed_synthetic_text_update(prefix_message, text=prefix_to_flush)
        return False

    return True


async def _finalize_media_group_after_delay(media_group_id: str) -> None:
    """在短暂延迟后合并媒体组消息，确保 Telegram 全部照片到齐。"""

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
            "媒体组消息推送模型失败：%s",
            exc,
            extra=_session_extra(media_group=media_group_id),
        )


async def _enqueue_media_group_message(message: Message, text_part: Optional[str]) -> None:
    """收集媒体组中的每一条消息，统一延迟推送。"""

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
            # 若期间被清理，重新创建并继续积累，避免丢失后续内容。
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
        # 使用首条消息作为引用对象，便于 Telegram 回复。
        if state.origin_message.message_id > message.message_id:
            state.origin_message = message
        if state.finalize_task and not state.finalize_task.done():
            state.finalize_task.cancel()
        state.finalize_task = asyncio.create_task(_finalize_media_group_after_delay(media_group_id))


async def _handle_prompt_dispatch(
    message: Message,
    prompt: str,
    *,
    dispatch_context: Optional[ParallelDispatchContext] = None,
) -> None:
    """统一封装向模型推送提示词的流程。"""

    if ENV_ISSUES:
        allow_with_existing_session = False
        reusable_session = CHAT_SESSION_MAP.get(message.chat.id)
        if reusable_session:
            candidate = Path(reusable_session)
            if candidate.exists():
                allow_with_existing_session = True
                worker_log.warning(
                    "检测到环境异常，但存在可复用会话，继续处理消息",
                    extra={"chat": message.chat.id, **_session_extra(path=candidate)},
                )
        if not allow_with_existing_session:
            message_text = _format_env_issue_message()
            worker_log.warning(
                "拒绝处理消息，环境异常: %s",
                message_text,
                extra={**_session_extra(), "chat": message.chat.id},
            )
            await message.answer(message_text)
            return

    # 全局长文本处理：合成消息（>4096）通常意味着 Telegram 拆分后的聚合结果；
    # 为避免把超长文本直接塞进 tmux/CLI，这里统一转为“本地附件 + 附件提示词”再推送模型。
    dispatch_prompt = prompt
    if len(dispatch_prompt) > TELEGRAM_MESSAGE_LIMIT:
        try:
            attachment = _persist_text_paste_as_attachment(message, dispatch_prompt)
            dispatch_prompt = _build_prompt_with_attachments(
                "收到一段超长文本，已自动保存为附件，请阅读附件获取全文。",
                [attachment],
            )
        except Exception as exc:  # noqa: BLE001
            worker_log.warning(
                "超长文本转附件失败，将回退为原始提示词推送：%s",
                exc,
                extra={**_session_extra(), "chat": message.chat.id},
            )

    bot = current_bot()
    await bot.send_chat_action(message.chat.id, "typing")

    if MODE == "A":
        if not AGENT_CMD:
            await message.answer("AGENT_CMD 未配置（.env）")
            return
        rc, out = run_subprocess_capture(AGENT_CMD, input_text=dispatch_prompt)
        out = out or ""
        out = out + ("" if rc == 0 else f"\n(exit={rc})")
        await reply_large_text(message.chat.id, out)
        return

    active_user_id = getattr(message.from_user, "id", None) if message.from_user else None
    _remember_chat_active_user(message.chat.id, active_user_id)
    # 需求约定：普通 Telegram 文本消息不再自动注入 /plan。
    # PLAN/YOLO 由用户在交互流程中显式选择，避免“默认强制切到 PLAN”。
    dispatch_kwargs: dict[str, Any] = {
        "reply_to": message,
        "intended_mode": None,
    }
    if dispatch_context is not None:
        dispatch_kwargs["dispatch_context"] = dispatch_context
    await _dispatch_prompt_to_model(
        message.chat.id,
        dispatch_prompt,
        **dispatch_kwargs,
    )

BOT_COMMANDS: list[tuple[str, str]] = [
    ("start", "打开任务概览"),
    ("help", "查看全部命令"),
]

COMMAND_KEYWORDS: set[str] = {command for command, _ in BOT_COMMANDS}
COMMAND_KEYWORDS.update(
    {
        "task_child",
        "task_children",
        "task_delete",
        "task_show",
        "task_new",
        "task_list",
        "tasks",
        "commands",
        "task_note",
        "task_update",
        "attach",
    }
)

WORKER_MENU_BUTTON_TEXT = "📋 任务列表"
WORKER_COMMANDS_BUTTON_TEXT = "📟 命令管理"
WORKER_TERMINAL_SNAPSHOT_BUTTON_TEXT = "💻 会话实况"
WORKER_PLAN_MODE_BUTTON_PREFIX = "🧭 PLAN MODE:"
WORKER_PLAN_MODE_BUTTON_TEXT_ON = f"{WORKER_PLAN_MODE_BUTTON_PREFIX} ON"
WORKER_PLAN_MODE_BUTTON_TEXT_OFF = f"{WORKER_PLAN_MODE_BUTTON_PREFIX} OFF"
WORKER_PLAN_MODE_BUTTON_TEXT_UNKNOWN = f"{WORKER_PLAN_MODE_BUTTON_PREFIX} ?"
WORKER_PLAN_MODE_TOGGLE_KEY = (os.environ.get("WORKER_PLAN_MODE_TOGGLE_KEY") or PLAN_EXECUTION_EXIT_PLAN_KEY).strip() or PLAN_EXECUTION_EXIT_PLAN_KEY
WORKER_PLAN_MODE_PROBE_LINES = max(_env_int("WORKER_PLAN_MODE_PROBE_LINES", 80), 20)
WORKER_PLAN_MODE_PROBE_TIMEOUT_SECONDS = max(_env_float("WORKER_PLAN_MODE_PROBE_TIMEOUT_SECONDS", 0.8), 0.1)
WORKER_PLAN_MODE_TOGGLE_STABILIZE_SECONDS = max(_env_float("WORKER_PLAN_MODE_TOGGLE_STABILIZE_SECONDS", 0.12), 0.0)
WORKER_PLAN_MODE_TOGGLE_RETRY_ROUNDS = max(_env_int("WORKER_PLAN_MODE_TOGGLE_RETRY_ROUNDS", 3), 0)
WORKER_PLAN_MODE_TOGGLE_RETRY_GAP_SECONDS = max(_env_float("WORKER_PLAN_MODE_TOGGLE_RETRY_GAP_SECONDS", 0.12), 0.0)
WORKER_PLAN_MODE_STATUS_TAIL_LINES = max(_env_int("WORKER_PLAN_MODE_STATUS_TAIL_LINES", 8), 1)
WORKER_PLAN_MODE_STATUS_LINE_RE = re.compile(
    r"\b(?P<mode>plan|default)\s+mode(?:\s*\(shift\+tab\s+to\s+cycle\))?\s*$",
    re.IGNORECASE,
)
WORKER_CREATE_TASK_BUTTON_TEXT = "➕ 创建任务"
# Worker 主菜单 PLAN MODE 状态缓存（按 tmux session 维度）。
WORKER_PLAN_MODE_STATE_CACHE: Dict[str, Literal["on", "off", "unknown"]] = {}


@dataclass
class SessionLiveEntry:
    """描述“会话实况”页中的单个可查看会话。"""

    key: str
    label: str
    tmux_session: str
    kind: Literal["main", "parallel"]
    task_id: Optional[str] = None

COMMAND_EXEC_PREFIX = "cmd:run:"
COMMAND_EXEC_GLOBAL_PREFIX = "cmd_global:run:"
COMMAND_EDIT_PREFIX = "cmd:edit:"
COMMAND_FIELD_PREFIX = "cmd:field:"
COMMAND_TOGGLE_PREFIX = "cmd:toggle:"
COMMAND_NEW_CALLBACK = "cmd:new"
COMMAND_REFRESH_CALLBACK = "cmd:refresh"
COMMAND_HISTORY_CALLBACK = "cmd:history"
COMMAND_HISTORY_DETAIL_PREFIX = "cmd:history_detail:"
COMMAND_HISTORY_DETAIL_GLOBAL_PREFIX = "cmd_global:history_detail:"
COMMAND_READONLY_CALLBACK = "cmd:readonly"
COMMAND_TRIGGER_PREFIXES = ("/", "!", ".")
COMMAND_HISTORY_LIMIT = 8
COMMAND_INLINE_LIMIT = 12
COMMAND_OUTPUT_MAX_CHARS = _env_int("COMMAND_OUTPUT_MAX_CHARS", 3500)
COMMAND_STDERR_MAX_CHARS = _env_int("COMMAND_STDERR_MAX_CHARS", 1200)
COMMAND_OUTPUT_PREVIEW_LINES = _env_int("COMMAND_OUTPUT_PREVIEW_LINES", 5)
WX_PREVIEW_COMMAND_NAME = "wx-dev-preview"
WX_UPLOAD_COMMAND_NAME = "wx-dev-upload"
WX_PREVIEW_CHOICE_PREFIX = "wxpreview:choose:"
WX_PREVIEW_CANCEL = "wxpreview:cancel"
WX_PREVIEW_PORT_USE_PREFIX = "wxpreview:port_use:"
WX_PREVIEW_PORT_CANCEL = "wxpreview:port_cancel"
WX_PREVIEW_PORT_STATE_KEY = "wx_preview_port"
WX_UPLOAD_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


@dataclass
class WxPreviewCandidate:
    """描述扫描到的可用小程序目录。"""

    project_root: Path
    app_dir: Path
    source: Literal["current", "child"]

TASK_ID_VALID_PATTERN = re.compile(r"^TASK_[A-Z0-9_]+$")
TASK_ID_USAGE_TIP = "任务 ID 格式无效，请使用 TASK_0001"


def _worker_plan_mode_cache_key() -> str:
    """返回 PLAN MODE 状态缓存键（按 tmux session 区分）。"""

    key = (TMUX_SESSION or "").strip()
    return key or "__default__"


def _resolve_worker_plan_mode_state_from_output(raw_output: str) -> Literal["on", "off"]:
    """根据 tmux 输出文本解析 Worker 侧 PLAN MODE 状态。"""

    text = normalize_newlines(raw_output or "")
    text = strip_ansi(text)
    lines = [(line or "").strip() for line in text.splitlines() if (line or "").strip()]
    if not lines:
        return "off"

    # 仅扫描尾部若干行，避免把历史消息中的“Plan mode”误判为当前状态。
    tail_lines = lines[-WORKER_PLAN_MODE_STATUS_TAIL_LINES:]
    for line in reversed(tail_lines):
        match = WORKER_PLAN_MODE_STATUS_LINE_RE.search(line)
        if not match:
            continue
        lowered = line.lower()
        starts_with_mode = lowered.startswith("plan mode") or lowered.startswith("default mode")
        has_status_hint = ("·" in line) or bool(re.search(r"\b\d{1,3}%\b", lowered))
        if not (starts_with_mode or has_status_hint):
            continue
        mode = (match.group("mode") or "").strip().lower()
        if mode == "plan":
            return "on"
        return "off"
    return "off"


def _set_worker_plan_mode_state_cache(state: Literal["on", "off", "unknown"]) -> Literal["on", "off", "unknown"]:
    """写入当前 tmux session 的 PLAN MODE 缓存。"""

    normalized: Literal["on", "off", "unknown"]
    if state in {"on", "off", "unknown"}:
        normalized = state
    else:  # pragma: no cover - typing 已兜底，运行时防御
        normalized = "unknown"
    WORKER_PLAN_MODE_STATE_CACHE[_worker_plan_mode_cache_key()] = normalized
    return normalized


def _get_worker_plan_mode_state_cache() -> Optional[Literal["on", "off", "unknown"]]:
    """读取当前 tmux session 的 PLAN MODE 缓存。"""

    cached = WORKER_PLAN_MODE_STATE_CACHE.get(_worker_plan_mode_cache_key())
    if cached in {"on", "off", "unknown"}:
        return cached
    return None


def _refresh_worker_plan_mode_state_cache(*, force_probe: bool = True) -> Literal["on", "off", "unknown"]:
    """刷新 PLAN MODE 缓存；可按需仅返回已缓存状态。"""

    cached = _get_worker_plan_mode_state_cache()
    if cached is not None and not force_probe:
        return cached
    return _set_worker_plan_mode_state_cache(_probe_worker_plan_mode_state())


async def _refresh_worker_plan_mode_state_cache_async(
    *,
    force_probe: bool = True,
) -> Literal["on", "off", "unknown"]:
    """异步刷新 PLAN MODE 缓存，避免阻塞事件循环。"""

    return await asyncio.to_thread(_refresh_worker_plan_mode_state_cache, force_probe=force_probe)


async def _refresh_worker_plan_mode_state_after_toggle_async(
    *,
    before_state: Literal["on", "off", "unknown"],
) -> Literal["on", "off", "unknown"]:
    """切换 PLAN MODE 后短暂轮询，尽可能拿到稳定的新状态。"""

    if WORKER_PLAN_MODE_TOGGLE_STABILIZE_SECONDS > 0:
        await asyncio.sleep(WORKER_PLAN_MODE_TOGGLE_STABILIZE_SECONDS)

    observed_state = await _refresh_worker_plan_mode_state_cache_async(force_probe=True)
    if before_state == "unknown":
        return observed_state
    if observed_state in {"on", "off"} and observed_state != before_state:
        return observed_state

    for _ in range(WORKER_PLAN_MODE_TOGGLE_RETRY_ROUNDS):
        if WORKER_PLAN_MODE_TOGGLE_RETRY_GAP_SECONDS > 0:
            await asyncio.sleep(WORKER_PLAN_MODE_TOGGLE_RETRY_GAP_SECONDS)
        observed_state = await _refresh_worker_plan_mode_state_cache_async(force_probe=True)
        if observed_state in {"on", "off"} and observed_state != before_state:
            return observed_state

    return observed_state


def _probe_worker_plan_mode_state() -> Literal["on", "off", "unknown"]:
    """探测 Worker 主键盘上的 PLAN MODE 状态。"""

    timeout = max(WORKER_PLAN_MODE_PROBE_TIMEOUT_SECONDS, 0.1)
    try:
        raw_output = subprocess.check_output(
            _tmux_cmd(
                tmux_bin(),
                "capture-pane",
                "-p",
                "-t",
                TMUX_SESSION,
                "-S",
                f"-{WORKER_PLAN_MODE_PROBE_LINES}",
            ),
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
        return "unknown"

    # 约定：终端底部仅在 PLAN 模式显示 "Plan mode"；无标识时视为非 PLAN。
    return _resolve_worker_plan_mode_state_from_output(raw_output)


def _build_worker_main_keyboard(
    *,
    plan_mode_state: Optional[Literal["on", "off", "unknown"]] = None,
    refresh_plan_mode_state: bool = True,
) -> ReplyKeyboardMarkup:
    """Worker 端常驻键盘，提供任务列表与 PLAN MODE 切换入口。

    默认每次渲染都会强探测 tmux 中的实时状态，尽可能保证按钮状态正确。
    仅当 refresh_plan_mode_state=False 时，才允许使用显式状态或缓存状态。
    """

    if refresh_plan_mode_state:
        resolved_plan_mode_state = _refresh_worker_plan_mode_state_cache(force_probe=True)
    elif plan_mode_state is not None:
        resolved_plan_mode_state = _set_worker_plan_mode_state_cache(plan_mode_state)
    else:
        resolved_plan_mode_state = _refresh_worker_plan_mode_state_cache(force_probe=False)
    if resolved_plan_mode_state == "on":
        plan_mode_button_text = WORKER_PLAN_MODE_BUTTON_TEXT_ON
    elif resolved_plan_mode_state == "off":
        plan_mode_button_text = WORKER_PLAN_MODE_BUTTON_TEXT_OFF
    else:
        plan_mode_button_text = WORKER_PLAN_MODE_BUTTON_TEXT_UNKNOWN

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=WORKER_MENU_BUTTON_TEXT),
                KeyboardButton(text=WORKER_COMMANDS_BUTTON_TEXT),
            ],
            [
                KeyboardButton(text=WORKER_TERMINAL_SNAPSHOT_BUTTON_TEXT),
                KeyboardButton(text=plan_mode_button_text),
            ]
        ],
        resize_keyboard=True,
    )


def _build_model_quick_reply_keyboard(
    *,
    task_id: Optional[str] = None,
    parallel_task_title: Optional[str] = None,
    enable_parallel_actions: bool = False,
    parallel_callback_payload: Optional[str] = None,
    native_quick_reply_payload: Optional[str] = None,
    native_commit_callback_payload: Optional[str] = None,
) -> InlineKeyboardMarkup:
    """构建“模型答案消息”底部的快捷回复按钮（InlineKeyboard）。"""

    normalized_task_id = _normalize_task_id(task_id) if task_id else None
    all_callback = MODEL_QUICK_REPLY_ALL_CALLBACK
    partial_callback = MODEL_QUICK_REPLY_PARTIAL_CALLBACK
    if enable_parallel_actions and normalized_task_id:
        payload = (parallel_callback_payload or normalized_task_id).strip()
        all_callback = f"{MODEL_QUICK_REPLY_ALL_TASK_PREFIX}{payload}"
        partial_callback = f"{MODEL_QUICK_REPLY_PARTIAL_TASK_PREFIX}{payload}"
    elif normalized_task_id and native_quick_reply_payload:
        payload = native_quick_reply_payload.strip()
        all_callback = f"{MODEL_QUICK_REPLY_ALL_SESSION_PREFIX}{payload}"
        partial_callback = f"{MODEL_QUICK_REPLY_PARTIAL_SESSION_PREFIX}{payload}"

    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="✅ 全部按推荐", callback_data=all_callback),
            InlineKeyboardButton(text="🧩 部分按推荐（需补充）", callback_data=partial_callback),
        ]
    ]
    if enable_parallel_actions and normalized_task_id:
        title_text = (parallel_task_title or normalized_task_id).strip() or normalized_task_id
        rows.append(
            [
                InlineKeyboardButton(
                    text="⬆️ 提交并行分支",
                    callback_data=f"{PARALLEL_COMMIT_CALLBACK_PREFIX}{normalized_task_id}",
                ),
                InlineKeyboardButton(
                    text="🧪 任务状态更新为测试中",
                    callback_data=f"{MODEL_TASK_TO_TEST_PREFIX}{normalized_task_id}",
                ),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🏷 { _format_task_command(normalized_task_id) } {title_text}".strip()[:64],
                    callback_data=f"task:detail:{normalized_task_id}",
                ),
                InlineKeyboardButton(
                    text=f"↩️ 回复 { _format_task_command(normalized_task_id) }".strip()[:40],
                    callback_data=f"{PARALLEL_REPLY_CALLBACK_PREFIX}{(parallel_callback_payload or normalized_task_id).strip()}",
                ),
            ]
        )
    elif normalized_task_id and native_commit_callback_payload:
        rows.append(
            [
                InlineKeyboardButton(
                    text="⬆️ 提交分支",
                    callback_data=f"{SESSION_COMMIT_CALLBACK_PREFIX}{native_commit_callback_payload.strip()}",
                ),
                InlineKeyboardButton(
                    text="🧪 任务状态更新为测试中",
                    callback_data=f"{MODEL_TASK_TO_TEST_PREFIX}{normalized_task_id}",
                ),
            ]
        )
    elif normalized_task_id:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🧪 任务状态更新为测试中",
                    callback_data=f"{MODEL_TASK_TO_TEST_PREFIX}{normalized_task_id}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _remove_inline_button_from_markup(
    reply_markup: Any,
    *,
    callback_data: Optional[str],
) -> tuple[Optional[InlineKeyboardMarkup], bool]:
    """从 inline keyboard 中精确移除一个 callback 对应的按钮。"""

    normalized_callback = (callback_data or "").strip()
    if not normalized_callback or not isinstance(reply_markup, InlineKeyboardMarkup):
        return reply_markup if isinstance(reply_markup, InlineKeyboardMarkup) else None, False

    rows: list[list[InlineKeyboardButton]] = []
    removed = False
    for row in reply_markup.inline_keyboard:
        next_row: list[InlineKeyboardButton] = []
        for button in row:
            if not removed and (getattr(button, "callback_data", None) or "").strip() == normalized_callback:
                removed = True
                continue
            next_row.append(button)
        if next_row:
            rows.append(next_row)

    if not removed:
        return reply_markup, False
    if not rows:
        return None, True
    return InlineKeyboardMarkup(inline_keyboard=rows), True


async def _try_remove_clicked_inline_button(
    message: Optional[Message],
    *,
    callback_data: Optional[str],
) -> bool:
    """在业务成功后，尝试从原消息中仅移除当前点击的那个 inline 按钮。"""

    if message is None:
        return False
    edit_reply_markup = getattr(message, "edit_reply_markup", None)
    if edit_reply_markup is None:
        return False

    current_markup = getattr(message, "reply_markup", None)
    next_markup, changed = _remove_inline_button_from_markup(
        current_markup,
        callback_data=callback_data,
    )
    if not changed:
        return False

    try:
        await edit_reply_markup(reply_markup=next_markup)
    except (TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter) as exc:
        worker_log.warning(
            "业务成功后移除已点击按钮失败：%s",
            exc,
            extra={"callback_data": str(callback_data or "")},
        )
        return False
    return True


def _build_parallel_post_commit_keyboard(task_id: str, *, can_merge: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if can_merge:
        rows.append(
            [
                InlineKeyboardButton(text="🔀 尝试自动合并", callback_data=f"{PARALLEL_MERGE_CALLBACK_PREFIX}{task_id}"),
                InlineKeyboardButton(text="🗑️ 删除并行目录", callback_data=f"{PARALLEL_DELETE_CALLBACK_PREFIX}{task_id}"),
            ]
        )
        rows.append([InlineKeyboardButton(text="稍后再说", callback_data=f"{PARALLEL_MERGE_SKIP_CALLBACK_PREFIX}{task_id}")])
    else:
        rows.append([InlineKeyboardButton(text="🗑️ 删除并行目录", callback_data=f"{PARALLEL_DELETE_CALLBACK_PREFIX}{task_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_parallel_post_merge_keyboard(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🗑️ 删除并行目录", callback_data=f"{PARALLEL_DELETE_CALLBACK_PREFIX}{task_id}"),
                InlineKeyboardButton(text="保留目录", callback_data=f"{PARALLEL_MERGE_SKIP_CALLBACK_PREFIX}{task_id}"),
            ]
        ]
    )


def _build_parallel_delete_confirm_keyboard(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ 确认删除并行目录", callback_data=f"{PARALLEL_DELETE_CONFIRM_CALLBACK_PREFIX}{task_id}"),
            ],
            [
                InlineKeyboardButton(text="❌ 取消", callback_data=f"{PARALLEL_DELETE_CANCEL_CALLBACK_PREFIX}{task_id}"),
            ],
        ]
    )


def _build_command_edit_cancel_keyboard() -> ReplyKeyboardMarkup:
    """命令编辑输入阶段的取消按钮键盘。"""

    rows = [[KeyboardButton(text="取消")]]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


def _is_global_command(command: CommandDefinition) -> bool:
    """判断命令是否来源于 master 通用配置。"""

    return (command.scope or "project") == GLOBAL_COMMAND_SCOPE


async def _list_combined_commands() -> List[CommandDefinition]:
    """合并项目命令与通用命令，并按类型+标题排序。"""

    project_commands = await COMMAND_SERVICE.list_commands()
    global_commands = await GLOBAL_COMMAND_SERVICE.list_commands()

    def _sort_key(item: CommandDefinition) -> tuple[int, str, str]:
        scope_rank = 0 if _is_global_command(item) else 1
        title_key = (item.title or item.name or "").casefold()
        name_key = (item.name or "").casefold()
        return (scope_rank, title_key, name_key)

    combined = sorted([*project_commands, *global_commands], key=_sort_key)
    return combined


async def _resolve_global_command_conflict(identifier: str) -> Optional[CommandDefinition]:
    """查询指定名称/别名是否与通用命令冲突。"""

    candidate = (identifier or "").strip()
    if not candidate:
        return None
    return await GLOBAL_COMMAND_SERVICE.resolve_by_trigger(candidate)


def _command_alias_label(aliases: Sequence[str]) -> str:
    """格式化别名文本。"""

    if not aliases:
        return "-"
    return ", ".join(f"`{_escape_markdown_text(alias)}`" for alias in aliases)


async def _build_command_overview_view(
    notice: Optional[str] = None,
) -> tuple[str, InlineKeyboardMarkup]:
    """渲染命令列表及配套按钮。"""

    commands = await _list_combined_commands()
    project_count = sum(1 for item in commands if not _is_global_command(item))
    global_count = len(commands) - project_count
    lines = [
        "*命令管理*",
        f"项目：`{_escape_markdown_text(PROJECT_SLUG)}`",
        f"命令数量：{len(commands)}（项目 {project_count} / 通用 {global_count}）",
        "可直接点击下方按钮执行或编辑，每条命令详情已隐藏以便快速操作。",
        "",
    ]
    if not commands:
        lines.append("暂无命令，点击下方“🆕 新增命令”即可录入。")
    if notice:
        lines.append(f"_提示：{_escape_markdown_text(notice)}_")
    markup = _build_command_overview_keyboard(commands)
    return "\n".join(lines).rstrip(), markup


def _build_command_overview_keyboard(commands: Sequence[CommandDefinition]) -> InlineKeyboardMarkup:
    """根据命令数量构造操作面板。"""

    inline_keyboard: list[list[InlineKeyboardButton]] = []
    for command in commands[:COMMAND_INLINE_LIMIT]:
        exec_prefix = COMMAND_EXEC_GLOBAL_PREFIX if _is_global_command(command) else COMMAND_EXEC_PREFIX
        edit_button: InlineKeyboardButton
        if _is_global_command(command):
            edit_button = InlineKeyboardButton(text="🔒 仅 master 可改", callback_data=COMMAND_READONLY_CALLBACK)
        else:
            edit_button = InlineKeyboardButton(text="✏️ 编辑", callback_data=f"{COMMAND_EDIT_PREFIX}{command.id}")
        inline_keyboard.append(
            [
                InlineKeyboardButton(
                    text=f"▶️ {command.name}",
                    callback_data=f"{exec_prefix}{command.id}",
                ),
                edit_button,
            ]
        )
    inline_keyboard.append([InlineKeyboardButton(text="🆕 新增命令", callback_data=COMMAND_NEW_CALLBACK)])
    inline_keyboard.append([InlineKeyboardButton(text="🧾 最近执行", callback_data=COMMAND_HISTORY_CALLBACK)])
    return InlineKeyboardMarkup(inline_keyboard=inline_keyboard)


def _build_command_edit_keyboard(command: CommandDefinition) -> InlineKeyboardMarkup:
    """编辑面板。"""

    toggle_label = "⏸ 停用" if command.enabled else "▶️ 启用"
    inline_keyboard = [
        [
            InlineKeyboardButton(text="📝 标题", callback_data=f"{COMMAND_FIELD_PREFIX}title:{command.id}"),
            InlineKeyboardButton(text="💻 指令", callback_data=f"{COMMAND_FIELD_PREFIX}command:{command.id}"),
        ],
        [
            InlineKeyboardButton(text="📛 描述", callback_data=f"{COMMAND_FIELD_PREFIX}description:{command.id}"),
            InlineKeyboardButton(text="⏱ 超时", callback_data=f"{COMMAND_FIELD_PREFIX}timeout:{command.id}"),
        ],
        [InlineKeyboardButton(text="🔁 别名", callback_data=f"{COMMAND_FIELD_PREFIX}aliases:{command.id}")],
        [InlineKeyboardButton(text=toggle_label, callback_data=f"{COMMAND_TOGGLE_PREFIX}{command.id}")],
        [InlineKeyboardButton(text="⬅️ 返回列表", callback_data=COMMAND_REFRESH_CALLBACK)],
    ]
    return InlineKeyboardMarkup(inline_keyboard=inline_keyboard)


def _read_miniprogram_root_from_config(config_path: Path) -> Optional[Path]:
    """读取 project.config.json 中的 miniprogramRoot 并验证 app.json 存在。"""

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    root = data.get("miniprogramRoot")
    if not isinstance(root, str) or not root.strip():
        return None
    candidate = (config_path.parent / root.strip()).resolve()
    app_json = candidate / "app.json"
    if candidate.is_dir() and app_json.is_file():
        return candidate
    return None


def _resolve_miniprogram_app_dir(project_root: Path) -> Optional[Path]:
    """判断目录是否为有效小程序根（含 app.json 或有效 miniprogramRoot）。"""

    app_json = project_root / "app.json"
    if app_json.is_file():
        return app_json.parent
    config_path = project_root / "project.config.json"
    if config_path.is_file():
        resolved = _read_miniprogram_root_from_config(config_path)
        if resolved is not None:
            return resolved
    return None


def _detect_wx_preview_candidates(base: Path) -> List[WxPreviewCandidate]:
    """扫描当前目录与一层子目录，找出包含 app.json 的项目根。"""

    candidates: List[WxPreviewCandidate] = []
    seen: set[str] = set()
    owned_app_dirs: set[str] = set()
    base_resolved = base.resolve()

    def _add(project_root: Path, app_dir: Path, source: Literal["current", "child"]) -> None:
        key = str(project_root.resolve())
        if key in seen:
            return
        seen.add(key)
        candidates.append(
            WxPreviewCandidate(
                project_root=project_root.resolve(),
                app_dir=app_dir.resolve(),
                source=source,
            )
        )

    app_dir = _resolve_miniprogram_app_dir(base_resolved)
    if app_dir:
        owned_app_dirs.add(str(app_dir.resolve()))
        _add(base_resolved, app_dir, "current")

    with suppress(FileNotFoundError, PermissionError):
        for child in sorted(base_resolved.iterdir()):
            if not child.is_dir():
                continue
            app_dir = _resolve_miniprogram_app_dir(child)
            if app_dir:
                if str(app_dir.resolve()) in owned_app_dirs:
                    continue
                _add(child, app_dir, "child")

    return candidates


def _default_wx_preview_output_dir() -> Path:
    """匹配脚本逻辑的默认输出目录。"""

    home = os.environ.get("HOME")
    if home and Path(home).is_dir():
        return Path(home) / "Downloads"
    return Path("/tmp/Downloads")


def _build_wx_preview_prompt(
    base: Path,
    candidates: Sequence[WxPreviewCandidate],
    *,
    command_name: str = WX_PREVIEW_COMMAND_NAME,
    version_override: Optional[str] = None,
) -> str:
    """渲染微信开发命令候选目录提示文案。"""

    ports_file = CONFIG_DIR_PATH / "wx_devtools_ports.json"
    if command_name == WX_UPLOAD_COMMAND_NAME:
        lines = [
            "*请选择要上传代码的小程序目录*",
            f"扫描范围：当前目录及一层子目录（基准：`{_escape_markdown_text(str(base))}`）",
            f"端口配置文件：`{_escape_markdown_text(str(ports_file))}`（未配置将无法执行）",
            (
                f"上传版本号：`{_escape_markdown_text(version_override)}`（来自命令参数）"
                if version_override
                else "上传版本号：默认使用时间戳；可通过 `wx-dev-upload --version <版本号>` 覆盖。"
            ),
            "",
            "候选目录：",
        ]
    else:
        output_dir = _default_wx_preview_output_dir()
        sample_file = output_dir / f"wx-preview-{int(time.time())}.jpg"
        lines = [
            "*请选择要生成预览的小程序目录*",
            f"扫描范围：当前目录及一层子目录（基准：`{_escape_markdown_text(str(base))}`）",
            f"默认输出目录：`{_escape_markdown_text(str(output_dir))}`",
            f"输出文件示例：`{_escape_markdown_text(str(sample_file))}`",
            f"端口配置文件：`{_escape_markdown_text(str(ports_file))}`（未配置将无法执行）",
            "",
            "候选目录：",
        ]
    for idx, candidate in enumerate(candidates, start=1):
        label = "当前目录" if candidate.source == "current" else candidate.project_root.name
        lines.append(
            f"{idx}. {label} → `{_escape_markdown_text(str(candidate.project_root))}`"
            f"（app.json：`{_escape_markdown_text(str(candidate.app_dir))}`）"
        )
    if command_name == WX_UPLOAD_COMMAND_NAME:
        lines.append("_请选择其一后执行上传（二维码请在微信后台查看）。_")
    else:
        lines.append("_请选择其一或取消。_")
    return "\n".join(lines)


def _build_wx_preview_keyboard(candidates: Sequence[WxPreviewCandidate]) -> InlineKeyboardMarkup:
    """为 wx-dev-preview 生成目录选择按钮。"""

    inline_keyboard: list[list[InlineKeyboardButton]] = []
    for idx, candidate in enumerate(candidates, start=1):
        label = "当前目录" if candidate.source == "current" else candidate.project_root.name
        inline_keyboard.append(
            [
                InlineKeyboardButton(
                    text=f"{idx}. {label}",
                    callback_data=f"{WX_PREVIEW_CHOICE_PREFIX}{idx - 1}",
                )
            ]
        )
    inline_keyboard.append([InlineKeyboardButton(text="❌ 取消", callback_data=WX_PREVIEW_CANCEL)])
    return InlineKeyboardMarkup(inline_keyboard=inline_keyboard)


def _wrap_wx_preview_command(command: CommandDefinition, project_root: Path) -> CommandDefinition:
    """为微信开发命令注入 PROJECT_PATH/PROJECT_BASE。"""

    quoted_root = shlex.quote(str(project_root))
    return CommandDefinition(
        id=command.id,
        project_slug=command.project_slug,
        name=command.name,
        title=command.title,
        command=f"PROJECT_PATH={quoted_root} PROJECT_BASE={quoted_root} {command.command}",
        scope=command.scope,
        description=command.description,
        timeout=command.timeout,
        enabled=command.enabled,
        created_at=command.created_at,
        updated_at=command.updated_at,
        aliases=command.aliases,
    )


def _is_wx_devtools_command(command_name: Optional[str]) -> bool:
    """判断是否为微信开发者工具相关命令。"""

    return command_name in {WX_PREVIEW_COMMAND_NAME, WX_UPLOAD_COMMAND_NAME}


def _collect_wx_command_env_overrides(command_text: str) -> Dict[str, str]:
    """从命令字符串中提取可透传的环境变量覆盖项。"""

    overrides: Dict[str, str] = {}
    for key in ("VERSION",):
        value = _extract_shell_env_value(command_text, key)
        if value is None:
            continue
        cleaned = value.strip()
        if cleaned:
            overrides[key] = cleaned
    return overrides


def _apply_command_env_overrides(command: CommandDefinition, env_overrides: Optional[Dict[str, str]]) -> CommandDefinition:
    """为命令注入额外环境变量（仅本次执行生效）。"""

    if not env_overrides:
        return command
    safe_items: list[tuple[str, str]] = []
    for key, value in env_overrides.items():
        key_text = (key or "").strip()
        value_text = (value or "").strip()
        if not key_text or not value_text:
            continue
        safe_items.append((key_text, value_text))
    if not safe_items:
        return command
    prefix = " ".join(f"{key}={shlex.quote(value)}" for key, value in safe_items)
    return CommandDefinition(
        id=command.id,
        project_slug=command.project_slug,
        name=command.name,
        title=command.title,
        command=f"{prefix} {command.command}",
        scope=command.scope,
        description=command.description,
        timeout=command.timeout,
        enabled=command.enabled,
        created_at=command.created_at,
        updated_at=command.updated_at,
        aliases=command.aliases,
    )


def _parse_numeric_port(text: str) -> Optional[int]:
    """将用户输入解析为端口号（1-65535），非法则返回 None。"""

    raw = (text or "").strip()
    if not raw.isdigit():
        return None
    try:
        port = int(raw)
    except ValueError:
        return None
    if 1 <= port <= 65535:
        return port
    return None


def _is_wx_preview_missing_port_error(exit_code: Optional[int], stderr_text: str) -> bool:
    """判断是否为 wx-dev-preview 缺少端口配置导致的可恢复错误。"""

    if exit_code != 2:
        return False
    if not stderr_text:
        return False
    # scripts/gen_preview.sh / scripts/wx_preview.sh 的统一错误前缀
    return "未配置微信开发者工具 IDE 服务端口" in stderr_text


_WX_PREVIEW_PORT_MISMATCH_RE = re.compile(
    r"IDE server has started on https?://[^:\s]+:(\d+)\s+and must be restarted on port\s+(\d+)\s+first",
    re.IGNORECASE,
)
_WX_PREVIEW_PROJECT_ROOT_PATTERNS = (
    # 从 wx-dev-preview 的输出中提取实际小程序目录
    re.compile(r"\[信息\]\s*生成预览，项目：(?P<path>[^，\n]+)", flags=re.MULTILINE),
    re.compile(r"\[信息\]\s*执行上传，项目：(?P<path>[^，\n]+)", flags=re.MULTILINE),
    re.compile(r"小程序目录：(?P<path>[^\n]+)", flags=re.MULTILINE),
    re.compile(r"项目目录：(?P<path>[^\n]+)", flags=re.MULTILINE),
)


def _parse_wx_preview_port_mismatch(stderr_text: str) -> tuple[Optional[int], Optional[int]]:
    """从微信开发者工具 CLI 的“端口不匹配”报错中解析（当前端口，期望端口）。"""

    if not stderr_text:
        return None, None
    match = _WX_PREVIEW_PORT_MISMATCH_RE.search(stderr_text)
    if not match:
        return None, None
    try:
        current_port = int(match.group(1))
        expected_port = int(match.group(2))
    except (TypeError, ValueError):
        return None, None
    if not (1 <= current_port <= 65535 and 1 <= expected_port <= 65535):
        return None, None
    return current_port, expected_port


def _extract_wx_preview_project_root(stdout_text: str, stderr_text: str) -> Optional[Path]:
    """从 wx-dev-preview 输出中解析小程序目录路径。"""

    for source in (stdout_text, stderr_text):
        if not source:
            continue
        for pattern in _WX_PREVIEW_PROJECT_ROOT_PATTERNS:
            match = pattern.search(source)
            if not match:
                continue
            raw_path = (match.group("path") or "").strip()
            if not raw_path:
                continue
            # 清理可能的引号或标点，避免路径解析失败
            cleaned = raw_path.strip().strip("\"'").rstrip("，,").strip()
            if not cleaned:
                continue
            candidate = Path(cleaned).expanduser()
            try:
                if candidate.is_dir():
                    return candidate.resolve()
            except OSError:
                continue
    return None


def _is_wx_preview_port_mismatch_error(exit_code: Optional[int], stderr_text: str) -> bool:
    """判断是否为 wx-dev-preview 端口不匹配导致的可恢复错误。"""

    if exit_code is None or exit_code == 0:
        return False
    current_port, expected_port = _parse_wx_preview_port_mismatch(stderr_text)
    return current_port is not None and expected_port is not None


def _extract_shell_env_value(command_text: str, key: str) -> Optional[str]:
    """从 shell 命令字符串中提取形如 KEY=... 的首个赋值。"""

    if not command_text or not key:
        return None
    try:
        tokens = shlex.split(command_text, posix=True)
    except ValueError:
        tokens = command_text.split()
    prefix = f"{key}="
    for token in tokens:
        if token.startswith(prefix):
            return token[len(prefix) :]
    return None


def _detect_wechat_devtools_security_settings() -> tuple[Optional[int], Optional[bool], Optional[Path]]:
    """从微信开发者工具本地配置读取服务端口与开关（macOS）。"""

    support_dir = Path.home() / "Library" / "Application Support"
    candidates: list[Path] = []
    # 常见目录名：微信开发者工具（当前版本）/ 微信web开发者工具（旧版本）
    for product_name in ("微信开发者工具", "微信web开发者工具"):
        base = support_dir / product_name
        if not base.is_dir():
            continue
        candidates.extend(
            base.glob("*/WeappLocalData/localstorage_b72da75d79277d2f5f9c30c9177be57e.json")
        )
    if not candidates:
        return None, None, None

    # 以 mtime 倒序，优先读取最近使用的配置
    candidates.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    for path in candidates:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        security = payload.get("security") or {}
        if not isinstance(security, dict):
            continue
        enabled = security.get("enableServicePort")
        enabled_flag: Optional[bool] = enabled if isinstance(enabled, bool) else None
        raw_port = security.get("port")
        port: Optional[int] = None
        if isinstance(raw_port, int):
            port = raw_port
        elif isinstance(raw_port, str) and raw_port.strip().isdigit():
            port = int(raw_port.strip())
        if port is not None and 1 <= port <= 65535:
            return port, enabled_flag, path
        # 即使端口缺失，也返回开关状态（便于提示用户去开启）
        if enabled_flag is not None:
            return None, enabled_flag, path
    return None, None, None


def _detect_wechat_devtools_listen_ports(timeout: float = 1.0) -> list[int]:
    """尝试从本机监听端口中推断微信开发者工具正在使用的端口（macOS 优先）。"""

    if shutil.which("lsof") is None:
        return []
    try:
        proc = subprocess.run(
            ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    ports: set[int] = set()
    for line in (proc.stdout or "").splitlines():
        if not line or line.startswith("COMMAND"):
            continue
        # 第一列是进程名（无空格）
        cmd = line.split(None, 1)[0]
        if cmd not in {"wechatwebdevtools", "wechatdevtools"}:
            continue
        match = re.search(r":(\\d+)\\s*\\(LISTEN\\)\\s*$", line)
        if not match:
            continue
        try:
            port = int(match.group(1))
        except ValueError:
            continue
        if 1 <= port <= 65535:
            ports.add(port)
    return sorted(ports)


def _suggest_wx_devtools_ports() -> tuple[list[int], Optional[bool], Optional[Path]]:
    """综合本地配置与监听端口，输出候选端口列表。"""

    listen_ports = _detect_wechat_devtools_listen_ports()
    config_port, enabled_flag, config_path = _detect_wechat_devtools_security_settings()

    candidates: list[int] = []
    if config_port is not None:
        candidates.append(config_port)
    for port in listen_ports:
        if port not in candidates:
            candidates.append(port)
    return candidates, enabled_flag, config_path


def _upsert_wx_devtools_ports_file(
    *,
    ports_file: Path,
    project_slug: str,
    project_root: Optional[Path],
    port: int,
) -> None:
    """写入 wx_devtools_ports.json（同时写 projects 与 paths）。"""

    ports_file.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {"projects": {}, "paths": {}}
    try:
        if ports_file.is_file():
            raw = json.loads(ports_file.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                if "projects" in raw or "paths" in raw:
                    payload["projects"] = raw.get("projects") if isinstance(raw.get("projects"), dict) else {}
                    payload["paths"] = raw.get("paths") if isinstance(raw.get("paths"), dict) else {}
                else:
                    # 兼容旧格式：{"my-project": 12605}
                    payload["projects"] = raw
    except (OSError, json.JSONDecodeError):
        # 解析失败则直接重建，避免卡死在坏配置
        payload = {"projects": {}, "paths": {}}

    projects = payload.get("projects")
    paths = payload.get("paths")
    if not isinstance(projects, dict):
        projects = {}
        payload["projects"] = projects
    if not isinstance(paths, dict):
        paths = {}
        payload["paths"] = paths

    if project_slug:
        projects[project_slug] = port
    if project_root is not None:
        try:
            paths[str(project_root.resolve())] = port
        except OSError:
            paths[str(project_root)] = port

    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp_path = ports_file.with_name(f"{ports_file.name}.tmp.{uuid.uuid4().hex}")
    tmp_path.write_text(serialized, encoding="utf-8")
    tmp_path.replace(ports_file)


def _is_cancel_text(text: str) -> bool:
    """判断输入是否代表取消。"""

    normalized = (text or "").strip().lower()
    return normalized in {"取消", "cancel", "quit", "退出"}


def _parse_alias_input(text: str) -> List[str]:
    """将用户输入解析为别名列表。"""

    sanitized = (text or "").replace("，", ",").strip()
    if not sanitized or sanitized == "-":
        return []
    parts = re.split(r"[,\s]+", sanitized)
    return [part for part in parts if part]


def _extract_command_trigger(prompt: str) -> Optional[str]:
    """提取以限定前缀开头的触发词。"""

    if not prompt or prompt[0] not in COMMAND_TRIGGER_PREFIXES:
        return None
    token = prompt[1:].strip()
    if not token:
        return None
    parts = token.split(maxsplit=1)
    trigger = parts[0].strip()
    if not trigger:
        return None
    return trigger


def _extract_command_args(prompt: str) -> str:
    """提取命令触发词后的原始参数文本。"""

    if not prompt or prompt[0] not in COMMAND_TRIGGER_PREFIXES:
        return ""
    token = prompt[1:].strip()
    if not token:
        return ""
    parts = token.split(maxsplit=1)
    if len(parts) < 2:
        return ""
    return parts[1].strip()


def _parse_wx_upload_args(args_text: str) -> tuple[Optional[str], Optional[str]]:
    """解析 wx-dev-upload 参数，仅支持 `--version <版本号>`。"""

    raw = (args_text or "").strip()
    if not raw:
        return None, None
    try:
        tokens = shlex.split(raw, posix=True)
    except ValueError:
        return None, "参数解析失败，请检查引号是否闭合。"

    version_override: Optional[str] = None
    idx = 0
    while idx < len(tokens):
        token = (tokens[idx] or "").strip()
        if token == "--version":
            if version_override is not None:
                return None, "参数 `--version` 重复，请仅保留一个。"
            if idx + 1 >= len(tokens):
                return None, "参数 `--version` 缺少值，请使用 `--version <版本号>`。"
            candidate = (tokens[idx + 1] or "").strip()
            if not candidate:
                return None, "参数 `--version` 值不能为空。"
            if not WX_UPLOAD_VERSION_PATTERN.match(candidate):
                return None, "版本号仅支持字母、数字、点、下划线、中划线，长度 1-64。"
            version_override = candidate
            idx += 2
            continue
        return None, f"不支持的参数：`{_escape_markdown_text(token)}`。仅支持 `--version <版本号>`。"

    return version_override, None


def _limit_text(text: str, limit: int) -> tuple[str, bool]:
    """截断文本并返回是否发生截断。"""

    if len(text) <= limit:
        return text, False
    return text[:limit].rstrip() + "\n…<截断>", True


def _tail_lines(text: str, max_lines: int) -> str:
    """返回文本末尾指定行数，避免预览过长。"""

    if max_lines <= 0 or not text:
        return text.strip()
    lines = text.splitlines()
    tail = lines[-max_lines:]
    return "\n".join(tail).strip()


def _command_actor_meta(user: Optional[User]) -> tuple[Optional[int], Optional[str], Optional[str]]:
    """抽取执行者的关键信息。"""

    if user is None:
        return None, None, None
    username = user.username or None
    return user.id, username, user.full_name or username


def _extract_command_id(data: Optional[str], prefix: str) -> Optional[int]:
    """从 callback data 中提取命令 ID。"""

    if not data or not data.startswith(prefix):
        return None
    suffix = data[len(prefix) :]
    return int(suffix) if suffix.isdigit() else None


class CommandExecutionTimeout(RuntimeError):
    """命令执行超时。"""


def _command_workdir() -> Path:
    """返回命令执行目录。"""

    return PRIMARY_WORKDIR or ROOT_DIR_PATH


async def _run_shell_command(command_text: str, timeout: int) -> tuple[int, str, str, float]:
    """在受控环境中执行 shell 命令。"""

    workdir = _command_workdir()
    start = time.monotonic()
    process = await asyncio.create_subprocess_shell(
        command_text,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(workdir),
        env=os.environ.copy(),
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        process.kill()
        with suppress(ProcessLookupError):
            await process.wait()
        raise CommandExecutionTimeout("命令执行超时") from exc
    duration = time.monotonic() - start
    stdout_text = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
    stderr_text = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
    return process.returncode or 0, stdout_text, stderr_text, duration


async def _maybe_handle_wx_preview(
    *,
    command: CommandDefinition,
    reply_message: Optional[Message],
    trigger: Optional[str],
    actor_user: Optional[User],
    service: CommandService,
    history_detail_prefix: str,
    fsm_state: Optional[FSMContext],
    env_overrides: Optional[Dict[str, str]] = None,
) -> bool:
    """对微信开发命令进行目录扫描与 FSM 选择。"""

    if not _is_wx_devtools_command(command.name):
        return False
    if fsm_state is None or reply_message is None:
        return False

    base_dir = _command_workdir()
    candidates = _detect_wx_preview_candidates(base_dir)
    if not candidates:
        text = (
            "未在当前目录或一层子目录发现包含 app.json 的小程序项目。\n"
            f"基准目录：`{_escape_markdown_text(str(base_dir))}`\n"
            "请切换到正确目录，或手动设置 PROJECT_PATH/PROJECT_HINT 后重试。"
        )
        await _answer_with_markdown(reply_message, text)
        return True

    await fsm_state.clear()
    await fsm_state.set_state(WxPreviewStates.waiting_choice)
    await fsm_state.update_data(
        wx_preview={
            "command_id": command.id,
            "scope": command.scope,
            "history_prefix": history_detail_prefix,
            "trigger": trigger,
            "command_name": command.name,
            "env_overrides": env_overrides or {},
            "candidates": [
                {
                    "project_root": str(item.project_root),
                    "app_dir": str(item.app_dir),
                    "source": item.source,
                }
                for item in candidates
            ],
        }
    )

    version_override = (env_overrides or {}).get("VERSION")
    prompt = _build_wx_preview_prompt(
        base_dir,
        candidates,
        command_name=command.name,
        version_override=version_override,
    )
    markup = _build_wx_preview_keyboard(candidates)
    await _answer_with_markdown(reply_message, prompt, reply_markup=markup)
    return True


async def _execute_command_definition(
    *,
    command: CommandDefinition,
    reply_message: Optional[Message],
    trigger: Optional[str],
    actor_user: Optional[User],
    service: CommandService,
    history_detail_prefix: str,
    fsm_state: Optional[FSMContext] = None,
) -> None:
    """执行命令并推送结果，记录审计日志。"""

    if not command.enabled:
        text = f"命令 `{_escape_markdown_text(command.name)}` 已停用，请先在“命令管理”中启用。"
        await _answer_with_markdown(reply_message, text)
        return

    actor_id, actor_username, actor_name = _command_actor_meta(actor_user)
    started_at = shanghai_now_iso()
    display_name = command.title or command.name
    if reply_message is not None:
        progress_lines = [
            "*命令执行中*",
            f"标题：`{_escape_markdown_text(display_name)}`",
            f"开始时间：{started_at}",
            "_执行完成后将自动推送摘要与详情入口_",
        ]
        await _answer_with_markdown(reply_message, "\n".join(progress_lines))
    stdout_text = ""
    stderr_text = ""
    exit_code: Optional[int] = None
    duration = 0.0
    status = "success"
    try:
        exit_code, stdout_text, stderr_text, duration = await _run_shell_command(command.command, command.timeout)
        status = "success" if exit_code == 0 else "failed"
    except CommandExecutionTimeout:
        status = "timeout"
        stderr_text = f"命令在 {command.timeout} 秒内未完成，已强制终止。"
    except Exception as exc:
        status = "error"
        stderr_text = f"执行失败：{exc}"
        worker_log.exception(
            "命令执行异常：%s",
            exc,
            extra={**_session_extra(), "command": command.name},
        )
    finished_at = shanghai_now_iso()
    history_record = await service.record_history(
        command.id,
        trigger=trigger,
        actor_id=actor_id,
        actor_username=actor_username,
        actor_name=actor_name,
        exit_code=exit_code,
        status=status,
        output=stdout_text or None,
        error=stderr_text or None,
        started_at=started_at,
        finished_at=finished_at,
    )

    # 先记录图片路径，待摘要消息发送完成后再作为最后一条消息回传到 Telegram。
    photo_path: Optional[Path] = None
    if reply_message is not None and stdout_text:
        photo_match = re.search(r"^TG_PHOTO_FILE:\s*(.+)$", stdout_text, flags=re.MULTILINE)
        if photo_match:
            candidate = Path(photo_match.group(1).strip())
            if candidate.is_file():
                photo_path = candidate

    status_label = {
        "success": "✅ 成功",
        "failed": "⚠️ 失败",
        "timeout": "⏰ 超时",
        "error": "❌ 异常",
    }.get(status, status)
    lines = [
        "*命令执行结果*",
        f"标题：`{_escape_markdown_text(display_name)}`",
        f"触发：{_escape_markdown_text(trigger or '按钮')}",
        f"开始：{started_at}",
        f"完成：{finished_at}",
        f"耗时：{duration:.2f}s / 超时：{command.timeout}s",
        f"状态：{status_label}",
    ]
    if exit_code is not None:
        lines.append(f"退出码：{exit_code}")
    if stdout_text:
        stdout_preview = _tail_lines(stdout_text.strip(), COMMAND_OUTPUT_PREVIEW_LINES)
        truncated_stdout, stdout_truncated = _limit_text(stdout_preview, COMMAND_OUTPUT_MAX_CHARS)
        stdout_block, _ = _wrap_text_in_code_block(truncated_stdout or "-")
        lines.append(f"标准输出摘要（末尾 {COMMAND_OUTPUT_PREVIEW_LINES} 行）：")
        lines.append(stdout_block)
        if stdout_truncated:
            lines.append("_输出已截断_")
    if stderr_text:
        stderr_preview = _tail_lines(stderr_text.strip(), COMMAND_OUTPUT_PREVIEW_LINES)
        truncated_stderr, stderr_truncated = _limit_text(stderr_preview, COMMAND_STDERR_MAX_CHARS)
        stderr_block, _ = _wrap_text_in_code_block(truncated_stderr or "-")
        lines.append(f"标准错误摘要（末尾 {COMMAND_OUTPUT_PREVIEW_LINES} 行）：")
        lines.append(stderr_block)
        if stderr_truncated:
            lines.append("_错误输出已截断_")

    wx_port_keyboard_rows: list[list[InlineKeyboardButton]] = []
    if (
        _is_wx_devtools_command(command.name)
        and (
            _is_wx_preview_missing_port_error(exit_code, stderr_text)
            or _is_wx_preview_port_mismatch_error(exit_code, stderr_text)
        )
        and fsm_state is not None
        and reply_message is not None
    ):
        mismatch_current_port, mismatch_expected_port = _parse_wx_preview_port_mismatch(stderr_text)
        suggested_ports, enabled_flag, config_path = _suggest_wx_devtools_ports()
        if mismatch_current_port is not None and mismatch_current_port not in suggested_ports:
            suggested_ports = [mismatch_current_port, *suggested_ports]
        ports_file = CONFIG_DIR_PATH / "wx_devtools_ports.json"
        # 优先从脚本输出解析实际目录，避免写入错误路径
        project_root = _extract_wx_preview_project_root(stdout_text, stderr_text)
        if project_root is None:
            raw_project_root = _extract_shell_env_value(command.command, "PROJECT_PATH") or _extract_shell_env_value(
                command.command, "PROJECT_BASE"
            )
            project_root = Path(raw_project_root).expanduser() if raw_project_root else None
        env_overrides = _collect_wx_command_env_overrides(command.command)

        await fsm_state.clear()
        await fsm_state.set_state(WxPreviewStates.waiting_port)
        await fsm_state.update_data(
            **{
                WX_PREVIEW_PORT_STATE_KEY: {
                    "command_id": command.id,
                    "scope": command.scope,
                    "trigger": trigger or "按钮",
                    "command_name": command.name,
                    "project_root": str(project_root) if project_root is not None else "",
                    "env_overrides": env_overrides,
                }
            }
        )

        lines.append("")
        if _is_wx_preview_missing_port_error(exit_code, stderr_text):
            lines.append("*端口配置缺失（可恢复）*")
        else:
            lines.append("*端口配置不匹配（可恢复）*")
        lines.append(
            f"`{_escape_markdown_text(command.name)}` 需要微信开发者工具 CLI 的 `--port`（IDE HTTP 服务端口）。"
        )
        if mismatch_current_port is not None and mismatch_expected_port is not None:
            lines.append(
                "检测到 IDE 当前端口为 "
                f"`{mismatch_current_port}`，但本次命令使用端口为 `{mismatch_expected_port}`。"
            )
            lines.append("可选择使用当前端口重试（推荐），或退出 IDE 并在安全设置把服务端口切回旧端口后再重试。")
        if enabled_flag is False:
            lines.append("检测到 IDE 的“服务端口”开关可能未开启，请在 IDE：设置 → 安全设置 → 服务端口 打开后重试。")
        if suggested_ports:
            ports_label = ", ".join(str(port) for port in suggested_ports[:5])
            lines.append(f"检测到可能的端口：`{_escape_markdown_text(ports_label)}`")
        else:
            lines.append("未能自动读取端口，请在 IDE：设置 → 安全设置 → 服务端口 查看端口号后回复。")
        if config_path is not None:
            lines.append(f"端口来源：`{_escape_markdown_text(str(config_path))}`")
        lines.append(f"端口配置文件：`{_escape_markdown_text(str(ports_file))}`（确认后将自动写入）")
        lines.append("请直接回复端口号（只发数字），或点击下方按钮使用。")
        lines.append("官方文档：https://developers.weixin.qq.com/miniprogram/dev/devtools/cli.html")

        for port in suggested_ports[:3]:
            wx_port_keyboard_rows.append(
                [
                    InlineKeyboardButton(
                        text=f"✅ 使用 {port} 并重试",
                        callback_data=f"{WX_PREVIEW_PORT_USE_PREFIX}{port}",
                    )
                ]
            )
        wx_port_keyboard_rows.append(
            [InlineKeyboardButton(text="❌ 取消", callback_data=WX_PREVIEW_PORT_CANCEL)]
        )
    lines.append("_如需完整输出，请点击下方“查询详情”下载 txt 文件。_")
    summary_markup = InlineKeyboardMarkup(
        inline_keyboard=[
            *wx_port_keyboard_rows,
            [
                InlineKeyboardButton(
                    text="🔎 查询详情",
                    callback_data=f"{history_detail_prefix}{history_record.id}",
                )
            ],
            [InlineKeyboardButton(text="🧾 最近执行", callback_data=COMMAND_HISTORY_CALLBACK)],
        ]
    )
    await _answer_with_markdown(
        reply_message,
        "\n".join(lines),
        reply_markup=summary_markup,
    )
    # 按用户体验要求，二维码图片放在摘要之后发送；若图片发送失败则降级为文件发送。
    if reply_message is not None and photo_path is not None:
        bot = current_bot()
        try:
            await _send_with_retry(
                lambda: bot.send_photo(
                    chat_id=reply_message.chat.id,
                    photo=FSInputFile(str(photo_path)),
                    caption=f"{display_name} 的预览二维码",
                )
            )
        except Exception as exc:  # noqa: BLE001
            worker_log.warning(
                "命令输出图片发送失败",
                extra={"error": str(exc), **_session_extra(), "photo": str(photo_path)},
            )
            try:
                await _send_with_retry(
                    lambda: bot.send_document(
                        chat_id=reply_message.chat.id,
                        document=FSInputFile(str(photo_path)),
                        caption=f"{display_name} 的预览二维码（图片发送失败，已降级为文件）",
                    )
                )
            except Exception as fallback_exc:  # noqa: BLE001
                worker_log.warning(
                    "命令输出图片降级文件发送失败",
                    extra={"error": str(fallback_exc), **_session_extra(), "photo": str(photo_path)},
                )


async def _handle_command_trigger_message(message: Message, prompt: str, state: Optional[FSMContext]) -> bool:
    """处理以别名触发的命令执行。"""

    trigger = _extract_command_trigger(prompt)
    raw_args = _extract_command_args(prompt)
    if not trigger:
        return False
    if trigger in COMMAND_KEYWORDS:
        return False
    command = await COMMAND_SERVICE.resolve_by_trigger(trigger)
    service = COMMAND_SERVICE
    history_prefix = COMMAND_HISTORY_DETAIL_PREFIX
    if command is None:
        command = await GLOBAL_COMMAND_SERVICE.resolve_by_trigger(trigger)
        if command is None:
            return False
        service = GLOBAL_COMMAND_SERVICE
        history_prefix = COMMAND_HISTORY_DETAIL_GLOBAL_PREFIX
    env_overrides: Dict[str, str] = {}
    if raw_args:
        if command.name != WX_UPLOAD_COMMAND_NAME:
            await message.answer("命令暂不支持附带参数，请仅发送触发词。")
            return True
        version_override, parse_error = _parse_wx_upload_args(raw_args)
        if parse_error:
            await _answer_with_markdown(message, parse_error)
            return True
        if version_override:
            env_overrides["VERSION"] = version_override
    command_for_execute = _apply_command_env_overrides(command, env_overrides)
    if await _maybe_handle_wx_preview(
        command=command_for_execute,
        reply_message=message,
        trigger=trigger,
        actor_user=message.from_user,
        service=service,
        history_detail_prefix=history_prefix,
        fsm_state=state,
        env_overrides=env_overrides,
    ):
        return True
    await _execute_command_definition(
        command=command_for_execute,
        reply_message=message,
        trigger=trigger,
        actor_user=message.from_user,
        service=service,
        history_detail_prefix=history_prefix,
        fsm_state=state,
    )
    return True


async def _send_command_overview(message: Message, notice: Optional[str] = None) -> None:
    """发送命令列表。"""

    text, markup = await _build_command_overview_view(notice)
    await _answer_with_markdown(message, text, reply_markup=markup)


async def _refresh_command_overview(callback: CallbackQuery, notice: Optional[str] = None) -> None:
    """在原消息上刷新命令列表。"""

    if callback.message is None:
        return
    text, markup = await _build_command_overview_view(notice)
    parse_mode = _parse_mode_value()
    try:
        await callback.message.edit_text(
            text,
            reply_markup=markup,
            parse_mode=parse_mode,
        )
    except TelegramBadRequest:
        await _answer_with_markdown(callback.message, text, reply_markup=markup)


async def _build_command_history_view(
    limit: int = COMMAND_HISTORY_LIMIT,
) -> tuple[str, Optional[InlineKeyboardMarkup]]:
    """渲染最近的执行历史，附带详情查询按钮。"""

    local_records = await COMMAND_SERVICE.list_history(limit=limit)
    global_records = await GLOBAL_COMMAND_SERVICE.list_history(limit=limit)
    combined: list[tuple[str, CommandHistoryRecord]] = [
        ("local", record) for record in local_records
    ] + [
        ("global", record) for record in global_records
    ]

    def _record_sort_key(item: tuple[str, CommandHistoryRecord]) -> str:
        """按完成时间倒序排列。"""

        _, record = item
        return (record.finished_at or record.started_at or "")

    combined.sort(key=_record_sort_key, reverse=True)
    combined = combined[:limit]

    lines = ["*最近命令执行记录*"]
    if not combined:
        lines.append("暂无历史记录。")
        return "\n".join(lines), None

    def _shorten_label(text: str, max_length: int = 32) -> str:
        """压缩按钮标题，防止超出 Telegram 限制。"""

        if len(text) <= max_length:
            return text
        return text[: max_length - 1] + "…"

    detail_buttons: list[list[InlineKeyboardButton]] = []
    for source, record in combined:
        title = record.command_title or record.command_name
        status_icon = {
            "success": "✅",
            "failed": "⚠️",
            "timeout": "⏰",
            "error": "❌",
        }.get(record.status, "•")
        finished_at = record.finished_at or record.started_at
        exit_text = record.exit_code if record.exit_code is not None else "-"
        source_label = "（通用）" if source == "global" else ""
        lines.append(
            f"{status_icon} `{_escape_markdown_text(title)}` - {finished_at} (exit={exit_text}){source_label}"
        )
        prefix = (
            COMMAND_HISTORY_DETAIL_GLOBAL_PREFIX
            if source == "global"
            else COMMAND_HISTORY_DETAIL_PREFIX
        )
        detail_buttons.append(
            [
                InlineKeyboardButton(
                    text=f"🔎 {_shorten_label(title)}",
                    callback_data=f"{prefix}{record.id}",
                )
            ]
        )
    markup = InlineKeyboardMarkup(inline_keyboard=detail_buttons)
    return "\n".join(lines), markup


def _history_detail_filename(record: CommandHistoryRecord) -> str:
    """根据记录生成可读的 txt 文件名。"""

    base = re.sub(r"[^a-zA-Z0-9._-]+", "-", record.command_name).strip("-") or "command"
    timestamp_source = record.finished_at or record.started_at or shanghai_now_iso()
    sanitized_timestamp = re.sub(r"[^0-9A-Za-z_]", "", timestamp_source.replace(":", "").replace("-", "").replace("T", "_"))
    return f"{base}-{sanitized_timestamp or 'log'}.txt"


def _build_history_detail_document(record: CommandHistoryRecord) -> BufferedInputFile:
    """将命令历史记录转换为可下载的 txt 文件。"""

    title = record.command_title or record.command_name
    exit_text = record.exit_code if record.exit_code is not None else "-"
    lines = [
        f"命令标题：{title}",
        f"命令名称：{record.command_name}",
        f"状态：{record.status} (exit={exit_text})",
        f"开始时间：{record.started_at}",
        f"完成时间：{record.finished_at}",
        "",
        "=== 标准输出 (stdout) ===",
        record.output or "(空)",
        "",
        "=== 标准错误 (stderr) ===",
        record.error or "(空)",
        "",
        "（由 vibego 自动生成）",
    ]
    payload = "\n".join(lines)
    filename = _history_detail_filename(record)
    return BufferedInputFile(payload.encode("utf-8"), filename=filename)



def _resolve_worker_target_chat_ids() -> List[int]:
    """收集需要推送菜单的 chat id，优先使用状态文件记录。"""
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
            worker_log.debug("STATE_FILE 不存在，跳过菜单推送来源", extra=_session_extra(key="state_file_missing"))
        except json.JSONDecodeError as exc:
            worker_log.warning("STATE_FILE 解析失败：%s", exc, extra=_session_extra(key="state_file_invalid"))
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
        worker_log.debug("未找到项目配置 %s，跳过 allowed_chat_id", config_path, extra=_session_extra(key="projects_missing"))
    except json.JSONDecodeError as exc:
        worker_log.warning("项目配置解析失败：%s", exc, extra=_session_extra(key="projects_invalid"))
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
    """首次收到消息时自动将 chat_id 记录到 state 文件。

    仅在以下条件同时满足时写入：
    1. STATE_FILE 环境变量已配置
    2. state 文件存在
    3. 当前项目在 state 中的 chat_id 为空
    """
    state_file_env = os.environ.get("STATE_FILE")
    if not state_file_env:
        return

    state_path = Path(state_file_env).expanduser()
    if not state_path.exists():
        worker_log.debug(
            "STATE_FILE 不存在，跳过自动记录 chat_id",
            extra={**_session_extra(), "path": str(state_path)},
        )
        return

    # 使用文件锁保证并发安全
    lock_path = state_path.with_suffix(state_path.suffix + ".lock")
    import fcntl

    try:
        with open(lock_path, "w", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)

            try:
                # 读取当前 state
                raw_state = json.loads(state_path.read_text(encoding="utf-8"))
                if not isinstance(raw_state, dict):
                    worker_log.warning(
                        "STATE_FILE 格式异常，跳过自动记录",
                        extra=_session_extra(),
                    )
                    return

                # 检查当前项目的 chat_id
                project_key = PROJECT_SLUG or PROJECT_NAME
                if not project_key:
                    worker_log.warning(
                        "PROJECT_SLUG 和 PROJECT_NAME 均未设置，跳过自动记录",
                        extra=_session_extra(),
                    )
                    return

                project_state = raw_state.get(project_key)
                if not isinstance(project_state, dict):
                    # 项目不存在，创建新条目
                    raw_state[project_key] = {
                        "chat_id": chat_id,
                        "model": ACTIVE_MODEL or "codex",
                        "status": "running",
                    }
                    need_write = True
                elif project_state.get("chat_id") is None:
                    # chat_id 为空，更新
                    project_state["chat_id"] = chat_id
                    need_write = True
                else:
                    # chat_id 已存在，无需更新
                    need_write = False

                if need_write:
                    # 写入更新后的 state
                    tmp_path = state_path.with_suffix(state_path.suffix + ".tmp")
                    tmp_path.write_text(
                        json.dumps(raw_state, ensure_ascii=False, indent=4),
                        encoding="utf-8",
                    )
                    tmp_path.replace(state_path)
                    worker_log.info(
                        "已自动记录 chat_id=%s 到 state 文件",
                        chat_id,
                        extra={**_session_extra(), "project": project_key},
                    )
                else:
                    worker_log.debug(
                        "chat_id 已存在，跳过自动记录",
                        extra={**_session_extra(), "existing_chat_id": project_state.get("chat_id")},
                    )

            except json.JSONDecodeError as exc:
                worker_log.error(
                    "STATE_FILE 解析失败，跳过自动记录：%s",
                    exc,
                    extra=_session_extra(),
                )
            except Exception as exc:
                worker_log.error(
                    "自动记录 chat_id 失败：%s",
                    exc,
                    extra={**_session_extra(), "chat": chat_id},
                )
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    except Exception as exc:
        worker_log.error(
            "获取文件锁失败：%s",
            exc,
            extra=_session_extra(),
        )
    finally:
        # 清理锁文件
        try:
            if lock_path.exists():
                lock_path.unlink()
        except Exception:
            pass


def _record_worker_identity(username: Optional[str], user_id: Optional[int]) -> None:
    """在 worker 启动时记录实际的 Telegram 用户名，便于 master 侧展示跳转链接。"""

    if not username:
        return

    state_file_env = os.environ.get("STATE_FILE")
    if not state_file_env:
        return

    state_path = Path(state_file_env).expanduser()
    if not state_path.exists():
        worker_log.debug(
            "STATE_FILE 不存在，跳过记录实际 username",
            extra={**_session_extra(), "path": str(state_path)},
        )
        return

    lock_path = state_path.with_suffix(state_path.suffix + ".lock")
    import fcntl

    try:
        with open(lock_path, "w", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                raw_state = json.loads(state_path.read_text(encoding="utf-8"))
                if not isinstance(raw_state, dict):
                    worker_log.warning(
                        "STATE_FILE 结构异常，跳过记录 username",
                        extra=_session_extra(),
                    )
                    return
                project_key = PROJECT_SLUG or PROJECT_NAME
                if not project_key:
                    worker_log.warning(
                        "PROJECT_SLUG 与 PROJECT_NAME 均为空，无法记录 username",
                        extra=_session_extra(),
                    )
                    return
                project_state = raw_state.get(project_key)
                if not isinstance(project_state, dict):
                    project_state = {}
                    raw_state[project_key] = project_state
                changed = False
                if project_state.get("actual_username") != username:
                    project_state["actual_username"] = username
                    changed = True
                if user_id is not None and project_state.get("telegram_user_id") != user_id:
                    project_state["telegram_user_id"] = user_id
                    changed = True
                if changed:
                    tmp_path = state_path.with_suffix(state_path.suffix + ".tmp")
                    tmp_path.write_text(
                        json.dumps(raw_state, ensure_ascii=False, indent=4),
                        encoding="utf-8",
                    )
                    tmp_path.replace(state_path)
                    worker_log.info(
                        "已记录实际 username=%s",
                        username,
                        extra={**_session_extra(), "project": project_key},
                    )
                else:
                    worker_log.debug(
                        "实际 username 未变化，跳过 state 更新",
                        extra={**_session_extra(), "username": username},
                    )
            except json.JSONDecodeError as exc:
                worker_log.error(
                    "STATE_FILE 解析失败，跳过记录 username：%s",
                    exc,
                    extra=_session_extra(),
                )
            except Exception as exc:
                worker_log.error(
                    "记录实际 username 失败：%s",
                    exc,
                    extra=_session_extra(),
                )
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    except Exception as exc:
        worker_log.error(
            "记录实际 username 失败：%s",
            exc,
            extra=_session_extra(),
        )
    finally:
        try:
            if lock_path.exists():
                lock_path.unlink()
        except Exception:
            pass


async def _broadcast_worker_keyboard(bot: Bot) -> None:
    """启动时主动推送菜单，确保 Telegram 键盘同步。"""
    targets = _resolve_worker_target_chat_ids()
    if not targets:
        worker_log.info("无可推送的聊天，跳过菜单广播", extra=_session_extra())
        return
    for chat_id in targets:
        try:
            text, inline_markup = await _build_task_list_view(status=None, page=1, limit=DEFAULT_PAGE_SIZE)
        except Exception as exc:
            worker_log.error(
                "构建任务列表失败：%s",
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
            worker_log.warning("推送任务列表被拒绝：%s", exc, extra={**_session_extra(), "chat": chat_id})
        except TelegramBadRequest as exc:
            worker_log.warning("推送任务列表失败：%s", exc, extra={**_session_extra(), "chat": chat_id})
        except (TelegramRetryAfter, TelegramNetworkError) as exc:
            worker_log.error("推送任务列表网络异常：%s", exc, extra={**_session_extra(), "chat": chat_id})
            await _notify_send_failure_message(chat_id)
        except Exception as exc:
            worker_log.error("推送任务列表异常：%s", exc, extra={**_session_extra(), "chat": chat_id})
        else:
            worker_log.info(
                "已推送任务列表至 chat_id=%s",
                chat_id,
                extra={**_session_extra(), "length": str(len(delivered))},
            )

STATUS_LABELS = {
    "research": "🔍 调研中",
    "test": "🧪 测试中",
    "done": "✅ 已完成",
}

NOTE_LABELS = {
    "research": "调研",
    "test": "测试",
    "bug": "缺陷",
    "misc": "其他",
}

TASK_TYPE_LABELS = {
    "requirement": "需求",
    "defect": "缺陷",
    "task": "优化",
    "risk": "风险",
}

TASK_TYPE_EMOJIS = {
    "requirement": "📌",
    "defect": "🐞",
    "task": "🛠️",
    "risk": "⚠️",
}

HISTORY_FIELD_LABELS = {
    "title": "标题",
    "status": "状态",
    "priority": "优先级",
    "description": "描述",
    "due_date": "截止时间",
    "task_type": "类型",
    "type": "类型",
    "tags": "标签",
    "assignee": "负责人",
    "parent_id": "父任务",
    "root_id": "根任务",
    "archived": "归档状态",
    "create": "创建任务",
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
        "需求": "requirement",
        "feature": "requirement",
        "story": "requirement",
        "bug": "defect",
        "issue": "defect",
        "缺陷": "defect",
        "任务": "task",
        "risk": "risk",
        "风险": "risk",
    }
)

_STATUS_ALIAS_MAP: dict[str, str] = {key.lower(): value for key, value in STATUS_ALIASES.items()}

SKIP_TEXT = "跳过"
TASK_LIST_CREATE_CALLBACK = "task:list_create"
TASK_LIST_SEARCH_CALLBACK = "task:list_search"
TASK_LIST_SEARCH_PAGE_CALLBACK = "task:list_search_page"
TASK_LIST_RETURN_CALLBACK = "task:list_return"
TASK_BATCH_PUSH_START_CALLBACK = "task:batch_push:start"
TASK_BATCH_PUSH_TOGGLE_PREFIX = "task:batch_push:toggle:"
TASK_BATCH_PUSH_PAGE_PREFIX = "task:batch_push:page:"
TASK_BATCH_PUSH_CONFIRM_CALLBACK = "task:batch_push:confirm"
TASK_BATCH_PUSH_CANCEL_CALLBACK = "task:batch_push:cancel"
TASK_BATCH_PUSH_SESSION_MAIN_CALLBACK = "task:batch_push_session:main"
TASK_BATCH_PUSH_SESSION_PARALLEL_PREFIX = "task:batch_push_session:parallel:"
TASK_BATCH_PUSH_SESSION_REFRESH_CALLBACK = "task:batch_push_session:refresh"
TASK_BATCH_PUSH_SESSION_CANCEL_CALLBACK = "task:batch_push_session:cancel"
SESSION_LIVE_LIST_CALLBACK = "session:view:list"
SESSION_LIVE_MAIN_CALLBACK = "session:view:main"
SESSION_LIVE_PARALLEL_PREFIX = "session:view:parallel:"
SESSION_LIVE_REFRESH_MAIN_CALLBACK = "session:view:refresh:main"
SESSION_LIVE_REFRESH_PARALLEL_PREFIX = "session:view:refresh:parallel:"
PUSH_EXISTING_SESSION_MAIN_CALLBACK = "task:push_existing_session:main"
PUSH_EXISTING_SESSION_PARALLEL_PREFIX = "task:push_existing_session:parallel:"
PUSH_EXISTING_SESSION_REFRESH_CALLBACK = "task:push_existing_session:refresh"
PUSH_EXISTING_SESSION_CANCEL_CALLBACK = "task:push_existing_session:cancel"
TASK_DETAIL_BACK_CALLBACK = "task:detail_back"
TASK_DETAIL_DELETE_PROMPT_CALLBACK = "task:delete_prompt"
TASK_DETAIL_DELETE_CONFIRM_CALLBACK = "task:delete_confirm"
TASK_HISTORY_PAGE_CALLBACK = "task:history_page"
TASK_HISTORY_BACK_CALLBACK = "task:history_back"
TASK_DESC_INPUT_CALLBACK = "task:desc_input"
TASK_DESC_CLEAR_CALLBACK = "task:desc_clear"
TASK_DESC_CONFIRM_CALLBACK = "task:desc_confirm"
TASK_DESC_RETRY_CALLBACK = "task:desc_retry"
TASK_DESC_CANCEL_CALLBACK = "task:desc_cancel"
TASK_DESC_CLEAR_TEXT = "🗑️ 清空描述"
TASK_DESC_CANCEL_TEXT = "❌ 取消"
TASK_DESC_REPROMPT_TEXT = "✏️ 重新打开输入提示"
TASK_DESC_CONFIRM_TEXT = "✅ 确认更新"
TASK_DESC_RETRY_TEXT = "✏️ 重新输入"

TASK_RELATED_PAGE_SIZE = 5
TASK_RELATED_SELECT_PREFIX = "task:rel_sel"
TASK_RELATED_PAGE_PREFIX = "task:rel_page"
TASK_RELATED_SKIP_CALLBACK = "task:rel_skip"
TASK_RELATED_CANCEL_CALLBACK = "task:rel_cancel"

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
HISTORY_TRUNCATION_NOTICE = "⚠️ 本页部分记录因 Telegram 长度限制已截断，建议导出历史查看完整内容。"
HISTORY_TRUNCATION_NOTICE_SHORT = "⚠️ 本页已截断"

_NUMBER_PREFIX_RE = re.compile(r"^\d+\.\s")


def _format_numbered_label(index: int, label: str) -> str:
    text = label or ""
    if _NUMBER_PREFIX_RE.match(text):
        return text
    return f"{index}. {text}" if text else f"{index}."


def _number_inline_buttons(rows: list[list[InlineKeyboardButton]], *, start: int = 1) -> None:
    """仅用于 FSM 交互的 inline 按钮，添加数字前缀以便键盘选择。"""
    counter = start
    for row in rows:
        for button in row:
            button.text = _format_numbered_label(counter, button.text or "")
            counter += 1


def _number_reply_buttons(rows: list[list[KeyboardButton]], *, start: int = 1) -> None:
    """仅用于 FSM 交互的 reply 按钮，添加数字前缀便于输入。"""
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
    """统一处理按钮输入文本，移除序号并规范大小写。"""

    if value is None:
        return ""
    stripped = _strip_number_prefix(value)
    return stripped.strip()


def _is_skip_message(value: Optional[str]) -> bool:
    """判断用户是否选择了跳过。"""

    token = _normalize_choice_token(value).lower()
    return token in {SKIP_TEXT.lower(), "skip"}


def _is_cancel_message(value: Optional[str]) -> bool:
    """判断用户是否输入了取消指令。"""

    token = _normalize_choice_token(value)
    if not token:
        return False
    lowered = token.lower()
    cancel_tokens = {"取消", "cancel", "quit"}
    cancel_tokens.add("取消创建任务")
    # 兼容含有表情的菜单按钮文本，避免用户需重复点击取消。
    cancel_tokens.add(_normalize_choice_token(TASK_DESC_CANCEL_TEXT).lower())
    return lowered in cancel_tokens


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
    """返回状态展示顺序，保持与任务状态定义一致。"""

    return tuple(TASK_STATUSES)


STATUS_DISPLAY_ORDER: tuple[str, ...] = _status_display_order()
STATUS_FILTER_OPTIONS: tuple[Optional[str], ...] = (None, *STATUS_DISPLAY_ORDER)

VIBE_PHASE_BODY = """## 需求调研问题分析阶段 - 严禁修改文件｜允许访问网络｜自定义扫描范围
以上是任务和背景描述，你是一名专业的全栈工程师，使用尽可能多的专业 agents，产出调研结论：给出实现思路、方案优劣与决策选项；
重要约束：
- 响应的内容以及思考过程都始终使用简体中文回复，在 CLI 终端中用格式化后的 markdown 的格式来呈现数据，禁止使用 markdown 表格，流程图的话改用纯文本绘制，markdown 中的代码、流程等有必要的内容需要使用围栏代码块。
- 先通读项目：厘清部署架构、系统架构、代码风格与通用组件；不确定时先提问再推进。
- 充分分析，详细讨论清楚需求以及可能发送的边缘场景，列出需我确认的关键决策点；不明之处及时澄清。
- 使用 Task 工具时必须标注：RESEARCH ONLY - NO FILE MODIFICATIONS。
- 可调用所需的 tools / subAgent / MCP 等一切辅助工具调研，本地没有的时候自己上网找文档安装。
- 涉及开发设计时，明确依赖、数据库表与字段、伪代码与影响范围，按生产级别的安全、性能、高可用等标准考虑。
- 制定方案：列出至少两种可选的思路，比较其优缺点后推荐最佳方案。
- 需要用户做出决策或待用户确认时，给出待决策项的纯数字编号以及 ABCD 的选项，方便用户回复你。
- 自行整理出本次会话的 checklist ，防止在后续的任务执行中遗漏。
- 最后列出本次使用的模型、MCP、Tools、subAgent 及 token 消耗； ultrathink"""

TEST_PHASE_REQUIREMENTS = """## 测试阶段（可改文件｜可联网｜自定义扫描范围）
以上是任务和任务描述，你是一名专业全栈工程师，使用尽可能多的专业 agents，在终端一次性跑完前后端测试（与该任务相关的代码），覆盖：单元、集成契约、API/数据交互、冒烟、端到端（后端视角）、性能压力、并发正确性（可选）、安全与依赖漏洞、覆盖率统计与阈值校验；最终产出报告与待确认修复清单。IMPLEMENTATION APPROVED

### 全局约定
- 工具与依赖：缺失即联网安装；优先 use context7（如无则自动安装，可用 chrome-devtools-mcp）。
- 仅在**当前仓库**内操作；遵循现有代码风格与 lint；最小化改动。
- 统一输出：HTML/文本报告、Trace/Video/Screenshot、覆盖率阈值硬闸可配置。

### 后端
- 构建与运行：所有 Maven 命令用 `./mvnw`；启动附加参数：
  -Dspring-boot.run.profiles=dev -Dspring-boot.run.jvmArguments="-javaagent:/Users/david/devops/opentelemetry-javaagent.jar -Dotel.service.name=main-application -Dotel.traces.exporter=none -Dotel.metrics.exporter=none -Dotel.logs.exporter=none"
- 测试基线：若无用例，按生产标准为各层（Controller/Service/Repository）与每个 REST API 生成丰富完整的 JUnit 5 + Spring 测试与集成用例。
- 生态与规范：若缺失则安装并配置——JUnit 5、Mockito、Testcontainers、JaCoCo、JMeter、Checkstyle。
- 冒烟：对健康检查与关键 API 做 200/超时鉴权三类断言（健康检查为 `/health/check`），生成 JaCoCo 并按行分支阈值硬闸。
- 性能负载：在压力场景下给出系统当前可承受的关键边界指标。
- 并发正确性（可选）：高风险类用 JMH（微基准）与 jcstress（可见性原子性）抽样验证。
- 变更策略：明显低风险且确定性高的问题直接修（选择器等待策略不稳 Mock/可复现小缺陷）；高风险变更列清单与建议，待确认后再改。

### 前端（Playwright）
- 目标：跨浏览器（Chromium/Firefox/WebKit）与品牌兼容；E2E/冒烟功能交互/UI 可视回归（`toHaveScreenshot`）；接口与数据交互（拦截/Mock/HAR 回放）；网络失败与重试；移动端环境模拟（iPhone/Android 视口、触摸、定位时区、慢网离线）。
- 性能：采集 Navigation/Resource Timing；（可选）如检测到 Lighthouse 依赖则对首页关键路由跑桌面移动审计并输出 JSON/HTML 与阈值告警。
- 执行策略（按序，压缩版）：
  1) 安装校验 Playwright 依赖与三大浏览器二进制（仅当前项目）。
  2) 生成校验 `playwright.config.ts`（chromium/firefox/webkit + Desktop Chrome/iPhone14/Pixel7；全局 `trace: retain-on-failure, video: retain-on-failure, screenshot: only-on-failure`）；无基线则首次运行生成快照基线（记录为“基线生成”而非失败）。
  3) 冒烟优先：仅跑主流程用例（可按 `tests/e2e/**/smoke*.spec.ts` 约定），收集 `console.error/requestfailed`，并将任何错误计入报告。
  4) 全量回归：按“Project”维度并行跑：三大浏览器 + 两款移动设备；UI 测试对关键页面与组件使用 `toHaveScreenshot`；对动态区域应用 mask/threshold 以减少抖动；交互与接口使用 `route()` 进行定向 Mock 与异常场景注入；必要时使用 HAR 回放；模拟慢 3G、离线、地理位置、时区、深/浅色模式、权限（通知/定位）。
  5) 性能小节：汇总 Web Performance API 指标（如 FCP/LCP/TBT/TTFB 可得时）并输出到报告；如检测到 lighthouse 依赖，对首页/关键路由跑 Lighthouse（桌面/移动各一次），输出 JSON/HTML 报告与阈值告警。
  6) 结果汇总（文本表）
    | 维度 | 浏览器/设备 | 用例数 | 失败 | 重跑后 | 截图 Diff | 性能阈值告警 | 备注 |
    |---|---|---:|---:|---:|---:|---:|---|
  7) 自动最小化修复（仅限安全改动）
    - 分类：用例问题/测试夹具问题/应用真实缺陷
    - 对“明显低风险且确定性高”的问题直接修复（如选择器失效、等待策略、Mock 不稳、易复现前端异常的局部修正）；
    - 修复后**本地自测**：新增/更新最少 10 条测试输入（正常/边界/异常）与预期，并复跑相关项目
    - 产出：变更清单（文件/函数/影响面）、回滚命令、后续观察项
  8) 高风险的改动记录为清单并给出修改建议等，最后所有任务执行完成后，由我确认是否需要修复
    - 如“是否引入/更新 lighthouse、是否提高视觉阈值、是否纳入 WebKit 移动模拟”等

### 输出顺序（严格执行）
A. 背景与假设（含不确定项）  
B. 预检结果与配置要点  
C. 冒烟与全量汇总表 + 关键失败 TopN（含直链到 Trace）  
D. 性能摘录（及阈值对比）  
E. 自动修复的变更清单（含回滚说明）与自测用例×≥10  
F. 仍需我确认的决策点  
- 最后列出本次使用的模型、MCP、Tools、subAgent、token 消耗以及执行耗时；ultrathink"""

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

SUMMARY_COMMAND_PREFIX = "/task_summary_request_"
SUMMARY_COMMAND_ALIASES: tuple[str, ...] = (
    "/task_summary_request_",
    "/tasksummaryrequest",
)


LEGACY_BUG_HISTORY_HEADER = "缺陷记录（最近 3 条）"


def _strip_legacy_bug_header(text: str) -> str:
    """移除历史模板遗留的缺陷标题，防止提示词重复。"""

    if not text:
        return ""
    cleaned_lines: list[str] = []
    for line in text.splitlines():
        token = line.strip()
        if token and token.startswith(LEGACY_BUG_HISTORY_HEADER):
            # 兼容旧模板形式，如“缺陷记录（最近 3 条） -”或带冒号的写法
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip()


def _append_prompt_field_as_code_block(lines: list[str], *, label: str, value: Optional[str]) -> None:
    """将任务字段按代码块样式追加到提示词，提升长文本可读性。"""

    normalized = (value or "").strip()
    if not normalized or normalized == "-":
        lines.append(f"{label}：-")
        return
    lines.append(f"{label}：")
    # 使用 ~~~ 代码块，避免与外层 Telegram ``` 预览代码块冲突。
    lines.append("~~~")
    lines.extend(normalized.splitlines() or [normalized])
    lines.append("~~~")


DEFECT_REPRODUCTION_LABEL = "复现步骤"
DEFECT_EXPECTED_RESULT_LABEL = "期望结果"
OPTIMIZE_CURRENT_EFFECT_LABEL = "当前效果"
OPTIMIZE_EXPECTED_EFFECT_LABEL = "期望效果"
DEFECT_DESCRIPTION_PATTERN = re.compile(
    rf"^{DEFECT_REPRODUCTION_LABEL}：\n(?P<reproduction>.*)\n\n{DEFECT_EXPECTED_RESULT_LABEL}：\n(?P<expected_result>.*)$",
    re.DOTALL,
)
OPTIMIZE_DESCRIPTION_PATTERN = re.compile(
    rf"^{OPTIMIZE_CURRENT_EFFECT_LABEL}：\n(?P<current_effect>.*)\n\n{OPTIMIZE_EXPECTED_EFFECT_LABEL}：\n(?P<expected_effect>.*)$",
    re.DOTALL,
)


def _build_structured_pair_description(
    first_label: str,
    second_label: str,
    first_value: Optional[str],
    second_value: Optional[str],
) -> str:
    """将双字段正文组装为统一结构化文本。"""

    normalized_first = normalize_newlines(first_value or "").strip() or "-"
    normalized_second = normalize_newlines(second_value or "").strip() or "-"
    return f"{first_label}：\n{normalized_first}\n\n{second_label}：\n{normalized_second}"


def _parse_structured_pair_description(
    description: Optional[str],
    *,
    pattern: re.Pattern[str],
    first_group: str,
    second_group: str,
) -> Optional[tuple[str, str]]:
    """解析统一的双字段结构化文本；历史自由文本返回 None。"""

    normalized = normalize_newlines(description or "").strip()
    if not normalized:
        return None
    match = pattern.match(normalized)
    if not match:
        return None
    first_value = (match.group(first_group) or "").strip() or "-"
    second_value = (match.group(second_group) or "").strip() or "-"
    return first_value, second_value


def _build_defect_description(reproduction: Optional[str], expected_result: Optional[str]) -> str:
    """将缺陷任务的复现步骤与期望结果组装为统一存储结构。"""

    return _build_structured_pair_description(
        DEFECT_REPRODUCTION_LABEL,
        DEFECT_EXPECTED_RESULT_LABEL,
        reproduction,
        expected_result,
    )


def _parse_defect_description(description: Optional[str]) -> Optional[tuple[str, str]]:
    """解析缺陷任务的结构化描述；历史自由文本会返回 None 以便回退兼容。"""

    return _parse_structured_pair_description(
        description,
        pattern=DEFECT_DESCRIPTION_PATTERN,
        first_group="reproduction",
        second_group="expected_result",
    )


def _build_optimize_description(current_effect: Optional[str], expected_effect: Optional[str]) -> str:
    """将优化任务的当前效果与期望效果组装为统一存储结构。"""

    return _build_structured_pair_description(
        OPTIMIZE_CURRENT_EFFECT_LABEL,
        OPTIMIZE_EXPECTED_EFFECT_LABEL,
        current_effect,
        expected_effect,
    )


def _parse_optimize_description(description: Optional[str]) -> Optional[tuple[str, str]]:
    """解析优化任务的结构化描述；历史自由文本会返回 None 以便回退兼容。"""

    return _parse_structured_pair_description(
        description,
        pattern=OPTIMIZE_DESCRIPTION_PATTERN,
        first_group="current_effect",
        second_group="expected_effect",
    )


def _get_structured_task_labels(task_type: Optional[str]) -> Optional[tuple[str, str]]:
    """返回结构化任务类型对应的双字段标签。"""

    normalized_task_type = _normalize_task_type(task_type)
    if normalized_task_type == "defect":
        return DEFECT_REPRODUCTION_LABEL, DEFECT_EXPECTED_RESULT_LABEL
    if normalized_task_type == "task":
        return OPTIMIZE_CURRENT_EFFECT_LABEL, OPTIMIZE_EXPECTED_EFFECT_LABEL
    return None


def _build_structured_task_description(
    task_type: Optional[str],
    first_value: Optional[str],
    second_value: Optional[str],
) -> Optional[str]:
    """按任务类型组装结构化正文；非结构化类型返回 None。"""

    normalized_task_type = _normalize_task_type(task_type)
    if normalized_task_type == "defect":
        return _build_defect_description(first_value, second_value)
    if normalized_task_type == "task":
        return _build_optimize_description(first_value, second_value)
    return None


def _parse_structured_task_description(task_type: Optional[str], description: Optional[str]) -> Optional[tuple[str, str]]:
    """按任务类型解析结构化正文；非结构化类型或历史文本返回 None。"""

    normalized_task_type = _normalize_task_type(task_type)
    if normalized_task_type == "defect":
        return _parse_defect_description(description)
    if normalized_task_type == "task":
        return _parse_optimize_description(description)
    return None


def _append_task_prompt_description_fields(
    lines: list[str],
    *,
    task: TaskRecord,
    description: Optional[str],
    supplement_value: Optional[str],
) -> None:
    """按任务类型输出推送提示词中的描述字段，结构化任务优先拆成双字段。"""

    normalized_task_type = _normalize_task_type(getattr(task, "task_type", None))
    parsed_structured = _parse_structured_task_description(normalized_task_type, description)
    labels = _get_structured_task_labels(normalized_task_type)
    if parsed_structured is not None and labels is not None:
        _append_prompt_field_as_code_block(lines, label=labels[0], value=parsed_structured[0])
        _append_prompt_field_as_code_block(lines, label=labels[1], value=parsed_structured[1])
    else:
        _append_prompt_field_as_code_block(lines, label="任务描述", value=description)
    _append_prompt_field_as_code_block(lines, label="补充任务描述", value=supplement_value)


def _format_task_detail_value(value: Optional[str]) -> str:
    """统一格式化任务详情中的文本值，兼容 Markdown 与预转义内容。"""

    cleaned = _clean_user_text(value or "") or "-"
    if _IS_MARKDOWN_V2:
        return cleaned
    return _escape_markdown_text(cleaned)


def _append_defect_summary_field(lines: list[str], *, label: str, value: Optional[str]) -> None:
    """将确认页字段按“标题 + 内容”形式写入，便于长文本阅读。"""

    normalized = normalize_newlines(value or "").strip()
    if not normalized or normalized == "-":
        lines.append(f"{label}：-")
        return
    lines.append(f"{label}：")
    lines.extend(normalized.splitlines())


def _build_defect_confirm_summary_lines(
    *,
    title: str,
    origin_task: Optional[TaskRecord],
    origin_task_id: Optional[str],
    reproduction: Optional[str],
    expected_result: Optional[str],
    pending_attachments: Sequence[Mapping[str, str]],
) -> list[str]:
    """构建缺陷确认页摘要，统一复现步骤/期望结果与附件展示顺序。"""

    summary_lines = [
        "请确认缺陷任务信息：",
        f"标题：{title or '-'}",
        f"类型：{_format_task_type('defect')}",
    ]
    if origin_task is not None:
        origin_title = (origin_task.title or "").strip() or "-"
        summary_lines.append(f"关联任务：/{origin_task.id} {origin_title}")
    elif origin_task_id:
        summary_lines.append(f"关联任务：/{origin_task_id}")
    else:
        summary_lines.append("关联任务：-")
    _append_defect_summary_field(summary_lines, label=DEFECT_REPRODUCTION_LABEL, value=reproduction)
    _append_defect_summary_field(summary_lines, label=DEFECT_EXPECTED_RESULT_LABEL, value=expected_result)
    if isinstance(pending_attachments, Sequence):
        summary_lines.extend(_format_pending_attachments_for_create_summary(pending_attachments))
    else:
        summary_lines.append("附件列表：-")
    return summary_lines


async def _build_task_create_confirm_summary_lines(
    *,
    title: str,
    task_type_code: Optional[str],
    priority: int,
    related_task_id: Optional[str],
    description: Optional[str],
    pending_attachments: Sequence[Mapping[str, str]],
) -> list[str]:
    """构建 `/task_new` 创建流程的确认摘要。"""

    normalized_task_type = _normalize_task_type(task_type_code)
    summary_lines = [
        "请确认任务信息：",
        f"标题：{title or '-'}",
        f"类型：{_format_task_type(normalized_task_type)}",
        f"优先级：{_format_priority(int(priority or DEFAULT_PRIORITY))}（默认）",
    ]
    if normalized_task_type == "defect":
        if related_task_id:
            related_task = await TASK_SERVICE.get_task(related_task_id)
            if related_task is not None:
                related_title = (related_task.title or "").strip() or "-"
                summary_lines.append(f"关联任务：/{related_task.id} {related_title}")
            else:
                summary_lines.append(f"关联任务：/{related_task_id}")
        else:
            summary_lines.append("关联任务：-（未选择）")
    parsed_structured = _parse_structured_task_description(normalized_task_type, description)
    labels = _get_structured_task_labels(normalized_task_type)
    if parsed_structured is not None and labels is not None:
        _append_defect_summary_field(summary_lines, label=labels[0], value=parsed_structured[0])
        _append_defect_summary_field(summary_lines, label=labels[1], value=parsed_structured[1])
    elif description:
        summary_lines.append("描述：")
        summary_lines.append(description)
    else:
        summary_lines.append("描述：暂无（可稍后通过 /task_desc 补充）")
    if isinstance(pending_attachments, Sequence):
        summary_lines.extend(_format_pending_attachments_for_create_summary(pending_attachments))
    else:
        summary_lines.append("附件列表：-")
    return summary_lines


def _build_model_push_payload(
    task: TaskRecord,
    supplement: Optional[str] = None,
    history: Optional[str] = None,
    notes: Optional[Sequence[TaskNoteRecord]] = None,
    attachments: Optional[Sequence[TaskAttachmentRecord]] = None,
    is_bug_report: bool = False,
    push_mode: Optional[str] = None,
) -> str:
    """根据任务状态构造推送到 tmux 的指令。

    Args:
        task: 任务记录
        supplement: 补充描述
        history: 历史记录文本
        notes: 任务备注列表
        is_bug_report: 是否为缺陷报告推送，True 时会在提示词前添加缺陷前缀
        push_mode: 推送模式（PLAN/YOLO），用于替换“进入vibe/测试阶段”前缀
    """

    config = MODEL_PUSH_CONFIG.get(task.status)
    if config is None:
        raise ValueError(f"状态 {task.status!r} 未配置推送模板")

    body = config.get("body", "")
    include_task = bool(config.get("include_task_info"))
    body = (body or "").strip()
    history_block = (history or "").strip()
    status = task.status

    if status in {"research", "test"}:
        body = ""

    if "{history}" in body:
        replacement = history_block or "（暂无任务执行记录）"
        body = body.replace("{history}", replacement).strip()
        history_block = ""

    supplement_text = (supplement or "").strip()
    segments: list[str] = []

    notes = notes or ()  # 推送阶段暂不展示备注文本，仅保留参数兼容
    attachments = attachments or ()

    task_code_plain = f"/{task.id}" if task.id else "-"

    if include_task and status in {"research", "test"}:
        normalized_push_mode = (push_mode or "").strip().upper()
        if normalized_push_mode == PUSH_MODE_PLAN:
            phase_line = f"进入 {PUSH_MODE_PLAN} 模式{AGENTS_PHASE_SUFFIX}"
        elif normalized_push_mode == PUSH_MODE_YOLO:
            phase_line = f"{PUSH_MODE_YOLO} 模式：默认直接执行{AGENTS_PHASE_SUFFIX}"
        else:
            phase_line = VIBE_PHASE_PROMPT
        # 如果是缺陷报告推送，在阶段提示前添加缺陷前缀
        if is_bug_report:
            phase_line = f"{BUG_REPORT_PREFIX}\n{phase_line}"
        title = (task.title or "").strip() or "-"
        description = (task.description or "").strip() or "-"
        supplement_value = supplement_text or "-"
        # 关联任务编码：仅透传编码，不展开关联任务详情，避免提示词过长。
        normalized_related_task_id = _normalize_task_id(getattr(task, "related_task_id", None))
        related_task_code = (
            f"/{normalized_related_task_id}"
            if normalized_related_task_id and normalized_related_task_id != task.id
            else "-"
        )

        lines: list[str] = [
            phase_line,
            f"任务标题：{title}",
            f"任务编码：{task_code_plain}",
        ]
        _append_task_prompt_description_fields(
            lines,
            task=task,
            description=description,
            supplement_value=supplement_value,
        )
        lines.extend([f"关联任务编码：{related_task_code}", ""])
        if attachments:
            lines.append("附件列表：")
            limit = TASK_ATTACHMENT_PREVIEW_LIMIT
            for idx, item in enumerate(attachments[:limit], 1):
                lines.append(f"{idx}. {item.display_name}（{item.mime_type}）→ {item.path}")
            if len(attachments) > limit:
                lines.append(f"… 其余 {len(attachments) - limit} 个附件未展开")
            lines.append("")
        else:
            lines.append("附件列表：-")
            lines.append("")
        history_intro = "以下为任务执行记录，用于辅助回溯任务处理记录："
        if history_block:
            lines.append(history_intro)
            lines.extend(history_block.splitlines())
        else:
            lines.append(f"{history_intro} -")
        return _strip_legacy_bug_header("\n".join(lines))
    else:
        # 非上述状态维持旧逻辑，避免影响完成等场景
        info_lines: list[str] = []
        if include_task:
            title = (task.title or "-").strip() or "-"
            description = (task.description or "").strip() or "暂无"
            supplement_value = supplement_text or "-"
            info_lines.extend([f"任务标题：{title}", f"任务编码：{task_code_plain}"])
            _append_task_prompt_description_fields(
                info_lines,
                task=task,
                description=description,
                supplement_value=supplement_value,
            )
        elif supplement_text:
            _append_prompt_field_as_code_block(info_lines, label="补充任务描述", value=supplement_text)

        if history_block:
            if info_lines and info_lines[-1].strip():
                info_lines.append("")
            info_lines.append("任务执行记录：")
            info_lines.append(history_block)

        if attachments:
            if info_lines and info_lines[-1].strip():
                info_lines.append("")
            info_lines.append("附件列表：")
            limit = TASK_ATTACHMENT_PREVIEW_LIMIT
            for idx, item in enumerate(attachments[:limit], 1):
                info_lines.append(f"{idx}. {item.display_name}（{item.mime_type}）→ {item.path}")
            if len(attachments) > limit:
                info_lines.append(f"… 其余 {len(attachments) - limit} 个附件未展开")
        elif include_task:
            info_lines.append("附件列表：-")

        if info_lines:
            info_segment = "\n".join(info_lines)
            if info_segment.strip():
                segments.append(info_segment)

    if body:
        segments.append(body)

    tail_prompt = ""
    if status in {"research", "test"}:
        normalized_push_mode = (push_mode or "").strip().upper()
        if normalized_push_mode == PUSH_MODE_PLAN:
            tail_prompt = f"进入 {PUSH_MODE_PLAN} 模式{AGENTS_PHASE_SUFFIX}"
        elif normalized_push_mode == PUSH_MODE_YOLO:
            tail_prompt = f"{PUSH_MODE_YOLO} 模式：默认直接执行{AGENTS_PHASE_SUFFIX}"
        else:
            tail_prompt = VIBE_PHASE_PROMPT

    result = "\n\n".join(segment for segment in segments if segment)
    if tail_prompt:
        if result:
            result = f"{result}\n{tail_prompt}"
        else:
            result = tail_prompt
    return _strip_legacy_bug_header(result or body)


def _build_task_context_block_for_model(
    task: TaskRecord,
    *,
    supplement: Optional[str],
    history: str,
    attachments: Sequence[TaskAttachmentRecord],
) -> str:
    """构建任务上下文块（字段格式与推送任务一致，但不包含阶段提示）。"""

    task_code_plain = f"/{task.id}" if task.id else "-"
    title = (task.title or "").strip() or "-"
    description = (task.description or "").strip() or "-"
    supplement_value = (supplement or "").strip() or "-"
    history_block = (history or "").strip()

    lines: list[str] = [
        f"任务标题：{title}",
        f"任务编码：{task_code_plain}",
    ]
    _append_task_prompt_description_fields(
        lines,
        task=task,
        description=description,
        supplement_value=supplement_value,
    )
    lines.append("")

    if attachments:
        lines.append("附件列表：")
        limit = TASK_ATTACHMENT_PREVIEW_LIMIT
        for idx, item in enumerate(attachments[:limit], 1):
            lines.append(f"{idx}. {item.display_name}（{item.mime_type}）→ {item.path}")
        if len(attachments) > limit:
            lines.append(f"… 其余 {len(attachments) - limit} 个附件未展开")
        lines.append("")
    else:
        lines.append("附件列表：-")
        lines.append("")

    history_intro = "以下为任务执行记录，用于辅助回溯任务处理记录："
    if history_block:
        lines.append(history_intro)
        lines.extend(history_block.splitlines())
    else:
        lines.append(f"{history_intro} -")

    return _strip_legacy_bug_header("\n".join(lines))


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
    """根据当前 parse_mode 输出可点击的任务命令文本。"""

    command = f"/{task_id}"
    if _IS_MARKDOWN and not _IS_MARKDOWN_V2:
        return command.replace("_", r"\_")
    return command


def _wrap_text_in_code_block(text: str) -> tuple[str, str]:
    """将推送消息包装为 Telegram 代码块，并返回渲染文本与 parse_mode。"""

    if MODEL_OUTPUT_PARSE_MODE == ParseMode.HTML:
        escaped = html.escape(text, quote=False)
        return f"<pre><code>{escaped}</code></pre>", ParseMode.HTML.value
    if MODEL_OUTPUT_PARSE_MODE == ParseMode.MARKDOWN_V2:
        # 先清理已有的 MarkdownV2 转义字符，避免重复转义导致显示反斜杠
        cleaned = _unescape_if_already_escaped(text)
        # 在代码块中只需要转义反引号和反斜杠
        escaped = cleaned.replace("\\", "\\\\").replace("`", "\\`")
        return f"```\n{escaped}\n```", ParseMode.MARKDOWN_V2.value
    # 默认退回 Telegram Markdown，保证代码块高亮可用
    return f"```\n{text}\n```", ParseMode.MARKDOWN.value


def _is_text_too_long_for_telegram(text: str) -> bool:
    """判断文本在当前 parse_mode 下是否会超过 Telegram 单条消息限制。"""

    prepared = _prepare_model_payload(text)
    return len(prepared) > TELEGRAM_MESSAGE_LIMIT


def _build_task_detail_failure_keyboard(task_id: str) -> InlineKeyboardMarkup:
    """任务详情兜底：当详情无法展示时提供“删除/重试/返回”等可操作入口。"""

    rows = [
        [
            InlineKeyboardButton(
                text="🗑️ 删除（归档）",
                callback_data=f"{TASK_DETAIL_DELETE_PROMPT_CALLBACK}:{task_id}",
            ),
            InlineKeyboardButton(
                text="🔄 重试",
                callback_data=f"task:refresh:{task_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                text="⬅️ 返回任务列表",
                callback_data=TASK_DETAIL_BACK_CALLBACK,
            )
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_task_delete_confirm_keyboard(task_id: str) -> InlineKeyboardMarkup:
    """任务删除二次确认键盘（归档语义，可恢复）。"""

    rows = [
        [
            InlineKeyboardButton(
                text="✅ 确认删除（可恢复）",
                callback_data=f"{TASK_DETAIL_DELETE_CONFIRM_CALLBACK}:{task_id}",
            ),
            InlineKeyboardButton(
                text="❌ 取消",
                callback_data=f"task:refresh:{task_id}",
            ),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_task_detail_overflow_summary(task_id: str, attachment_name: str) -> str:
    """生成“详情超长”时的摘要提示文案，确保可挂载操作按钮。"""

    task_text = _format_task_command(task_id)
    return "\n".join(
        [
            f"📄 任务详情（{task_text}）",
            f"⚠️ 详情内容过长，已生成附件 `{attachment_name}`，请下载查看全文。",
            "你仍可使用下方按钮继续操作（刷新/删除/返回）。",
        ]
    )


async def _send_task_detail_as_attachment(
    message: Message,
    *,
    task_id: str,
    detail_text: str,
    reply_markup: InlineKeyboardMarkup,
    prefer_edit: bool,
) -> tuple[Optional[Message], bool]:
    """任务详情超长时：发送摘要（含按钮）+ 全文附件（md）。"""

    # 说明：Telegram sendMessage 单条限制 4096 字符；详情超长时直接 edit/send 会失败。
    bot = current_bot()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    attachment_name = f"task-detail-{task_id}-{timestamp}.md"
    summary_text = _build_task_detail_overflow_summary(task_id, attachment_name)

    sent_summary: Optional[Message] = None
    edited_summary = False
    if prefer_edit:
        edited = await _try_edit_message(message, summary_text, reply_markup=reply_markup)
        edited_summary = bool(edited)
        if not edited_summary:
            sent_summary = await _answer_with_markdown(message, summary_text, reply_markup=reply_markup)
    else:
        sent_summary = await _answer_with_markdown(message, summary_text, reply_markup=reply_markup)

    document = BufferedInputFile(detail_text.encode("utf-8", errors="ignore"), filename=attachment_name)

    async def _send_document() -> None:
        await bot.send_document(chat_id=message.chat.id, document=document)

    try:
        await _send_with_retry(_send_document)
    except (TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter) as exc:
        # 文档发送失败时仍然保留摘要与操作按钮，避免用户完全卡死在详情页。
        worker_log.warning(
            "任务详情附件发送失败：%s",
            exc,
            extra={**_session_extra(), "task_id": task_id},
        )

    return sent_summary, edited_summary


async def _reply_task_detail_message(message: Message, task_id: str) -> None:
    try:
        detail_text, markup = await _render_task_detail(task_id)
    except ValueError:
        await _answer_with_markdown(message, f"任务 {task_id} 不存在")
        return
    if _is_text_too_long_for_telegram(detail_text):
        await _send_task_detail_as_attachment(
            message,
            task_id=task_id,
            detail_text=detail_text,
            reply_markup=markup,
            prefer_edit=False,
        )
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
            worker_log.warning("检测到未知任务状态：%s", value)
        return token
    if mapped != token and not quiet:
        worker_log.info("任务状态别名已自动转换：%s -> %s", token, mapped)
    return mapped


def _format_status(status: str) -> str:
    canonical = _canonical_status_token(status)
    if canonical and canonical in STATUS_LABELS:
        return STATUS_LABELS[canonical]
    return status


def _status_icon(status: Optional[str]) -> str:
    """提取状态对应的 emoji 图标，用于紧凑展示。"""

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
    # 避免把纯文字当图标
    if first_token[0].isalnum():
        return ""
    return first_token


def _strip_task_type_emoji(value: str) -> str:
    """去除前缀的任务类型 emoji，保持其余文本原样。"""

    trimmed = value.strip()
    emoji_prefixes = list(TASK_TYPE_EMOJIS.values()) + ["⚪"]
    for emoji in emoji_prefixes:
        if trimmed.startswith(emoji):
            return trimmed[len(emoji):].strip()
    return trimmed


def _format_task_type(task_type: Optional[str]) -> str:
    if not task_type:
        return "⚪ 未设置"
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
        return "⭐ 全部"
    canonical = _canonical_status_token(value)
    if canonical and canonical in STATUS_LABELS:
        return STATUS_LABELS[canonical]
    return value


def _build_status_filter_row(current_status: Optional[str], limit: int) -> list[list[InlineKeyboardButton]]:
    """构造任务列表顶部的状态筛选按钮，并根据数量动态换行。"""

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
    # 修复：智能清理预转义文本
    if not title_raw:
        title = "-"
    elif _IS_MARKDOWN_V2:
        # 智能清理预转义文本（保护代码块）
        title = _unescape_if_already_escaped(title_raw)
    else:
        title = _escape_markdown_text(title_raw)
    return f"{indent}- {title}"


def _compose_task_button_label(
    task: TaskRecord,
    *,
    max_length: int = 60,
    is_session_running: bool = False,
) -> str:
    """生成任务列表按钮文本：状态图标 + 任务编码 + 标题。"""

    title_raw = (task.title or "").strip()
    title = title_raw if title_raw else "-"
    status_icon = _status_icon(task.status)
    task_code = (task.id or "").strip()
    running_suffix = " ▶️" if is_session_running else ""

    prefix_parts: list[str] = []
    if status_icon:
        prefix_parts.append(status_icon)
    if task_code:
        prefix_parts.append(task_code)
    prefix = " ".join(part for part in prefix_parts if part).strip()

    separator = " " if prefix else ""
    reserved = len(prefix) + len(separator) + len(running_suffix)
    available = max_length - reserved

    if available <= 0:
        label = f"{prefix}{running_suffix}" if prefix else running_suffix.strip()
        if len(label) > max_length:
            label = label[: max_length - 1] + "…"
        return label

    if len(title) > available:
        truncated_title = "…" if available <= 1 else title[: available - 1] + "…"
    else:
        truncated_title = title

    if prefix:
        label = f"{prefix} {truncated_title}{running_suffix}"
    else:
        label = f"{truncated_title}{running_suffix}"
    if len(label) > max_length:
        label = label[: max_length - 1] + "…"
    return label


def _list_native_active_task_ids() -> set[str]:
    """汇总当前原生主会话正在绑定的任务，用于列表展示运行中图标。"""

    active_task_ids: set[str] = set()
    for session_key in CHAT_SESSION_MAP.values():
        normalized_task_id = _normalize_task_id(SESSION_TASK_BINDINGS.get(session_key))
        if normalized_task_id:
            active_task_ids.add(normalized_task_id)
    return active_task_ids


async def _list_active_parallel_sessions() -> list[ParallelSessionRecord]:
    """列出当前项目仍健康可用的并行会话，并自动降级 stale 记录。"""

    sessions = await PARALLEL_SESSION_STORE.list_sessions()
    active_sessions: list[ParallelSessionRecord] = []
    for session in sessions:
        if session.status in {"deleted", "closed"}:
            continue
        issue = _parallel_session_runtime_issue(session)
        if issue:
            await PARALLEL_SESSION_STORE.update_status(session.task_id, status="closed", last_error=issue)
            await _drop_parallel_session_bindings(session.task_id)
            worker_log.warning(
                "会话实况过滤掉 stale 并行会话，已自动降级为 closed",
                extra={"task_id": session.task_id, "issue": issue, "tmux_session": session.tmux_session},
            )
            continue
        active_sessions.append(session)
    return active_sessions


async def _list_running_task_ids_for_task_list() -> set[str]:
    """汇总任务列表中需要追加运行中图标的任务集合。"""

    task_ids = set(_list_native_active_task_ids())
    for session in await _list_active_parallel_sessions():
        normalized_task_id = _normalize_task_id(session.task_id)
        if normalized_task_id:
            task_ids.add(normalized_task_id)
    return task_ids


async def _build_native_session_live_entry() -> SessionLiveEntry:
    """构造主会话入口。"""

    native_task_ids = sorted(_list_native_active_task_ids())
    label = f"💻 主会话（{TMUX_SESSION}）"
    bound_task_id: Optional[str] = None
    if len(native_task_ids) == 1:
        bound_task_id = native_task_ids[0]
        task = await TASK_SERVICE.get_task(bound_task_id)
        title = ((task.title or "").strip() if task is not None else "") or "-"
        label = f"{label} · /{bound_task_id} {title}"
    elif len(native_task_ids) > 1:
        label = f"{label} · {len(native_task_ids)} 个任务上下文"
    if len(label) > 60:
        label = label[:59] + "…"
    return SessionLiveEntry(
        key="main",
        label=label,
        tmux_session=TMUX_SESSION,
        kind="main",
        task_id=bound_task_id,
    )


def _build_parallel_session_live_entry(session: ParallelSessionRecord) -> SessionLiveEntry:
    """构造并行会话入口。"""

    title = (session.title_snapshot or "").strip() or "-"
    label = f"/{session.task_id} {title}"
    if len(label) > 60:
        label = label[:59] + "…"
    return SessionLiveEntry(
        key=f"parallel:{session.task_id}",
        label=label,
        tmux_session=session.tmux_session,
        kind="parallel",
        task_id=session.task_id,
    )


async def _list_project_live_sessions() -> list[SessionLiveEntry]:
    """汇总“会话实况”列表要展示的主会话与活动并行会话。"""

    entries: list[SessionLiveEntry] = [await _build_native_session_live_entry()]
    parallel_sessions = await _list_active_parallel_sessions()
    entries.extend(_build_parallel_session_live_entry(session) for session in parallel_sessions)
    return entries


async def _resolve_session_live_entry(entry_key: str) -> Optional[SessionLiveEntry]:
    """根据入口键解析当前仍可查看的会话。"""

    normalized_key = (entry_key or "").strip()
    if normalized_key == "main":
        return await _build_native_session_live_entry()
    if normalized_key.startswith("parallel:"):
        task_id = _normalize_task_id(normalized_key.split(":", 1)[1])
        if not task_id:
            return None
        session = await _get_active_parallel_session_for_task(task_id)
        if session is None:
            return None
        return _build_parallel_session_live_entry(session)
    return None


def _build_push_existing_session_markup(entries: Sequence[SessionLiveEntry]) -> InlineKeyboardMarkup:
    """构造“现有 CLI 会话处理”的会话选择按钮。"""

    rows: list[list[InlineKeyboardButton]] = []
    for entry in entries:
        if entry.kind == "main":
            callback_data = PUSH_EXISTING_SESSION_MAIN_CALLBACK
        else:
            callback_data = f"{PUSH_EXISTING_SESSION_PARALLEL_PREFIX}{entry.task_id}"
        rows.append([InlineKeyboardButton(text=entry.label, callback_data=callback_data)])
    rows.append([InlineKeyboardButton(text="🔄 刷新会话列表", callback_data=PUSH_EXISTING_SESSION_REFRESH_CALLBACK)])
    rows.append([InlineKeyboardButton(text="❌ 取消推送", callback_data=PUSH_EXISTING_SESSION_CANCEL_CALLBACK)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _build_push_existing_session_view() -> tuple[str, InlineKeyboardMarkup]:
    """构造“现有 CLI 会话处理”的会话选择页。"""

    entries = await _list_project_live_sessions()
    return _build_push_existing_session_prompt(session_count=len(entries)), _build_push_existing_session_markup(entries)


async def _show_push_existing_session_view(message: Message, *, prefer_edit: bool = False) -> bool:
    """展示现有 CLI 会话选择页；编辑失败时回退为发送新消息。"""

    text, markup = await _build_push_existing_session_view()
    if prefer_edit and await _try_edit_message(message, text, reply_markup=markup):
        return True
    sent = await message.answer(text, reply_markup=markup)
    return sent is not None


def _build_task_batch_push_session_markup(entries: Sequence[SessionLiveEntry]) -> InlineKeyboardMarkup:
    """构造批量推送的现有会话选择按钮。"""

    rows: list[list[InlineKeyboardButton]] = []
    for entry in entries:
        if entry.kind == "main":
            callback_data = TASK_BATCH_PUSH_SESSION_MAIN_CALLBACK
        else:
            callback_data = f"{TASK_BATCH_PUSH_SESSION_PARALLEL_PREFIX}{entry.task_id}"
        rows.append([InlineKeyboardButton(text=entry.label, callback_data=callback_data)])
    rows.append([InlineKeyboardButton(text="🔄 刷新会话列表", callback_data=TASK_BATCH_PUSH_SESSION_REFRESH_CALLBACK)])
    rows.append([InlineKeyboardButton(text="⬅️ 返回勾选列表", callback_data=TASK_BATCH_PUSH_SESSION_CANCEL_CALLBACK)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _build_task_batch_push_existing_session_view() -> tuple[str, InlineKeyboardMarkup]:
    """构造批量推送的现有会话选择页。"""

    entries = await _list_project_live_sessions()
    return (
        _build_task_batch_push_existing_session_prompt(session_count=len(entries)),
        _build_task_batch_push_session_markup(entries),
    )


async def _show_task_batch_push_existing_session_view(message: Message) -> bool:
    """在当前消息中展示批量推送的现有会话选择页。"""

    text, markup = await _build_task_batch_push_existing_session_view()
    if await _try_edit_message(message, text, reply_markup=markup):
        return True
    sent = await _answer_with_markdown(message, text, reply_markup=markup)
    return sent is not None


async def _resolve_selected_existing_dispatch_context(data: Mapping[str, Any]) -> Optional[ParallelDispatchContext]:
    """根据状态中记录的“现有会话选择”解析真实派发上下文。"""

    selected_key = str(data.get("selected_existing_session_key") or "main").strip()
    if not selected_key or selected_key == "main":
        return None
    if not selected_key.startswith("parallel:"):
        raise ValueError("会话选择已失效，请重新点击推送到模型。")

    task_id = _normalize_task_id(selected_key.split(":", 1)[1])
    if not task_id:
        raise ValueError("会话选择已失效，请重新点击推送到模型。")
    session = await _get_active_parallel_session_for_task(task_id)
    if session is None:
        raise ValueError("所选会话已失效，请重新选择。")
    return _parallel_dispatch_context_from_session(session)


def _build_session_live_list_markup(entries: Sequence[SessionLiveEntry]) -> InlineKeyboardMarkup:
    """构造会话列表页按钮。"""

    rows: list[list[InlineKeyboardButton]] = []
    for entry in entries:
        if entry.kind == "main":
            callback_data = SESSION_LIVE_MAIN_CALLBACK
        else:
            callback_data = f"{SESSION_LIVE_PARALLEL_PREFIX}{entry.task_id}"
        rows.append([InlineKeyboardButton(text=entry.label, callback_data=callback_data)])
    rows.append([InlineKeyboardButton(text="🔄 刷新列表", callback_data=SESSION_LIVE_LIST_CALLBACK)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _build_session_live_list_view() -> tuple[str, InlineKeyboardMarkup]:
    """构造“会话实况”列表页。"""

    entries = await _list_project_live_sessions()
    lines = [
        "*会话实况*",
        f"当前项目可查看会话：{len(entries)} 个",
    ]
    if entries:
        lines.append("点击下方按钮查看对应会话的最近输出。")
    else:
        lines.append("当前没有可查看的会话。")
    return "\n".join(lines), _build_session_live_list_markup(entries)


def _build_session_live_snapshot_markup(entry: SessionLiveEntry) -> InlineKeyboardMarkup:
    """构造单会话实况页按钮。"""

    if entry.kind == "main":
        refresh_callback = SESSION_LIVE_REFRESH_MAIN_CALLBACK
    else:
        refresh_callback = f"{SESSION_LIVE_REFRESH_PARALLEL_PREFIX}{entry.task_id}"
    rows = [
        [InlineKeyboardButton(text="🔄 刷新当前会话", callback_data=refresh_callback)],
        [InlineKeyboardButton(text="⬅️ 返回会话列表", callback_data=SESSION_LIVE_LIST_CALLBACK)],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _build_session_live_snapshot_view(entry_key: str) -> tuple[str, InlineKeyboardMarkup]:
    """构造指定会话的最近输出视图。"""

    entry = await _resolve_session_live_entry(entry_key)
    if entry is None:
        raise ValueError("会话不存在或已失活，请返回会话列表刷新。")

    try:
        raw_output = await asyncio.to_thread(
            _capture_tmux_recent_lines,
            TMUX_SNAPSHOT_LINES,
            entry.tmux_session,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("未检测到 tmux，可通过 'brew install tmux' 安装后重试。") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"会话 {entry.label} 截取超时（{TMUX_SNAPSHOT_TIMEOUT_SECONDS:.1f} 秒），请稍后重试。"
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"无法读取 tmux 会话 {entry.tmux_session} 的输出，请确认会话仍在运行。") from exc

    if entry.kind == "main":
        _set_worker_plan_mode_state_cache(_resolve_worker_plan_mode_state_from_output(raw_output))

    cleaned = postprocess_tmux_output(raw_output)
    header = "\n".join(
        [
            "*会话实况*",
            f"会话：{entry.label}",
            f"最近 {TMUX_SNAPSHOT_LINES} 行：",
        ]
    )
    if cleaned:
        text = f"{header}\n\n{cleaned}"
    else:
        text = f"{header}\n\n暂无可展示的输出，请稍后再试。"
    return text, _build_session_live_snapshot_markup(entry)


TASK_ATTACHMENT_PREVIEW_LIMIT = 5


def _format_task_detail(
        task: TaskRecord,
        *,
        notes: Sequence[TaskNoteRecord],
        attachments: Sequence[TaskAttachmentRecord] = (),
    ) -> str:
    # 修复：智能处理预转义文本
    # - MarkdownV2 模式：先清理可能的预转义，再由 _prepare_model_payload() 统一处理
    # - 其他模式：手动转义
    title_raw = (task.title or "").strip()
    if _IS_MARKDOWN_V2:
        # 智能清理预转义文本（保护代码块）
        title_text = _unescape_if_already_escaped(title_raw) if title_raw else "-"
    else:
        title_text = _escape_markdown_text(title_raw) if title_raw else "-"

    task_id_text = _format_task_command(task.id)
    type_text = _strip_task_type_emoji(_format_task_type(task.task_type))
    if not type_text:
        type_text = "-"
    # 任务详情的元信息仅保留任务编码与类型，去除状态字段保持更紧凑展示
    meta_line = (
        f"🏷️ 任务编码：{task_id_text}"
        f" · 📂 类型：{type_text}"
    )
    lines: list[str] = [
        f"📝 标题：{title_text}",
        meta_line,
    ]

    # 修复：描述字段智能清理预转义
    normalized_task_type = _normalize_task_type(getattr(task, "task_type", None))
    parsed_structured = _parse_structured_task_description(normalized_task_type, task.description)
    labels = _get_structured_task_labels(normalized_task_type)
    if parsed_structured is not None and labels is not None:
        first_value_text = _format_task_detail_value(parsed_structured[0])
        second_value_text = _format_task_detail_value(parsed_structured[1])
        if normalized_task_type == "defect":
            lines.append(f"🧪 {labels[0]}：{first_value_text}")
            lines.append(f"🎯 {labels[1]}：{second_value_text}")
        else:
            lines.append(f"🧭 {labels[0]}：{first_value_text}")
            lines.append(f"🎯 {labels[1]}：{second_value_text}")
    else:
        description_text = _format_task_detail_value(task.description or "暂无")
        lines.append(f"🖊️ 描述：{description_text}")
    lines.append(f"📅 创建时间：{_format_local_time(task.created_at)}")
    lines.append(f"🔁 更新时间：{_format_local_time(task.updated_at)}")

    # 修复：父任务ID字段智能清理预转义
    if task.parent_id:
        if _IS_MARKDOWN_V2:
            # 智能清理预转义文本（保护代码块）
            parent_text = _unescape_if_already_escaped(task.parent_id)
        else:
            parent_text = _escape_markdown_text(task.parent_id)
        lines.append(f"👪 父任务：{parent_text}")

    related_task_id = (getattr(task, "related_task_id", None) or "").strip()
    if related_task_id:
        lines.append(f"🔗 关联任务：{_format_task_command(related_task_id)}")

    # 附件预览
    if attachments:
        lines.append("📎 附件：")
        limit = TASK_ATTACHMENT_PREVIEW_LIMIT
        for idx, item in enumerate(attachments[:limit], 1):
            display_raw = _clean_user_text(item.display_name or "-")
            mime_raw = _clean_user_text(item.mime_type or "-")
            path_raw = _clean_user_text(item.path or "-")
            if _IS_MARKDOWN_V2:
                display = display_raw
                mime = mime_raw
                path_text = path_raw
            else:
                display = _escape_markdown_text(display_raw)
                mime = _escape_markdown_text(mime_raw)
                path_text = _escape_markdown_text(path_raw)
            lines.append(f"{idx}. {display}（{mime}）→ {path_text}")
        if len(attachments) > limit:
            lines.append(f"… 其余 {len(attachments) - limit} 个附件未展开，可继续使用 /attach {task.id} 查看/追加")
    else:
        lines.append("📎 附件：-")

    return "\n".join(lines)


def _parse_history_payload(payload_raw: Optional[str]) -> dict[str, Any]:
    if not payload_raw:
        return {}
    try:
        data = json.loads(payload_raw)
    except json.JSONDecodeError:
        worker_log.warning("历史 payload 解析失败：%s", payload_raw, extra=_session_extra())
        return {}
    if isinstance(data, dict):
        return data
    worker_log.warning("历史 payload 类型异常：%s", type(data), extra=_session_extra())
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


def _filter_history_records(records: Sequence[TaskHistoryRecord]) -> list[TaskHistoryRecord]:
    """过滤掉无需展示的历史记录，例如附件绑定事件。"""

    filtered: list[TaskHistoryRecord] = []
    for item in records:
        event = (item.event_type or "").strip().lower()
        field = (item.field or "").strip().lower()
        if event == "attachment_added" or field == "attachment":
            continue
        filtered.append(item)
    return filtered


def _history_field_label(field: Optional[str]) -> str:
    """返回历史字段的中文标签。"""

    token = (field or "").strip().lower()
    if not token:
        return "字段"
    return HISTORY_FIELD_LABELS.get(token, token)


def _format_history_value(field: Optional[str], value: Optional[str]) -> str:
    """将字段值转为更易读的文本。"""

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
            return "已归档"
        if lowered in {"false", "0", "no"}:
            return "未归档"
    return text


def _format_history_timestamp(value: Optional[str]) -> str:
    """将历史时间压缩为“月-日 小时:分钟”格式，减少自动换行。"""

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
    """生成首行摘要，突出按钮语义。"""

    event_type = (item.event_type or HISTORY_EVENT_FIELD_CHANGE).strip() or HISTORY_EVENT_FIELD_CHANGE
    payload = _parse_history_payload(item.payload)
    if event_type == HISTORY_EVENT_FIELD_CHANGE:
        field = (item.field or "").strip().lower()
        if field == "create":
            return "创建任务"
        return f"更新{_history_field_label(field)}"
    if event_type == HISTORY_EVENT_TASK_ACTION:
        action = payload.get("action") if isinstance(payload, dict) else None
        if action == "add_note":
            note_type = payload.get("note_type", "misc") if isinstance(payload, dict) else "misc"
            if note_type and note_type != "misc":
                return f"添加备注（{_format_note_type(note_type)}）"
            return "添加备注"
        if action == "push_model":
            return "推送到模型"
        if action == "bug_report":
            return "报告缺陷"
        if action == "summary_request":
            return "生成模型摘要"
        if action == "model_session":
            return "记录模型会话"
        label = action or (item.field or "任务动作")
        return f"执行操作：{label}"
    if event_type == HISTORY_EVENT_MODEL_REPLY:
        return "模型回复"
    if event_type == HISTORY_EVENT_MODEL_SUMMARY:
        return "模型摘要"
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
            return f"标题：\"{title_text}\"" if title_text != "-" else "标题：-"
        old_text = _format_history_value(field, item.old_value)
        new_text = _format_history_value(field, item.new_value)
        if old_text == "-" and new_text != "-":
            return f"{label}：{new_text}"
        return f"{label}：{old_text} -> {new_text}"
    if event_type == HISTORY_EVENT_TASK_ACTION:
        action = payload.get("action")
        if action == "add_note":
            note_type = payload.get("note_type", "misc")
            content_text = _trim_history_value(item.new_value)
            lines: list[str] = []
            if note_type and note_type != "misc":
                lines.append(f"类型：{_format_note_type(note_type)}")
            lines.append(f"内容：{content_text}")
            return "\n".join(lines)
        if action == "push_model":
            details: list[str] = []
            supplement_text: Optional[str] = None
            result = payload.get("result") or "success"
            details.append(f"结果：{result}")
            model_name = payload.get("model")
            if model_name:
                details.append(f"模型：{model_name}")
            history_items = payload.get("history_items")
            if isinstance(history_items, int) and history_items > 0:
                details.append(f"包含事件：{history_items}条")
            supplement_raw = payload.get("supplement")
            if supplement_raw is None and payload.get("has_supplement"):
                supplement_raw = item.new_value
            if supplement_raw is not None:
                supplement_text = _clean_user_text(_trim_history_value(str(supplement_raw)))
            detail_text = "；".join(details) if details else "已触发"
            if supplement_text and supplement_text != "-":
                return f"{detail_text}\n补充描述：{supplement_text}"
            if payload.get("has_supplement") and (item.new_value or "").strip():
                supplement_fallback = _clean_user_text(_trim_history_value(item.new_value))
                if supplement_fallback != "-":
                    return f"{detail_text}\n补充描述：{supplement_fallback}"
            return detail_text
        if action == "bug_report":
            has_logs = bool(payload.get("has_logs"))
            has_repro = bool(payload.get("has_reproduction"))
            note_preview = _trim_history_value(item.new_value)
            details = ["缺陷描述：" + (note_preview or "-")]
            details.append(f"包含复现：{'是' if has_repro else '否'}")
            details.append(f"包含日志：{'是' if has_logs else '否'}")
            return "\n".join(details)
        if action == "summary_request":
            request_id = payload.get("request_id") or (item.new_value or "-")
            model_name = payload.get("model")
            lines = [f"摘要请求 ID：{request_id}"]
            if model_name:
                lines.append(f"目标模型：{model_name}")
            return "\n".join(lines)
        if action == "model_session":
            session = payload.get("session")
            return f"模型会话：{session or '-'}"
        label = action or (item.field or "动作")
        return f"{label}：{_trim_history_value(item.new_value)}"
    if event_type == HISTORY_EVENT_MODEL_REPLY:
        model_name = payload.get("model") or payload.get("source") or ""
        content = payload.get("content") or item.new_value
        text = _trim_history_value(content, limit=HISTORY_MODEL_REPLY_LIMIT)
        prefix = f"{model_name} 回复" if model_name else "模型回复"
        return f"{prefix}：{text}"
    if event_type == HISTORY_EVENT_MODEL_SUMMARY:
        payload_content = payload.get("content") if isinstance(payload, dict) else None
        content = payload_content or item.new_value
        text = _trim_history_value(content, limit=HISTORY_MODEL_SUMMARY_LIMIT)
        return f"摘要内容：{text}"
    fallback_field = item.field or event_type
    return f"{fallback_field}：{_trim_history_value(item.new_value)}"


def _format_history_line(item: TaskHistoryRecord) -> str:
    """以 Markdown 列表形式构建历史文本，首行展示摘要，后续为缩进详情。"""

    timestamp = _format_history_timestamp(item.created_at)
    summary = _format_history_summary(item)
    description = _format_history_description(item)
    detail_lines = [
        line.strip()
        for line in description.splitlines()
        if line.strip()
    ]
    # Markdown 列表使用"- "起始，后续详情以缩进列表呈现，便于聊天端渲染。
    # MarkdownV2 使用单星号 * 表示加粗
    formatted = [f"- *{summary}* · {timestamp}"]
    for detail in detail_lines:
        formatted.append(f"  - {detail}")
    formatted.append("")  # 追加空行分隔历史记录
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
    history = _filter_history_records(await TASK_SERVICE.list_history(task_id))
    if not history:
        return "", 0
    selected = history[-MODEL_HISTORY_MAX_ITEMS:]
    lines = [_format_history_line_for_model(item) for item in selected]
    trimmed_lines = _trim_history_lines_for_limit(lines, MODEL_HISTORY_MAX_CHARS)
    return "\n".join(trimmed_lines), len(trimmed_lines)


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
    """封装任务事件写入，出现异常时记录日志避免打断主流程。"""

    data_payload: Optional[Dict[str, Any]]
    if payload is None:
        data_payload = {"action": action}
    else:
        data_payload = {"action": action, **payload}
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
            "任务事件写入失败：%s",
            exc,
            extra={"task_id": task_id, **_session_extra()},
        )


async def _auto_push_after_bug_report(task: TaskRecord, *, message: Message, actor: Optional[str]) -> None:
    """缺陷上报完成后尝试自动推送模型，保持与手动推送一致的提示格式。"""

    chat_id = message.chat.id
    if task.status not in MODEL_PUSH_ELIGIBLE_STATUSES:
        await _reply_to_chat(
            chat_id,
            "缺陷已记录，当前状态暂不支持自动推送到模型，如需同步请调整任务状态后手动推送。",
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
            "自动推送到模型失败：模板缺失",
            exc_info=exc,
            extra={"task_id": task.id, "status": task.status},
        )
        await _reply_to_chat(
            chat_id,
            "缺陷已记录，但推送模板缺失，请稍后手动重试推送到模型。",
            reply_to=message,
            reply_markup=_build_worker_main_keyboard(),
        )
        return
    if not success:
        await _reply_to_chat(
            chat_id,
            "缺陷已记录，模型当前未就绪，请稍后手动重新推送。",
            reply_to=message,
            reply_markup=_build_worker_main_keyboard(),
        )
        return
    preview_block, preview_parse_mode = _wrap_text_in_code_block(prompt)
    # 复用“推送到模型”的预览发送逻辑：当预览超出 Telegram 单条限制时自动降级为附件，
    # 避免因 TelegramBadRequest: message is too long 导致流程中断（用户看不到成功提示且底部菜单不恢复）。
    await _send_model_push_preview(
        chat_id,
        preview_block,
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
            text = f"{text} (当前)"
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
                text="✏️ 编辑字段",
                callback_data=f"task:edit:{task.id}",
            ),
            InlineKeyboardButton(
                # 任务详情：移除“归档任务/恢复任务”按钮，用“添加附件”替换到该位置；
                # 归档/恢复仍可通过 /task_delete 命令完成。
                text="📎 添加附件",
                callback_data=f"task:attach:{task.id}",
            ),
        ]
    )
    keyboard.append(
        [
            InlineKeyboardButton(
                text="🚨 报告缺陷",
                callback_data=f"task:bug_report:{task.id}",
            ),
            InlineKeyboardButton(
                # 任务详情：按 TASK_0060 调整按钮布局：
                # - 移除“查看历史”入口（历史能力仍保留，但不在详情页暴露入口）
                # - 将“删除（归档）”移动到原“查看历史”位置，并复用既有二次确认流程
                text="🗑️ 删除（归档）",
                callback_data=f"{TASK_DETAIL_DELETE_PROMPT_CALLBACK}:{task.id}",
            ),
        ]
    )
    if task.status in MODEL_PUSH_ELIGIBLE_STATUSES:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text="🚀 推送到模型",
                    callback_data=f"task:push_model:{task.id}",
                )
            ]
        )
    keyboard.append(
        [
            InlineKeyboardButton(
                text="⬅️ 返回任务列表",
                callback_data=TASK_DETAIL_BACK_CALLBACK,
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def _build_task_desc_confirm_keyboard() -> ReplyKeyboardMarkup:
    """任务描述确认阶段的菜单按钮。"""

    rows = [
        [KeyboardButton(text=TASK_DESC_CONFIRM_TEXT)],
        [KeyboardButton(text=TASK_DESC_RETRY_TEXT), KeyboardButton(text=TASK_DESC_CANCEL_TEXT)],
    ]
    _number_reply_buttons(rows)
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


def _build_task_desc_input_keyboard() -> ReplyKeyboardMarkup:
    """任务描述输入阶段的菜单按钮。"""

    rows = [
        [KeyboardButton(text=TASK_DESC_CLEAR_TEXT), KeyboardButton(text=TASK_DESC_REPROMPT_TEXT)],
        [KeyboardButton(text=TASK_DESC_CANCEL_TEXT)],
    ]
    _number_reply_buttons(rows)
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=False)


def _build_task_desc_cancel_keyboard() -> ReplyKeyboardMarkup:
    """仅保留取消操作的菜单，用于提示场景。"""

    rows = [[KeyboardButton(text=TASK_DESC_CANCEL_TEXT)]]
    _number_reply_buttons(rows)
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


def _build_task_desc_confirm_text(preview_segment: str) -> str:
    """生成任务描述确认阶段的提示文案。"""

    return (
        "请确认新的任务描述：\n"
        f"{preview_segment}\n\n"
        "1. 点击“✅ 确认更新”立即保存\n"
        "2. 点击“✏️ 重新输入”重新填写描述\n"
        "3. 点击“❌ 取消”终止本次编辑"
    )


async def _prompt_task_description_input(
    target: Optional[Message],
    *,
    current_description: str,
) -> None:
    """向用户展示当前描述，提供取消按钮及后续操作提示。"""

    if target is None:
        # Telegram 已删除原消息时直接忽略，避免流程中断。
        return
    preview = (current_description or "").strip()
    preview_segment = preview or "（当前描述为空，确认后将保存为空）"
    await target.answer(
        "当前描述如下，可复制后直接编辑，菜单中的选项可快速完成清空或取消操作。",
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
        "请直接发送新的任务描述，或通过菜单按钮执行快捷操作。",
    )


async def _begin_task_desc_edit_flow(
    *,
    state: FSMContext,
    task: TaskRecord,
    actor: str,
    origin_message: Optional[Message],
) -> None:
    """统一初始化任务描述编辑 FSM，兼容回调与命令入口。"""

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
            "发送消息失败：%s",
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
            "编辑任务列表消息失败，将改用新消息展示",
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
    rows.append([KeyboardButton(text="取消")])
    _number_reply_buttons(rows)
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


def _build_description_keyboard() -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=SKIP_TEXT)],
        [KeyboardButton(text="取消")],
    ]
    _number_reply_buttons(rows)
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


def _build_parallel_reply_input_keyboard() -> ReplyKeyboardMarkup:
    """并行回复输入态键盘：仅保留取消按钮，减少误触。"""

    rows = [[KeyboardButton(text="取消")]]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


def _build_push_mode_keyboard() -> ReplyKeyboardMarkup:
    """推送到模型：模式选择阶段菜单按钮。"""

    rows = [
        [KeyboardButton(text=PUSH_MODE_PLAN), KeyboardButton(text=PUSH_MODE_YOLO)],
        [KeyboardButton(text="取消")],
    ]
    _number_reply_buttons(rows)
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


def _build_push_dispatch_target_keyboard() -> ReplyKeyboardMarkup:
    """推送到模型：处理方式选择阶段菜单按钮。"""

    rows = [
        [KeyboardButton(text=PUSH_TARGET_CURRENT), KeyboardButton(text=PUSH_TARGET_PARALLEL)],
        [KeyboardButton(text="取消")],
    ]
    _number_reply_buttons(rows)
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


def _build_related_task_action_keyboard() -> ReplyKeyboardMarkup:
    """缺陷创建：关联任务选择阶段的菜单栏按钮。"""

    rows = [
        [KeyboardButton(text=SKIP_TEXT)],
        [KeyboardButton(text="取消创建任务")],
    ]
    _number_reply_buttons(rows)
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


def _build_confirm_keyboard() -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="✅ 确认创建")],
        [KeyboardButton(text="❌ 取消")],
    ]
    _number_reply_buttons(rows)
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


def _build_bug_confirm_keyboard() -> ReplyKeyboardMarkup:
    """缺陷提交流程确认键盘。"""

    rows = [
        [KeyboardButton(text="✅ 确认提交")],
        [KeyboardButton(text="❌ 取消")],
    ]
    _number_reply_buttons(rows)
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


def _collect_message_payload(
    message: Message,
    attachments: Sequence[TelegramSavedAttachment] | None = None,
    *,
    text_override: Optional[str] = None,
) -> str:
    """提取消息中的文字与附件信息，优先输出已落地的本地路径。"""

    parts: list[str] = []
    text = _normalize_choice_token(text_override if text_override is not None else (message.text or message.caption))
    if text:
        parts.append(text)

    # 若调用方已经下载附件，优先输出本地路径，避免仅展示 file_id
    attachments = tuple(attachments or ())
    if attachments:
        for item in attachments:
            path_hint = item.relative_path or item.display_name or item.kind
            parts.append(f"[附件:{path_hint}]")
    else:
        # 兼容未传入附件的场景，回退到 Telegram file_id 标识
        if message.photo:
            file_id = message.photo[-1].file_id
            parts.append(f"[图片:{file_id}]")
        if message.document:
            doc = message.document
            name = doc.file_name or doc.file_id
            parts.append(f"[文件:{name}]")
        if message.voice:
            parts.append(f"[语音:{message.voice.file_id}]")
        if message.video:
            parts.append(f"[视频:{message.video.file_id}]")

    return "\n".join(parts).strip()


def _summarize_note_text(value: str) -> str:
    """压缩备注内容，维持主要信息并控制长度。"""

    cleaned = normalize_newlines(value or "").strip()
    return cleaned.replace("\n", " / ")


def _build_bug_report_intro(task: TaskRecord) -> str:
    """生成缺陷报告开场提示。"""

    # 直接拼接命令文本，确保提示语中不出现 Markdown 转义后的反斜杠。
    task_code = f"/{task.id}" if task.id else "-"
    title = task.title or "-"
    return (
        f"正在为任务 {task_code}（{title}）记录缺陷。\n"
        "请先描述缺陷现象（必填），例如发生了什么、期待的行为是什么，可直接发送图片/文件作为附件。"
    )


def _build_defect_report_intro(task: TaskRecord) -> str:
    """生成“报告缺陷=创建缺陷任务”的开场提示。"""

    # 直接拼接命令文本，确保提示语中不出现 Markdown 转义后的反斜杠。
    task_code = f"/{task.id}" if task.id else "-"
    title = task.title or "-"
    return (
        f"正在为任务 {task_code}（{title}）创建缺陷任务。\n"
        "请输入缺陷标题（必填），例如：登录按钮点击无响应。"
    )


def _build_bug_repro_prompt() -> str:
    """生成复现步骤提示。"""

    return (
        "若有复现步骤，请按顺序列出，例如：\n"
        "1. 打开页面...\n"
        "2. 操作...\n"
        "如暂无可发送“跳过”，发送“取消”随时结束流程。"
    )


def _build_bug_log_prompt() -> str:
    """生成日志信息提示。"""

    return (
        "请提供错误日志、截图或相关附件，可直接发送图片/文件作为附件。\n"
        "若无额外信息，可发送“跳过”，发送“取消”结束流程。"
    )


def _build_bug_preview_text(
    *,
    task: TaskRecord,
    description: str,
    reproduction: str,
    logs: str,
    reporter: str,
) -> str:
    """构建缺陷预览文本，便于用户确认。"""

    # 预览信息面向纯文本消息，直接使用任务命令避免额外的反斜杠。
    task_code = f"/{task.id}" if task.id else "-"
    parts = [
        f"任务编码：{task_code}",
        f"缺陷描述：{description or '-'}",
        f"复现步骤：{reproduction or '-'}",
        f"日志信息：{logs or '-'}",
        f"报告人：{reporter}",
    ]
    return "\n".join(parts)


def _build_summary_prompt(
    task: TaskRecord,
    *,
    request_id: str,
    history_text: str,
    notes: Sequence[TaskNoteRecord],
) -> str:
    """构造模型摘要提示词，要求携带请求标识。"""

    # 摘要提示词是发送给模型的，使用纯文本格式，不需要 Markdown 转义
    task_code = f"/{task.id}" if task.id else "-"
    title = task.title or "-"
    status_label = STATUS_LABELS.get(task.status, task.status)
    note_lines: list[str] = []
    if notes:
        note_lines.append("备注汇总：")
        for note in notes[-5:]:
            label = NOTE_LABELS.get(note.note_type or "", note.note_type or "备注")
            content = _summarize_note_text(note.content or "")
            timestamp = _format_local_time(note.created_at)
            note_lines.append(f"- [{label}] {timestamp} — {content or '-'}")
    else:
        note_lines.append("备注汇总：-")
    history_lines = ["历史记录："]
    if history_text.strip():
        history_lines.extend(history_text.splitlines())
    else:
        history_lines.append("-")
    instructions = [
        "进入摘要阶段...",
        f"任务编码：{task_code}",
        f"SUMMARY_REQUEST_ID::{request_id}，模型必须原样回传。",
        "",
        f"任务标题：{title}",
        f"任务阶段：{status_label}",
        f"优先级：{task.priority}",
        "",
        f"请基于以下信息为任务 {task_code} 生成处理摘要。",
        "输出要求：",
        "- 第一行必须原样包含 SUMMARY_REQUEST_ID::{request_id}。",
        "- 汇总任务目标、近期动作、当前状态与待办事项。",
        "- 采用项目同事可直接阅读的简洁段落或列表格式。",
        "- 若存在未解决缺陷或测试问题请明确指出。",
        "",
    ]
    instructions.extend(note_lines)
    instructions.append("")
    instructions.extend(history_lines)
    instructions.append("")
    instructions.append("请在输出末尾补充下一步建议。")
    return "\n".join(instructions)


def _build_push_supplement_prompt() -> str:
    return (
        "请输入补充任务描述，建议说明任务背景与期望结果，支持直接发送图片/文件作为附件。\n"
        "若暂时没有可点击“跳过”按钮或直接发送空消息，发送“取消”可终止。"
    )


def _build_push_mode_prompt() -> str:
    """推送到模型：构建 PLAN/YOLO 模式选择提示文案。"""

    return "请选择本次推送到模型的模式：PLAN / YOLO（发送“取消”退出）"


def _build_push_send_mode_prompt() -> str:
    """推送到模型：构建立即/排队发送方式选择提示。"""

    return "请选择本次发送方式：立即发送 / 排队发送（发送“取消”退出）"


def _build_push_dispatch_target_prompt() -> str:
    """推送到模型：构建“现有 CLI 会话 / 并行 CLI”选择提示。"""

    return "请选择处理方式：现有 CLI 会话处理 / 新建分支 + 新 CLI 并行处理（发送“取消”退出）"


def _build_push_existing_session_prompt(*, session_count: int) -> str:
    """推送到模型：构建“现有 CLI 会话选择”提示。"""

    return f"请选择要推送到哪个现有 CLI 会话（当前共 {session_count} 个，发送“取消”可退出）"


def _build_task_batch_push_existing_session_prompt(*, session_count: int) -> str:
    """批量推送：构建“现有 CLI 会话选择”提示。"""

    return f"请选择本批任务要推送到哪个现有 CLI 会话（当前共 {session_count} 个，发送“取消”可返回勾选列表）"


def _build_quick_reply_partial_supplement_prompt() -> str:
    """构建“部分按推荐（需补充）”的补充输入提示文案。"""

    return (
        "请发送需要补充的说明（未提及的决策项默认按推荐）。\n"
        "发送“跳过”表示全部按推荐，发送“取消”退出。"
    )


async def _prompt_quick_reply_partial_supplement_input(message: Message) -> None:
    """提示用户输入“部分按推荐（需补充）”的补充说明。"""

    await message.answer(
        _build_quick_reply_partial_supplement_prompt(),
        reply_markup=_build_description_keyboard(),
    )

async def _prompt_push_mode_input(message: Message) -> None:
    """推送到模型：提示用户选择 PLAN/YOLO 模式。"""

    await message.answer(
        _build_push_mode_prompt(),
        reply_markup=_build_push_mode_keyboard(),
    )


def _build_push_send_mode_keyboard() -> ReplyKeyboardMarkup:
    """推送到模型：发送方式选择阶段菜单按钮。"""

    rows = [
        [KeyboardButton(text=PUSH_SEND_MODE_IMMEDIATE_LABEL)],
        [KeyboardButton(text=PUSH_SEND_MODE_QUEUED_LABEL)],
        [KeyboardButton(text="取消")],
    ]
    _number_reply_buttons(rows)
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


async def _prompt_push_send_mode_input(message: Message, *, push_mode: Optional[str] = None) -> None:
    """推送到模型：提示用户选择立即发送或排队发送。"""

    prompt = _build_push_send_mode_prompt()
    if push_mode:
        prompt = f"已选择 {push_mode} 模式。\n{prompt}"
    await message.answer(
        prompt,
        reply_markup=_build_push_send_mode_keyboard(),
    )


async def _prompt_push_dispatch_target_input(message: Message) -> None:
    """推送到模型：提示用户选择当前 CLI 或并行 CLI。"""

    await message.answer(
        _build_push_dispatch_target_prompt(),
        reply_markup=_build_push_dispatch_target_keyboard(),
    )


async def _continue_push_after_existing_session_selected(
    *,
    message: Message,
    state: FSMContext,
    selected_entry_key: str,
) -> None:
    """在用户选定“现有 CLI 会话”后，继续原推送流程。"""

    data = await state.get_data()
    task_id = (data.get("task_id") or "").strip()
    if not task_id:
        await state.clear()
        await message.answer("推送会话已失效，请重新点击按钮。", reply_markup=_build_worker_main_keyboard())
        return

    task = await TASK_SERVICE.get_task(task_id)
    if task is None:
        await state.clear()
        await message.answer("任务不存在，已取消推送。", reply_markup=_build_worker_main_keyboard())
        return

    entry = await _resolve_session_live_entry(selected_entry_key)
    if entry is None:
        raise ValueError("会话不存在或已失活，请刷新会话列表后重试。")

    await state.update_data(selected_existing_session_key=entry.key)
    if task.status in MODEL_PUSH_SUPPLEMENT_STATUSES:
        await state.set_state(TaskPushStates.waiting_choice)
        await _prompt_push_mode_input(message)
        return

    chat_id = data.get("chat_id") or message.chat.id
    origin_message = data.get("origin_message") or message
    actor = data.get("actor") or _actor_from_message(message)
    dispatch_context = await _resolve_selected_existing_dispatch_context(await state.get_data())
    await state.clear()
    try:
        success, prompt, session_path = await _push_task_to_model(
            task,
            chat_id=chat_id,
            reply_to=origin_message,
            supplement=None,
            actor=actor,
            dispatch_context=dispatch_context,
        )
    except ValueError as exc:
        await message.answer(f"推送失败：{exc}", reply_markup=_build_worker_main_keyboard())
        return

    if not success:
        await message.answer("推送失败：模型未就绪，请稍后再试。", reply_markup=_build_worker_main_keyboard())
        return

    preview_block, preview_parse_mode = _wrap_text_in_code_block(prompt)
    await _send_model_push_preview(
        chat_id,
        preview_block,
        reply_to=origin_message,
        parse_mode=preview_parse_mode,
        reply_markup=_build_worker_main_keyboard(),
    )
    if session_path is not None:
        await _send_session_ack(chat_id, session_path, reply_to=origin_message)


async def _restore_task_batch_push_view(
    *,
    target_message: Optional[Message],
    fallback_message: Message,
    status: Optional[str],
    page: int,
    limit: int,
    selected_task_ids: Sequence[str],
    selected_task_order: Sequence[str],
) -> None:
    """恢复批量推送勾选视图。"""

    text, markup = await _build_task_batch_push_view(
        status=status,
        page=page,
        limit=limit,
        selected_task_ids=selected_task_ids,
        selected_task_order=selected_task_order,
    )
    view_state = _make_batch_push_view_state(
        status=status,
        page=page,
        limit=limit,
        selected_task_ids=selected_task_ids,
        selected_task_order=selected_task_order,
    )
    if await _try_edit_message(target_message, text, reply_markup=markup):
        _set_task_view_context(target_message, view_state)
        return
    origin_chat = getattr(target_message, "chat", None)
    if target_message is not None and origin_chat is not None:
        _clear_task_view(origin_chat.id, target_message.message_id)
    sent = await _answer_with_markdown(fallback_message, text, reply_markup=markup)
    if sent is not None:
        _init_task_view_context(sent, view_state)


async def _restore_task_list_after_batch_push(
    *,
    target_message: Optional[Message],
    fallback_message: Message,
    status: Optional[str],
    page: int,
    limit: int,
) -> None:
    """批量推送结束后恢复普通任务列表视图。"""

    text, markup = await _build_task_list_view(status=status, page=page, limit=limit)
    view_state = _make_list_view_state(status=status, page=page, limit=limit)
    if await _try_edit_message(target_message, text, reply_markup=markup):
        _set_task_view_context(target_message, view_state)
        return
    origin_chat = getattr(target_message, "chat", None)
    if target_message is not None and origin_chat is not None:
        _clear_task_view(origin_chat.id, target_message.message_id)
    sent = await _answer_with_markdown(fallback_message, text, reply_markup=markup)
    if sent is not None:
        _init_task_view_context(sent, view_state)


def _format_task_batch_push_summary(
    *,
    task_ids: Sequence[str],
    push_mode: str,
    session_label: str,
    success_items: Sequence[str],
    failed_items: Sequence[tuple[str, str]],
    skipped_items: Sequence[tuple[str, str]],
) -> str:
    """格式化批量推送结果摘要。"""

    lines = [
        "*批量推送结果*",
        f"目标会话：{session_label or '-'}",
        f"推送模式：{push_mode}",
        f"发送方式：{PUSH_SEND_MODE_QUEUED_LABEL}",
        (
            f"总览：{len(task_ids)} 个任务｜成功 {len(success_items)}"
            f"｜失败 {len(failed_items)}｜跳过 {len(skipped_items)}"
        ),
    ]
    if success_items:
        lines.append("")
        lines.append(f"✅ 已排队（{len(success_items)}）")
        lines.extend(f"- /{task_id}" for task_id in success_items)
    if failed_items:
        lines.append("")
        lines.append(f"❌ 失败（{len(failed_items)}）")
        for task_id, reason in failed_items:
            lines.append(f"- /{task_id}：{reason}")
    if skipped_items:
        lines.append("")
        lines.append(f"⏭️ 已跳过（{len(skipped_items)}）")
        for task_id, reason in skipped_items:
            lines.append(f"- /{task_id}：{reason}")
    return "\n".join(lines)


async def _execute_task_batch_push(
    *,
    trigger_message: Message,
    state: FSMContext,
    push_mode: str,
) -> None:
    """执行已选任务的批量排队推送。"""

    data = await state.get_data()
    task_ids = [str(item).strip() for item in (data.get("batch_task_ids") or []) if str(item).strip()]
    origin_message = data.get("batch_origin_message")
    chat_id = int(data.get("chat_id") or trigger_message.chat.id)
    actor = data.get("actor") or _actor_from_message(trigger_message)
    status = data.get("batch_status")
    page = int(data.get("batch_page", 1) or 1)
    limit = int(data.get("batch_limit", DEFAULT_PAGE_SIZE) or DEFAULT_PAGE_SIZE)
    session_label = str(data.get("batch_session_label") or "").strip() or "主会话"

    dispatch_context = await _resolve_selected_existing_dispatch_context(data)
    success_items: list[str] = []
    failed_items: list[tuple[str, str]] = []
    skipped_items: list[tuple[str, str]] = []

    for task_id in task_ids:
        task = await TASK_SERVICE.get_task(task_id)
        if task is None:
            failed_items.append((task_id, "任务不存在"))
            continue
        if task.status not in MODEL_PUSH_ELIGIBLE_STATUSES:
            skipped_items.append((task_id, f"状态 {task.status!r} 当前不支持推送"))
            continue
        try:
            success, _prompt, _session_path = await _push_task_to_model(
                task,
                chat_id=chat_id,
                reply_to=origin_message,
                supplement=None,
                actor=actor,
                push_mode=push_mode,
                send_mode=PUSH_SEND_MODE_QUEUED,
                dispatch_context=dispatch_context,
            )
        except ValueError as exc:
            failed_items.append((task_id, str(exc)))
            continue
        if success:
            success_items.append(task_id)
        else:
            failed_items.append((task_id, "模型未就绪，请稍后再试"))

    await state.clear()
    await _restore_task_list_after_batch_push(
        target_message=origin_message,
        fallback_message=trigger_message,
        status=status,
        page=page,
        limit=limit,
    )
    summary = _format_task_batch_push_summary(
        task_ids=task_ids,
        push_mode=push_mode,
        session_label=session_label,
        success_items=success_items,
        failed_items=failed_items,
        skipped_items=skipped_items,
    )
    await _answer_with_markdown(
        trigger_message,
        summary,
        reply_markup=_build_worker_main_keyboard(),
    )


def _create_parallel_launch_token() -> str:
    return uuid.uuid4().hex[:8]


def _parallel_dispatch_context_from_session(session: ParallelSessionRecord | Mapping[str, Any]) -> ParallelDispatchContext:
    if isinstance(session, Mapping):
        return ParallelDispatchContext(
            task_id=_normalize_task_id(session.get("task_id")) or "",
            tmux_session=str(session.get("tmux_session") or TMUX_SESSION),
            pointer_file=Path(str(session.get("pointer_file") or CODEX_SESSION_FILE_PATH or "")),
            workspace_root=Path(str(session.get("workspace_root") or (os.environ.get("MODEL_WORKDIR") or CODEX_WORKDIR or ROOT_DIR_PATH))),
        )
    return ParallelDispatchContext(
        task_id=session.task_id,
        tmux_session=session.tmux_session,
        pointer_file=Path(session.pointer_file),
        workspace_root=Path(session.workspace_root),
    )


async def _begin_parallel_launch(
    *,
    task: TaskRecord,
    chat_id: int,
    origin_message: Optional[Message],
    actor: Optional[str],
    push_mode: Optional[str],
    send_mode: Optional[str],
    supplement: Optional[str],
) -> None:
    """开始并行分支选择与创建流程。"""

    existing = await _get_active_parallel_session_for_task(task.id)
    if existing is not None and existing.status != "deleted":
        try:
            success, prompt, session_path = await _push_task_to_model(
                task,
                chat_id=chat_id,
                reply_to=origin_message,
                supplement=supplement,
                actor=actor,
                push_mode=push_mode,
                send_mode=send_mode,
                dispatch_context=_parallel_dispatch_context_from_session(existing),
            )
        except ValueError as exc:
            if origin_message is not None:
                await origin_message.answer(f"推送失败：{exc}", reply_markup=_build_worker_main_keyboard())
            return
        if not success:
            if origin_message is not None:
                await origin_message.answer("推送失败：并行 CLI 未就绪，请稍后再试。", reply_markup=_build_worker_main_keyboard())
            return
        if origin_message is not None:
            preview_block, preview_parse_mode = _wrap_text_in_code_block(prompt)
            await _send_model_push_preview(
                chat_id,
                preview_block,
                reply_to=origin_message,
                parse_mode=preview_parse_mode,
                reply_markup=_build_worker_main_keyboard(),
            )
            if session_path is not None:
                await _send_session_ack(chat_id, session_path, reply_to=origin_message)
        return

    base_dir = PRIMARY_WORKDIR or ROOT_DIR_PATH
    repos = discover_git_repos(base_dir, include_nested=True)
    if not repos:
        if origin_message is not None:
            await origin_message.answer("当前工作目录下未发现 Git 仓库，无法创建并行分支。", reply_markup=_build_worker_main_keyboard())
        return

    repo_options: list[tuple[str, Path, str, list[BranchRef]]] = []
    current_branch_labels: dict[str, str] = {}
    for repo_key, repo_path, relative_path in repos:
        current_branch_label, current_local_branch = get_current_branch_state(repo_path)
        branches = list_branch_refs(repo_path, current_local_branch=current_local_branch)
        if not branches:
            if origin_message is not None:
                await origin_message.answer(
                    f"仓库 {relative_path or '.'} 未发现可选分支，已中止并行创建。",
                    reply_markup=_build_worker_main_keyboard(),
                )
            return
        repo_options.append((repo_key, repo_path, relative_path, branches))
        current_branch_labels[repo_key] = current_branch_label

    common_branch_repo_options, ignored_common_branch_repos = filter_common_branch_repo_options(
        [(repo_key, relative_path, branches) for repo_key, _repo_path, relative_path, branches in repo_options]
    )
    common_branch_options = collect_common_branch_refs(common_branch_repo_options)
    common_branch_repo_keys = {repo_key for repo_key, _branches in common_branch_repo_options}
    token = _create_parallel_launch_token()
    session = ParallelLaunchSession(
        token=token,
        task=task,
        chat_id=chat_id,
        actor=actor,
        origin_message=origin_message,
        push_mode=push_mode,
        send_mode=send_mode,
        supplement=supplement,
        repo_options=repo_options,
        selections={},
        current_branch_labels=current_branch_labels,
        base_dir=base_dir,
        common_branch_options=common_branch_options,
        common_branch_repo_keys=common_branch_repo_keys,
        common_branch_scope_repo_count=len(common_branch_repo_options),
        common_branch_ignored_repos=ignored_common_branch_repos,
        selection_mode="bulk" if common_branch_options else "individual",
    )
    PARALLEL_LAUNCH_SESSIONS[token] = session
    if origin_message is not None:
        if common_branch_options:
            await _answer_with_markdown(
                origin_message,
                _build_parallel_common_branch_title(session),
                reply_markup=_build_parallel_common_branch_keyboard(session, page=0),
            )
        else:
            await _answer_with_markdown(
                origin_message,
                _build_parallel_branch_title(session, 0),
                reply_markup=_build_parallel_branch_keyboard(session, repo_index=0, page=0),
            )


@router.callback_query(F.data.startswith(PARALLEL_COMMON_BRANCH_PAGE_PREFIX))
async def on_parallel_common_branch_page_callback(callback: CallbackQuery) -> None:
    payload = (callback.data or "")[len(PARALLEL_COMMON_BRANCH_PAGE_PREFIX) :]
    token, page_text = (payload.split(":", 1) + ["0"])[:2]
    session = PARALLEL_LAUNCH_SESSIONS.get(token)
    if session is None:
        await callback.answer("分支选择会话已失效", show_alert=True)
        return
    page = int(page_text) if page_text.isdigit() else 0
    await callback.answer()
    if callback.message is not None:
        await _render_parallel_branch_flow_message(
            callback.message,
            _build_parallel_common_branch_title(session),
            reply_markup=_build_parallel_common_branch_keyboard(session, page=page),
        )


@router.callback_query(F.data.startswith(PARALLEL_COMMON_BRANCH_SELECT_PREFIX))
async def on_parallel_common_branch_select_callback(callback: CallbackQuery) -> None:
    payload = (callback.data or "")[len(PARALLEL_COMMON_BRANCH_SELECT_PREFIX) :]
    token, branch_index_text = (payload.split(":", 1) + ["0"])[:2]
    session = PARALLEL_LAUNCH_SESSIONS.get(token)
    if session is None:
        await callback.answer("分支选择会话已失效", show_alert=True)
        return
    branch_index = int(branch_index_text) if branch_index_text.isdigit() else 0
    if branch_index < 0 or branch_index >= len(session.common_branch_options):
        await callback.answer("分支不存在", show_alert=True)
        return
    common_branch = session.common_branch_options[branch_index]
    selections: dict[str, BranchRef] = {}
    scoped_repo_keys = session.common_branch_repo_keys or {repo_key for repo_key, _repo_path, _rel, _branches in session.repo_options}
    for repo_key, _repo_path, _rel, branches in session.repo_options:
        if repo_key not in scoped_repo_keys:
            continue
        matched = next(
            (
                branch
                for branch in branches
                if branch.name == common_branch.name and branch.source == common_branch.source
            ),
            None,
        )
        if matched is None:
            await callback.answer("共同分支已失效，请改用逐个选择。", show_alert=True)
            return
        selections[repo_key] = matched
    session.selection_mode = "bulk"
    session.selections = selections
    next_index = _find_next_unselected_repo_index(session)
    if next_index is not None:
        session.selection_mode = "individual"
        await callback.answer(f"已批量应用共同分支 {common_branch.name}，请继续补选剩余仓库。")
        if callback.message is not None:
            await _render_parallel_branch_flow_message(
                callback.message,
                _build_parallel_branch_title(session, next_index),
                reply_markup=_build_parallel_branch_keyboard(session, repo_index=next_index, page=0),
            )
        return
    await callback.answer(f"已选择共同分支 {common_branch.name}")
    if callback.message is not None:
        await _render_parallel_branch_flow_message(
            callback.message,
            _build_parallel_branch_summary(session),
            reply_markup=_build_parallel_branch_summary_keyboard(session),
        )


@router.callback_query(F.data.startswith(PARALLEL_BRANCH_INDIVIDUAL_PREFIX))
async def on_parallel_branch_individual_callback(callback: CallbackQuery) -> None:
    token = (callback.data or "")[len(PARALLEL_BRANCH_INDIVIDUAL_PREFIX) :]
    session = PARALLEL_LAUNCH_SESSIONS.get(token)
    if session is None:
        await callback.answer("分支选择会话已失效", show_alert=True)
        return
    session.selection_mode = "individual"
    await callback.answer("已切换为逐个选择")
    if callback.message is not None:
        next_index = _find_next_unselected_repo_index(session)
        if next_index is None:
            await _render_parallel_branch_flow_message(
                callback.message,
                _build_parallel_branch_summary(session),
                reply_markup=_build_parallel_branch_summary_keyboard(session),
            )
            return
        await _render_parallel_branch_flow_message(
            callback.message,
            _build_parallel_branch_title(session, next_index),
            reply_markup=_build_parallel_branch_keyboard(session, repo_index=next_index, page=0),
        )


@router.callback_query(F.data.startswith(PARALLEL_BRANCH_PAGE_PREFIX))
async def on_parallel_branch_page_callback(callback: CallbackQuery) -> None:
    payload = (callback.data or "")[len(PARALLEL_BRANCH_PAGE_PREFIX) :]
    token, repo_index_text, page_text = (payload.split(":", 2) + ["0", "0"])[:3]
    session = PARALLEL_LAUNCH_SESSIONS.get(token)
    if session is None:
        await callback.answer("分支选择会话已失效", show_alert=True)
        return
    repo_index = int(repo_index_text) if repo_index_text.isdigit() else 0
    page = int(page_text) if page_text.isdigit() else 0
    await callback.answer()
    if callback.message is not None:
        await _render_parallel_branch_flow_message(
            callback.message,
            _build_parallel_branch_title(session, repo_index),
            reply_markup=_build_parallel_branch_keyboard(session, repo_index=repo_index, page=page),
        )


@router.callback_query(F.data.startswith(PARALLEL_BRANCH_SELECT_PREFIX))
async def on_parallel_branch_select_callback(callback: CallbackQuery) -> None:
    payload = (callback.data or "")[len(PARALLEL_BRANCH_SELECT_PREFIX) :]
    token, repo_index_text, branch_index_text = (payload.split(":", 2) + ["0", "0"])[:3]
    session = PARALLEL_LAUNCH_SESSIONS.get(token)
    if session is None:
        await callback.answer("分支选择会话已失效", show_alert=True)
        return
    repo_index = int(repo_index_text) if repo_index_text.isdigit() else 0
    branch_index = int(branch_index_text) if branch_index_text.isdigit() else 0
    repo_key, _repo_path, _rel, branches = session.repo_options[repo_index]
    if branch_index < 0 or branch_index >= len(branches):
        await callback.answer("分支不存在", show_alert=True)
        return
    branch = branches[branch_index]
    session.selection_mode = "individual"
    session.selections[repo_key] = branch
    next_index = _find_next_unselected_repo_index(session, start=repo_index + 1, wrap=True)
    await callback.answer(f"已选择 {branch.name}")
    if callback.message is None:
        return
    if next_index is not None:
        await _render_parallel_branch_flow_message(
            callback.message,
            _build_parallel_branch_title(session, next_index),
            reply_markup=_build_parallel_branch_keyboard(session, repo_index=next_index, page=0),
        )
        return
    await _render_parallel_branch_flow_message(
        callback.message,
        _build_parallel_branch_summary(session),
        reply_markup=_build_parallel_branch_summary_keyboard(session),
    )


def _clear_parallel_branch_prefix_input(*, token: Optional[str] = None, chat_id: Optional[int] = None) -> None:
    """清理等待输入分支前缀的 chat 绑定。"""

    if chat_id is not None:
        CHAT_PARALLEL_BRANCH_PREFIX_INPUTS.pop(chat_id, None)
    if token is not None:
        stale_chat_ids = [item_chat_id for item_chat_id, item_token in CHAT_PARALLEL_BRANCH_PREFIX_INPUTS.items() if item_token == token]
        for item_chat_id in stale_chat_ids:
            CHAT_PARALLEL_BRANCH_PREFIX_INPUTS.pop(item_chat_id, None)


async def _start_parallel_launch_session(
    session: ParallelLaunchSession,
    *,
    trigger_message: Optional[Message],
) -> None:
    """在前缀已确定后，继续执行并行副本创建与 CLI 启动。"""

    token = session.token
    PARALLEL_LAUNCH_SESSIONS.pop(token, None)
    _clear_parallel_branch_prefix_input(token=token, chat_id=session.chat_id)

    task = session.task
    workspace_root = _parallel_workspace_root(task.id)
    selections = [
        RepoBranchSelection(
            repo_key=repo_key,
            source_repo_path=repo_path,
            selected_ref=session.selections[repo_key].name,
            selected_remote=session.selections[repo_key].remote,
            relative_path=rel,
        )
        for repo_key, repo_path, rel, _branches in session.repo_options
    ]

    await _render_parallel_branch_flow_message(
        trigger_message,
        PARALLEL_BRANCH_PREPARING_MESSAGE,
        reply_markup=None,
    )

    try:
        prepare_kwargs = {
            "workspace_root": workspace_root,
            "task_id": task.id,
            "title": task.title,
            "selections": selections,
            "source_root": session.base_dir or PRIMARY_WORKDIR or ROOT_DIR_PATH,
            "branch_prefix": session.branch_prefix or DEFAULT_PARALLEL_BRANCH_PREFIX,
        }
        if PARALLEL_WORKSPACE_PREPARE_TIMEOUT_SECONDS > 0:
            try:
                repo_records = await asyncio.wait_for(
                    asyncio.to_thread(prepare_parallel_workspace, **prepare_kwargs),
                    timeout=PARALLEL_WORKSPACE_PREPARE_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError as exc:
                raise RuntimeError(
                    f"并行副本准备超时（{PARALLEL_WORKSPACE_PREPARE_TIMEOUT_SECONDS:.2f}s），请检查 Git 网络或仓库状态后重试。"
                ) from exc
        else:
            repo_records = await asyncio.to_thread(prepare_parallel_workspace, **prepare_kwargs)
        await _render_parallel_branch_flow_message(
            trigger_message,
            PARALLEL_BRANCH_STARTING_CLI_MESSAGE,
            reply_markup=None,
        )
        await _ensure_codex_trusted_project_path(
            workspace_root,
            scope=CODEX_TRUST_SCOPE_PARALLEL_WORKSPACE,
            owner_key=task.id,
        )
        tmux_session, pointer_file = await _start_parallel_tmux_session(task, workspace_root)
        await PARALLEL_SESSION_STORE.upsert_session(
            task_id=task.id,
            title_snapshot=task.title,
            workspace_root=str(workspace_root),
            tmux_session=tmux_session,
            pointer_file=str(pointer_file),
            task_branch=build_parallel_branch_name(task.id, task.title, prefix=session.branch_prefix),
            status="running",
            repos=repo_records,
        )
        success, prompt, session_path = await _push_task_to_model(
            task,
            chat_id=session.chat_id,
            reply_to=session.origin_message or trigger_message,
            supplement=session.supplement,
            actor=session.actor,
            push_mode=session.push_mode,
            send_mode=session.send_mode,
            dispatch_context=ParallelDispatchContext(
                task_id=task.id,
                tmux_session=tmux_session,
                pointer_file=pointer_file,
                workspace_root=workspace_root,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(_parallel_runtime_root(task.id), ignore_errors=True)
        await PARALLEL_SESSION_STORE.update_status(task.id, status="merge_failed", last_error=str(exc))
        await _reply_to_chat(
            session.chat_id,
            f"并行创建失败：{exc}",
            reply_to=session.origin_message or trigger_message,
            reply_markup=_build_worker_main_keyboard(),
        )
        return

    if not success:
        await PARALLEL_SESSION_STORE.update_status(
            task.id,
            status="closed",
            last_error="并行 CLI 未启动成功：首次推送未建立 fresh session",
        )
        await _reply_to_chat(
            session.chat_id,
            "并行 CLI 未启动成功，请稍后重试。",
            reply_to=session.origin_message or trigger_message,
            reply_markup=_build_worker_main_keyboard(),
        )
        return

    if trigger_message is not None:
        task_branch = build_parallel_branch_name(task.id, task.title, prefix=session.branch_prefix)
        summary_lines = [
            "已创建并行开发副本（原目录未改动）：",
            f"- 任务：/{task.id} {task.title}",
            f"- 分支前缀：{session.branch_prefix or DEFAULT_PARALLEL_BRANCH_PREFIX}",
            f"- 任务分支：{task_branch}",
        ]
        for repo in selections:
            summary_lines.append(f"- {repo.relative_path or '.'} -> {repo.selected_ref}")
        await _render_parallel_branch_flow_message(
            trigger_message,
            "\n".join(summary_lines),
            reply_markup=None,
        )
        preview_block, preview_parse_mode = _wrap_text_in_code_block(prompt)
        await _send_model_push_preview(
            session.chat_id,
            preview_block,
            reply_to=session.origin_message or trigger_message,
            parse_mode=preview_parse_mode,
            reply_markup=_build_worker_main_keyboard(),
        )
        if session_path is not None:
            await _send_session_ack(session.chat_id, session_path, reply_to=session.origin_message or trigger_message)


@router.callback_query(F.data.startswith(PARALLEL_BRANCH_CANCEL_PREFIX))
async def on_parallel_branch_cancel_callback(callback: CallbackQuery) -> None:
    token = (callback.data or "")[len(PARALLEL_BRANCH_CANCEL_PREFIX) :]
    PARALLEL_LAUNCH_SESSIONS.pop(token, None)
    _clear_parallel_branch_prefix_input(token=token)
    await callback.answer("已取消并行创建")
    if callback.message is not None:
        await _render_parallel_branch_flow_message(callback.message, "已取消并行创建。", reply_markup=None)


@router.callback_query(F.data.startswith(PARALLEL_BRANCH_CONFIRM_PREFIX))
async def on_parallel_branch_confirm_callback(callback: CallbackQuery) -> None:
    token = (callback.data or "")[len(PARALLEL_BRANCH_CONFIRM_PREFIX) :]
    session = PARALLEL_LAUNCH_SESSIONS.get(token)
    if session is None:
        await callback.answer("并行创建会话已失效", show_alert=True)
        return
    if _find_next_unselected_repo_index(session) is not None:
        await callback.answer("仍有仓库未选择基线分支", show_alert=True)
        return
    if session.branch_prefix is None:
        CHAT_PARALLEL_BRANCH_PREFIX_INPUTS[session.chat_id] = token
        await callback.answer("请输入分支前缀；发送取消将使用默认前缀")
        if callback.message is not None:
            await _answer_with_markdown(
                callback.message,
                _build_parallel_branch_prefix_prompt(session),
                reply_markup=_build_parallel_branch_prefix_input_keyboard(),
            )
        return

    await _answer_callback_safely(callback)
    await _start_parallel_launch_session(session, trigger_message=callback.message)


@router.callback_query(F.data.startswith(PARALLEL_BRANCH_PREFIX_CANCEL_PREFIX))
async def on_parallel_branch_prefix_cancel_callback(callback: CallbackQuery) -> None:
    token = (callback.data or "")[len(PARALLEL_BRANCH_PREFIX_CANCEL_PREFIX) :]
    session = PARALLEL_LAUNCH_SESSIONS.get(token)
    if session is None:
        await callback.answer("分支选择会话已失效", show_alert=True)
        return
    session.branch_prefix = DEFAULT_PARALLEL_BRANCH_PREFIX
    await _answer_callback_safely(callback)
    await _start_parallel_launch_session(session, trigger_message=callback.message)


async def _prompt_model_supplement_input(
    message: Message,
    *,
    push_mode: Optional[str] = None,
    send_mode: Optional[str] = None,
) -> None:
    """推送到模型：提示用户输入补充描述，可选展示已选择的模式与发送方式。"""

    prompt = _build_push_supplement_prompt()
    prefix_parts: list[str] = []
    if push_mode:
        prefix_parts.append(f"已选择 {push_mode} 模式")
    if send_mode:
        prefix_parts.append(_push_send_mode_label(send_mode))
    if prefix_parts:
        prompt = f"{'，'.join(prefix_parts)}。\n{prompt}"
    await message.answer(
        prompt,
        reply_markup=_build_description_keyboard(),
    )


def _build_task_search_prompt() -> str:
    return (
        "请输入任务搜索关键词（至少 2 个字符），支持标题和描述模糊匹配。\n"
        "发送“跳过”或“取消”可返回任务列表。"
    )


async def _prompt_task_search_keyword(message: Message) -> None:
    await message.answer(
        _build_task_search_prompt(),
        reply_markup=_build_description_keyboard(),
    )


def _build_edit_field_keyboard() -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="标题"), KeyboardButton(text="优先级")],
        [KeyboardButton(text="类型"), KeyboardButton(text="描述")],
        [KeyboardButton(text="状态")],
        [KeyboardButton(text="取消")],
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
        raise ValueError("任务不存在")
    notes = await TASK_SERVICE.list_notes(task_id)
    history: Sequence[TaskHistoryRecord]
    if include_history:
        history = _filter_history_records(await TASK_SERVICE.list_history(task_id))
    else:
        history = ()
    return task, notes, history


async def _render_task_detail(task_id: str) -> tuple[str, InlineKeyboardMarkup]:
    task, notes, _ = await _load_task_context(task_id)
    attachments = await TASK_SERVICE.list_attachments(task_id)
    detail_text = _format_task_detail(task, notes=notes, attachments=attachments)
    return detail_text, _build_task_actions(task)


@dataclass(**_DATACLASS_SLOT_KW)
class _HistoryViewPage:
    """历史分页渲染所需的文本切片。"""

    lines: list[str]
    notice: str
    truncated: bool


def _build_truncated_history_entry(item: TaskHistoryRecord) -> str:
    """生成单条历史的截断提示文本，保留摘要时间信息。"""

    timestamp = _format_history_timestamp(item.created_at)
    summary = _format_history_summary(item)
    return "\n".join(
        [
            f"- *{summary}* · {timestamp}",
            "  - ⚠️ 该记录内容较长，仅展示摘要概要。",
        ]
    )


def _select_truncation_variant(
    entry_text: str,
    *,
    notice: str,
    body_limit: int,
) -> tuple[str, str]:
    """在长度限制内挑选截断文本与提示。"""

    variants = [
        (entry_text, notice),
        ("- ⚠️ 历史记录内容过长，已简化展示。", notice),
        ("- ⚠️ 历史记录内容过长，已简化展示。", HISTORY_TRUNCATION_NOTICE_SHORT),
        ("- ⚠️ 已截断", HISTORY_TRUNCATION_NOTICE_SHORT),
    ]
    for candidate_text, candidate_notice in variants:
        combined = "\n\n".join([candidate_text, candidate_notice])
        if len(_prepare_model_payload(combined)) <= body_limit:
            return candidate_text, candidate_notice
    # 最差情况下仅返回极短提示，避免再次触发超长错误。
    fallback_text = "- ⚠️ 历史记录已截断，详细内容请导出查看。"
    return fallback_text, HISTORY_TRUNCATION_NOTICE_SHORT


def _build_task_history_view(
    task: TaskRecord,
    history: Sequence[TaskHistoryRecord],
    *,
    page: int,
) -> tuple[str, InlineKeyboardMarkup, int, int]:
    """根据任务历史构造分页视图内容与内联按钮。"""

    limited = list(history[-MODEL_HISTORY_MAX_ITEMS:])
    total_items = len(limited)
    if total_items == 0:
        raise ValueError("暂无事件记录")

    # 历史记录会被包裹在代码块中显示，使用纯文本格式，不需要 Markdown 转义
    title_text = normalize_newlines(task.title or "").strip() or "-"
    title_display = title_text

    digit_width = len(str(max(total_items, 1)))
    placeholder_page = "9" * digit_width
    header_placeholder = "\n".join(
        [
            f"任务 {task.id} 事件历史（最近 {total_items} 条）",
            f"标题：{title_display}",
            f"页码：{placeholder_page} / {placeholder_page}",
        ]
    )
    header_reserved = len(_prepare_model_payload(header_placeholder))
    # 保留额外两个换行为正文与抬头的分隔，确保总长度不超 4096。
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
            # 单条记录即超出限制，需降级展示并追加截断提示。
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
        # 未能放入默认提示时至少保留简短信息。
        notice_text = HISTORY_TRUNCATION_NOTICE_SHORT
    if notice_text:
        body_segments.append(notice_text)
    body_text = "\n\n".join(body_segments).strip()

    header_text = "\n".join(
        [
            f"任务 {task.id} 事件历史（最近 {total_items} 条）",
            f"标题：{title_display}",
            f"页码：{normalized_page} / {total_pages}",
        ]
    )
    text = f"{header_text}\n\n{body_text}" if body_text else header_text
    prepared = _prepare_model_payload(text)
    if len(prepared) > TELEGRAM_MESSAGE_LIMIT:
        worker_log.warning(
            "历史视图仍超过 Telegram 限制，使用安全提示内容",
            extra={"task_id": task.id, "page": str(normalized_page), "length": str(len(prepared))},
        )
        text = "\n".join(
            [
                f"任务 {task.id} 事件历史（最近 {total_items} 条）",
                f"标题：{title_display}",
                f"页码：{normalized_page} / {total_pages}",
                "",
                "⚠️ 历史记录内容超出 Telegram 长度限制，请导出或筛选后重试。",
            ]
        )

    nav_row: list[InlineKeyboardButton] = []
    if normalized_page > 1:
        nav_row.append(
            InlineKeyboardButton(
                text="⬅️ 上一页",
                callback_data=f"{TASK_HISTORY_PAGE_CALLBACK}:{task.id}:{normalized_page - 1}",
            )
        )
    if normalized_page < total_pages:
        nav_row.append(
            InlineKeyboardButton(
                text="下一页 ➡️",
                callback_data=f"{TASK_HISTORY_PAGE_CALLBACK}:{task.id}:{normalized_page + 1}",
            )
        )

    keyboard_rows: list[list[InlineKeyboardButton]] = []
    if nav_row:
        keyboard_rows.append(nav_row)
    keyboard_rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ 返回任务详情",
                callback_data=f"{TASK_HISTORY_BACK_CALLBACK}:{task.id}",
            )
        ]
    )

    return text, InlineKeyboardMarkup(inline_keyboard=keyboard_rows), normalized_page, total_pages


async def _render_task_history(
    task_id: str,
    page: int,
) -> tuple[str, InlineKeyboardMarkup, int, int]:
    """渲染指定任务的历史视图，返回内容、按钮及页码信息。"""

    task, _notes, history_records = await _load_task_context(task_id, include_history=True)
    trimmed = list(history_records[-MODEL_HISTORY_MAX_ITEMS:])
    if not trimmed:
        raise ValueError("暂无事件记录")
    return _build_task_history_view(task, trimmed, page=page)


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


ANSI_ESCAPE_RE = re.compile(r"\x1B[@-_][0-?]*[ -/]*[@-~]")
# 终端底部协作模式标识，例如：Plan mode / Plan mode (shift+tab to cycle)
TERMINAL_COLLABORATION_MODE_RE = re.compile(
    r"\b([a-z]+)\s+mode(?:\s*\(shift\+tab\s+to\s+cycle\))?\b",
    re.IGNORECASE,
)


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
    """将会话路径转换为日志使用的标识。"""
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
    # 从日志文件偏移量开始读取，直到连续 idle 秒无新增或超时
    start = time.time()
    p = resolve_path(log_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # 等待日志文件出现
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
PARALLEL_TASK_SESSION_MAP: Dict[str, str] = {}
PARALLEL_TASK_WATCHERS: Dict[str, asyncio.Task] = {}
PARALLEL_SESSION_CONTEXTS: Dict[str, "ParallelDispatchContext"] = {}
PARALLEL_CALLBACK_BINDINGS: Dict[str, Any] = {}
CHAT_LAST_MESSAGE: Dict[int, Dict[str, str]] = {}
CHAT_FAILURE_NOTICES: Dict[int, float] = {}
CHAT_PLAN_MESSAGES: Dict[int, int] = {}
CHAT_PLAN_TEXT: Dict[int, str] = {}
CHAT_PLAN_COMPLETION: Dict[int, bool] = {}
CHAT_DELIVERED_HASHES: Dict[int, Dict[str, set[str]]] = {}
CHAT_DELIVERED_OFFSETS: Dict[int, Dict[str, set[int]]] = {}
CHAT_MESSAGE_RECOVERY_POLL_TASKS: Dict[int, asyncio.Task] = {}
CHAT_REPLY_COUNT: Dict[int, Dict[str, int]] = {}
CHAT_COMPACT_STATE: Dict[int, Dict[str, Dict[str, Any]]] = {}
# 记录每个 chat 最近一次向模型发起请求的用户（用于按钮权限校验）。
CHAT_ACTIVE_USERS: Dict[int, int] = {}
# request_user_input：token -> session
REQUEST_INPUT_SESSIONS: Dict[str, RequestInputSession] = {}
# request_user_input：每个 chat 当前“自定义文本输入焦点”token。
# 说明：同 chat 可并存多个按钮交互，但自由文本一次只路由到一个会话。
CHAT_ACTIVE_REQUEST_INPUT_TOKENS: Dict[int, str] = {}
# plan confirm：token -> session
PLAN_CONFIRM_SESSIONS: Dict[str, PlanConfirmSession] = {}
# plan confirm：每个 chat 最近一次下发 token（仅用于最新态记录，旧 token 仍可并存）
CHAT_ACTIVE_PLAN_CONFIRM_TOKENS: Dict[int, str] = {}
# Plan Yes 并发点击幂等保护：记录当前正在处理中的确认 token。
PLAN_CONFIRM_PROCESSING_TOKENS: set[str] = set()
# 长轮询状态：用于延迟轮询机制
CHAT_LONG_POLL_STATE: Dict[int, Dict[str, Any]] = {}
CHAT_LONG_POLL_LOCK: Optional[asyncio.Lock] = None  # 在事件循环启动后初始化
SUMMARY_REQUEST_TIMEOUT_SECONDS = 300.0


@dataclass(**_DATACLASS_SLOT_KW)
class PendingSummary:
    """记录待落库的模型摘要请求。"""

    task_id: str
    request_id: str
    actor: Optional[str]
    session_key: str
    session_path: Path
    created_at: float
    buffer: str = ""


PENDING_SUMMARIES: Dict[str, PendingSummary] = {}
# 会话与任务的绑定关系：用于在“模型答案消息”底部提供一键入口（如切换到测试）
SESSION_TASK_BINDINGS: Dict[str, str] = {}
PARALLEL_SESSION_TASK_BINDINGS: Dict[str, str] = {}
CHAT_PARALLEL_REPLY_TARGETS: Dict[int, dict[str, Any]] = {}
SESSION_COMMIT_CALLBACK_BINDINGS: Dict[str, "SessionCommitBinding"] = {}
SESSION_QUICK_REPLY_CALLBACK_BINDINGS: Dict[str, "SessionQuickReplyBinding"] = {}


@dataclass
class ParallelLaunchSession:
    """描述并行分支选择与创建过程中的临时交互会话。"""

    token: str
    task: TaskRecord
    chat_id: int
    actor: Optional[str]
    origin_message: Optional[Message]
    push_mode: Optional[str]
    send_mode: Optional[str]
    supplement: Optional[str]
    repo_options: list[tuple[str, Path, str, list[BranchRef]]]
    selections: dict[str, BranchRef]
    current_branch_labels: dict[str, str]
    base_dir: Optional[Path] = None
    common_branch_options: list[CommonBranchRef] = field(default_factory=list)
    common_branch_repo_keys: set[str] = field(default_factory=set)
    common_branch_scope_repo_count: int = 0
    common_branch_ignored_repos: list[str] = field(default_factory=list)
    selection_mode: str = "individual"
    branch_prefix: Optional[str] = None
    created_at: float = field(default_factory=time.time)


PARALLEL_LAUNCH_SESSIONS: Dict[str, ParallelLaunchSession] = {}
CHAT_PARALLEL_BRANCH_PREFIX_INPUTS: Dict[int, str] = {}


@dataclass
class ParallelDispatchContext:
    """描述向指定并行 tmux/session pointer 推送消息所需的上下文。"""

    task_id: str
    tmux_session: str
    pointer_file: Path
    workspace_root: Path


@dataclass
class ParallelCallbackBinding:
    """描述并行消息底部按钮到并行上下文的精确路由绑定。"""

    token: str
    task_id: str
    session_key: str
    dispatch_context: ParallelDispatchContext
    title_snapshot: Optional[str] = None
    created_at: float = field(default_factory=time.time)


@dataclass
class SessionCommitBinding:
    """描述原生会话“提交分支”按钮到具体工作目录的绑定。"""

    token: str
    task_id: str
    session_key: str
    workspace_root: Path
    created_at: float = field(default_factory=time.time)


@dataclass
class SessionQuickReplyBinding:
    """描述原生会话 quick reply 按钮到具体 session 的绑定。"""

    token: str
    task_id: str
    session_key: str
    created_at: float = field(default_factory=time.time)


def _bind_session_task(session_key: str, task_id: str) -> None:
    """将 session_key 与 task_id 绑定，便于从会话回溯当前任务。"""

    key = (session_key or "").strip()
    normalized_task_id = _normalize_task_id(task_id)
    if not key or not normalized_task_id:
        return
    SESSION_TASK_BINDINGS[key] = normalized_task_id


def _bind_parallel_session_task(session_key: str, task_id: str) -> None:
    """将 session_key 绑定到并行任务，便于渲染并行消息底部按钮。"""

    key = (session_key or "").strip()
    normalized_task_id = _normalize_task_id(task_id)
    if not key or not normalized_task_id:
        return
    PARALLEL_SESSION_TASK_BINDINGS[key] = normalized_task_id
    PARALLEL_TASK_SESSION_MAP[normalized_task_id] = key


def _bind_parallel_dispatch_context(session_key: str, dispatch_context: ParallelDispatchContext) -> None:
    """缓存并行会话的精确派发上下文，避免回落到原生会话。"""

    key = (session_key or "").strip()
    task_id = _normalize_task_id(dispatch_context.task_id)
    if not key or not task_id:
        return
    PARALLEL_SESSION_CONTEXTS[key] = dispatch_context
    PARALLEL_TASK_SESSION_MAP[task_id] = key


def _build_parallel_callback_payload(task_id: str, token: str) -> str:
    """构造并行按钮的 session-scoped payload。"""

    return f"{task_id}:{token}"


def _build_session_commit_callback_payload(task_id: str, token: str) -> str:
    """构造原生会话“提交分支”按钮的 session-scoped payload。"""

    return f"{task_id}:{token}"


def _build_session_quick_reply_callback_payload(task_id: str, token: str) -> str:
    """构造原生会话 quick reply 的 session-scoped payload。"""

    return f"{task_id}:{token}"


def _parse_parallel_callback_payload(raw_payload: str) -> tuple[Optional[str], Optional[str]]:
    """解析并行按钮 payload，兼容旧版仅 task_id 的数据格式。"""

    payload = (raw_payload or "").strip()
    if not payload:
        return None, None
    task_id = _normalize_task_id(payload)
    if task_id:
        return task_id, None
    task_part, _, token = payload.partition(":")
    task_id = _normalize_task_id(task_part)
    if task_id and token.strip():
        return task_id, token.strip()
    return None, None


def _ensure_parallel_callback_binding(
    session_key: str,
    dispatch_context: ParallelDispatchContext,
    *,
    title_snapshot: Optional[str] = None,
) -> str:
    """为并行消息生成稳定 token，便于按钮精准路由。"""

    token = hashlib.sha1(f"{dispatch_context.task_id}:{session_key}".encode("utf-8")).hexdigest()[:10]
    PARALLEL_CALLBACK_BINDINGS[token] = ParallelCallbackBinding(
        token=token,
        task_id=dispatch_context.task_id,
        session_key=session_key,
        dispatch_context=dispatch_context,
        title_snapshot=title_snapshot,
    )
    return token


def _ensure_session_commit_binding(session_key: str, task_id: str, workspace_root: Path) -> str:
    """为原生会话消息生成稳定 token，便于按钮精准命中对应目录。"""

    token = hashlib.sha1(f"native-commit:{task_id}:{session_key}".encode("utf-8")).hexdigest()[:10]
    SESSION_COMMIT_CALLBACK_BINDINGS[token] = SessionCommitBinding(
        token=token,
        task_id=task_id,
        session_key=session_key,
        workspace_root=Path(workspace_root),
    )
    return token


def _ensure_session_quick_reply_binding(session_key: str, task_id: str) -> str:
    """为原生会话消息生成稳定 token，便于 quick reply 精准命中所属 session。"""

    token = hashlib.sha1(f"native-quick-reply:{task_id}:{session_key}".encode("utf-8")).hexdigest()[:10]
    SESSION_QUICK_REPLY_CALLBACK_BINDINGS[token] = SessionQuickReplyBinding(
        token=token,
        task_id=task_id,
        session_key=session_key,
    )
    return token


def _resolve_session_quick_reply_binding(
    chat_id: int,
    task_id: Optional[str],
    token: Optional[str],
) -> Optional[SessionQuickReplyBinding]:
    """解析原生会话 quick reply 绑定，仅允许命中当前活动原生会话。"""

    normalized_task_id = _normalize_task_id(task_id)
    normalized_token = (token or "").strip()
    if not normalized_task_id or not normalized_token:
        return None
    binding = SESSION_QUICK_REPLY_CALLBACK_BINDINGS.get(normalized_token)
    if binding is None:
        return None
    if _normalize_task_id(binding.task_id) != normalized_task_id:
        return None
    current_session_key = (CHAT_SESSION_MAP.get(chat_id) or "").strip()
    if not current_session_key or current_session_key != (binding.session_key or "").strip():
        return None
    return binding


def _should_fail_closed_legacy_native_quick_reply(chat_id: int) -> bool:
    """判断无 session token 的旧原生 quick reply 是否应直接 fail-closed。"""

    current_session_key = (CHAT_SESSION_MAP.get(chat_id) or "").strip()
    if not current_session_key:
        return False
    if _normalize_task_id(SESSION_TASK_BINDINGS.get(current_session_key)):
        return True
    if CHAT_ACTIVE_PLAN_CONFIRM_TOKENS.get(chat_id):
        return True
    return False


async def _resolve_parallel_dispatch_context(
    task_id: Optional[str],
    token: Optional[str],
) -> tuple[Optional[str], Optional[ParallelDispatchContext]]:
    """按 token 优先、task_id 兜底解析并行派发上下文。"""

    if token:
        binding = PARALLEL_CALLBACK_BINDINGS.get(token)
        if binding is not None:
            resolved_task_id = _normalize_task_id(getattr(binding, "task_id", task_id))
            dispatch_context = getattr(binding, "dispatch_context", None)
            if isinstance(dispatch_context, ParallelDispatchContext):
                return resolved_task_id, dispatch_context

    normalized_task_id = _normalize_task_id(task_id)
    if not normalized_task_id:
        return None, None

    session_key = PARALLEL_TASK_SESSION_MAP.get(normalized_task_id)
    if session_key:
        dispatch_context = PARALLEL_SESSION_CONTEXTS.get(session_key)
        if dispatch_context is not None:
            return normalized_task_id, dispatch_context

    session = await _get_active_parallel_session_for_task(normalized_task_id)
    if session is None:
        return normalized_task_id, None
    dispatch_context = _parallel_dispatch_context_from_session(session)
    return normalized_task_id, dispatch_context


async def _resolve_parallel_request_input_context(
    session_key: str,
) -> tuple[Optional[str], Optional[ParallelDispatchContext]]:
    """根据 request_input 所属 session_key 回溯并行上下文。"""

    key = (session_key or "").strip()
    if not key:
        return None, None
    task_id = _normalize_task_id(PARALLEL_SESSION_TASK_BINDINGS.get(key))
    if not task_id:
        return None, None
    dispatch_context = PARALLEL_SESSION_CONTEXTS.get(key)
    if isinstance(dispatch_context, ParallelDispatchContext):
        return task_id, dispatch_context
    return await _resolve_parallel_dispatch_context(task_id, None)


async def _resolve_parallel_plan_confirm_context(
    session_key: str,
) -> tuple[Optional[str], Optional[ParallelDispatchContext]]:
    """根据 Plan Confirm 所属 session_key 回溯并行上下文。"""

    return await _resolve_parallel_request_input_context(session_key)


def _parallel_tmux_session_exists(session_name: str) -> bool:
    """检查并行 tmux 会话是否仍存在。"""

    if not session_name.strip():
        return False
    try:
        subprocess.check_call(
            _tmux_cmd(tmux_bin(), "has-session", "-t", session_name),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False
    return True


def _parallel_session_runtime_issue(session: ParallelSessionRecord) -> Optional[str]:
    """判断并行会话是否已失活，返回首个失活原因。"""

    if not _parallel_tmux_session_exists(session.tmux_session):
        return f"tmux 会话 {session.tmux_session} 不存在"
    current_command = _get_tmux_pane_current_command(session.tmux_session)
    if _is_tmux_shell_command(current_command):
        return f"tmux 会话 {session.tmux_session} 当前仍停留在 shell：{current_command}"
    workspace_root = resolve_path(session.workspace_root)
    if not workspace_root.exists():
        return f"并行目录 {workspace_root} 不存在"
    pointer_file = resolve_path(session.pointer_file)
    if not pointer_file.exists():
        return f"会话指针 {pointer_file} 不存在"
    return None


def _clear_parallel_session_scoped_runtime_state(session_key: Optional[str]) -> None:
    """清理并行会话对应的 session 级缓存，避免残留到进程重启前。"""

    normalized_key = (session_key or "").strip()
    if not normalized_key:
        return
    SESSION_OFFSETS.pop(normalized_key, None)
    PENDING_SUMMARIES.pop(normalized_key, None)
    SESSION_TASK_BINDINGS.pop(normalized_key, None)

    stale_native_commit_tokens = [
        token
        for token, binding in SESSION_COMMIT_CALLBACK_BINDINGS.items()
        if (getattr(binding, "session_key", "") or "").strip() == normalized_key
    ]
    for token in stale_native_commit_tokens:
        SESSION_COMMIT_CALLBACK_BINDINGS.pop(token, None)

    for chat_id in list(CHAT_LAST_MESSAGE):
        _clear_last_message(chat_id, normalized_key)
    for chat_id in list(CHAT_DELIVERED_HASHES):
        _reset_delivered_hashes(chat_id, normalized_key)
    for chat_id in list(CHAT_DELIVERED_OFFSETS):
        _reset_delivered_offsets(chat_id, normalized_key)


def _clear_parallel_reply_targets_for_task(task_id: str) -> None:
    """清理命中指定任务的并行回复态，避免任务删除后仍可误入旧会话。"""

    normalized = _normalize_task_id(task_id) or task_id
    if not normalized:
        return
    stale_chat_ids: list[int] = []
    for chat_id, payload in CHAT_PARALLEL_REPLY_TARGETS.items():
        payload_task_id = _normalize_task_id(payload.get("task_id"))
        dispatch_context = payload.get("dispatch_context")
        context_task_id = _normalize_task_id(getattr(dispatch_context, "task_id", None))
        if payload_task_id == normalized or context_task_id == normalized:
            stale_chat_ids.append(chat_id)
    for chat_id in stale_chat_ids:
        CHAT_PARALLEL_REPLY_TARGETS.pop(chat_id, None)


async def _drop_parallel_session_bindings(task_id: str, *, session_key: Optional[str] = None) -> None:
    """清理并行会话的内存绑定，避免 stale session 继续被路由命中。"""

    normalized = _normalize_task_id(task_id) or task_id
    watcher = PARALLEL_TASK_WATCHERS.pop(normalized, None)
    if watcher is not None and not watcher.done():
        watcher.cancel()
        if hasattr(watcher, "__await__"):
            with suppress(asyncio.CancelledError):
                await watcher
    bound_session_key = session_key or PARALLEL_TASK_SESSION_MAP.get(normalized)
    PARALLEL_TASK_SESSION_MAP.pop(normalized, None)
    if bound_session_key:
        PARALLEL_SESSION_CONTEXTS.pop(bound_session_key, None)
        PARALLEL_SESSION_TASK_BINDINGS.pop(bound_session_key, None)
        _clear_parallel_session_scoped_runtime_state(bound_session_key)
    stale_tokens = [
        token
        for token, binding in PARALLEL_CALLBACK_BINDINGS.items()
        if _normalize_task_id(getattr(binding, "task_id", None)) == normalized
        or (getattr(binding, "session_key", "") or "").strip() == (bound_session_key or "")
    ]
    for token in stale_tokens:
        PARALLEL_CALLBACK_BINDINGS.pop(token, None)
    _clear_parallel_reply_targets_for_task(normalized)


def _clear_parallel_reply_target(chat_id: int) -> None:
    CHAT_PARALLEL_REPLY_TARGETS.pop(chat_id, None)


def _set_parallel_reply_target(
    chat_id: int,
    task_id: str,
    *,
    dispatch_context: Optional[ParallelDispatchContext] = None,
    token: Optional[str] = None,
) -> None:
    CHAT_PARALLEL_REPLY_TARGETS[chat_id] = {
        "task_id": task_id,
        "dispatch_context": dispatch_context,
        "token": token,
        "expires_at": time.time() + 600,
    }


def _consume_parallel_reply_target(chat_id: int) -> Optional[dict[str, Any]]:
    payload = CHAT_PARALLEL_REPLY_TARGETS.get(chat_id)
    if not payload:
        return None
    expires_at = float(payload.get("expires_at") or 0.0)
    if expires_at and expires_at < time.time():
        _clear_parallel_reply_target(chat_id)
        return None
    task_id = _normalize_task_id(payload.get("task_id"))
    _clear_parallel_reply_target(chat_id)
    if not task_id:
        return None
    return {
        "task_id": task_id,
        "dispatch_context": payload.get("dispatch_context"),
        "token": payload.get("token"),
    }


async def _get_active_parallel_session_for_task(task_id: str) -> Optional[ParallelSessionRecord]:
    normalized = _normalize_task_id(task_id)
    if not normalized:
        return None
    session = await PARALLEL_SESSION_STORE.get_session(normalized)
    if session is None:
        return None
    if session.status in {"deleted", "closed"}:
        return None
    issue = _parallel_session_runtime_issue(session)
    if issue:
        await PARALLEL_SESSION_STORE.update_status(normalized, status="closed", last_error=issue)
        await _drop_parallel_session_bindings(normalized)
        worker_log.warning(
            "检测到 stale 并行会话，已自动降级为 closed",
            extra={"task_id": normalized, "issue": issue, "tmux_session": session.tmux_session},
        )
        return None
    return session


async def _get_parallel_session_repos(task_id: str) -> list[ParallelRepoRecord]:
    normalized = _normalize_task_id(task_id)
    if not normalized:
        return []
    return await PARALLEL_SESSION_STORE.list_repos(normalized)


def _parallel_runtime_root(task_id: str) -> Path:
    normalized = _normalize_task_id(task_id) or task_id
    return CONFIG_ROOT_PATH / "runtime" / "parallel" / PROJECT_SLUG / normalized


def _parallel_workspace_root(task_id: str) -> Path:
    return _parallel_runtime_root(task_id) / "workspace"


def _parallel_runtime_meta_dir(task_id: str) -> Path:
    return _parallel_runtime_root(task_id) / "_runtime"


def _parallel_pointer_file(task_id: str) -> Path:
    return _parallel_runtime_meta_dir(task_id) / "current_session.txt"


def _parallel_active_session_file(task_id: str) -> Path:
    return _parallel_runtime_meta_dir(task_id) / "active_session_id.txt"


def _parallel_session_binder_log(task_id: str) -> Path:
    return _parallel_runtime_meta_dir(task_id) / "session_binder.log"


def _parallel_session_binder_pid(task_id: str) -> Path:
    return _parallel_runtime_meta_dir(task_id) / "session_binder.pid"


def _parallel_model_log(task_id: str) -> Path:
    return _parallel_runtime_meta_dir(task_id) / "model.log"


def _parallel_tmux_ready_file(task_id: str) -> Path:
    """返回并行 CLI 启动完成后的 ready 回执文件路径。"""

    return _parallel_runtime_meta_dir(task_id) / "tmux_ready"


def _parallel_tmux_session(task_id: str) -> str:
    normalized = (_normalize_task_id(task_id) or task_id).lower()
    return f"vibe-par-{PROJECT_SLUG[:12]}-{normalized.lower()}"[:48]


def _extract_task_prefixed_prompt(prompt: str) -> tuple[Optional[str], Optional[str]]:
    stripped = (prompt or "").strip()
    if not stripped:
        return None, None
    first, _, rest = stripped.partition(" ")
    task_id = _normalize_task_id(first)
    if not task_id or not rest.strip():
        return None, None
    return task_id, rest.strip()


async def _start_parallel_tmux_session(task: TaskRecord, workspace_root: Path) -> tuple[str, Path]:
    meta_dir = _parallel_runtime_meta_dir(task.id)
    meta_dir.mkdir(parents=True, exist_ok=True)
    tmux_session = _parallel_tmux_session(task.id)
    pointer_file = _parallel_pointer_file(task.id)
    ready_file = _parallel_tmux_ready_file(task.id)
    ready_file.unlink(missing_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "TMUX_SESSION": tmux_session,
            "MODEL_WORKDIR": str(workspace_root),
            "SESSION_POINTER_FILE": str(pointer_file),
            "SESSION_ACTIVE_ID_FILE": str(_parallel_active_session_file(task.id)),
            "SESSION_BINDER_LOG": str(_parallel_session_binder_log(task.id)),
            "SESSION_BINDER_PID_FILE": str(_parallel_session_binder_pid(task.id)),
            "TMUX_LOG": str(_parallel_model_log(task.id)),
            "LOG_PATH": str(_parallel_model_log(task.id)),
            "SESSION_READY_FILE": str(ready_file),
            "SESSION_READY_TIMEOUT_SECONDS": str(PARALLEL_PLAN_READY_TIMEOUT_SECONDS),
            "SESSION_READY_POLL_INTERVAL_SECONDS": str(PARALLEL_PLAN_READY_POLL_INTERVAL_SECONDS),
            "SESSION_READY_PROBE_LINES": str(PARALLEL_PLAN_READY_PROBE_LINES),
            "SESSION_READY_MARKERS": "||".join(PARALLEL_PLAN_READY_MARKERS),
            "PROJECT_NAME": f"{PROJECT_SLUG}-{task.id.lower()}",
        }
    )
    script = ROOT_DIR_PATH / "scripts" / "start_tmux_codex.sh"
    process = await asyncio.create_subprocess_exec(
        str(script),
        "--kill",
        cwd=str(ROOT_DIR_PATH),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await process.communicate()
    if process.returncode != 0:
        output = (stdout_bytes or b"").decode("utf-8", errors="ignore") + (stderr_bytes or b"").decode("utf-8", errors="ignore")
        raise RuntimeError(output.strip() or "启动并行 CLI 失败")
    if not ready_file.exists():
        raise RuntimeError(f"启动并行 CLI 失败：缺少 ready 回执（{ready_file}）")
    return tmux_session, pointer_file


async def _delete_parallel_session_workspace(task_id: str) -> None:
    normalized = _normalize_task_id(task_id)
    if not normalized:
        return
    session = await PARALLEL_SESSION_STORE.get_session(normalized)
    if session is None:
        return
    await _drop_parallel_session_bindings(normalized)
    delete_parallel_workspace(
        workspace_root=Path(session.workspace_root),
        tmux_session=session.tmux_session,
        binder_pid_file=_parallel_session_binder_pid(normalized),
    )
    meta_root = _parallel_runtime_root(normalized)
    shutil.rmtree(meta_root, ignore_errors=True)
    try:
        await _cleanup_codex_trusted_project_path(
            Path(session.workspace_root),
            scope=CODEX_TRUST_SCOPE_PARALLEL_WORKSPACE,
            owner_key=normalized,
        )
    except Exception as exc:  # noqa: BLE001
        worker_log.warning(
            "回收 Codex trusted 路径失败：%s",
            exc,
            extra={"task_id": normalized, "workspace_root": session.workspace_root},
        )
    await PARALLEL_SESSION_STORE.update_status(
        normalized,
        status="deleted",
        deleted_at=shanghai_now_iso(),
    )


async def _cleanup_parallel_session_workspace_safely(task_id: str) -> None:
    """后台清理任务的并行运行态，避免影响前台状态更新交互。"""

    try:
        await _delete_parallel_session_workspace(task_id)
    except Exception as exc:  # noqa: BLE001
        worker_log.warning(
            "任务完成后的并行清理失败：%s",
            exc,
            extra={"task_id": task_id},
        )


def _schedule_parallel_cleanup_for_done(task_id: str) -> None:
    """任务切换为 done 后，立即异步清理对应并行资源。"""

    normalized = _normalize_task_id(task_id)
    if not normalized:
        return
    asyncio.create_task(_cleanup_parallel_session_workspace_safely(normalized))


async def _refresh_done_task_detail_markup_safely(
    message: Optional[Message],
    task: TaskRecord,
) -> None:
    """后台刷新 done 状态的详情按钮，避免阻塞前台成功反馈。"""

    if message is None:
        return
    edit_reply_markup = getattr(message, "edit_reply_markup", None)
    if edit_reply_markup is None:
        return
    detail_state = TaskViewState(kind="detail", data={"task_id": task.id})
    try:
        await edit_reply_markup(reply_markup=_build_task_actions(task))
        _set_task_view_context(message, detail_state)
    except TelegramBadRequest as exc:
        worker_log.info(
            "后台刷新已完成任务详情按钮失败：%s",
            exc,
            extra={"task_id": task.id, **_session_extra()},
        )
    except Exception as exc:  # noqa: BLE001
        worker_log.warning(
            "后台刷新已完成任务详情按钮异常：%s",
            exc,
            extra={"task_id": task.id, **_session_extra()},
        )


async def _finalize_done_status_update_safely(
    message: Optional[Message],
    task: TaskRecord,
) -> None:
    """后台完成 done 状态收尾：刷新按钮并清理并行运行态。"""

    # 先刷新详情按钮，让用户更快看到“已完成（当前）”。
    await _refresh_done_task_detail_markup_safely(message, task)
    # 再执行并行运行态清理；无论成功与否都不影响前台已返回的成功提示。
    await _cleanup_parallel_session_workspace_safely(task.id)


def _build_parallel_common_branch_title(session: ParallelLaunchSession) -> str:
    """构造“共同分支批量选择”页标题。"""

    total_repos = len(session.repo_options)
    scoped_repos = session.common_branch_scope_repo_count or total_repos
    lines = [
        f"并行任务：/{session.task.id} {session.task.title}",
        f"已发现 Git 仓库：{total_repos} 个",
        "请先选择所有 Git 仓库共用的基线分支：",
    ]
    if session.common_branch_ignored_repos:
        lines.append(f"共同分支计算范围：{scoped_repos}/{total_repos} 个仓库")
        lines.append(f"已忽略仓库：{'、'.join(session.common_branch_ignored_repos)}（根仓库无远端分支）")
    if not session.common_branch_options:
        lines.append("当前未检测到所有 Git 仓库共同拥有的分支，请点击下方“逐个选择”。")
    else:
        lines.append("若不使用批量分支，请点击下方“🧩 逐个选择”。")
    return "\n".join(lines)


def _format_parallel_common_branch_button_label(branch: CommonBranchRef, *, limit: int = 48) -> str:
    """格式化共同分支按钮文案，并展示当前分支命中情况。"""

    prefix = "🌐 " if branch.source == "remote" else "📍 "
    if branch.current_count == branch.total_repos and branch.total_repos > 0:
        suffix = "（当前）"
    elif branch.current_count > 0:
        suffix = f"（当前 {branch.current_count}/{branch.total_repos}）"
    else:
        suffix = ""
    available = max(limit - len(prefix) - len(suffix), 1)
    name = branch.name
    if len(name) > available:
        if available <= 1:
            name = "…"
        else:
            name = name[: available - 1] + "…"
    return f"{prefix}{name}{suffix}"


def _build_parallel_common_branch_keyboard(
    session: ParallelLaunchSession,
    *,
    page: int,
    page_size: int = 8,
) -> InlineKeyboardMarkup:
    """构造共同分支批量选择键盘。"""

    branches = session.common_branch_options
    total = len(branches)
    page = max(page, 0)
    start = page * page_size
    chunk = branches[start : start + page_size]
    rows: list[list[InlineKeyboardButton]] = []
    for offset, branch in enumerate(chunk):
        branch_idx = start + offset
        rows.append(
            [
                InlineKeyboardButton(
                    text=_format_parallel_common_branch_button_label(branch),
                    callback_data=f"{PARALLEL_COMMON_BRANCH_SELECT_PREFIX}{session.token}:{branch_idx}",
                )
            ]
        )
    nav_row: list[InlineKeyboardButton] = []
    if start > 0:
        nav_row.append(
            InlineKeyboardButton(
                text="⬅️ 上一页",
                callback_data=f"{PARALLEL_COMMON_BRANCH_PAGE_PREFIX}{session.token}:{page-1}",
            )
        )
    if start + page_size < total:
        nav_row.append(
            InlineKeyboardButton(
                text="➡️ 下一页",
                callback_data=f"{PARALLEL_COMMON_BRANCH_PAGE_PREFIX}{session.token}:{page+1}",
            )
        )
    if nav_row:
        rows.append(nav_row)
    rows.append([InlineKeyboardButton(text="🧩 逐个选择", callback_data=f"{PARALLEL_BRANCH_INDIVIDUAL_PREFIX}{session.token}")])
    rows.append([InlineKeyboardButton(text="❌ 取消", callback_data=f"{PARALLEL_BRANCH_CANCEL_PREFIX}{session.token}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_parallel_branch_title(session: ParallelLaunchSession, repo_index: int) -> str:
    repo_key, _repo_path, rel, _branches = session.repo_options[repo_index]
    current_branch_label = session.current_branch_labels.get(repo_key, "读取失败")
    lines = [
        f"并行任务：/{session.task.id} {session.task.title}",
        f"当前仓库：{rel or '.'}",
        f"当前分支：{current_branch_label}",
        "请选择该仓库的基线分支（本地 + 远端）：",
    ]
    if repo_index > 0:
        lines.append(f"已完成选择：{len(session.selections)}/{len(session.repo_options)}")
    return "\n".join(lines)


def _format_parallel_branch_button_label(branch: BranchRef, *, limit: int = 48) -> str:
    """格式化分支按钮文案，优先保留“当前”标记。"""

    prefix = "🌐 " if branch.source == "remote" else "📍 "
    suffix = "（当前）" if branch.is_current else ""
    available = max(limit - len(prefix) - len(suffix), 1)
    name = branch.name
    if len(name) > available:
        if available <= 1:
            name = "…"
        else:
            name = name[: available - 1] + "…"
    return f"{prefix}{name}{suffix}"


def _build_parallel_branch_keyboard(
    session: ParallelLaunchSession,
    *,
    repo_index: int,
    page: int,
    page_size: int = 8,
) -> InlineKeyboardMarkup:
    _repo_key, _repo_path, _rel, branches = session.repo_options[repo_index]
    total = len(branches)
    page = max(page, 0)
    start = page * page_size
    chunk = branches[start : start + page_size]
    rows: list[list[InlineKeyboardButton]] = []
    for offset, branch in enumerate(chunk):
        branch_idx = start + offset
        rows.append(
            [
                InlineKeyboardButton(
                    text=_format_parallel_branch_button_label(branch),
                    callback_data=f"{PARALLEL_BRANCH_SELECT_PREFIX}{session.token}:{repo_index}:{branch_idx}",
                )
            ]
        )
    nav_row: list[InlineKeyboardButton] = []
    if start > 0:
        nav_row.append(
            InlineKeyboardButton(
                text="⬅️ 上一页",
                callback_data=f"{PARALLEL_BRANCH_PAGE_PREFIX}{session.token}:{repo_index}:{page-1}",
            )
        )
    if start + page_size < total:
        nav_row.append(
            InlineKeyboardButton(
                text="➡️ 下一页",
                callback_data=f"{PARALLEL_BRANCH_PAGE_PREFIX}{session.token}:{repo_index}:{page+1}",
            )
        )
    if nav_row:
        rows.append(nav_row)
    rows.append([InlineKeyboardButton(text="❌ 取消", callback_data=f"{PARALLEL_BRANCH_CANCEL_PREFIX}{session.token}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_parallel_branch_summary(session: ParallelLaunchSession) -> str:
    lines = [
        f"并行任务：/{session.task.id} {session.task.title}",
        f"分支前缀：{session.branch_prefix or f'{DEFAULT_PARALLEL_BRANCH_PREFIX}（默认）'}",
        "以下仓库将进入并行处理：",
    ]
    for repo_key, _repo_path, rel, _branches in session.repo_options:
        selected = session.selections.get(repo_key)
        selected_text = selected.name if selected else "（未选择）"
        lines.append(f"- {rel or '.'} -> {selected_text}")
    pending_labels = [rel or "." for repo_key, _repo_path, rel, _branches in session.repo_options if repo_key not in session.selections]
    lines.append("")
    if pending_labels:
        lines.append(f"待补选仓库：{'、'.join(pending_labels)}")
        lines.append("请先完成剩余仓库的基线分支选择。")
    else:
        lines.append("确认后将完整复刻工作目录（排除生成物目录）、创建任务分支和新 CLI。")
    return "\n".join(lines)


def _build_parallel_branch_prefix_prompt(session: ParallelLaunchSession) -> str:
    """构造分支前缀输入提示文案。"""

    suffix_example = build_parallel_branch_name(session.task.id, session.task.title, prefix="Sprint001").split("/", 1)[1]
    example = f"Sprint001/{suffix_example}"
    default_example = build_parallel_branch_name(
        session.task.id,
        session.task.title,
        prefix=DEFAULT_PARALLEL_BRANCH_PREFIX,
    )
    lines = [
        f"并行任务：/{session.task.id} {session.task.title}",
        "请输入本次并行任务的分支前缀（例如：Sprint001）。",
        f"若不指定，发送“取消”将使用默认前缀：{DEFAULT_PARALLEL_BRANCH_PREFIX}",
        "",
        f"示例（自定义）：{example}",
        f"示例（默认）：{default_example}",
    ]
    return "\n".join(lines)


def _build_parallel_branch_prefix_input_keyboard() -> ReplyKeyboardMarkup:
    """分支前缀输入态键盘：仅保留底部取消按钮。"""

    rows = [[KeyboardButton(text="取消")]]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


def _build_parallel_branch_summary_keyboard(session: ParallelLaunchSession) -> InlineKeyboardMarkup:
    next_index = _find_next_unselected_repo_index(session)
    rows: list[list[InlineKeyboardButton]] = []
    if next_index is None:
        rows.append([InlineKeyboardButton(text="✅ 开始并行处理", callback_data=f"{PARALLEL_BRANCH_CONFIRM_PREFIX}{session.token}")])
    else:
        rows.append([InlineKeyboardButton(text="🧩 继续补选剩余仓库", callback_data=f"{PARALLEL_BRANCH_INDIVIDUAL_PREFIX}{session.token}")])
    rows.append([InlineKeyboardButton(text="❌ 取消", callback_data=f"{PARALLEL_BRANCH_CANCEL_PREFIX}{session.token}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _find_next_unselected_repo_index(
    session: ParallelLaunchSession,
    *,
    start: int = 0,
    wrap: bool = False,
) -> Optional[int]:
    """返回下一个尚未选择基线分支的仓库索引。"""

    total = len(session.repo_options)
    if total <= 0:
        return None
    normalized_start = min(max(start, 0), total)
    search_ranges = [range(normalized_start, total)]
    # 用户可能通过翻页或历史消息从中间仓库继续补选，因此允许回绕检查前面的仓库。
    if wrap and normalized_start > 0:
        search_ranges.append(range(0, normalized_start))
    for search_range in search_ranges:
        for repo_index in search_range:
            repo_key, _repo_path, _rel, _branches = session.repo_options[repo_index]
            if repo_key not in session.selections:
                return repo_index
    return None


async def _render_parallel_branch_flow_message(
    message: Optional[Message],
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None,
) -> None:
    """并行分支选择流程优先覆盖同一条消息，失败时再降级为新消息。"""

    if await _try_edit_message(message, text, reply_markup=reply_markup):
        return
    if message is not None:
        await _answer_with_markdown(message, text, reply_markup=reply_markup)

# --- 任务视图上下文缓存 ---
TaskViewKind = Literal["list", "search", "detail", "history", "batch_push"]


@dataclass
class TaskViewState:
    """缓存任务视图的渲染参数，支持消息编辑式导航。"""

    kind: TaskViewKind
    data: Dict[str, Any]


TASK_VIEW_STACK: Dict[int, Dict[int, List[TaskViewState]]] = {}


def _task_view_stack(chat_id: int) -> Dict[int, List[TaskViewState]]:
    """获取指定聊天的视图栈映射。"""

    return TASK_VIEW_STACK.setdefault(chat_id, {})


def _push_task_view(chat_id: int, message_id: int, state: TaskViewState) -> None:
    """压入新的视图状态，用于进入详情等场景。"""

    stack = _task_view_stack(chat_id).setdefault(message_id, [])
    stack.append(state)


def _replace_task_view(chat_id: int, message_id: int, state: TaskViewState) -> None:
    """替换栈顶视图，常见于列表分页或刷新操作。"""

    stack = _task_view_stack(chat_id).setdefault(message_id, [])
    if stack:
        stack[-1] = state
    else:
        stack.append(state)


def _peek_task_view(chat_id: int, message_id: int) -> Optional[TaskViewState]:
    """查看当前栈顶视图。"""

    stack = TASK_VIEW_STACK.get(chat_id, {}).get(message_id)
    if not stack:
        return None
    return stack[-1]


def _pop_task_view(chat_id: int, message_id: int) -> Optional[TaskViewState]:
    """弹出栈顶视图，必要时清理空栈。"""

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
    """清理缓存，防止内存泄漏或上下文污染。"""

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
    """初始化指定消息的视图栈（新发送的列表或搜索视图）。"""

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
    """更新现有消息的栈顶视图，保持已有历史。"""

    if message is None:
        return
    chat = getattr(message, "chat", None)
    if chat is None:
        return
    _replace_task_view(chat.id, message.message_id, state)


def _push_detail_view(message: Optional[Message], task_id: str) -> None:
    """在视图栈中压入详情视图，便于回退。"""

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
    """弹出详情视图，返回移除的状态。"""

    if message is None:
        return None
    chat = getattr(message, "chat", None)
    if chat is None:
        return None
    state = _pop_task_view(chat.id, message.message_id)
    if state and state.kind != "detail":
        # 栈顶不是详情，说明上下文异常，放回以免破坏结构。
        _push_task_view(chat.id, message.message_id, state)
        return None
    return state


async def _render_task_view_from_state(state: TaskViewState) -> tuple[str, InlineKeyboardMarkup]:
    """根据视图状态重新渲染对应的任务界面。"""

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
            raise ValueError("任务详情缺少 task_id")
        return await _render_task_detail(task_id)
    if state.kind == "history":
        task_id = state.data.get("task_id")
        if not task_id:
            raise ValueError("任务历史缺少 task_id")
        page = int(state.data.get("page", 1) or 1)
        text, markup, _, _ = await _render_task_history(task_id, page)
        return text, markup
    if state.kind == "batch_push":
        status = state.data.get("status")
        page = int(state.data.get("page", 1) or 1)
        limit = int(state.data.get("limit", DEFAULT_PAGE_SIZE) or DEFAULT_PAGE_SIZE)
        selected_task_ids = [str(item).strip() for item in (state.data.get("selected_task_ids") or []) if str(item).strip()]
        selected_order = [str(item).strip() for item in (state.data.get("selected_task_order") or []) if str(item).strip()]
        return await _build_task_batch_push_view(
            status=status,
            page=page,
            limit=limit,
            selected_task_ids=selected_task_ids,
            selected_task_order=selected_order,
        )
    raise ValueError(f"未知的任务视图类型：{state.kind}")


def _make_list_view_state(*, status: Optional[str], page: int, limit: int) -> TaskViewState:
    """构造列表视图的上下文。"""

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
    """构造搜索视图的上下文。"""

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


def _make_batch_push_view_state(
    *,
    status: Optional[str],
    page: int,
    limit: int,
    selected_task_ids: Sequence[str],
    selected_task_order: Sequence[str],
) -> TaskViewState:
    """构造批量推送选择视图的上下文。"""

    normalized_selected = [item for item in (_normalize_task_id(task_id) for task_id in selected_task_ids) if item]
    normalized_order = [item for item in (_normalize_task_id(task_id) for task_id in selected_task_order) if item]
    return TaskViewState(
        kind="batch_push",
        data={
            "status": status,
            "page": page,
            "limit": limit,
            "selected_task_ids": normalized_selected,
            "selected_task_order": normalized_order,
        },
    )


def _make_history_view_state(*, task_id: str, page: int) -> TaskViewState:
    """构造历史视图的上下文。"""

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
                "清空聊天的已发送消息哈希",
                extra={"chat": chat_id},
            )
        return
    sessions = CHAT_DELIVERED_HASHES.get(chat_id)
    if not sessions:
        return
    if session_key in sessions:
        sessions.pop(session_key, None)
        worker_log.info(
            "清空会话的已发送消息哈希",
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
    """清理自动压缩相关状态，避免历史计数影响后续判断。"""

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
    """移除超时未完成的摘要请求。"""

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
            "摘要请求超时已清理",
            extra={"session": key},
        )


def _extract_task_ids_from_text(text: str) -> list[str]:
    """从模型文本中提取标准任务编号。"""

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
    """将模型回复写入任务历史。"""

    trimmed = _trim_history_value(content, limit=HISTORY_DISPLAY_VALUE_LIMIT)
    payload = {
        "model": ACTIVE_MODEL or "",
        "session": str(session_path),
        "offset": event_offset,
    }
    if content:
        payload["content"] = content[:MODEL_REPLY_PAYLOAD_LIMIT]
    try:
        await TASK_SERVICE.log_task_event(
            task_id,
            event_type=HISTORY_EVENT_MODEL_REPLY,
            actor=f"model/{ACTIVE_MODEL or 'codex'}",
            new_value=trimmed,
            payload=payload,
        )
    except ValueError:
        worker_log.warning(
            "模型回复写入失败：任务不存在",
            extra={"task_id": task_id, **_session_extra(path=session_path)},
        )


async def _maybe_finalize_summary(
    session_key: str,
    *,
    content: str,
    event_offset: int,
    session_path: Path,
) -> None:
    """检测并记录模型返回的摘要。"""

    pending = PENDING_SUMMARIES.get(session_key)
    if not pending:
        return
    request_tag = f"SUMMARY_REQUEST_ID::{pending.request_id}"
    normalized_buffer = (pending.buffer or "").replace("\\_", "_")
    normalized_content = content.replace("\\_", "_")
    combined_text = (
        f"{normalized_buffer}\n{normalized_content}"
        if normalized_buffer
        else normalized_content
    )
    if request_tag not in combined_text:
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
            "摘要写入失败：任务不存在",
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
    """统一持久化模型输出，并处理摘要落库。"""

    _cleanup_expired_summaries()
    await _maybe_finalize_summary(
        session_key,
        content=content,
        event_offset=event_offset,
        session_path=session_path,
    )
    # 仅在摘要请求落库时记录历史，普通模型回复不再写入 task_history。
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
    """向用户发送无需 Markdown 格式的提示信息。"""

    bot = current_bot()

    async def _do() -> None:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode=None)

    await _send_with_retry(_do)


async def _maybe_trigger_auto_compact(chat_id: int, session_key: str, count: int) -> None:
    """达到阈值后自动执行 /compact，同时向用户提示。"""

    if AUTO_COMPACT_THRESHOLD <= 0:
        return
    if count < AUTO_COMPACT_THRESHOLD:
        return
    if _is_compact_pending(chat_id, session_key):
        return

    notice = (
        f"模型已连续回复 {count} 条，准备自动执行 /compact，请稍候。"
    )
    await _send_plain_notice(chat_id, notice)

    try:
        tmux_send_line(TMUX_SESSION, "/compact")
    except subprocess.CalledProcessError as exc:
        worker_log.error(
            "自动触发 /compact 失败: %s",
            exc,
            extra={
                "chat": chat_id,
                **_session_extra(key=session_key),
            },
        )
        failure_text = f"自动执行 /compact 失败：{exc}"
        await _send_plain_notice(chat_id, failure_text)
        fallback = max(AUTO_COMPACT_THRESHOLD - 1, 0)
        _set_reply_count(chat_id, session_key, fallback)
        return

    _set_reply_count(chat_id, session_key, 0)
    _mark_compact_pending(chat_id, session_key)

    worker_log.info(
        "已自动发送 /compact",
        extra={
            "chat": chat_id,
            **_session_extra(key=session_key),
            "threshold": str(AUTO_COMPACT_THRESHOLD),
        },
    )

    await _send_plain_notice(chat_id, "已向模型发送 /compact，等待整理结果。")


async def _post_delivery_compact_checks(chat_id: int, session_key: str) -> None:
    """在模型消息发送成功后执行计数和自动压缩检查。"""

    if _is_compact_pending(chat_id, session_key):
        started = _clear_compact_pending(chat_id, session_key)
        elapsed = 0.0
        if started > 0:
            elapsed = max(time.monotonic() - started, 0.0)
        duration_hint = f"，耗时约 {elapsed:.1f} 秒" if elapsed > 0 else ""
        await _send_plain_notice(
            chat_id,
            f"自动执行 /compact 已完成{duration_hint}。",
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
                "清空聊天的已处理事件偏移",
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
            "清空会话的已处理事件偏移",
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


def _remember_chat_active_user(chat_id: int, user_id: Optional[int]) -> None:
    """记录 chat 最近一次主动发起请求的用户。"""

    if user_id is None:
        return
    CHAT_ACTIVE_USERS[chat_id] = int(user_id)


def _cleanup_expired_request_input_sessions() -> None:
    """清理过期或已终结的 request_user_input 交互会话。"""

    if not REQUEST_INPUT_SESSIONS:
        return
    now = time.monotonic()
    expired_tokens: list[str] = []
    for token, session in REQUEST_INPUT_SESSIONS.items():
        if session.cancelled or session.submitted or session.expires_at <= now:
            expired_tokens.append(token)

    for token in expired_tokens:
        session = REQUEST_INPUT_SESSIONS.pop(token, None)
        if session is None:
            continue
        if CHAT_ACTIVE_REQUEST_INPUT_TOKENS.get(session.chat_id) == token:
            CHAT_ACTIVE_REQUEST_INPUT_TOKENS.pop(session.chat_id, None)


def _drop_request_input_session(token: str) -> None:
    """按 token 删除 request_user_input 会话及其 chat 映射。"""

    session = REQUEST_INPUT_SESSIONS.pop(token, None)
    if session is None:
        return
    if CHAT_ACTIVE_REQUEST_INPUT_TOKENS.get(session.chat_id) == token:
        CHAT_ACTIVE_REQUEST_INPUT_TOKENS.pop(session.chat_id, None)


def _set_request_input_text_focus(chat_id: int, token: str) -> None:
    """将 chat 的 request_input 自定义输入焦点切到指定 token。"""

    normalized = (token or "").strip()
    if not normalized:
        CHAT_ACTIVE_REQUEST_INPUT_TOKENS.pop(chat_id, None)
        return
    CHAT_ACTIVE_REQUEST_INPUT_TOKENS[chat_id] = normalized


def _clear_request_input_text_focus(chat_id: int, token: Optional[str] = None) -> None:
    """清理 chat 的 request_input 自定义输入焦点。"""

    if token is None:
        CHAT_ACTIVE_REQUEST_INPUT_TOKENS.pop(chat_id, None)
        return
    if CHAT_ACTIVE_REQUEST_INPUT_TOKENS.get(chat_id) == token:
        CHAT_ACTIVE_REQUEST_INPUT_TOKENS.pop(chat_id, None)


def _drop_plan_confirm_session(token: str) -> None:
    """按 token 删除 Plan 结束确认会话及其 chat 映射。"""

    session = PLAN_CONFIRM_SESSIONS.pop(token, None)
    PLAN_CONFIRM_PROCESSING_TOKENS.discard(token)
    if session is None:
        return
    if CHAT_ACTIVE_PLAN_CONFIRM_TOKENS.get(session.chat_id) == token:
        CHAT_ACTIVE_PLAN_CONFIRM_TOKENS.pop(session.chat_id, None)


def _claim_plan_confirm_processing_token(token: str) -> bool:
    """尝试抢占 Plan Yes 处理令牌（返回 True 表示可继续处理）。"""

    normalized = (token or "").strip()
    if not normalized:
        return False
    if normalized in PLAN_CONFIRM_PROCESSING_TOKENS:
        return False
    PLAN_CONFIRM_PROCESSING_TOKENS.add(normalized)
    return True


def _release_plan_confirm_processing_token(token: str) -> None:
    """释放 Plan Yes 处理令牌，避免后续点击被误判为并发。"""

    normalized = (token or "").strip()
    if not normalized:
        return
    PLAN_CONFIRM_PROCESSING_TOKENS.discard(normalized)


def _drop_chat_plan_confirm_session(chat_id: int) -> None:
    """删除指定 chat 的当前 Plan 确认会话。"""

    token = CHAT_ACTIVE_PLAN_CONFIRM_TOKENS.get(chat_id)
    if not token:
        return
    _drop_plan_confirm_session(token)


def _find_plan_confirm_tokens(chat_id: int, *, session_key: Optional[str] = None) -> List[str]:
    """查找指定 chat 下的 PlanConfirm token，可按 session_key 过滤。"""

    normalized_session_key = (session_key or "").strip()
    matched_tokens: List[str] = []
    for token, session in PLAN_CONFIRM_SESSIONS.items():
        if session.chat_id != chat_id:
            continue
        if normalized_session_key and session.session_key != normalized_session_key:
            continue
        matched_tokens.append(token)
    return matched_tokens


def _drop_plan_confirm_sessions_for_session(chat_id: int, session_key: Optional[str]) -> None:
    """仅删除指定 chat + session_key 的 PlanConfirm，会保留其他并存会话。"""

    for token in _find_plan_confirm_tokens(chat_id, session_key=session_key):
        _drop_plan_confirm_session(token)


def _build_plan_confirm_callback_data(token: str, action: str) -> str:
    """构造 Plan 结束确认按钮回调数据（遵循 Telegram 64 字节限制）。"""

    payload = f"{PLAN_CONFIRM_CALLBACK_PREFIX.rstrip(':')}:{token}:{action}"
    if len(payload.encode("utf-8")) > 64:
        payload = f"{PLAN_CONFIRM_CALLBACK_PREFIX.rstrip(':')}:{token}:x"
    return payload


def _parse_plan_confirm_callback_data(data: Optional[str]) -> Optional[Tuple[str, str]]:
    """解析 Plan 结束确认按钮回调数据。"""

    if not data or not data.startswith(PLAN_CONFIRM_CALLBACK_PREFIX):
        return None
    parts = data.split(":")
    if len(parts) < 3:
        return None
    _, token, action = parts[:3]
    if not token:
        return None
    return token, action


def _build_plan_develop_retry_callback_data(token: str, action: str) -> str:
    """构造“进入开发失败重试”按钮回调数据（遵循 Telegram 64 字节限制）。"""

    payload = f"{PLAN_DEVELOP_RETRY_CALLBACK_PREFIX.rstrip(':')}:{token}:{action}"
    if len(payload.encode("utf-8")) > 64:
        payload = f"{PLAN_DEVELOP_RETRY_CALLBACK_PREFIX.rstrip(':')}:{token}:x"
    return payload


def _parse_plan_develop_retry_callback_data(data: Optional[str]) -> Optional[Tuple[str, str]]:
    """解析“进入开发失败重试”按钮回调数据。"""

    if not data or not data.startswith(PLAN_DEVELOP_RETRY_CALLBACK_PREFIX):
        return None
    parts = data.split(":")
    if len(parts) < 3:
        return None
    _, token, action = parts[:3]
    if not token:
        return None
    return token, action


def _build_request_input_callback_data(token: str, action: str, *values: int) -> str:
    """构造 request_user_input 按钮回调数据（遵循 Telegram 64 字节限制）。"""

    parts = [REQUEST_INPUT_CALLBACK_PREFIX.rstrip(":"), token, action]
    for value in values:
        parts.append(str(value))
    payload = ":".join(parts)
    if len(payload.encode("utf-8")) > 64:
        # 理论上不会触发（token/action 都是短字符串），保底兜底。
        payload = f"{REQUEST_INPUT_CALLBACK_PREFIX.rstrip(':')}:{token}:{action}"
    return payload


def _parse_request_input_callback_data(data: Optional[str]) -> Optional[Tuple[str, str, List[int]]]:
    """解析 request_user_input 回调数据。"""

    if not data or not data.startswith(REQUEST_INPUT_CALLBACK_PREFIX):
        return None
    parts = data.split(":")
    if len(parts) < 3:
        return None
    _, token, action, *rest = parts
    if not token:
        return None
    numeric_values: List[int] = []
    if rest:
        for candidate_raw in rest:
            candidate = (candidate_raw or "").strip()
            if not candidate.isdigit():
                return None
            numeric_values.append(int(candidate))
    return token, action, numeric_values


def _truncate_button_label(text: str, *, limit: int = 18) -> str:
    """压缩按钮文案，避免 Telegram 按钮过长影响可读性。"""

    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if not cleaned:
        return "-"
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[: max(limit - 1, 1)]}…"


def _request_input_option_code(index: int) -> str:
    """返回 request_user_input 选项序号（A/B/C...）。"""

    if index < 0:
        return "?"
    # request_user_input 按约定通常为 2~3 个选项，这里保留 A-Z 的兜底映射。
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if index < len(alphabet):
        return alphabet[index]
    return str(index + 1)


def _normalize_request_input_questions(raw: Any) -> List[RequestInputQuestion]:
    """将 request_user_input 的原始 questions 归一化。"""

    if not isinstance(raw, list):
        return []

    normalized: List[RequestInputQuestion] = []
    used_ids: set[str] = set()
    for index, item in enumerate(raw, 1):
        if len(normalized) >= REQUEST_INPUT_MAX_QUESTIONS:
            break
        if not isinstance(item, dict):
            continue

        question_text = str(item.get("question") or "").strip()
        header_text = str(item.get("header") or "").strip()
        if not question_text:
            # 与 request_user_input 定义对齐：问题文案是核心字段。
            continue

        raw_id = str(item.get("id") or "").strip() or f"question_{index}"
        # question_id 仅保留字母数字/下划线，避免后续 payload 键值异常。
        safe_id = re.sub(r"[^a-zA-Z0-9_]+", "_", raw_id).strip("_") or f"question_{index}"
        base_id = safe_id
        suffix = 2
        while safe_id in used_ids:
            safe_id = f"{base_id}_{suffix}"
            suffix += 1
        used_ids.add(safe_id)

        options_raw = item.get("options")
        if not isinstance(options_raw, list):
            continue

        options: List[RequestInputOption] = []
        for option in options_raw:
            if len(options) >= REQUEST_INPUT_MAX_OPTIONS:
                break
            if not isinstance(option, dict):
                continue
            label = str(option.get("label") or "").strip()
            if not label:
                continue
            description = str(option.get("description") or "").strip()
            options.append(RequestInputOption(label=label, description=description))

        if not options:
            continue

        normalized.append(
            RequestInputQuestion(
                question_id=safe_id,
                question=question_text,
                header=header_text,
                options=options,
            )
        )

    return normalized


def _parse_request_user_input_function_call(payload: Dict[str, Any]) -> Optional[Tuple[str, Dict[str, Any]]]:
    """解析 Codex 的 request_user_input function_call。"""

    call_id = str(payload.get("call_id") or "").strip()
    if not call_id:
        return None

    arguments = payload.get("arguments")
    parsed_args: Optional[Dict[str, Any]] = None
    if isinstance(arguments, str):
        try:
            candidate = json.loads(arguments)
        except (TypeError, json.JSONDecodeError):
            candidate = None
        if isinstance(candidate, dict):
            parsed_args = candidate
    elif isinstance(arguments, dict):
        parsed_args = arguments

    if not isinstance(parsed_args, dict):
        return None

    questions = _normalize_request_input_questions(parsed_args.get("questions"))
    if not questions:
        return None

    # 仅存储可序列化结构，避免 metadata 中出现复杂对象。
    serialized_questions = [
        {
            "id": question.question_id,
            "question": question.question,
            "header": question.header,
            "options": [
                {
                    "label": option.label,
                    "description": option.description,
                }
                for option in question.options
            ],
        }
        for question in questions
    ]
    summary = f"🧩 模型请求你补充决策（共 {len(serialized_questions)} 题），请点击按钮作答。"
    metadata = {
        "call_id": call_id,
        "questions": serialized_questions,
    }
    return summary, metadata


def _build_request_input_session(
    *,
    token: str,
    chat_id: int,
    session_key: str,
    user_id: int,
    call_id: str,
    questions_raw: Sequence[Mapping[str, Any]],
    parallel_task_id: Optional[str] = None,
    parallel_dispatch_context: Optional["ParallelDispatchContext"] = None,
) -> Optional[RequestInputSession]:
    """根据 metadata 反序列化 request_user_input 会话对象。"""

    questions = _normalize_request_input_questions(list(questions_raw))
    if not questions:
        return None
    now = time.monotonic()
    return RequestInputSession(
        token=token,
        chat_id=chat_id,
        user_id=user_id,
        call_id=call_id,
        session_key=session_key,
        questions=questions,
        current_index=0,
        created_at=now,
        expires_at=now + REQUEST_INPUT_SESSION_TTL_SECONDS,
        parallel_task_id=_normalize_task_id(parallel_task_id),
        parallel_dispatch_context=parallel_dispatch_context,
    )


def _render_request_input_question_text(session: RequestInputSession, *, question_index: int) -> str:
    """渲染指定题目的完整文案（逐题独立消息）。"""

    total = len(session.questions)
    index = max(0, min(question_index, total - 1))
    question = session.questions[index]
    answered_count = sum(
        1 for item in session.questions if _is_request_input_question_answered(session, item.question_id)
    )
    remaining_count = max(total - answered_count, 0)
    remaining_seconds = max(int(session.expires_at - time.monotonic()), 0)
    remaining_minutes = max(1, remaining_seconds // 60) if remaining_seconds else 0

    lines: List[str] = [
        "🧩 模型请求补充决策",
        f"进度：第 {index + 1}/{total} 题（已答 {answered_count} 题，剩余 {remaining_count} 题）",
    ]
    if question.header:
        lines.append(f"分组：{question.header}")
    lines.append(f"问题：{question.question}")
    lines.append("")
    lines.append("选项：")
    for option_index, option in enumerate(question.options):
        option_code = _request_input_option_code(option_index)
        suffix = f"（{option.description}）" if option.description else ""
        lines.append(f"{option_code}. {option.label}{suffix}")

    lines.append(f"D. {REQUEST_INPUT_CUSTOM_LABEL}")

    if session.input_mode_question_id == question.question_id:
        lines.append("")
        lines.append("📝 当前题处于自定义输入模式：请发送文本，或发送“取消”返回选项。")

    lines.append("")
    lines.append("说明：本题一旦作答即锁定不可修改；仅当前会话发起人可操作。")
    if remaining_minutes > 0:
        lines.append(f"有效期：剩余约 {remaining_minutes} 分钟")
    else:
        lines.append("有效期：已过期，请重新触发。")
    return "\n".join(lines)


def _build_request_input_keyboard(session: RequestInputSession, *, question_index: int) -> InlineKeyboardMarkup:
    """构造指定题目的交互按钮（仅选项 + 自定义决策）。"""

    total = len(session.questions)
    index = max(0, min(question_index, total - 1))
    question = session.questions[index]

    rows: list[list[InlineKeyboardButton]] = []
    for option_index, option in enumerate(question.options):
        option_code = _request_input_option_code(option_index)
        label = f"{option_code}. {_truncate_button_label(option.label)}"
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=_build_request_input_callback_data(
                        session.token,
                        REQUEST_INPUT_ACTION_OPTION,
                        index,
                        option_index,
                    ),
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=f"D. {REQUEST_INPUT_CUSTOM_LABEL}",
                callback_data=_build_request_input_callback_data(
                    session.token,
                    REQUEST_INPUT_ACTION_CUSTOM,
                    index,
                ),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_request_input_custom_input_keyboard() -> ReplyKeyboardMarkup:
    """构造“自定义决策输入态”菜单栏，提供取消按钮。"""

    rows = [[KeyboardButton(text="取消")]]
    _number_reply_buttons(rows)
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


async def _send_request_input_question(session: RequestInputSession, *, reply_to: Optional[Message]) -> bool:
    """向 Telegram 发送当前待答题目的交互消息。"""

    total = len(session.questions)
    if total <= 0:
        return False
    index = max(0, min(session.current_index, total - 1))
    question = session.questions[index]
    text = _render_request_input_question_text(session, question_index=index)
    markup = _build_request_input_keyboard(session, question_index=index)
    sent_message: Optional[Message] = None
    try:
        if reply_to is not None:
            sent_message = await reply_to.answer(
                text,
                parse_mode=None,
                reply_markup=markup,
            )
        else:
            bot = current_bot()

            async def _send() -> None:
                nonlocal sent_message
                sent_message = await bot.send_message(
                    chat_id=session.chat_id,
                    text=text,
                    parse_mode=None,
                    reply_markup=markup,
                )

            await _send_with_retry(_send)
    except TelegramBadRequest as exc:
        worker_log.warning(
            "发送 request_user_input 题目失败：%s",
            exc,
            extra={"chat": session.chat_id, "token": session.token},
        )
        return False
    message_id = getattr(sent_message, "message_id", None)
    if isinstance(message_id, int):
        session.question_message_ids[question.question_id] = message_id
    return True


def _build_request_input_output_payload(session: RequestInputSession) -> Dict[str, Any]:
    """构建 request_user_input 的结构化 answers 负载。"""

    answers: Dict[str, Dict[str, List[str]]] = {}
    for question in session.questions:
        selected_index = session.selected_option_indexes.get(question.question_id)
        if selected_index is None:
            continue
        if selected_index == REQUEST_INPUT_CUSTOM_OPTION_INDEX:
            custom_text = (session.custom_answers.get(question.question_id) or "").strip()
            if not custom_text:
                continue
            # 约定：D=自定义决策时，仅提交用户输入文本，不携带额外前缀。
            answers[question.question_id] = {"answers": [custom_text]}
            continue
        if selected_index < 0 or selected_index >= len(question.options):
            continue
        selected_label = question.options[selected_index].label
        answers[question.question_id] = {"answers": [selected_label]}
    return {"answers": answers}


def _request_input_message_has_media(message: Message) -> bool:
    """判断当前 request_input 自定义输入消息是否携带媒体附件。"""

    return bool(
        getattr(message, "photo", None)
        or getattr(message, "document", None)
        or getattr(message, "video", None)
        or getattr(message, "audio", None)
        or getattr(message, "voice", None)
        or getattr(message, "animation", None)
        or getattr(message, "video_note", None)
    )


def _build_request_input_submission_prompt(call_id: str, output_payload: Mapping[str, Any]) -> str:
    """构造回推到模型的提示词（结构化 JSON + call_id 绑定）。"""

    payload_json = json.dumps(output_payload, ensure_ascii=False, separators=(",", ":"))
    return "\n".join(
        [
            "request_user_input 工具结果（来自 Telegram 按钮交互）：",
            f"call_id={call_id}",
            payload_json,
            "请基于上述工具结果继续执行后续步骤。",
        ]
    )


async def _start_request_input_interaction(
    chat_id: int,
    session_path: Path,
    *,
    summary_text: str,
    metadata: Optional[Dict[str, Any]],
) -> bool:
    """创建并发送 request_user_input 交互会话。"""

    _cleanup_expired_request_input_sessions()
    metadata = metadata or {}
    call_id = str(metadata.get("call_id") or "").strip()
    questions_raw = metadata.get("questions")
    if not call_id or not isinstance(questions_raw, list):
        fallback_text = summary_text.strip() or "⚠️ 检测到 request_user_input，但解析失败，请手动回复模型。"
        await reply_large_text(chat_id, fallback_text, parse_mode=None, preformatted=True)
        return True

    token = uuid.uuid4().hex[:10]
    owner_user_id = int(CHAT_ACTIVE_USERS.get(chat_id, chat_id))
    parallel_task_id, parallel_dispatch_context = await _resolve_parallel_request_input_context(str(session_path))
    session = _build_request_input_session(
        token=token,
        chat_id=chat_id,
        session_key=str(session_path),
        user_id=owner_user_id,
        call_id=call_id,
        questions_raw=questions_raw,
        parallel_task_id=parallel_task_id,
        parallel_dispatch_context=parallel_dispatch_context,
    )
    if session is None:
        fallback_text = summary_text.strip() or "⚠️ request_user_input 题目为空，请手动回复模型。"
        await reply_large_text(chat_id, fallback_text, parse_mode=None, preformatted=True)
        return True

    REQUEST_INPUT_SESSIONS[token] = session
    sent = await _send_request_input_question(session, reply_to=None)
    if not sent:
        _drop_request_input_session(token)
    return sent


async def _handle_request_input_deliverable(
    chat_id: int,
    session_path: Path,
    deliverable: SessionDeliverable,
) -> bool:
    """处理 request_user_input 事件（发送按钮交互或降级提示）。"""

    text_to_send = (deliverable.text or "").strip()
    metadata = deliverable.metadata or {}
    if not ENABLE_REQUEST_USER_INPUT_UI:
        fallback = text_to_send or "检测到 request_user_input，请在终端中手动回复模型。"
        await reply_large_text(chat_id, fallback, parse_mode=None, preformatted=True)
        return True

    return await _start_request_input_interaction(
        chat_id,
        session_path,
        summary_text=text_to_send,
        metadata=metadata,
    )


def _contains_proposed_plan_block(text: str) -> bool:
    """判断模型输出中是否包含 `<proposed_plan>` 收口块。"""

    payload = (text or "").lower()
    return "<proposed_plan>" in payload and "</proposed_plan>" in payload


def _build_plan_confirm_keyboard(token: str) -> InlineKeyboardMarkup:
    """构造 Plan 结束确认按钮。"""

    rows = [
        [
            InlineKeyboardButton(
                text="✅ Yes, implement this plan",
                callback_data=_build_plan_confirm_callback_data(token, PLAN_CONFIRM_ACTION_YES),
            )
        ],
        [
            InlineKeyboardButton(
                text="📝 No, stay in Plan mode",
                callback_data=_build_plan_confirm_callback_data(token, PLAN_CONFIRM_ACTION_NO),
            )
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _maybe_send_plan_confirm_prompt(chat_id: int, session_key: str) -> bool:
    """在计划收口后向 Telegram 下发“是否进入开发”确认按钮。"""

    if _find_plan_confirm_tokens(chat_id, session_key=session_key):
        # 同会话已存在确认，不重复发送；但保留同 chat 的其他并存确认。
        return False

    token = uuid.uuid4().hex[:10]
    owner_user_id = CHAT_ACTIVE_USERS.get(chat_id)
    parallel_task_id, parallel_dispatch_context = await _resolve_parallel_plan_confirm_context(session_key)
    session = PlanConfirmSession(
        token=token,
        chat_id=chat_id,
        session_key=session_key,
        user_id=int(owner_user_id) if isinstance(owner_user_id, int) else None,
        created_at=time.monotonic(),
        parallel_task_id=_normalize_task_id(parallel_task_id),
        parallel_dispatch_context=parallel_dispatch_context,
    )

    bot = current_bot()
    markup = _build_plan_confirm_keyboard(token)
    text = (
        "Implement this plan?\n"
        "1. Yes, implement this plan\n"
        "2. No, stay in Plan mode"
    )
    sent_message: Optional[Message] = None

    async def _send() -> None:
        nonlocal sent_message
        sent_message = await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=None,
            reply_markup=markup,
        )

    try:
        await _send_with_retry(_send)
    except (TelegramNetworkError, TelegramRetryAfter, TelegramBadRequest) as exc:
        worker_log.warning(
            "Plan 结束确认按钮发送失败：%s",
            exc,
            extra={"chat": chat_id, **_session_extra(key=session_key)},
        )
        return False

    PLAN_CONFIRM_SESSIONS[token] = session
    CHAT_ACTIVE_PLAN_CONFIRM_TOKENS[chat_id] = token
    worker_log.info(
        "已发送 Plan 结束确认按钮",
        extra={
            "chat": chat_id,
            **_session_extra(key=session_key),
            "token": token,
            "message_id": str(getattr(sent_message, "message_id", "-")),
        },
    )
    return True


async def _deliver_pending_messages(
    chat_id: int,
    session_path: Path,
    *,
    add_completion_header: bool = True
) -> bool:
    """发送待处理的模型消息。

    Args:
        chat_id: Telegram 聊天 ID
        session_path: 会话文件路径
        add_completion_header: 是否添加"✅模型执行完成"前缀（快速轮询阶段为 True，延迟轮询为 False）
    """
    session_key = str(session_path)
    previous_offset = SESSION_OFFSETS.get(session_key, 0)
    new_offset, events = _read_session_events(session_path)
    delivered_response = False
    last_sent = _get_last_message(chat_id, session_key)
    # 需求约定：仅在“模型答案消息”（本函数投递的模型输出）底部展示快捷按钮。
    bound_task_id = SESSION_TASK_BINDINGS.get(session_key)
    parallel_task_id = PARALLEL_SESSION_TASK_BINDINGS.get(session_key)
    parallel_title = None
    parallel_dispatch_context = None
    parallel_callback_payload = None
    native_quick_reply_payload = None
    native_commit_callback_payload = None
    if parallel_task_id:
        parallel_session = await _get_active_parallel_session_for_task(parallel_task_id)
        if parallel_session is not None:
            parallel_title = parallel_session.title_snapshot
        resolved_task_id, parallel_dispatch_context = await _resolve_parallel_dispatch_context(parallel_task_id, None)
        if resolved_task_id and parallel_dispatch_context is not None:
            token = _ensure_parallel_callback_binding(
                session_key,
                parallel_dispatch_context,
                title_snapshot=parallel_title,
            )
            parallel_callback_payload = _build_parallel_callback_payload(resolved_task_id, token)
    elif bound_task_id:
        token = _ensure_session_quick_reply_binding(session_key, bound_task_id)
        native_quick_reply_payload = _build_session_quick_reply_callback_payload(bound_task_id, token)
        current_cwd = _read_session_meta_cwd(session_path)
        if current_cwd:
            token = _ensure_session_commit_binding(session_key, bound_task_id, Path(current_cwd))
            native_commit_callback_payload = _build_session_commit_callback_payload(bound_task_id, token)
    quick_reply_markup = _build_model_quick_reply_keyboard(
        task_id=bound_task_id or parallel_task_id,
        parallel_task_title=parallel_title,
        enable_parallel_actions=bool(parallel_task_id),
        parallel_callback_payload=parallel_callback_payload,
        native_quick_reply_payload=native_quick_reply_payload,
        native_commit_callback_payload=native_commit_callback_payload,
    )
    delivered_hashes = _get_delivered_hashes(chat_id, session_key)
    delivered_offsets = _get_delivered_offsets(chat_id, session_key)
    last_committed_offset = previous_offset

    if not events:
        SESSION_OFFSETS[session_key] = max(previous_offset, new_offset)
        return False

    worker_log.info(
        "检测到待发送的模型事件",
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
                "跳过已处理的模型事件",
                extra={
                    **_session_extra(path=session_path),
                    "chat": chat_id,
                    "offset": str(event_offset),
                },
            )
            last_committed_offset = event_offset
            SESSION_OFFSETS[session_key] = event_offset
            continue
        if not text_to_send:
            last_committed_offset = event_offset
            SESSION_OFFSETS[session_key] = event_offset
            continue
        if deliverable.kind == DELIVERABLE_KIND_PLAN:
            if ENABLE_PLAN_PROGRESS:
                plan_completed = False
                if deliverable.metadata and "plan_completed" in deliverable.metadata:
                    plan_completed = bool(deliverable.metadata.get("plan_completed"))
                worker_log.info(
                    "更新计划进度",
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
                # 计划事件可能在同一批次后继续跟随模型输出，这里刷新本地状态避免误判
                plan_active = ENABLE_PLAN_PROGRESS and (chat_id in CHAT_PLAN_TEXT)
                plan_completed_flag = bool(CHAT_PLAN_COMPLETION.get(chat_id))
            delivered_offsets.add(event_offset)
            last_committed_offset = event_offset
            SESSION_OFFSETS[session_key] = event_offset
            continue
        if deliverable.kind == DELIVERABLE_KIND_REQUEST_INPUT:
            handled = await _handle_request_input_deliverable(chat_id, session_path, deliverable)
            if handled:
                delivered_response = True
            delivered_offsets.add(event_offset)
            last_committed_offset = event_offset
            SESSION_OFFSETS[session_key] = event_offset
            continue
        if deliverable.kind != DELIVERABLE_KIND_MESSAGE:
            delivered_offsets.add(event_offset)
            last_committed_offset = event_offset
            SESSION_OFFSETS[session_key] = event_offset
            continue
        should_prompt_plan_confirm = _contains_proposed_plan_block(text_to_send)
        # 根据轮询阶段决定是否添加完成前缀
        formatted_text = _prepend_completion_header(text_to_send) if add_completion_header else text_to_send
        payload_for_hash = _prepare_model_payload(formatted_text)
        initial_hash = hashlib.sha256(payload_for_hash.encode("utf-8", errors="ignore")).hexdigest()
        if initial_hash in delivered_hashes:
            worker_log.info(
                "跳过重复的模型输出",
                extra={
                    **_session_extra(path=session_path),
                    "chat": chat_id,
                    "offset": str(event_offset),
                },
            )
            delivered_offsets.add(event_offset)
            last_committed_offset = event_offset
            SESSION_OFFSETS[session_key] = event_offset
            continue
        worker_log.info(
            "准备发送模型输出",
            extra={
                **_session_extra(path=session_path),
                "chat": chat_id,
                "offset": str(event_offset),
                "length": str(len(formatted_text)),
            },
        )
        try:
            delivered_payload = await reply_large_text(
                chat_id,
                formatted_text,
                reply_markup=quick_reply_markup,
                attachment_reply_markup=quick_reply_markup,
            )
        except TelegramBadRequest as exc:
            SESSION_OFFSETS[session_key] = previous_offset
            _clear_last_message(chat_id, session_key)
            worker_log.error(
                "发送消息失败（请求无效）: %s",
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
            SESSION_OFFSETS[session_key] = last_committed_offset
            _clear_last_message(chat_id, session_key)
            worker_log.warning(
                "发送消息失败，将重试: %s",
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
            final_hash_payload = _prepare_model_payload(delivered_payload or formatted_text)
            message_hash = hashlib.sha256(final_hash_payload.encode("utf-8", errors="ignore")).hexdigest()
            _set_last_message(chat_id, session_key, delivered_payload or formatted_text)
            delivered_hashes.add(initial_hash)
            delivered_hashes.add(message_hash)
            delivered_offsets.add(event_offset)
            CHAT_FAILURE_NOTICES.pop(chat_id, None)
            last_committed_offset = event_offset
            SESSION_OFFSETS[session_key] = event_offset
            worker_log.info(
                "模型输出发送成功",
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
            if should_prompt_plan_confirm:
                await _maybe_send_plan_confirm_prompt(chat_id, session_key)
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
            "本轮未发现可发送的模型输出",
            extra={
                **_session_extra(path=session_path),
                "chat": chat_id,
                "offset": str(last_committed_offset),
            },
        )
        SESSION_OFFSETS[session_key] = max(last_committed_offset, new_offset)

    if delivered_response:
        # 实际发送了消息，返回 True 表示本次调用成功发送
        # 这样可以确保延迟轮询机制被正确触发
        if ENABLE_PLAN_PROGRESS and plan_active:
            worker_log.info(
                "模型输出已发送，但计划仍在更新",
                extra={
                    **_session_extra(path=session_path),
                    "chat": chat_id,
                },
            )
            return False
        else:
            worker_log.info(
                "模型输出已发送且计划完成",
                extra={
                    **_session_extra(path=session_path),
                    "chat": chat_id,
                },
            )
        return True

    if ENABLE_PLAN_PROGRESS and not plan_active and final_response_sent:
        worker_log.info(
            "已存在历史响应，计划关闭后确认完成",
            extra={
                **_session_extra(path=session_path),
                "chat": chat_id,
            },
        )
        return True

    return False


async def _ensure_session_watcher(chat_id: int) -> Optional[Path]:
    """确保指定聊天已绑定模型会话并启动监听。"""

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
                "[session-map] chat=%s 记录的会话文件不存在，准备重新定位",
                chat_id,
                extra={"session": previous_key},
            )

    target_cwd_raw = (os.environ.get("MODEL_WORKDIR") or CODEX_WORKDIR or "").strip()
    target_cwd = target_cwd_raw or None

    if session_path is None and pointer_path is not None:
        session_path = _read_pointer_path(pointer_path)
        if session_path is not None:
            worker_log.info(
                "[session-map] chat=%s pointer -> %s",
                chat_id,
                session_path,
                extra=_session_extra(path=session_path),
            )
    if session_path is None and pointer_path is not None and not SESSION_BIND_STRICT:
        latest = (
            _find_latest_gemini_session(pointer_path, target_cwd)
            if _is_gemini_model()
            else _find_latest_rollout_for_cwd(pointer_path, target_cwd)
        )
        if latest is not None:
            session_path = latest
            _update_pointer(pointer_path, latest)
            worker_log.info(
                "[session-map] chat=%s locate latest rollout %s",
                chat_id,
                session_path,
                extra=_session_extra(path=session_path),
            )

    if pointer_path is not None and _is_claudecode_model() and not SESSION_BIND_STRICT:
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
        session_path = await _await_session_path(
            pointer_path,
            target_cwd,
            poll=SESSION_BIND_POLL_INTERVAL,
            strict=SESSION_BIND_STRICT,
            max_wait=SESSION_BIND_TIMEOUT_SECONDS,
        )
        if session_path is not None:
            _update_pointer(pointer_path, session_path)
            worker_log.info(
                "[session-map] chat=%s bind fresh session %s",
                chat_id,
                session_path,
                extra=_session_extra(path=session_path),
            )
    if session_path is None and pointer_path is not None and SESSION_BIND_STRICT:
        # strict 模式兜底：当 pointer 长时间未写入时，尝试直接扫描会话目录定位最新 session。
        session_path = _fallback_locate_latest_session(pointer_path, target_cwd)
        if session_path is not None:
            _update_pointer(pointer_path, session_path)
            worker_log.info(
                "[session-map] chat=%s strict fallback locate latest session %s",
                chat_id,
                session_path,
                extra=_session_extra(path=session_path),
            )
    if (
        session_path is None
        and pointer_path is not None
        and _is_claudecode_model()
        and not SESSION_BIND_STRICT
    ):
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
            "[session-map] chat=%s 无法确定 Codex 会话",
            chat_id,
        )
        return None

    session_key = str(session_path)
    if session_key not in SESSION_OFFSETS:
        initial_offset = _initial_session_offset(session_path)
        SESSION_OFFSETS[session_key] = initial_offset
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

    delivered_in_recheck = False
    try:
        delivered = await _deliver_pending_messages(chat_id, session_path)
        if delivered:
            delivered_in_recheck = True
            worker_log.info(
                "[session-map] chat=%s 已即时发送 pending 输出",
                chat_id,
                extra=_session_extra(path=session_path),
            )
    except Exception as exc:  # noqa: BLE001
        worker_log.warning(
            "推送后检查 Codex 事件失败: %s",
            exc,
            extra={"chat": chat_id, **_session_extra(path=session_path)},
        )

    watcher = CHAT_WATCHERS.get(chat_id)
    if watcher is not None and not watcher.done():
        return session_path
    if watcher is not None and watcher.done():
        CHAT_WATCHERS.pop(chat_id, None)

    # 中断旧的延迟轮询（如果存在）
    await _interrupt_long_poll(chat_id)

    start_in_long_poll = delivered_in_recheck or (session_key in (CHAT_LAST_MESSAGE.get(chat_id) or {}))
    CHAT_WATCHERS[chat_id] = asyncio.create_task(
        _watch_and_notify(
            chat_id,
            session_path,
            max_wait=WATCH_MAX_WAIT,
            interval=WATCH_INTERVAL,
            start_in_long_poll=start_in_long_poll,
        )
    )
    return session_path


async def _update_plan_progress(chat_id: int, plan_text: str, *, plan_completed: bool) -> bool:
    if not ENABLE_PLAN_PROGRESS:
        return False
    CHAT_PLAN_COMPLETION[chat_id] = plan_completed
    if CHAT_PLAN_TEXT.get(chat_id) == plan_text:
        worker_log.debug(
            "计划进度内容未变化，跳过更新",
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
                "计划进度发送失败，将停止更新: %s",
                exc,
                extra={"chat": chat_id},
            )
            return False
        except (TelegramNetworkError, TelegramRetryAfter) as exc:
            worker_log.warning(
                "计划进度发送遇到网络异常: %s",
                exc,
                extra={"chat": chat_id},
            )
            return False

        if sent_message is None:
            return False

        message_id = sent_message.message_id
        CHAT_PLAN_MESSAGES[chat_id] = message_id
        worker_log.info(
            "计划进度消息已发送",
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
                "计划进度编辑失败，将停止更新: %s",
                exc,
                extra={"chat": chat_id, "message_id": removed_id},
            )
            return False
        except (TelegramNetworkError, TelegramRetryAfter) as exc:
            worker_log.warning(
                "计划进度编辑遇到网络异常: %s",
                exc,
                extra={"chat": chat_id, "message_id": message_id},
            )
            return False
        worker_log.info(
            "计划进度消息已编辑",
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
    中断指定 chat 的延迟轮询。

    当用户发送新消息时调用，确保旧的延迟轮询被终止，
    为新的监听任务让路。

    线程安全：使用 asyncio.Lock 保护状态访问。
    """
    if CHAT_LONG_POLL_LOCK is None:
        state = CHAT_LONG_POLL_STATE.get(chat_id)
        if state is not None:
            state["interrupted"] = True
            worker_log.info(
                "标记延迟轮询为待中断",
                extra={"chat": chat_id},
            )
        return

    async with CHAT_LONG_POLL_LOCK:
        state = CHAT_LONG_POLL_STATE.get(chat_id)
        if state is not None:
            state["interrupted"] = True
            worker_log.info(
                "标记延迟轮询为待中断",
                extra={"chat": chat_id},
            )


async def _probe_new_model_message_once(
    chat_id: int,
    *,
    trigger_message_id: int,
    round_index: int,
    source: str,
) -> bool:
    """执行一次补偿检测：若本轮成功向 Telegram 发送了新的模型消息则返回 True。"""

    session_key = CHAT_SESSION_MAP.get(chat_id)
    if not session_key:
        worker_log.debug(
            "补偿轮询跳过：chat 未绑定会话",
            extra={
                "chat": chat_id,
                "source": source,
                "trigger_message_id": str(trigger_message_id),
                "round": str(round_index),
            },
        )
        return False

    session_path = resolve_path(session_key)
    if not session_path.exists():
        worker_log.debug(
            "补偿轮询跳过：会话文件不存在",
            extra={
                "chat": chat_id,
                "source": source,
                "trigger_message_id": str(trigger_message_id),
                "round": str(round_index),
                **_session_extra(key=session_key),
            },
        )
        return False

    before_last = _get_last_message(chat_id, session_key)
    before_offsets = len(_get_delivered_offsets(chat_id, session_key))

    try:
        await _deliver_pending_messages(chat_id, session_path, add_completion_header=False)
    except Exception as exc:  # noqa: BLE001 - 补偿检测不能影响主流程
        worker_log.warning(
            "补偿轮询检测失败：%s",
            exc,
            extra={
                "chat": chat_id,
                "source": source,
                "trigger_message_id": str(trigger_message_id),
                "round": str(round_index),
                **_session_extra(path=session_path),
            },
        )
        return False

    after_last = _get_last_message(chat_id, session_key)
    after_offsets = len(_get_delivered_offsets(chat_id, session_key))
    hit = bool(after_last is not None and after_last != before_last)
    if not hit and before_last is None and after_last:
        # 兜底：首次消息命中时，文本可能与 before 一致为空值，这里结合偏移变化补判一次。
        hit = after_offsets > before_offsets

    worker_log.info(
        "补偿轮询完成一次检测",
        extra={
            "chat": chat_id,
            "source": source,
            "trigger_message_id": str(trigger_message_id),
            "round": str(round_index),
            "hit": str(hit),
            "offset_delta": str(after_offsets - before_offsets),
            **_session_extra(path=session_path),
        },
    )
    return hit


async def _run_message_recovery_poll(
    chat_id: int,
    *,
    trigger_message_id: int,
    source: str,
) -> None:
    """执行 Telegram 入站消息触发的补偿轮询（1/3/10/30/90 分钟）。"""

    current_task = asyncio.current_task()
    if current_task is None:
        return

    try:
        for round_index, delay_seconds in enumerate(MESSAGE_RECOVERY_POLL_DELAYS_SECONDS, start=1):
            await asyncio.sleep(max(delay_seconds, 0.0))
            active_task = CHAT_MESSAGE_RECOVERY_POLL_TASKS.get(chat_id)
            if active_task is not current_task:
                # 有更新的消息已覆盖当前轮询任务，旧任务安静退出。
                return

            hit = await _probe_new_model_message_once(
                chat_id,
                trigger_message_id=trigger_message_id,
                round_index=round_index,
                source=source,
            )
            if hit:
                worker_log.info(
                    "补偿轮询命中新消息，提前结束后续检测",
                    extra={
                        "chat": chat_id,
                        "source": source,
                        "trigger_message_id": str(trigger_message_id),
                        "round": str(round_index),
                    },
                )
                return
    except asyncio.CancelledError:
        worker_log.debug(
            "补偿轮询任务被覆盖取消",
            extra={
                "chat": chat_id,
                "source": source,
                "trigger_message_id": str(trigger_message_id),
            },
        )
        raise
    finally:
        active_task = CHAT_MESSAGE_RECOVERY_POLL_TASKS.get(chat_id)
        if active_task is current_task:
            CHAT_MESSAGE_RECOVERY_POLL_TASKS.pop(chat_id, None)


async def _cancel_message_recovery_poll(chat_id: int) -> None:
    """取消指定 chat 现有的补偿轮询任务（若存在）。"""

    task = CHAT_MESSAGE_RECOVERY_POLL_TASKS.pop(chat_id, None)
    if task is None:
        return
    if task.done():
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


async def _schedule_message_recovery_poll(message: Message, *, source: str) -> None:
    """为入站 Telegram Message 安排补偿轮询任务（同 chat 新消息覆盖旧任务）。"""

    if _is_text_paste_synthetic_message(message):
        return
    chat = getattr(message, "chat", None)
    if chat is None:
        return
    chat_id = int(chat.id)
    trigger_message_id = int(getattr(message, "message_id", 0) or 0)

    await _cancel_message_recovery_poll(chat_id)
    task = asyncio.create_task(
        _run_message_recovery_poll(
            chat_id,
            trigger_message_id=trigger_message_id,
            source=source,
        )
    )
    CHAT_MESSAGE_RECOVERY_POLL_TASKS[chat_id] = task
    worker_log.debug(
        "已安排补偿轮询任务",
        extra={
            "chat": chat_id,
            "source": source,
            "trigger_message_id": str(trigger_message_id),
            "rounds": str(len(MESSAGE_RECOVERY_POLL_DELAYS_SECONDS)),
        },
    )


async def _watch_and_notify(
    chat_id: int,
    session_path: Path,
    max_wait: float,
    interval: float,
    *,
    start_in_long_poll: bool = False,
):
    """
    监听会话文件并发送消息。

    两阶段轮询机制：
    - 阶段1（快速轮询）：interval 间隔（通常 0.3 秒），直到首次发送成功
    - 阶段2（延迟轮询）：3 秒间隔，最多 600 次（持续 30 分钟），捕获长时间任务的后续输出

    异常安全：使用 try...finally 确保状态清理。
    中断机制：收到新 Telegram 消息时会设置 interrupted 标志，轮询自动停止。
    """
    start = time.monotonic()
    first_delivery_done = bool(start_in_long_poll)
    long_poll_interval = 3.0  # 3 秒
    current_interval = long_poll_interval if first_delivery_done else interval
    long_poll_rounds = 0
    long_poll_max_rounds = 600  # 30 分钟 / 3 秒 = 600 次

    if first_delivery_done:
        # 直接进入延迟轮询阶段：用于恢复 watcher，避免重复追加完成前缀。
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

    try:
        while True:
            # 检查是否被新消息中断（使用锁保护）
            if CHAT_LONG_POLL_LOCK is not None:
                async with CHAT_LONG_POLL_LOCK:
                    state = CHAT_LONG_POLL_STATE.get(chat_id)
                    if state is not None and state.get("interrupted", False):
                        worker_log.info(
                            "延迟轮询被新消息中断",
                            extra={
                                **_session_extra(path=session_path),
                                "chat": chat_id,
                                "round": long_poll_rounds,
                            },
                        )
                        return

            await asyncio.sleep(current_interval)

            # 检查超时（仅在快速轮询阶段）
            if not first_delivery_done and max_wait > 0 and time.monotonic() - start > max_wait:
                worker_log.warning(
                    "[session-map] chat=%s 长时间未获取到 Codex 输出，停止轮询",
                    chat_id,
                    extra=_session_extra(path=session_path),
                )
                return

            if not session_path.exists():
                continue

            try:
                # 快速轮询阶段添加前缀，延迟轮询阶段不添加
                delivered = await _deliver_pending_messages(
                    chat_id,
                    session_path,
                    add_completion_header=not first_delivery_done
                )
            except Exception as exc:
                worker_log.error(
                    "消息发送时发生未预期异常",
                    exc_info=exc,
                    extra={
                        **_session_extra(path=session_path),
                        "chat": chat_id,
                    },
                )
                delivered = False

            # 首次发送成功，切换到延迟轮询模式
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
                    "首次发送成功，启动延迟轮询模式",
                    extra={
                        **_session_extra(path=session_path),
                        "chat": chat_id,
                        "interval": long_poll_interval,
                        "max_rounds": long_poll_max_rounds,
                    },
                )
                continue

            # 延迟轮询阶段
            if first_delivery_done:
                if delivered:
                    # 又收到新消息，重置轮询计数
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
                        "延迟轮询中收到新消息，重置计数",
                        extra={
                            **_session_extra(path=session_path),
                            "chat": chat_id,
                        },
                    )
                else:
                    # 无新消息，增加轮询计数
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
                            "延迟轮询达到最大次数，停止监听",
                            extra={
                                **_session_extra(path=session_path),
                                "chat": chat_id,
                                "total_rounds": long_poll_rounds,
                            },
                        )
                        return

                    worker_log.debug(
                        "延迟轮询中无新消息",
                        extra={
                            **_session_extra(path=session_path),
                            "chat": chat_id,
                            "round": f"{long_poll_rounds}/{long_poll_max_rounds}",
                        },
                    )
                continue

            # 快速轮询阶段：如果已发送消息，退出
            if delivered:
                return

    finally:
        # 确保无论如何都清理延迟轮询状态
        if CHAT_LONG_POLL_LOCK is not None:
            async with CHAT_LONG_POLL_LOCK:
                if chat_id in CHAT_LONG_POLL_STATE:
                    CHAT_LONG_POLL_STATE.pop(chat_id, None)
                    worker_log.debug(
                        "监听任务退出，已清理延迟轮询状态",
                        extra={"chat": chat_id},
                    )
        else:
            if chat_id in CHAT_LONG_POLL_STATE:
                CHAT_LONG_POLL_STATE.pop(chat_id, None)
                worker_log.debug(
                    "监听任务退出，已清理延迟轮询状态",
                    extra={"chat": chat_id},
                )


async def _watch_parallel_and_notify(
    task_id: str,
    chat_id: int,
    session_path: Path,
    max_wait: float,
    interval: float,
    *,
    start_in_long_poll: bool = False,
) -> None:
    """监听单个并行会话，不干扰同 chat 的原生会话 watcher。"""

    start = time.monotonic()
    first_delivery_done = bool(start_in_long_poll)
    current_interval = 3.0 if first_delivery_done else interval
    long_poll_rounds = 0
    long_poll_max_rounds = 600

    try:
        while True:
            await asyncio.sleep(current_interval)

            if not first_delivery_done and max_wait > 0 and time.monotonic() - start > max_wait:
                worker_log.warning(
                    "[parallel-session] task=%s 长时间未获取到模型输出，停止轮询",
                    task_id,
                    extra={"chat": chat_id, **_session_extra(path=session_path)},
                )
                return

            if not session_path.exists():
                continue

            try:
                delivered = await _deliver_pending_messages(
                    chat_id,
                    session_path,
                    add_completion_header=not first_delivery_done,
                )
            except Exception as exc:  # noqa: BLE001
                worker_log.error(
                    "并行会话消息发送时发生未预期异常",
                    exc_info=exc,
                    extra={"chat": chat_id, "task_id": task_id, **_session_extra(path=session_path)},
                )
                delivered = False

            if delivered and not first_delivery_done:
                first_delivery_done = True
                current_interval = 3.0
                worker_log.info(
                    "并行会话首次发送成功，启动延迟轮询模式",
                    extra={"chat": chat_id, "task_id": task_id, **_session_extra(path=session_path)},
                )
                continue

            if first_delivery_done:
                if delivered:
                    long_poll_rounds = 0
                else:
                    long_poll_rounds += 1
                    if long_poll_rounds >= long_poll_max_rounds:
                        worker_log.info(
                            "并行会话延迟轮询达到最大次数，停止监听",
                            extra={"chat": chat_id, "task_id": task_id, **_session_extra(path=session_path)},
                        )
                        return
                continue

            if delivered:
                return
    finally:
        current_task = asyncio.current_task()
        active_task = PARALLEL_TASK_WATCHERS.get(task_id)
        if active_task is current_task:
            PARALLEL_TASK_WATCHERS.pop(task_id, None)


async def _start_parallel_task_watcher(
    task_id: str,
    chat_id: int,
    session_path: Path,
    *,
    start_in_long_poll: bool,
) -> None:
    """启动或替换指定并行任务的 watcher。"""

    prev_watcher = PARALLEL_TASK_WATCHERS.get(task_id)
    if prev_watcher is not None and not prev_watcher.done():
        prev_watcher.cancel()
        with suppress(asyncio.CancelledError):
            await prev_watcher
    watcher_task = asyncio.create_task(
        _watch_parallel_and_notify(
            task_id,
            chat_id,
            session_path,
            max_wait=WATCH_MAX_WAIT,
            interval=WATCH_INTERVAL,
            start_in_long_poll=start_in_long_poll,
        )
    )
    PARALLEL_TASK_WATCHERS[task_id] = watcher_task


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
    """ClaudeCode 专用：在缺少 cwd 元数据时按更新时间选择最新会话文件。

    注意：会排除 agent-*.jsonl 文件，因为这些是 agent 的 sidechain 会话，
    所有消息都标记为 isSidechain=true，会被忽略不处理。
    """

    pointer_target = _read_pointer_path(pointer)
    candidates: List[Path] = []
    if pointer_target is not None:
        # 如果 pointer 指向 agent 文件，跳过
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
                # 排除 agent-*.jsonl 文件
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

    # 记录找到的会话文件
    if latest_path:
        worker_log.info(
            "ClaudeCode 找到最新会话文件（已排除 agent-* 文件）",
            extra={"session_file": str(latest_path), "mtime": latest_mtime}
        )
    else:
        worker_log.warning(
            "ClaudeCode 未找到有效的会话文件（已排除 agent-* 文件）",
            extra={"search_roots": [str(r) for r in search_roots]}
        )

    return latest_path


def _find_latest_rollout_for_cwd(pointer: Path, target_cwd: Optional[str]) -> Optional[Path]:
    """依据目标 CWD 在候选目录中寻找最新会话文件。"""

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


def _gemini_project_hash_candidates(target_cwd: Optional[str]) -> set[str]:
    """为 Gemini 会话匹配生成候选 projectHash（同时覆盖逻辑路径/物理路径）。"""

    raw = (target_cwd or "").strip()
    if not raw:
        return set()

    expanded = resolve_path(raw)
    candidates: list[str] = []
    raw_str = str(expanded).rstrip("/")
    if raw_str:
        candidates.append(raw_str)
    try:
        resolved_str = str(expanded.resolve()).rstrip("/")
    except OSError:
        resolved_str = ""
    if resolved_str and resolved_str not in candidates:
        candidates.append(resolved_str)

    hashes: set[str] = set()
    for item in candidates:
        hashes.add(hashlib.sha256(item.encode("utf-8", errors="ignore")).hexdigest())
    return hashes


def _find_latest_gemini_session(pointer: Path, target_cwd: Optional[str]) -> Optional[Path]:
    """Gemini 专用：依据 projectHash 在候选目录中寻找最新 session-*.json。"""

    roots: List[Path] = []
    for candidate in (MODEL_SESSION_ROOT,):
        if candidate:
            roots.append(resolve_path(candidate))

    pointer_target = _read_pointer_path(pointer)
    if pointer_target is not None:
        roots.append(pointer_target.parent)

    roots.append(pointer.parent)
    roots.append(pointer.parent / "sessions")

    latest_path: Optional[Path] = None
    latest_mtime = -1.0
    seen: set[str] = set()
    expected_hashes = _gemini_project_hash_candidates(target_cwd)

    pattern = f"**/{MODEL_SESSION_GLOB}"
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

        for candidate in real_root.glob(pattern):
            if not candidate.is_file() or candidate.suffix.lower() != ".json":
                continue
            try:
                mtime = candidate.stat().st_mtime
            except OSError:
                continue
            if mtime <= latest_mtime:
                continue
            if expected_hashes:
                meta = _read_gemini_session_json(candidate) or {}
                project_hash = meta.get("projectHash")
                if not isinstance(project_hash, str) or project_hash not in expected_hashes:
                    continue
            latest_mtime = mtime
            latest_path = candidate

    return latest_path


async def _await_session_path(
    pointer: Optional[Path],
    target_cwd: Optional[str],
    poll: float = 0.5,
    *,
    strict: bool = False,
    max_wait: float = 0.0,
) -> Optional[Path]:
    """等待 pointer 写入新会话；strict=False 时会回退到旧 session。"""

    if pointer is None:
        await asyncio.sleep(poll)
        return None

    candidate = _read_pointer_path(pointer)
    if candidate is not None:
        return candidate

    poll_interval = max(poll, 0.1)
    if not strict:
        await asyncio.sleep(poll_interval)
        candidate = _read_pointer_path(pointer)
        if candidate is not None:
            return candidate
        if _is_gemini_model():
            return _find_latest_gemini_session(pointer, target_cwd)
        return _find_latest_rollout_for_cwd(pointer, target_cwd)

    deadline: Optional[float] = None
    if max_wait and max_wait > 0:
        deadline = time.monotonic() + max_wait

    while True:
        await asyncio.sleep(poll_interval)
        candidate = _read_pointer_path(pointer)
        if candidate is not None:
            return candidate
        if deadline is not None and time.monotonic() >= deadline:
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

    header = "当前任务执行计划："
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
        text = f"{text}\n\n状态更新中，最后更新时间：{suffix}"
    return text, all_completed


def _extract_codex_payload(data: dict, *, event_timestamp: Optional[str]) -> Optional[Tuple[str, str, Optional[Dict[str, Any]]]]:
    def _should_deliver_message(payload: Optional[dict] = None) -> bool:
        """按 Codex phase 过滤消息：仅 final_answer 投递到 Telegram。"""

        phase_value: Any = None
        if isinstance(payload, dict):
            phase_value = payload.get("phase")
        if not isinstance(phase_value, str):
            phase_value = data.get("phase")
        if not isinstance(phase_value, str):
            # 兼容旧版本：没有 phase 字段时，保持原有投递行为。
            return True
        normalized = phase_value.strip().lower()
        if not normalized:
            return True
        if normalized == CODEX_MESSAGE_PHASE_FINAL_ANSWER:
            return True
        # 只要出现 phase 且不是 final_answer（如 commentary），都视为中间过程并忽略。
        return False

    event_type = data.get("type")

    if event_type == "agent_message":
        if not _should_deliver_message():
            return None
        message = data.get("message")
        if isinstance(message, str) and message.strip():
            return DELIVERABLE_KIND_MESSAGE, message, None

    if event_type == "event_msg":
        payload = data.get("payload") or {}
        if payload.get("type") == "agent_message":
            # Codex 的 event_msg.agent_message 视为镜像流：
            # - response_item.message / assistant_message 才是 Telegram 正式正文来源
            # - event_msg.agent_message 即使带 final_answer，也可能只是短版镜像
            #   与 response_item 正文并存，导致同一轮出现“双发”
            # 因此这里统一忽略，由 response_item 路径负责最终投递。
            return None
        return None

    if event_type != "response_item":
        return None

    payload = data.get("payload") or {}
    payload_type = payload.get("type")

    if payload_type in {"message", "assistant_message"}:
        if not _should_deliver_message(payload):
            return None
        metadata: Dict[str, Any] = {"codex_response_item_type": payload_type}
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
                return DELIVERABLE_KIND_MESSAGE, "\n".join(fragments), metadata
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return DELIVERABLE_KIND_MESSAGE, message, metadata
        text = payload.get("text")
        if isinstance(text, str) and text.strip():
            return DELIVERABLE_KIND_MESSAGE, text, metadata

    if payload_type == "function_call":
        function_name = payload.get("name")
        if function_name == "request_user_input":
            request_input_result = _parse_request_user_input_function_call(payload)
            if request_input_result:
                text, extra = request_input_result
                return DELIVERABLE_KIND_REQUEST_INPUT, text, extra
            worker_log.warning(
                "检测到 request_user_input 但解析失败，已降级为提示文本",
                extra={"timestamp": event_timestamp or "-"},
            )
            return (
                DELIVERABLE_KIND_REQUEST_INPUT,
                "⚠️ 检测到 request_user_input，但题目解析失败，请手动在终端中继续交互。",
                {"error": "request_user_input_parse_failed"},
            )
        if function_name == "update_plan":
            plan_result = _format_plan_update(payload.get("arguments"), event_timestamp=event_timestamp)
            if plan_result:
                plan_text, plan_completed = plan_result
                extra: Dict[str, Any] = {"plan_completed": plan_completed}
                call_id = payload.get("call_id")
                if call_id:
                    extra["call_id"] = call_id
                return DELIVERABLE_KIND_PLAN, plan_text, extra

    if payload.get("event") == "final":
        if not _should_deliver_message(payload):
            return None
        delta = payload.get("delta")
        if isinstance(delta, str) and delta.strip():
            return DELIVERABLE_KIND_MESSAGE, delta, None

    return None


def _extract_claudecode_payload(
    data: dict, *, event_timestamp: Optional[str]
) -> Optional[Tuple[str, str, Optional[Dict[str, Any]]]]:
    # Claude Code 在启动时会输出 isSidechain=true 的欢迎语，此类事件直接忽略
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


def _collapse_codex_response_item_duplicates(events: List[SessionDeliverable]) -> List[SessionDeliverable]:
    """收敛 Codex 同时间戳的 assistant_message/message 双发，仅保留正式 message。"""

    if not _is_codex_model():
        return events
    preferred_timestamps = {
        item.timestamp
        for item in events
        if item.kind == DELIVERABLE_KIND_MESSAGE
        and item.timestamp
        and (item.metadata or {}).get("codex_response_item_type") == "message"
    }
    if not preferred_timestamps:
        return events

    collapsed: List[SessionDeliverable] = []
    for item in events:
        source_type = (item.metadata or {}).get("codex_response_item_type")
        if source_type == "assistant_message" and item.timestamp in preferred_timestamps:
            continue
        collapsed.append(item)
    return collapsed


def _read_session_events_jsonl(path: Path, offset: int) -> Tuple[int, List[SessionDeliverable]]:
    """读取 Codex/ClaudeCode 的 JSONL 会话增量事件（按字节偏移）。"""

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

    return new_offset, _collapse_codex_response_item_duplicates(events)


def _read_gemini_session_json(path: Path) -> Optional[dict]:
    """读取 Gemini session-*.json（可能在写入中，解析失败时返回 None）。"""

    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except FileNotFoundError:
        return None
    except OSError:
        return None
    if not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _read_session_events_gemini(path: Path, cursor: int) -> Tuple[int, List[SessionDeliverable]]:
    """读取 Gemini 的 JSON 会话增量事件（按 messages 下标游标）。"""

    data = _read_gemini_session_json(path)
    if data is None:
        return cursor, []

    messages = data.get("messages")
    if not isinstance(messages, list):
        return cursor, []

    total = len(messages)
    safe_cursor = max(int(cursor or 0), 0)
    # 若游标异常（例如被旧逻辑写入了字节偏移），回退到最近 N 条，避免完全跳过新输出。
    if safe_cursor > total:
        safe_cursor = max(total - max(GEMINI_SESSION_INITIAL_BACKTRACK_MESSAGES, 0), 0)

    deliverables: List[SessionDeliverable] = []
    for idx in range(safe_cursor, total):
        item = messages[idx]
        if not isinstance(item, dict):
            continue
        msg_type = item.get("type")
        if msg_type not in {"gemini", "assistant"}:
            continue
        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        ts = item.get("timestamp")
        event_timestamp = ts if isinstance(ts, str) else None
        metadata: Optional[Dict[str, Any]] = None
        message_id = item.get("id")
        if isinstance(message_id, str) and message_id:
            metadata = {"message_id": message_id}
        deliverables.append(
            SessionDeliverable(
                # offset 需要是整数且可去重：使用 1-based 的消息序号
                offset=idx + 1,
                kind=DELIVERABLE_KIND_MESSAGE,
                text=content,
                timestamp=event_timestamp,
                metadata=metadata,
            )
        )

    return total, deliverables


def _read_session_events(path: Path) -> Tuple[int, List[SessionDeliverable]]:
    key = str(path)
    offset = SESSION_OFFSETS.get(key)
    is_gemini_session = path.suffix.lower() == ".json"
    if offset is None:
        if is_gemini_session:
            offset = 0
        else:
            try:
                offset = path.stat().st_size
            except FileNotFoundError:
                offset = 0
        SESSION_OFFSETS[key] = offset

    if is_gemini_session:
        return _read_session_events_gemini(path, int(offset))
    return _read_session_events_jsonl(path, int(offset))


# --- 处理器 ---

@router.message(Command("help"))
async def on_help_command(message: Message) -> None:
    text = (
        "*指令总览*\n"
        "- /help — 查看全部命令\n"
        "- /tasks — 任务管理命令清单\n"
        "- /task_new — 创建任务（交互式或附带参数）\n"
        "- /task_list — 查看任务列表，支持 status/limit/offset\n"
        "- /task_show — 查看某个任务详情\n"
        "- /task_update — 快速更新任务字段\n"
        "- /task_note — 添加任务备注\n"
        "- /attach TASK_0001 — 为任务上传附件\n"
        "- /commands — 管理自定义命令（新增/执行/编辑）\n"
        "- /task_delete — 归档或恢复任务\n"
        "- 子任务功能已下线，请使用 /task_new 创建新的任务\n\n"
        "提示：大部分操作都提供按钮和多轮对话引导，无需记忆复杂参数。"
    )
    await _answer_with_markdown(message, text)


@router.message(Command("tasks"))
async def on_tasks_help(message: Message) -> None:
    text = (
        "*任务管理命令*\n"
        "- /task_new 标题 | type=需求 — 创建任务\n"
        "- /task_new 标题 | type=缺陷 | reproduction=复现步骤 | expected_result=期望结果 — 创建结构化缺陷\n"
        "- /task_new 标题 | type=优化 | current_effect=当前效果 | expected_effect=期望效果 — 创建结构化优化\n"
        "- /task_list [status=test] [limit=10] [offset=0] — 列出任务\n"
        "- /task_show TASK_0001 — 查看详情\n"
        "- /task_update TASK_0001 status=test | priority=2 | type=缺陷 — 更新字段\n"
        "- /task_note TASK_0001 备注内容 | type=research — 添加备注\n"
        "- /attach TASK_0001 — 上传附件并绑定\n"
        "- /task_delete TASK_0001 — 归档任务（再次执行可恢复）\n"
        "- 子任务功能已下线，请使用 /task_new 创建新的任务\n\n"
        "建议：使用 `/task_new`、`/task_show` 等命令触发后按按钮完成后续步骤。"
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


def _extract_actor_user_id(actor: Optional[str]) -> Optional[int]:
    """从 actor 文本中提取用户 ID（兼容 `name#123` / `123`）。"""

    raw = (actor or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        return int(raw)
    matched = re.search(r"#(\d+)$", raw)
    if matched:
        return int(matched.group(1))
    return None


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
    status_text = _format_status(status) if status else "全部"
    lines = [
        "*任务列表*",
        f"筛选状态：{status_text} · 页码 {current_page_display}/{display_pages} · 每页 {limit} 条 · 总数 {total}",
    ]
    if not tasks:
        lines.append("当前没有匹配的任务，可使用上方状态按钮切换。")
    text = "\n".join(lines)
    running_task_ids = await _list_running_task_ids_for_task_list()

    rows: list[list[InlineKeyboardButton]] = []
    rows.extend(_build_status_filter_row(status, limit))
    for task in tasks:
        label = _compose_task_button_label(
            task,
            is_session_running=((_normalize_task_id(task.id) or "") in running_task_ids),
        )
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
                text="⬅️ 上一页",
                callback_data=f"task:list_page:{status_token}:{page-1}:{limit}",
            )
        )
    if total_pages and page < total_pages:
        nav_row.append(
            InlineKeyboardButton(
                text="下一页 ➡️",
                callback_data=f"task:list_page:{status_token}:{page+1}:{limit}",
            )
        )
    if nav_row:
        rows.append(nav_row)

    rows.append(
        [
            InlineKeyboardButton(
                text="🔍 搜索任务",
                callback_data=f"{TASK_LIST_SEARCH_CALLBACK}:{status_token}:{page}:{limit}",
            ),
            InlineKeyboardButton(
                text="➕ 创建任务",
                callback_data=TASK_LIST_CREATE_CALLBACK,
            ),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="🚀 批量推送任务",
                callback_data=TASK_BATCH_PUSH_START_CALLBACK,
            )
        ]
    )

    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    return text, markup


async def _build_task_batch_push_view(
    *,
    status: Optional[str],
    page: int,
    limit: int,
    selected_task_ids: Sequence[str],
    selected_task_order: Sequence[str],
) -> tuple[str, InlineKeyboardMarkup]:
    """构造任务列表多选批量推送视图。"""

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
    status_text = _format_status(status) if status else "全部"
    selected_set = {
        item
        for item in (_normalize_task_id(task_id) for task_id in selected_task_ids)
        if item
    }
    selection_order = [
        item
        for item in (_normalize_task_id(task_id) for task_id in selected_task_order)
        if item
    ]
    lines = [
        "*批量推送任务*",
        f"筛选状态：{status_text} · 页码 {current_page_display}/{display_pages} · 每页 {limit} 条 · 总数 {total}",
        f"已选任务：{len(selection_order)} 个",
        "点击任务可切换勾选；确认后将统一选择会话与模式，并以排队消息依次发送到终端。",
    ]
    if not tasks:
        lines.append("当前页没有可选择的任务。")
    text = "\n".join(lines)

    rows: list[list[InlineKeyboardButton]] = []
    for task in tasks:
        normalized_task_id = _normalize_task_id(task.id) or task.id
        marker = "✅" if normalized_task_id in selected_set else "⬜️"
        label = f"{marker} {_compose_task_button_label(task, max_length=56)}"
        rows.append(
            [
                InlineKeyboardButton(
                    text=label[:64],
                    callback_data=f"{TASK_BATCH_PUSH_TOGGLE_PREFIX}{normalized_task_id}",
                )
            ]
        )

    nav_row: list[InlineKeyboardButton] = []
    if page > 1:
        nav_row.append(
            InlineKeyboardButton(
                text="⬅️ 上一页",
                callback_data=f"{TASK_BATCH_PUSH_PAGE_PREFIX}{page-1}",
            )
        )
    if total_pages and page < total_pages:
        nav_row.append(
            InlineKeyboardButton(
                text="下一页 ➡️",
                callback_data=f"{TASK_BATCH_PUSH_PAGE_PREFIX}{page+1}",
            )
        )
    if nav_row:
        rows.append(nav_row)

    rows.append(
        [
            InlineKeyboardButton(
                text=f"🚀 确认批量推送（{len(selection_order)}）",
                callback_data=TASK_BATCH_PUSH_CONFIRM_CALLBACK,
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="❌ 取消批量推送",
                callback_data=TASK_BATCH_PUSH_CANCEL_CALLBACK,
            )
        ]
    )
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


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
    # 修复：避免双重转义
    if _IS_MARKDOWN_V2:
        escaped_keyword = sanitized_keyword
    else:
        escaped_keyword = _escape_markdown_text(sanitized_keyword)
    lines = [
        "*任务搜索结果*",
        f"搜索关键词：{escaped_keyword}",
        "搜索范围：标题、描述",
        f"分页信息：页码 {current_page_display}/{display_pages} · 每页 {limit} 条 · 总数 {total}",
    ]
    if not tasks:
        lines.append("未找到匹配的任务，请调整关键词或重新搜索。")
    running_task_ids = await _list_running_task_ids_for_task_list()

    rows: list[list[InlineKeyboardButton]] = []
    for task in tasks:
        label = _compose_task_button_label(
            task,
            is_session_running=((_normalize_task_id(task.id) or "") in running_task_ids),
        )
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
                text="⬅️ 上一页",
                callback_data=(
                    f"{TASK_LIST_SEARCH_PAGE_CALLBACK}:{encoded_keyword}:"
                    f"{origin_status_token}:{origin_page}:{page-1}:{limit}"
                ),
            )
        )
    if total_pages and page < total_pages:
        nav_row.append(
            InlineKeyboardButton(
                text="下一页 ➡️",
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
                text="🔁 重新搜索",
                callback_data=f"{TASK_LIST_SEARCH_CALLBACK}:{origin_status_token}:{origin_page}:{limit}",
            ),
            InlineKeyboardButton(
                text="📋 返回列表",
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


async def _handle_terminal_snapshot_request(message: Message) -> None:
    """处理“会话实况”按钮，先展示当前项目的可查看会话列表。"""

    chat_id = message.chat.id
    try:
        text, markup = await _build_session_live_list_view()
        await _answer_with_markdown(message, text, reply_markup=markup)
    finally:
        # 轻量自愈：若 watcher 意外退出，尝试恢复推送通道，避免用户必须再发一条消息。
        await _resume_session_watcher_if_needed(chat_id, reason="session_live")


@router.message(Command("task_list"))
async def on_task_list(message: Message) -> None:
    await _handle_task_list_request(message)


@router.message(F.text == WORKER_MENU_BUTTON_TEXT)
async def on_task_list_button(message: Message) -> None:
    await _handle_task_list_request(message)


def _extract_task_batch_push_view_state(message: Optional[Message]) -> Optional[TaskViewState]:
    """读取当前消息绑定的批量推送视图状态。"""

    if message is None:
        return None
    chat = getattr(message, "chat", None)
    if chat is None:
        return None
    state = _peek_task_view(chat.id, message.message_id)
    if state is None or state.kind != "batch_push":
        return None
    return state


@router.callback_query(F.data == TASK_BATCH_PUSH_START_CALLBACK)
async def on_task_batch_push_start(callback: CallbackQuery) -> None:
    """从任务列表进入批量推送勾选模式。"""

    message = callback.message
    if message is None:
        await callback.answer("无法定位原始消息", show_alert=True)
        return
    chat = getattr(message, "chat", None)
    state = _peek_task_view(chat.id, message.message_id) if chat else None
    if state is None or state.kind != "list":
        await callback.answer("请先打开任务列表后再试。", show_alert=True)
        return
    status = state.data.get("status")
    page = int(state.data.get("page", 1) or 1)
    limit = int(state.data.get("limit", DEFAULT_PAGE_SIZE) or DEFAULT_PAGE_SIZE)
    text, markup = await _build_task_batch_push_view(
        status=status,
        page=page,
        limit=limit,
        selected_task_ids=[],
        selected_task_order=[],
    )
    next_state = _make_batch_push_view_state(
        status=status,
        page=page,
        limit=limit,
        selected_task_ids=[],
        selected_task_order=[],
    )
    if await _try_edit_message(message, text, reply_markup=markup):
        _set_task_view_context(message, next_state)
    else:
        origin_chat = getattr(message, "chat", None)
        if origin_chat is not None:
            _clear_task_view(origin_chat.id, message.message_id)
        sent = await _answer_with_markdown(message, text, reply_markup=markup)
        if sent is not None:
            _init_task_view_context(sent, next_state)
    await callback.answer("已进入批量推送模式")


@router.callback_query(F.data.startswith(TASK_BATCH_PUSH_TOGGLE_PREFIX))
async def on_task_batch_push_toggle(callback: CallbackQuery) -> None:
    """在批量推送勾选视图中切换任务选择状态。"""

    message = callback.message
    view_state = _extract_task_batch_push_view_state(message)
    if view_state is None or message is None:
        await callback.answer("批量推送视图已失效，请重新进入。", show_alert=True)
        return
    task_id = _normalize_task_id((callback.data or "")[len(TASK_BATCH_PUSH_TOGGLE_PREFIX) :])
    if not task_id:
        await callback.answer("任务参数错误", show_alert=True)
        return
    selected_set = {
        item
        for item in (_normalize_task_id(value) for value in (view_state.data.get("selected_task_ids") or []))
        if item
    }
    selected_order = [
        item
        for item in (_normalize_task_id(value) for value in (view_state.data.get("selected_task_order") or []))
        if item
    ]
    if task_id in selected_set:
        selected_set.remove(task_id)
        selected_order = [item for item in selected_order if item != task_id]
        notice = f"已取消 {task_id}"
    else:
        selected_set.add(task_id)
        selected_order.append(task_id)
        notice = f"已选择 {task_id}"
    status = view_state.data.get("status")
    page = int(view_state.data.get("page", 1) or 1)
    limit = int(view_state.data.get("limit", DEFAULT_PAGE_SIZE) or DEFAULT_PAGE_SIZE)
    text, markup = await _build_task_batch_push_view(
        status=status,
        page=page,
        limit=limit,
        selected_task_ids=list(selected_set),
        selected_task_order=selected_order,
    )
    next_state = _make_batch_push_view_state(
        status=status,
        page=page,
        limit=limit,
        selected_task_ids=list(selected_set),
        selected_task_order=selected_order,
    )
    if await _try_edit_message(message, text, reply_markup=markup):
        _set_task_view_context(message, next_state)
    else:
        origin_chat = getattr(message, "chat", None)
        if origin_chat is not None:
            _clear_task_view(origin_chat.id, message.message_id)
        sent = await _answer_with_markdown(message, text, reply_markup=markup)
        if sent is not None:
            _init_task_view_context(sent, next_state)
    await callback.answer(notice)


@router.callback_query(F.data.startswith(TASK_BATCH_PUSH_PAGE_PREFIX))
async def on_task_batch_push_page(callback: CallbackQuery) -> None:
    """批量推送勾选视图分页。"""

    message = callback.message
    view_state = _extract_task_batch_push_view_state(message)
    if view_state is None or message is None:
        await callback.answer("批量推送视图已失效，请重新进入。", show_alert=True)
        return
    page_text = (callback.data or "")[len(TASK_BATCH_PUSH_PAGE_PREFIX) :].strip()
    try:
        target_page = max(int(page_text), 1)
    except ValueError:
        await callback.answer("分页参数错误", show_alert=True)
        return
    status = view_state.data.get("status")
    limit = int(view_state.data.get("limit", DEFAULT_PAGE_SIZE) or DEFAULT_PAGE_SIZE)
    selected_task_ids = [str(item).strip() for item in (view_state.data.get("selected_task_ids") or []) if str(item).strip()]
    selected_task_order = [str(item).strip() for item in (view_state.data.get("selected_task_order") or []) if str(item).strip()]
    text, markup = await _build_task_batch_push_view(
        status=status,
        page=target_page,
        limit=limit,
        selected_task_ids=selected_task_ids,
        selected_task_order=selected_task_order,
    )
    next_state = _make_batch_push_view_state(
        status=status,
        page=target_page,
        limit=limit,
        selected_task_ids=selected_task_ids,
        selected_task_order=selected_task_order,
    )
    if await _try_edit_message(message, text, reply_markup=markup):
        _set_task_view_context(message, next_state)
    else:
        origin_chat = getattr(message, "chat", None)
        if origin_chat is not None:
            _clear_task_view(origin_chat.id, message.message_id)
        sent = await _answer_with_markdown(message, text, reply_markup=markup)
        if sent is not None:
            _init_task_view_context(sent, next_state)
    await callback.answer()


@router.callback_query(F.data == TASK_BATCH_PUSH_CANCEL_CALLBACK)
async def on_task_batch_push_cancel(callback: CallbackQuery) -> None:
    """退出批量推送勾选模式并恢复任务列表。"""

    message = callback.message
    view_state = _extract_task_batch_push_view_state(message)
    if view_state is None or message is None:
        await callback.answer("批量推送视图已失效，请重新打开任务列表。", show_alert=True)
        return
    status = view_state.data.get("status")
    page = int(view_state.data.get("page", 1) or 1)
    limit = int(view_state.data.get("limit", DEFAULT_PAGE_SIZE) or DEFAULT_PAGE_SIZE)
    await _restore_task_list_after_batch_push(
        target_message=message,
        fallback_message=message,
        status=status,
        page=page,
        limit=limit,
    )
    await callback.answer("已取消批量推送")


@router.callback_query(F.data == TASK_BATCH_PUSH_CONFIRM_CALLBACK)
async def on_task_batch_push_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    """确认勾选结果，进入批量推送统一参数选择流程。"""

    message = callback.message
    view_state = _extract_task_batch_push_view_state(message)
    if view_state is None or message is None:
        await callback.answer("批量推送视图已失效，请重新进入。", show_alert=True)
        return
    selected_task_ids = [str(item).strip() for item in (view_state.data.get("selected_task_order") or []) if str(item).strip()]
    if not selected_task_ids:
        await callback.answer("请先勾选至少一个任务。", show_alert=True)
        return
    if not _is_codex_model():
        await callback.answer("当前模型不支持批量排队推送，请切换到 Codex 后重试。", show_alert=True)
        return

    await state.clear()
    await state.update_data(
        batch_task_ids=selected_task_ids,
        batch_origin_message=message,
        batch_status=view_state.data.get("status"),
        batch_page=int(view_state.data.get("page", 1) or 1),
        batch_limit=int(view_state.data.get("limit", DEFAULT_PAGE_SIZE) or DEFAULT_PAGE_SIZE),
        actor=_actor_from_callback(callback),
        chat_id=message.chat.id,
    )

    entries = await _list_project_live_sessions()
    if len(entries) == 1 and entries[0].kind == "main":
        await state.update_data(
            selected_existing_session_key="main",
            batch_session_label=entries[0].label,
        )
        await state.set_state(TaskBatchPushStates.waiting_choice)
        await callback.answer("已默认选择主会话")
        await _prompt_push_mode_input(message)
        return

    await state.set_state(TaskBatchPushStates.waiting_existing_session)
    await callback.answer("请选择目标会话")
    await _show_task_batch_push_existing_session_view(message)


@router.message(F.text == WORKER_TERMINAL_SNAPSHOT_BUTTON_TEXT)
async def on_tmux_snapshot_button(message: Message) -> None:
    await _handle_terminal_snapshot_request(message)


async def _show_session_live_list(message: Message) -> bool:
    """在当前消息中展示会话列表；编辑失败时回退为发送新消息。"""

    text, markup = await _build_session_live_list_view()
    if await _try_edit_message(message, text, reply_markup=markup):
        return True
    sent = await _answer_with_markdown(message, text, reply_markup=markup)
    return sent is not None


async def _show_session_live_snapshot(message: Message, entry_key: str) -> bool:
    """在当前消息中展示指定会话的最近输出。"""

    text, markup = await _build_session_live_snapshot_view(entry_key)
    if await _try_edit_message(message, text, reply_markup=markup):
        return True
    sent = await _answer_with_markdown(message, text, reply_markup=markup)
    return sent is not None


@router.callback_query(F.data == SESSION_LIVE_LIST_CALLBACK)
async def on_session_live_list_callback(callback: CallbackQuery) -> None:
    message = callback.message
    if message is None:
        await callback.answer("无法定位原消息", show_alert=True)
        return
    success = await _show_session_live_list(message)
    await callback.answer("已刷新会话列表" if success else "会话列表发送失败", show_alert=not success)


@router.callback_query(F.data == SESSION_LIVE_MAIN_CALLBACK)
async def on_session_live_main_callback(callback: CallbackQuery) -> None:
    message = callback.message
    if message is None:
        await callback.answer("无法定位原消息", show_alert=True)
        return
    try:
        success = await _show_session_live_snapshot(message, "main")
    except (ValueError, RuntimeError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("已打开主会话" if success else "会话实况发送失败", show_alert=not success)


@router.callback_query(F.data.startswith(SESSION_LIVE_PARALLEL_PREFIX))
async def on_session_live_parallel_callback(callback: CallbackQuery) -> None:
    message = callback.message
    if message is None:
        await callback.answer("无法定位原消息", show_alert=True)
        return
    task_id = _normalize_task_id((callback.data or "")[len(SESSION_LIVE_PARALLEL_PREFIX) :])
    if not task_id:
        await callback.answer("会话参数错误", show_alert=True)
        return
    try:
        success = await _show_session_live_snapshot(message, f"parallel:{task_id}")
    except (ValueError, RuntimeError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("已打开并行会话" if success else "会话实况发送失败", show_alert=not success)


@router.callback_query(F.data == SESSION_LIVE_REFRESH_MAIN_CALLBACK)
async def on_session_live_refresh_main_callback(callback: CallbackQuery) -> None:
    message = callback.message
    if message is None:
        await callback.answer("无法定位原消息", show_alert=True)
        return
    try:
        success = await _show_session_live_snapshot(message, "main")
    except (ValueError, RuntimeError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("已刷新主会话" if success else "会话实况发送失败", show_alert=not success)


@router.callback_query(F.data.startswith(SESSION_LIVE_REFRESH_PARALLEL_PREFIX))
async def on_session_live_refresh_parallel_callback(callback: CallbackQuery) -> None:
    message = callback.message
    if message is None:
        await callback.answer("无法定位原消息", show_alert=True)
        return
    task_id = _normalize_task_id((callback.data or "")[len(SESSION_LIVE_REFRESH_PARALLEL_PREFIX) :])
    if not task_id:
        await callback.answer("会话参数错误", show_alert=True)
        return
    try:
        success = await _show_session_live_snapshot(message, f"parallel:{task_id}")
    except (ValueError, RuntimeError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("已刷新并行会话" if success else "会话实况发送失败", show_alert=not success)


async def _handle_worker_plan_mode_toggle_request(message: Message) -> None:
    """处理主键盘 PLAN MODE 切换按钮。"""

    before_state = await _refresh_worker_plan_mode_state_cache_async(force_probe=True)

    try:
        await asyncio.to_thread(tmux_send_key, TMUX_SESSION, WORKER_PLAN_MODE_TOGGLE_KEY)
    except FileNotFoundError:
        _set_worker_plan_mode_state_cache("unknown")
        await message.answer(
            "未检测到 tmux，可通过 'brew install tmux' 安装后重试。",
            reply_markup=_build_worker_main_keyboard(
                plan_mode_state="unknown",
                refresh_plan_mode_state=False,
            ),
        )
        return
    except subprocess.CalledProcessError:
        _set_worker_plan_mode_state_cache("unknown")
        await message.answer(
            f"无法切换 PLAN MODE，请确认 tmux 会话 {TMUX_SESSION} 已启动。",
            reply_markup=_build_worker_main_keyboard(
                plan_mode_state="unknown",
                refresh_plan_mode_state=False,
            ),
        )
        return

    after_state = await _refresh_worker_plan_mode_state_after_toggle_async(before_state=before_state)
    state_label = {
        "on": "ON",
        "off": "OFF",
        "unknown": "?",
    }.get(after_state, "?")

    summary = f"主菜单已刷新，当前 PLAN MODE：{state_label}"

    await message.answer(
        summary,
        reply_markup=_build_worker_main_keyboard(
            plan_mode_state=after_state,
            refresh_plan_mode_state=False,
        ),
    )


@router.message(F.text.regexp(r"^🧭 PLAN MODE:"))
async def on_worker_plan_mode_button(message: Message) -> None:
    await _handle_worker_plan_mode_toggle_request(message)


@router.message(Command("commands"))
async def on_commands_command(message: Message) -> None:
    await _send_command_overview(message)


@router.message(F.text == WORKER_COMMANDS_BUTTON_TEXT)
async def on_commands_button(message: Message) -> None:
    await _send_command_overview(message)


@router.callback_query(F.data.startswith(MODEL_QUICK_REPLY_ALL_CALLBACK))
async def on_model_quick_reply_all(callback: CallbackQuery) -> None:
    """将“全部按推荐”快捷回复注入 tmux，模拟用户发送一条消息到模型。"""

    chat_id = callback.message.chat.id if callback.message else callback.from_user.id
    origin_message = callback.message
    prompt = "待决策项全部按模型推荐"
    _remember_chat_active_user(chat_id, callback.from_user.id if callback.from_user else None)
    task_id = None
    token = None
    native_binding = None
    if callback.data and callback.data.startswith(MODEL_QUICK_REPLY_ALL_TASK_PREFIX):
        task_id, token = _parse_parallel_callback_payload(callback.data[len(MODEL_QUICK_REPLY_ALL_TASK_PREFIX) :].strip())
    elif callback.data and callback.data.startswith(MODEL_QUICK_REPLY_ALL_SESSION_PREFIX):
        task_id, token = _parse_parallel_callback_payload(callback.data[len(MODEL_QUICK_REPLY_ALL_SESSION_PREFIX) :].strip())
        native_binding = _resolve_session_quick_reply_binding(chat_id, task_id, token)
        if native_binding is None:
            await callback.answer("该消息所属会话已失效，请在最新会话中重试。", show_alert=True)
            if callback.message is not None:
                await callback.message.answer(
                    "该消息所属会话已失效，请在最新会话中重试。",
                    reply_markup=_build_worker_main_keyboard(),
                )
            return
    elif callback.data == MODEL_QUICK_REPLY_ALL_CALLBACK and _should_fail_closed_legacy_native_quick_reply(chat_id):
        await callback.answer("该消息所属会话已失效，请在最新会话中重试。", show_alert=True)
        if callback.message is not None:
            await callback.message.answer(
                "该消息所属会话已失效，请在最新会话中重试。",
                reply_markup=_build_worker_main_keyboard(),
            )
        return
    dispatch_context = None
    if native_binding is None and (task_id or token):
        task_id, dispatch_context = await _resolve_parallel_dispatch_context(task_id, token)
        if dispatch_context is None:
            await callback.answer("并行会话已失效，请在最新并行消息中重试。", show_alert=True)
            if callback.message is not None:
                await callback.message.answer(
                    "并行会话已失效，请回到最新并行消息重新操作。",
                    reply_markup=_build_worker_main_keyboard(),
                )
            return

    dispatch_kwargs: dict[str, Any] = {
        "reply_to": origin_message,
        "ack_immediately": False,
    }
    if dispatch_context is not None:
        dispatch_kwargs["dispatch_context"] = dispatch_context
    success, session_path = await _dispatch_prompt_to_model(
        chat_id,
        prompt,
        **dispatch_kwargs,
    )
    if not success:
        await callback.answer("推送失败：模型未就绪", show_alert=True)
        if callback.message is not None:
            await callback.message.answer(
                "推送失败：模型未就绪，请稍后再试。",
                reply_markup=_build_worker_main_keyboard(),
            )
        return

    await callback.answer("已推送到模型")
    preview_block, preview_parse_mode = _wrap_text_in_code_block(prompt)
    await _send_model_push_preview(
        chat_id,
        preview_block,
        reply_to=origin_message,
        parse_mode=preview_parse_mode,
        reply_markup=_build_worker_main_keyboard(),
    )
    if session_path is not None:
        await _send_session_ack(chat_id, session_path, reply_to=origin_message)
    await _try_remove_clicked_inline_button(
        callback.message,
        callback_data=callback.data,
    )


@router.callback_query(F.data.startswith(MODEL_QUICK_REPLY_PARTIAL_CALLBACK))
async def on_model_quick_reply_partial(callback: CallbackQuery, state: FSMContext) -> None:
    """进入“部分按推荐（需补充）”流程，先收集用户补充说明再推送到模型。"""

    chat_id = callback.message.chat.id if callback.message else callback.from_user.id
    origin_message = callback.message
    current_state = await state.get_state()
    if current_state and current_state != ModelQuickReplyStates.waiting_partial_supplement.state:
        await callback.answer("当前有进行中的流程，请先完成或发送“取消”。", show_alert=True)
        return

    parallel_task_id = None
    parallel_token = None
    parallel_dispatch_context = None
    native_quick_reply_session_key = None
    if callback.data and callback.data.startswith(MODEL_QUICK_REPLY_PARTIAL_TASK_PREFIX):
        parallel_task_id, parallel_token = _parse_parallel_callback_payload(
            callback.data[len(MODEL_QUICK_REPLY_PARTIAL_TASK_PREFIX) :].strip()
        )
        if parallel_task_id or parallel_token:
            parallel_task_id, parallel_dispatch_context = await _resolve_parallel_dispatch_context(
                parallel_task_id,
                parallel_token,
            )
            if parallel_dispatch_context is None:
                await callback.answer("并行会话已失效，请在最新并行消息中重试。", show_alert=True)
                if origin_message is not None:
                    await origin_message.answer(
                        "并行会话已失效，请回到最新并行消息重新操作。",
                        reply_markup=_build_worker_main_keyboard(),
                    )
                return
    elif callback.data and callback.data.startswith(MODEL_QUICK_REPLY_PARTIAL_SESSION_PREFIX):
        native_task_id, native_token = _parse_parallel_callback_payload(
            callback.data[len(MODEL_QUICK_REPLY_PARTIAL_SESSION_PREFIX) :].strip()
        )
        native_binding = _resolve_session_quick_reply_binding(chat_id, native_task_id, native_token)
        if native_binding is None:
            await callback.answer("该消息所属会话已失效，请在最新会话中重试。", show_alert=True)
            if origin_message is not None:
                await origin_message.answer(
                    "该消息所属会话已失效，请在最新会话中重试。",
                    reply_markup=_build_worker_main_keyboard(),
                )
            return
        native_quick_reply_session_key = native_binding.session_key
    elif callback.data == MODEL_QUICK_REPLY_PARTIAL_CALLBACK and _should_fail_closed_legacy_native_quick_reply(chat_id):
        await callback.answer("该消息所属会话已失效，请在最新会话中重试。", show_alert=True)
        if origin_message is not None:
            await origin_message.answer(
                "该消息所属会话已失效，请在最新会话中重试。",
                reply_markup=_build_worker_main_keyboard(),
            )
        return

    await state.clear()
    await state.update_data(
        chat_id=chat_id,
        origin_message=origin_message,
        parallel_task_id=parallel_task_id,
        parallel_dispatch_context=parallel_dispatch_context,
        native_quick_reply_session_key=native_quick_reply_session_key,
        partial_callback_data=(callback.data or MODEL_QUICK_REPLY_PARTIAL_CALLBACK),
        # 用于后续超时清理或排查问题（单位：秒）。
        started_at=time.time(),
    )
    await state.set_state(ModelQuickReplyStates.waiting_partial_supplement)
    await callback.answer("请发送补充说明，或点击跳过/取消")
    if origin_message is not None:
        await _prompt_quick_reply_partial_supplement_input(origin_message)


@router.callback_query(F.data.startswith(MODEL_TASK_TO_TEST_PREFIX))
async def on_model_task_to_test(callback: CallbackQuery) -> None:
    """从“模型答案消息”一键将任务切换到测试状态。"""

    raw_task_id = ""
    if callback.data:
        raw_task_id = callback.data[len(MODEL_TASK_TO_TEST_PREFIX) :].strip()
    task_id = _normalize_task_id(raw_task_id)
    if not task_id:
        await callback.answer("任务 ID 无效", show_alert=True)
        return
    task = await TASK_SERVICE.get_task(task_id)
    if task is None:
        await callback.answer("任务不存在", show_alert=True)
        return

    if task.status == "test":
        await callback.answer("任务已处于“测试”状态")
        if callback.message is not None:
            await _handle_task_list_request(callback.message)
            await _try_remove_clicked_inline_button(
                callback.message,
                callback_data=callback.data,
            )
        return

    actor = _actor_from_callback(callback)
    try:
        await TASK_SERVICE.update_task(
            task_id,
            actor=actor,
            status="test",
        )
    except ValueError as exc:
        await callback.answer(f"任务状态更新失败：{exc}", show_alert=True)
        return

    await callback.answer("已切换到测试")
    if callback.message is not None:
        await callback.message.answer(
            f"任务 /{task_id} 状态已更新为“测试”。",
            reply_markup=_build_worker_main_keyboard(),
        )
        # 体验优化：状态更新为“测试”后自动展示任务列表，减少用户一次额外点击/输入。
        await _handle_task_list_request(callback.message)
        await _try_remove_clicked_inline_button(
            callback.message,
            callback_data=callback.data,
        )


@router.callback_query(F.data.startswith(PARALLEL_REPLY_CALLBACK_PREFIX))
async def on_parallel_reply_callback(callback: CallbackQuery) -> None:
    payload = (callback.data or "")[len(PARALLEL_REPLY_CALLBACK_PREFIX) :].strip()
    task_id, token = _parse_parallel_callback_payload(payload)
    if not task_id:
        await callback.answer("任务 ID 无效", show_alert=True)
        return
    resolved_task_id, dispatch_context = await _resolve_parallel_dispatch_context(task_id, token)
    if dispatch_context is None:
        await callback.answer("未找到活动中的并行会话", show_alert=True)
        return
    chat_id = callback.message.chat.id if callback.message else callback.from_user.id
    _set_parallel_reply_target(
        chat_id,
        resolved_task_id or task_id,
        dispatch_context=dispatch_context,
        token=token,
    )
    await callback.answer("已进入回复模式")
    if callback.message is not None:
        await callback.message.answer(
            f"已进入 /{resolved_task_id or task_id} 回复模式。",
            reply_markup=_build_parallel_reply_input_keyboard(),
        )
        await _try_remove_clicked_inline_button(
            callback.message,
            callback_data=callback.data,
        )


def _collect_native_commit_repos(workspace_root: Path) -> tuple[list[ParallelRepoRecord], list[RepoOperationResult]]:
    """根据原生会话工作目录收敛可提交仓库，并提前过滤 Detached HEAD 仓库。"""

    repos: list[ParallelRepoRecord] = []
    skipped_results: list[RepoOperationResult] = []
    for repo_key, repo_path, _relative_path in discover_git_repos(workspace_root, include_nested=True):
        repo_label = repo_path.name if repo_key != "__root__" else Path(workspace_root).name
        current_label, current_local_branch = get_current_branch_state(repo_path)
        if not current_local_branch:
            skipped_results.append(
                RepoOperationResult(
                    repo_key=repo_key,
                    repo_name=repo_label,
                    ok=True,
                    status="skipped",
                    message=f"当前为 {current_label}，已跳过",
                )
            )
            continue
        repos.append(
            ParallelRepoRecord(
                repo_key=repo_key,
                source_repo_path=str(repo_path),
                workspace_repo_path=str(repo_path),
                selected_base_ref=current_local_branch,
                selected_remote=None,
                task_branch=current_local_branch,
            )
        )
    return repos, skipped_results


def _format_parallel_operation_lines(title: str, results: Sequence[RepoOperationResult]) -> str:
    """按失败/成功/跳过分组格式化并行操作结果，提升 Telegram 消息可读性。"""

    def _normalize_title(raw_title: str) -> str:
        """统一去掉标题末尾冒号，避免视觉噪音。"""

        cleaned = normalize_newlines(raw_title or "").strip()
        return cleaned.rstrip("：:").strip() or "执行结果"

    def _group_key(item: RepoOperationResult) -> Literal["failed", "success", "skipped"]:
        """根据操作结果映射到用户可见分组。"""

        if not item.ok:
            return "failed"
        if item.status == "skipped":
            return "skipped"
        return "success"

    def _append_group(lines: list[str], header: str, items: Sequence[RepoOperationResult]) -> None:
        """将单个结果分组追加到输出文本，并缩进多行 message。"""

        if not items:
            return
        lines.append("")
        lines.append(f"{header}（{len(items)}）")
        for item in items:
            lines.append(f"- {item.repo_name}")
            message_lines = [line.strip() for line in normalize_newlines(item.message or "").splitlines() if line.strip()]
            if not message_lines:
                message_lines = ["-"]
            for line in message_lines:
                lines.append(f"  {line}")

    grouped_results: dict[str, list[RepoOperationResult]] = {
        "failed": [],
        "success": [],
        "skipped": [],
    }
    for item in results:
        grouped_results[_group_key(item)].append(item)

    normalized_title = _normalize_title(title)
    lines = [
        normalized_title,
        (
            f"总览：{len(results)} 个仓库｜失败 {len(grouped_results['failed'])}"
            f"｜成功 {len(grouped_results['success'])}｜跳过 {len(grouped_results['skipped'])}"
        ),
    ]
    _append_group(lines, "❌ 失败", grouped_results["failed"])
    _append_group(lines, "✅ 成功", grouped_results["success"])
    _append_group(lines, "⏭️ 已跳过", grouped_results["skipped"])
    return "\n".join(lines)


@router.callback_query(F.data.startswith(SESSION_COMMIT_CALLBACK_PREFIX))
async def on_session_commit_callback(callback: CallbackQuery) -> None:
    """原生会话消息底部“提交分支”按钮：按会话绑定目录执行多仓库提交。"""

    payload = (callback.data or "")[len(SESSION_COMMIT_CALLBACK_PREFIX) :].strip()
    task_id, token = _parse_parallel_callback_payload(payload)
    if not task_id or not token:
        await callback.answer("会话提交参数无效", show_alert=True)
        return
    binding = SESSION_COMMIT_CALLBACK_BINDINGS.get(token)
    if binding is None or _normalize_task_id(binding.task_id) != task_id:
        await callback.answer("会话提交绑定已失效", show_alert=True)
        return
    workspace_root = resolve_path(binding.workspace_root)
    if not workspace_root.exists():
        await callback.answer("会话目录不存在", show_alert=True)
        return
    task = await TASK_SERVICE.get_task(task_id)
    if task is None:
        await callback.answer("任务不存在", show_alert=True)
        return

    repos, skipped_results = _collect_native_commit_repos(workspace_root)
    if not repos and skipped_results:
        await callback.answer("未发现可提交仓库")
        if callback.message is not None:
            await callback.message.answer(_format_parallel_operation_lines("分支提交结果：", skipped_results))
        return
    if not repos:
        await callback.answer("未发现 Git 仓库", show_alert=True)
        return

    await callback.answer("正在提交当前会话分支…")
    try:
        result = await asyncio.to_thread(commit_parallel_repos, task=task, repos=repos)
    except Exception as exc:  # noqa: BLE001
        worker_log.exception("原生会话提交分支失败：%s", exc, extra={"task_id": task_id, "workspace_root": str(workspace_root)})
        await callback.answer("提交失败", show_alert=True)
        if callback.message is not None:
            await callback.message.answer(
                f"提交失败：{exc}",
                reply_markup=_build_worker_main_keyboard(),
            )
        return
    combined_results = [*skipped_results, *result.results]
    if callback.message is not None:
        await callback.message.answer(_format_parallel_operation_lines("分支提交结果：", combined_results))
        await _try_remove_clicked_inline_button(
            callback.message,
            callback_data=callback.data,
        )


@router.callback_query(F.data.startswith(PARALLEL_COMMIT_CALLBACK_PREFIX))
async def on_parallel_commit_callback(callback: CallbackQuery) -> None:
    raw_task_id = (callback.data or "")[len(PARALLEL_COMMIT_CALLBACK_PREFIX) :].strip()
    task_id = _normalize_task_id(raw_task_id)
    if not task_id:
        await callback.answer("任务 ID 无效", show_alert=True)
        return
    session = await _get_active_parallel_session_for_task(task_id)
    if session is None:
        await callback.answer("并行会话不存在", show_alert=True)
        return
    task = await TASK_SERVICE.get_task(task_id)
    if task is None:
        await callback.answer("任务不存在", show_alert=True)
        return
    repos = await _get_parallel_session_repos(task_id)
    if not repos:
        await callback.answer("未找到并行仓库记录", show_alert=True)
        return
    await callback.answer("正在提交并行分支…")
    try:
        result = await asyncio.to_thread(commit_parallel_repos, task=task, repos=repos)
    except Exception as exc:  # noqa: BLE001
        worker_log.exception("提交并行分支失败：%s", exc, extra={"task_id": task_id})
        await PARALLEL_SESSION_STORE.update_status(task_id, status="running", last_error=f"提交失败：{exc}")
        await callback.answer("提交失败", show_alert=True)
        if callback.message is not None:
            await callback.message.answer(
                f"提交失败：{exc}",
                reply_markup=_build_worker_main_keyboard(),
            )
        return
    for item in result.results:
        await PARALLEL_SESSION_STORE.update_repo_status(
            task_id,
            item.repo_key,
            commit_status=item.status,
            last_error=None if item.ok else item.message,
        )
    await PARALLEL_SESSION_STORE.update_status(
        task_id,
        status="committed" if not result.failed else "running",
        last_error=None if not result.failed else _format_parallel_operation_lines("提交失败", result.results),
        last_commit_at=shanghai_now_iso() if not result.failed else None,
    )
    if callback.message is not None:
        await callback.message.answer(
            _format_parallel_operation_lines("并行分支提交结果：", result.results),
            reply_markup=_build_parallel_post_commit_keyboard(task_id, can_merge=not result.failed),
        )
        await _try_remove_clicked_inline_button(
            callback.message,
            callback_data=callback.data,
        )


@router.callback_query(F.data.startswith(PARALLEL_MERGE_CALLBACK_PREFIX))
async def on_parallel_merge_callback(callback: CallbackQuery) -> None:
    raw_task_id = (callback.data or "")[len(PARALLEL_MERGE_CALLBACK_PREFIX) :].strip()
    task_id = _normalize_task_id(raw_task_id)
    if not task_id:
        await callback.answer("任务 ID 无效", show_alert=True)
        return
    session = await _get_active_parallel_session_for_task(task_id)
    if session is None:
        await callback.answer("并行会话不存在", show_alert=True)
        return
    task = await TASK_SERVICE.get_task(task_id)
    if task is None:
        await callback.answer("任务不存在", show_alert=True)
        return
    repos = await _get_parallel_session_repos(task_id)
    if not repos:
        await callback.answer("未找到并行仓库记录", show_alert=True)
        return
    await callback.answer("正在尝试自动合并…")
    try:
        result = await asyncio.to_thread(merge_parallel_repos, task=task, repos=repos)
    except Exception as exc:  # noqa: BLE001
        worker_log.exception("自动合并并行分支失败：%s", exc, extra={"task_id": task_id})
        await PARALLEL_SESSION_STORE.update_status(task_id, status="merge_failed", last_error=f"自动合并失败：{exc}")
        await callback.answer("自动合并失败", show_alert=True)
        if callback.message is not None:
            await callback.message.answer(
                f"自动合并失败：{exc}",
                reply_markup=_build_worker_main_keyboard(),
            )
        return
    for item in result.results:
        await PARALLEL_SESSION_STORE.update_repo_status(
            task_id,
            item.repo_key,
            merge_status=item.status,
            last_error=None if item.ok else item.message,
        )
    await PARALLEL_SESSION_STORE.update_status(
        task_id,
        status="merged" if not result.failed else "merge_failed",
        last_error=None if not result.failed else _format_parallel_operation_lines("自动合并失败", result.results),
        last_merge_at=shanghai_now_iso() if not result.failed else None,
    )
    if callback.message is not None:
        title = "自动合并成功：" if not result.failed else "自动合并失败："
        await callback.message.answer(
            _format_parallel_operation_lines(title, result.results),
            reply_markup=_build_parallel_post_merge_keyboard(task_id),
        )


@router.callback_query(F.data.startswith(PARALLEL_MERGE_SKIP_CALLBACK_PREFIX))
async def on_parallel_merge_skip_callback(callback: CallbackQuery) -> None:
    await callback.answer("已保留并行目录")


@router.callback_query(F.data.startswith(PARALLEL_DELETE_CALLBACK_PREFIX))
async def on_parallel_delete_callback(callback: CallbackQuery) -> None:
    raw_task_id = (callback.data or "")[len(PARALLEL_DELETE_CALLBACK_PREFIX) :].strip()
    task_id = _normalize_task_id(raw_task_id)
    if not task_id:
        await callback.answer("任务 ID 无效", show_alert=True)
        return
    await callback.answer("请确认是否删除并行目录")
    if callback.message is not None:
        await callback.message.answer(
            f"确认删除 /{task_id} 的并行目录吗？删除后将无法继续在本地并行目录中开发。",
            reply_markup=_build_parallel_delete_confirm_keyboard(task_id),
        )


@router.callback_query(F.data.startswith(PARALLEL_DELETE_CONFIRM_CALLBACK_PREFIX))
async def on_parallel_delete_confirm_callback(callback: CallbackQuery) -> None:
    raw_task_id = (callback.data or "")[len(PARALLEL_DELETE_CONFIRM_CALLBACK_PREFIX) :].strip()
    task_id = _normalize_task_id(raw_task_id)
    if not task_id:
        await callback.answer("任务 ID 无效", show_alert=True)
        return
    await _delete_parallel_session_workspace(task_id)
    await callback.answer("并行目录已删除")
    if callback.message is not None:
        await callback.message.answer(f"/{task_id} 的并行目录已删除。", reply_markup=_build_worker_main_keyboard())


@router.callback_query(F.data.startswith(PARALLEL_DELETE_CANCEL_CALLBACK_PREFIX))
async def on_parallel_delete_cancel_callback(callback: CallbackQuery) -> None:
    await callback.answer("已取消删除")


def _build_quick_reply_partial_prompt(supplement: str) -> str:
    """构建“部分按推荐”最终推送给模型的提示词。"""

    cleaned = (supplement or "").strip()
    return "\n".join(
        [
            "未提及的决策项全部按推荐。",
            "用户补充说明：",
            cleaned,
        ]
    ).rstrip()


@router.message(ModelQuickReplyStates.waiting_partial_supplement)
async def on_model_quick_reply_partial_supplement(message: Message, state: FSMContext) -> None:
    """接收用户补充说明，并推送“部分按推荐”到模型。"""

    data = await state.get_data()
    chat_id = int(data.get("chat_id") or message.chat.id)
    origin_message = data.get("origin_message") or message
    partial_callback_data = (data.get("partial_callback_data") or "").strip() or MODEL_QUICK_REPLY_PARTIAL_CALLBACK

    raw_text = (message.text or message.caption or "")
    trimmed = raw_text.strip()
    resolved = _resolve_reply_choice(raw_text, options=[SKIP_TEXT, "取消"])
    if resolved == "取消" or trimmed == "取消":
        await state.clear()
        await message.answer("已取消快捷回复。", reply_markup=_build_worker_main_keyboard())
        return

    if not trimmed or resolved == SKIP_TEXT:
        prompt = "待决策项全部按模型推荐"
    else:
        if len(trimmed) > DESCRIPTION_MAX_LENGTH:
            # 补充说明超长：自动转为附件提示词推送模型，避免被长度限制卡住。
            attachment = _persist_text_paste_as_attachment(message, trimmed)
            prompt = _build_prompt_with_attachments(
                "\n".join(
                    [
                        "未提及的决策项全部按推荐。",
                        "用户补充说明较长，已作为附件提供，请阅读附件后再继续。",
                    ]
                ),
                [attachment],
            )
        else:
            prompt = _build_quick_reply_partial_prompt(trimmed)

    _remember_chat_active_user(chat_id, message.from_user.id if message.from_user else None)
    dispatch_context = data.get("parallel_dispatch_context")
    if not isinstance(dispatch_context, ParallelDispatchContext):
        dispatch_context = None
    native_quick_reply_session_key = (data.get("native_quick_reply_session_key") or "").strip()
    parallel_task_id = _normalize_task_id(data.get("parallel_task_id"))
    if parallel_task_id and dispatch_context is None:
        session = await _get_active_parallel_session_for_task(parallel_task_id)
        if session is not None:
            dispatch_context = _parallel_dispatch_context_from_session(session)
    if native_quick_reply_session_key:
        current_session_key = (CHAT_SESSION_MAP.get(chat_id) or "").strip()
        if not current_session_key or current_session_key != native_quick_reply_session_key:
            await state.clear()
            await message.answer(
                "该消息所属会话已失效，请在最新会话中重试。",
                reply_markup=_build_worker_main_keyboard(),
            )
            return
    dispatch_kwargs: dict[str, Any] = {
        "reply_to": origin_message,
        "ack_immediately": False,
    }
    if dispatch_context is not None:
        dispatch_kwargs["dispatch_context"] = dispatch_context
    success, session_path = await _dispatch_prompt_to_model(
        chat_id,
        prompt,
        **dispatch_kwargs,
    )
    await state.clear()
    if not success:
        await message.answer("推送失败：模型未就绪，请稍后再试。", reply_markup=_build_worker_main_keyboard())
        return

    preview_block, preview_parse_mode = _wrap_text_in_code_block(prompt)
    await _send_model_push_preview(
        chat_id,
        preview_block,
        reply_to=origin_message,
        parse_mode=preview_parse_mode,
        reply_markup=_build_worker_main_keyboard(),
    )
    if session_path is not None:
        await _send_session_ack(chat_id, session_path, reply_to=origin_message)
    await _try_remove_clicked_inline_button(
        origin_message,
        callback_data=partial_callback_data,
    )


def _is_request_input_question_answered(session: RequestInputSession, question_id: str) -> bool:
    """判断指定题目是否已完成作答。"""

    if question_id not in session.selected_option_indexes:
        return False
    selected_index = session.selected_option_indexes.get(question_id)
    if selected_index == REQUEST_INPUT_CUSTOM_OPTION_INDEX:
        custom_text = (session.custom_answers.get(question_id) or "").strip()
        return bool(custom_text)
    return isinstance(selected_index, int) and selected_index >= 0


def _find_request_input_question_index(session: RequestInputSession, question_id: str) -> Optional[int]:
    """根据 question_id 查找题目下标。"""

    for index, question in enumerate(session.questions):
        if question.question_id == question_id:
            return index
    return None


def _first_unanswered_request_input_index(session: RequestInputSession) -> Optional[int]:
    """返回首个未作答的问题下标。"""

    for index, question in enumerate(session.questions):
        if not _is_request_input_question_answered(session, question.question_id):
            return index
    return None


def _next_unanswered_request_input_index(session: RequestInputSession, *, after_index: int) -> Optional[int]:
    """优先返回当前题之后的未答题；若不存在，再返回首个未答题。"""

    total = len(session.questions)
    if total <= 0:
        return None

    normalized = max(0, min(after_index, total - 1))
    for index in range(normalized + 1, total):
        question = session.questions[index]
        if not _is_request_input_question_answered(session, question.question_id):
            return index
    return _first_unanswered_request_input_index(session)


def _build_request_input_submission_summary(session: RequestInputSession) -> str:
    """构建“已推送”后的决策摘要回显。"""

    lines = [
        "✅ 已推送到模型。",
        "决策摘要：",
    ]
    for index, question in enumerate(session.questions, 1):
        selected_index = session.selected_option_indexes.get(question.question_id)
        answer_text = "未作答"
        if selected_index == REQUEST_INPUT_CUSTOM_OPTION_INDEX:
            custom_text = session.custom_answers.get(question.question_id) or ""
            answer_text = f"自定义：{custom_text}"
        elif isinstance(selected_index, int) and 0 <= selected_index < len(question.options):
            answer_text = question.options[selected_index].label
        lines.append(f"{index}. {question.question} -> {answer_text}")
    return "\n".join(lines)


async def _send_request_input_submission_summary_message(
    session: RequestInputSession,
    *,
    summary_text: str,
    reply_to: Optional[Message],
    remove_reply_keyboard: bool,
) -> None:
    """发送 request_input 决策摘要，超长时自动降级为附件，避免业务层截断。"""

    summary_reply_markup = _build_worker_main_keyboard() if remove_reply_keyboard else None
    try:
        await _reply_to_chat(
            session.chat_id,
            summary_text,
            reply_to=reply_to,
            parse_mode=None,
            reply_markup=summary_reply_markup,
        )
        return
    except TelegramBadRequest as exc:
        reason = _extract_bad_request_message(exc).lower()
        if "message is too long" not in reason:
            raise
        worker_log.warning(
            "request_input 决策摘要超出 Telegram 限制，已降级为附件发送",
            extra={"chat": session.chat_id, "length": str(len(summary_text))},
        )

    await reply_large_text(
        session.chat_id,
        summary_text,
        parse_mode=None,
        preformatted=True,
        reply_markup=summary_reply_markup,
        attachment_reply_markup=summary_reply_markup,
    )


def _build_request_input_retry_submit_keyboard(token: str) -> InlineKeyboardMarkup:
    """构造自动提交失败后的“重试提交”按钮。"""

    rows = [
        [
            InlineKeyboardButton(
                text="🔁 重试提交",
                callback_data=_build_request_input_callback_data(
                    token,
                    REQUEST_INPUT_ACTION_RETRY_SUBMIT,
                ),
            )
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _send_request_input_retry_prompt(
    session: RequestInputSession,
    *,
    reply_to: Optional[Message],
) -> None:
    """在自动提交失败后提示用户手动重试。"""

    text = "⚠️ 自动提交失败。请点击“重试提交”再次尝试。"
    markup = _build_request_input_retry_submit_keyboard(session.token)
    if reply_to is not None:
        await reply_to.answer(text, parse_mode=None, reply_markup=markup)
        return
    await _reply_to_chat(
        session.chat_id,
        text,
        reply_to=None,
        parse_mode=None,
        reply_markup=markup,
    )


async def _submit_request_input_session(
    session: RequestInputSession,
    *,
    reply_to: Optional[Message],
    actor_user_id: Optional[int],
    remove_reply_keyboard: bool = False,
) -> bool:
    """将当前 request_user_input 会话答案推送到模型。"""

    output_payload = _build_request_input_output_payload(session)
    prompt = _build_request_input_submission_prompt(session.call_id, output_payload)
    _remember_chat_active_user(session.chat_id, actor_user_id)
    dispatch_kwargs: dict[str, Any] = {
        "reply_to": reply_to,
        "ack_immediately": False,
    }
    dispatch_context = session.parallel_dispatch_context
    parallel_task_id = _normalize_task_id(session.parallel_task_id)
    if not isinstance(dispatch_context, ParallelDispatchContext) and parallel_task_id:
        _resolved_task_id, dispatch_context = await _resolve_parallel_dispatch_context(parallel_task_id, None)
    if isinstance(dispatch_context, ParallelDispatchContext):
        dispatch_kwargs["dispatch_context"] = dispatch_context
    success, session_path = await _dispatch_prompt_to_model(
        session.chat_id,
        prompt,
        **dispatch_kwargs,
    )
    if not success:
        return False

    summary_text = _build_request_input_submission_summary(session)
    session.submitted = True
    _drop_request_input_session(session.token)
    await _send_request_input_submission_summary_message(
        session,
        summary_text=summary_text,
        reply_to=reply_to,
        remove_reply_keyboard=remove_reply_keyboard,
    )
    # request_user_input 场景已通过“决策摘要”收口，避免重复发送中间工具结果代码块。
    if session_path is not None:
        await _send_session_ack(session.chat_id, session_path, reply_to=reply_to)
    return True


async def _submit_request_input_session_with_auto_retry(
    session: RequestInputSession,
    *,
    reply_to: Optional[Message],
    actor_user_id: Optional[int],
    allow_auto_retry: bool,
    remove_reply_keyboard: bool = False,
) -> bool:
    """提交 request_user_input，会在自动提交链路中按约定做一次重试。"""

    if session.submitted:
        return True

    session.submission_state = "submitting"
    success = await _submit_request_input_session(
        session,
        reply_to=reply_to,
        actor_user_id=actor_user_id,
        remove_reply_keyboard=remove_reply_keyboard,
    )
    if success:
        session.submission_state = "submitted"
        session.submit_retry_count = 0
        return True

    if allow_auto_retry and session.submit_retry_count < REQUEST_INPUT_SUBMIT_AUTO_RETRY_MAX:
        session.submit_retry_count += 1
        success = await _submit_request_input_session(
            session,
            reply_to=reply_to,
            actor_user_id=actor_user_id,
            remove_reply_keyboard=remove_reply_keyboard,
        )
        if success:
            session.submission_state = "submitted"
            return True

    session.submission_state = "failed"
    await _send_request_input_retry_prompt(session, reply_to=reply_to)
    if remove_reply_keyboard:
        await _reply_to_chat(
            session.chat_id,
            "已恢复主菜单，可点击“📋 任务列表”继续。",
            reply_to=reply_to,
            parse_mode=None,
            reply_markup=_build_worker_main_keyboard(),
        )
    return False


def _get_request_input_session_for_chat(chat_id: int) -> Optional[RequestInputSession]:
    """获取 chat 当前拥有“自定义文本输入焦点”的 request_input 会话。"""

    _cleanup_expired_request_input_sessions()
    token = CHAT_ACTIVE_REQUEST_INPUT_TOKENS.get(chat_id)
    if not token:
        return None
    session = REQUEST_INPUT_SESSIONS.get(token)
    if session is None:
        CHAT_ACTIVE_REQUEST_INPUT_TOKENS.pop(chat_id, None)
        return None
    if session.expires_at <= time.monotonic() or session.cancelled or session.submitted:
        _drop_request_input_session(token)
        return None
    return session


def _find_request_input_question_index_by_message_id(
    session: RequestInputSession, message_id: Optional[int]
) -> Optional[int]:
    """根据消息 ID 反查题目下标。"""

    if not isinstance(message_id, int):
        return None
    for index, question in enumerate(session.questions):
        if session.question_message_ids.get(question.question_id) == message_id:
            return index
    return None


def _resolve_request_input_question_index_for_callback(
    session: RequestInputSession,
    values: Sequence[int],
    *,
    callback_message: Optional[Message],
) -> Optional[int]:
    """解析回调里的题目下标，兼容旧版按钮数据。"""

    total = len(session.questions)
    if total <= 0:
        return None

    if values:
        candidate = values[0]
        if 0 <= candidate < total:
            return candidate
        return None

    message_id = getattr(callback_message, "message_id", None)
    inferred = _find_request_input_question_index_by_message_id(session, message_id)
    if inferred is not None:
        return inferred

    if 0 <= session.current_index < total:
        return session.current_index
    return None


def _resolve_request_input_option_selection(
    session: RequestInputSession,
    values: Sequence[int],
    *,
    callback_message: Optional[Message],
) -> Optional[Tuple[int, int]]:
    """解析选项按钮回调中的题目与选项下标。"""

    if len(values) >= 2:
        question_index = _resolve_request_input_question_index_for_callback(
            session,
            [values[0]],
            callback_message=callback_message,
        )
        option_index = values[1]
    elif len(values) == 1:
        # 兼容旧版按钮：payload 里仅包含 option_index。
        question_index = _resolve_request_input_question_index_for_callback(
            session,
            [],
            callback_message=callback_message,
        )
        option_index = values[0]
    else:
        return None

    if question_index is None:
        return None

    return question_index, option_index


async def _lock_request_input_callback_message(message: Optional[Message]) -> None:
    """移除已完成题目的按钮，减少重复点击噪音。"""

    if message is None:
        return
    with suppress(TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter, AttributeError):
        await message.edit_reply_markup(reply_markup=None)


async def _lock_request_input_question_markup(session: RequestInputSession, question_id: str) -> None:
    """按题目映射移除按钮（用于自定义文本提交通道）。"""

    message_id = session.question_message_ids.get(question_id)
    if not isinstance(message_id, int):
        return
    bot = current_bot()
    editor = getattr(bot, "edit_message_reply_markup", None)
    if not callable(editor):
        return
    with suppress(TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter, TypeError):
        await editor(chat_id=session.chat_id, message_id=message_id, reply_markup=None)


async def _handle_request_input_custom_text_message(message: Message) -> bool:
    """处理“输入自定义决策”阶段的文本消息。"""

    session = _get_request_input_session_for_chat(message.chat.id)
    if session is None or not session.input_mode_question_id:
        return False

    user_id = message.from_user.id if message.from_user else None
    if user_id != session.user_id:
        await message.answer("仅会话发起人可输入该自定义决策。")
        return True

    raw_text = message.text or message.caption or ""
    attachments: list[TelegramSavedAttachment] = []
    if _request_input_message_has_media(message):
        attachment_dir = _attachment_dir_for_message(message)
        media_group_id = getattr(message, "media_group_id", None)
        if media_group_id:
            attachments, raw_text, processed_groups = await _collect_generic_media_group(
                message,
                attachment_dir,
                processed=set(session.processed_media_groups),
            )
            session.processed_media_groups = processed_groups
            # 媒体组的后续消息会重复触发 handler；若本次已被其他消息消费，则直接吞掉。
            if not attachments and not raw_text:
                return True
        else:
            attachments = await _collect_saved_attachments(message, attachment_dir)

    token = _normalize_choice_token(raw_text)
    if not attachments and _is_cancel_message(token):
        session.input_mode_question_id = None
        _clear_request_input_text_focus(message.chat.id, session.token)
        await message.answer("已取消自定义输入，返回当前题目。", reply_markup=ReplyKeyboardRemove())
        await _send_request_input_question(session, reply_to=message)
        return True

    trimmed = raw_text.strip()
    if not trimmed and not attachments:
        await message.answer(
            "自定义决策不能为空，请重新输入或发送“取消”。",
            reply_markup=_build_request_input_custom_input_keyboard(),
        )
        return True

    question_id = session.input_mode_question_id
    question_index = _find_request_input_question_index(session, question_id)
    if question_index is None:
        session.input_mode_question_id = None
        _clear_request_input_text_focus(message.chat.id, session.token)
        await message.answer("当前题目已失效，请重新选择。", reply_markup=ReplyKeyboardRemove())
        await _send_request_input_question(session, reply_to=message)
        return True

    question = session.questions[question_index]
    if _is_request_input_question_answered(session, question.question_id):
        session.input_mode_question_id = None
        _clear_request_input_text_focus(message.chat.id, session.token)
        await message.answer("该题已锁定，不可修改。", reply_markup=ReplyKeyboardRemove())
        return True

    if attachments:
        session.custom_answers[question.question_id] = _build_prompt_with_attachments(trimmed or None, attachments)
    else:
        session.custom_answers[question.question_id] = trimmed
    session.selected_option_indexes[question.question_id] = REQUEST_INPUT_CUSTOM_OPTION_INDEX
    session.input_mode_question_id = None
    _clear_request_input_text_focus(message.chat.id, session.token)
    await _lock_request_input_question_markup(session, question.question_id)

    next_unanswered = _next_unanswered_request_input_index(session, after_index=question_index)
    if next_unanswered is None:
        await _submit_request_input_session_with_auto_retry(
            session,
            reply_to=message,
            actor_user_id=user_id,
            allow_auto_retry=True,
            remove_reply_keyboard=True,
        )
        return True

    session.current_index = next_unanswered
    await message.answer(
        f"已记录第 {question_index + 1} 题自定义决策，进入下一题。",
        reply_markup=ReplyKeyboardRemove(),
    )
    await _send_request_input_question(session, reply_to=message)
    return True


@router.callback_query(F.data.startswith(PLAN_CONFIRM_CALLBACK_PREFIX))
async def on_plan_confirm_callback(callback: CallbackQuery) -> None:
    """处理 Plan 收口后的“进入开发”确认按钮。"""

    parsed = _parse_plan_confirm_callback_data(callback.data)
    if parsed is None:
        await callback.answer("交互参数无效", show_alert=True)
        return

    token, action = parsed
    session = PLAN_CONFIRM_SESSIONS.get(token)
    if session is None:
        await callback.answer("该确认已失效，请重新触发。", show_alert=True)
        return

    chat_id = callback.message.chat.id if callback.message else callback.from_user.id
    if chat_id != session.chat_id:
        await callback.answer("会话不匹配，无法操作。", show_alert=True)
        return

    if session.user_id is not None and callback.from_user and callback.from_user.id != session.user_id:
        await callback.answer("仅会话发起人可操作该按钮。", show_alert=True)
        return

    if action == PLAN_CONFIRM_ACTION_NO:
        _drop_plan_confirm_session(token)
        await _refresh_worker_plan_mode_state_cache_async(force_probe=True)
        await callback.answer("已保持 Plan 模式")
        with suppress(TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter):
            if callback.message is not None:
                await callback.message.edit_reply_markup(reply_markup=None)
        return

    if action != PLAN_CONFIRM_ACTION_YES:
        await callback.answer("暂不支持该操作。", show_alert=True)
        return

    if not _claim_plan_confirm_processing_token(token):
        await callback.answer("正在处理中，请勿重复点击。")
        return

    actor_user_id = callback.from_user.id if callback.from_user else session.user_id
    _remember_chat_active_user(chat_id, actor_user_id)
    try:
        dispatch_kwargs: dict[str, Any] = {
            "reply_to": callback.message,
            "ack_immediately": True,
            "intended_mode": None,
            "force_exit_plan_ui": True,
            "force_exit_plan_ui_key_sequence": _build_plan_develop_retry_exit_plan_key_sequence(),
            "force_exit_plan_ui_max_rounds": PLAN_DEVELOP_RETRY_EXIT_PLAN_MAX_ROUNDS,
        }
        dispatch_context = getattr(session, "parallel_dispatch_context", None)
        parallel_task_id = _normalize_task_id(getattr(session, "parallel_task_id", None))
        is_parallel_plan_confirm = isinstance(dispatch_context, ParallelDispatchContext) or bool(parallel_task_id)
        if not isinstance(dispatch_context, ParallelDispatchContext) and parallel_task_id:
            _resolved_task_id, dispatch_context = await _resolve_parallel_dispatch_context(parallel_task_id, None)
            parallel_task_id = _normalize_task_id(_resolved_task_id) or parallel_task_id
        if is_parallel_plan_confirm and not isinstance(dispatch_context, ParallelDispatchContext):
            await callback.answer("并行会话已失效，请在最新并行消息中重试。", show_alert=True)
            if callback.message is not None:
                await callback.message.answer(
                    "并行会话已失效，请回到最新并行消息重新操作。",
                    reply_markup=_build_worker_main_keyboard(),
                )
            return
        if isinstance(dispatch_context, ParallelDispatchContext):
            dispatch_kwargs["dispatch_context"] = dispatch_context
        success, _session_path = await _dispatch_prompt_to_model(
            chat_id,
            PLAN_IMPLEMENT_PROMPT,
            **dispatch_kwargs,
        )
        if not success:
            await callback.answer("推送失败：模型未就绪，请稍后重试。", show_alert=True)
            return

        _drop_plan_confirm_session(token)
        await _refresh_worker_plan_mode_state_cache_async(force_probe=True)
        await callback.answer("已确认并推送到模型")
        with suppress(TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter):
            if callback.message is not None:
                await callback.message.edit_reply_markup(reply_markup=None)
    finally:
        _release_plan_confirm_processing_token(token)


@router.callback_query(F.data.startswith(PLAN_DEVELOP_RETRY_CALLBACK_PREFIX))
async def on_plan_develop_retry_callback(callback: CallbackQuery) -> None:
    """兼容处理历史“重试进入开发”按钮：直接再次推送 Implement。"""

    parsed = _parse_plan_develop_retry_callback_data(callback.data)
    if parsed is None:
        await callback.answer("交互参数无效", show_alert=True)
        return

    _token, action = parsed
    if action != PLAN_DEVELOP_RETRY_ACTION_RETRY:
        await callback.answer("暂不支持该操作。", show_alert=True)
        return

    chat_id = callback.message.chat.id if callback.message else callback.from_user.id
    actor_user_id = callback.from_user.id if callback.from_user else None
    _remember_chat_active_user(chat_id, actor_user_id)
    success, _session_path = await _dispatch_prompt_to_model(
        chat_id,
        PLAN_IMPLEMENT_PROMPT,
        reply_to=callback.message,
        ack_immediately=True,
        intended_mode=None,
        force_exit_plan_ui=True,
        force_exit_plan_ui_key_sequence=_build_plan_develop_retry_exit_plan_key_sequence(),
        force_exit_plan_ui_max_rounds=PLAN_DEVELOP_RETRY_EXIT_PLAN_MAX_ROUNDS,
    )
    if not success:
        await callback.answer("重试失败：模型未就绪，请稍后再试。", show_alert=True)
        return

    await callback.answer("已重试并推送到模型")
    with suppress(TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter):
        if callback.message is not None:
            await callback.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data.startswith(REQUEST_INPUT_CALLBACK_PREFIX))
async def on_request_user_input_callback(callback: CallbackQuery) -> None:
    """处理 request_user_input 的按钮交互。"""

    parsed = _parse_request_input_callback_data(callback.data)
    if parsed is None:
        await callback.answer("交互参数无效", show_alert=True)
        return

    token, action, values = parsed
    _cleanup_expired_request_input_sessions()
    session = REQUEST_INPUT_SESSIONS.get(token)
    if session is None:
        await callback.answer("该交互已失效，请重新触发。", show_alert=True)
        return

    chat_id = callback.message.chat.id if callback.message else callback.from_user.id
    if chat_id != session.chat_id:
        await callback.answer("会话不匹配，无法操作。", show_alert=True)
        return

    if callback.from_user and callback.from_user.id != session.user_id:
        await callback.answer("仅会话发起人可操作该按钮。", show_alert=True)
        return

    if session.expires_at <= time.monotonic():
        _drop_request_input_session(token)
        await callback.answer("交互已过期，请重新触发。", show_alert=True)
        return

    if session.submitted:
        _drop_request_input_session(token)
        await callback.answer("该交互已提交。", show_alert=True)
        return
    if session.cancelled:
        _drop_request_input_session(token)
        await callback.answer("该交互已取消。", show_alert=True)
        return
    if session.submission_state == "submitting":
        await callback.answer("正在提交，请稍候。", show_alert=True)
        return

    if action == REQUEST_INPUT_ACTION_RETRY_SUBMIT:
        if session.submission_state != "failed":
            await callback.answer("当前无需重试提交。", show_alert=True)
            return
        success = await _submit_request_input_session_with_auto_retry(
            session,
            reply_to=callback.message,
            actor_user_id=callback.from_user.id if callback.from_user else None,
            allow_auto_retry=False,
            remove_reply_keyboard=True,
        )
        if success:
            await callback.answer("已重试并推送到模型")
            return
        await callback.answer("重试失败，请稍后再次点击“重试提交”。", show_alert=True)
        return

    if session.input_mode_question_id and action != REQUEST_INPUT_ACTION_CANCEL:
        await callback.answer("当前正在输入自定义决策，请先发送文本或发送“取消”。", show_alert=True)
        return

    if action == REQUEST_INPUT_ACTION_OPTION:
        resolved = _resolve_request_input_option_selection(
            session,
            values,
            callback_message=callback.message,
        )
        if resolved is None:
            await callback.answer("题目或选项无效，请重试。", show_alert=True)
            return
        question_index, option_index = resolved
        if question_index < 0 or question_index >= len(session.questions):
            await callback.answer("题目无效，请重试。", show_alert=True)
            return
        question = session.questions[question_index]
        if option_index < 0 or option_index >= len(question.options):
            await callback.answer("选项无效，请重试。", show_alert=True)
            return
        expected_message_id = session.question_message_ids.get(question.question_id)
        callback_message_id = getattr(callback.message, "message_id", None)
        if isinstance(expected_message_id, int) and isinstance(callback_message_id, int) and expected_message_id != callback_message_id:
            await callback.answer("该题已更新，请使用最新题目消息。", show_alert=True)
            return
        if _is_request_input_question_answered(session, question.question_id):
            await _lock_request_input_callback_message(callback.message)
            await callback.answer(f"第 {question_index + 1} 题已锁定，不可修改。", show_alert=True)
            return
        session.current_index = question_index
        session.selected_option_indexes[question.question_id] = option_index
        session.custom_answers.pop(question.question_id, None)
        session.input_mode_question_id = None
        _clear_request_input_text_focus(chat_id, token)
        await _lock_request_input_callback_message(callback.message)
        option_label = question.options[option_index].label
        next_unanswered = _next_unanswered_request_input_index(
            session,
            after_index=question_index,
        )
        if next_unanswered is None:
            success = await _submit_request_input_session_with_auto_retry(
                session,
                reply_to=callback.message,
                actor_user_id=callback.from_user.id if callback.from_user else None,
                allow_auto_retry=True,
                remove_reply_keyboard=True,
            )
            if not success:
                await callback.answer("自动提交失败，可点击“重试提交”继续。", show_alert=True)
                return
            await callback.answer("已自动推送到模型")
            return

        session.current_index = next_unanswered
        await _send_request_input_question(session, reply_to=callback.message)
        await callback.answer(f"已记录第 {question_index + 1} 题：{option_label}")
        return

    if action == REQUEST_INPUT_ACTION_CUSTOM:
        question_index = _resolve_request_input_question_index_for_callback(
            session,
            values,
            callback_message=callback.message,
        )
        if question_index is None or question_index < 0 or question_index >= len(session.questions):
            await callback.answer("题目无效，请重试。", show_alert=True)
            return
        question = session.questions[question_index]
        if _is_request_input_question_answered(session, question.question_id):
            await _lock_request_input_callback_message(callback.message)
            await callback.answer(f"第 {question_index + 1} 题已锁定，不可修改。", show_alert=True)
            return
        session.current_index = question_index
        session.input_mode_question_id = question.question_id
        _set_request_input_text_focus(chat_id, token)
        await callback.answer("请发送自定义决策文本")
        if callback.message is not None:
            await callback.message.answer(
                f"请发送第 {question_index + 1} 题的自定义决策文本，发送“取消”可返回本题。",
                reply_markup=_build_request_input_custom_input_keyboard(),
            )
        return

    if action == REQUEST_INPUT_ACTION_PREV:
        await callback.answer("当前交互不支持跳题，请按顺序作答。", show_alert=True)
        return

    if action == REQUEST_INPUT_ACTION_NEXT:
        await callback.answer("当前交互不支持跳题，请按顺序作答。", show_alert=True)
        return

    if action == REQUEST_INPUT_ACTION_CANCEL:
        session.cancelled = True
        session.input_mode_question_id = None
        _clear_request_input_text_focus(chat_id, token)
        _drop_request_input_session(token)
        await callback.answer("已取消")
        if callback.message is not None:
            await callback.message.answer("已取消本次决策交互。", reply_markup=_build_worker_main_keyboard())
        return

    if action == REQUEST_INPUT_ACTION_SUBMIT:
        missing_index = _first_unanswered_request_input_index(session)
        if missing_index is not None:
            await callback.answer("请先完成全部题目后再提交。", show_alert=True)
            return

        success = await _submit_request_input_session_with_auto_retry(
            session,
            reply_to=callback.message,
            actor_user_id=callback.from_user.id if callback.from_user else None,
            allow_auto_retry=False,
            remove_reply_keyboard=True,
        )
        if not success:
            await callback.answer("提交失败，可点击“重试提交”继续。", show_alert=True)
            return

        await callback.answer("已提交并推送到模型")
        return

    await callback.answer("暂不支持该操作。", show_alert=True)


@router.callback_query(F.data == COMMAND_REFRESH_CALLBACK)
async def on_command_refresh(callback: CallbackQuery) -> None:
    await _refresh_command_overview(callback)
    await callback.answer("已刷新")


@router.callback_query(F.data == COMMAND_HISTORY_CALLBACK)
async def on_command_history(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer("已忽略")
        return
    history_text, history_markup = await _build_command_history_view()
    await _answer_with_markdown(callback.message, history_text, reply_markup=history_markup)
    await callback.answer("已发送历史")


@router.callback_query(F.data.startswith(COMMAND_HISTORY_DETAIL_PREFIX))
async def on_command_history_detail(callback: CallbackQuery) -> None:
    history_id = _extract_command_id(callback.data, COMMAND_HISTORY_DETAIL_PREFIX)
    if history_id is None:
        await callback.answer("记录标识无效", show_alert=True)
        return
    await _send_history_detail(callback, history_id, COMMAND_SERVICE)


@router.callback_query(F.data.startswith(COMMAND_HISTORY_DETAIL_GLOBAL_PREFIX))
async def on_global_command_history_detail(callback: CallbackQuery) -> None:
    """发送通用命令的执行详情。"""

    history_id = _extract_command_id(callback.data, COMMAND_HISTORY_DETAIL_GLOBAL_PREFIX)
    if history_id is None:
        await callback.answer("记录标识无效", show_alert=True)
        return
    await _send_history_detail(callback, history_id, GLOBAL_COMMAND_SERVICE)


async def _send_history_detail(callback: CallbackQuery, history_id: int, service: CommandService) -> None:
    """发送指定命令执行记录的 txt 详情。"""

    if callback.message is None:
        await callback.answer("无法发送详情", show_alert=True)
        return
    try:
        record = await service.get_history_record(history_id)
    except CommandHistoryNotFoundError:
        await callback.answer("记录不存在或已清理", show_alert=True)
        return
    document = _build_history_detail_document(record)
    caption = f"{record.command_title or record.command_name} 的执行详情"
    try:
        await callback.message.answer_document(document, caption=caption)
    except TelegramBadRequest as exc:
        worker_log.warning(
            "发送命令详情失败：%s",
            exc,
            extra=_session_extra(key="history_detail_send_failed"),
        )
        await callback.answer("发送详情失败", show_alert=True)
        return
    await callback.answer("详情已发送")


@router.callback_query(F.data == COMMAND_NEW_CALLBACK)
async def on_command_new_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(CommandCreateStates.waiting_name)
    if callback.message:
        await callback.message.answer(
            "请输入命令名称（字母开头，可含数字/下划线/短横线），发送“取消”可终止。",
        )
    await callback.answer("请输入命令名称")


@router.callback_query(F.data.startswith(COMMAND_EXEC_PREFIX))
async def on_command_execute_callback(callback: CallbackQuery, state: FSMContext) -> None:
    command_id = _extract_command_id(callback.data, COMMAND_EXEC_PREFIX)
    if command_id is None:
        await callback.answer("命令标识无效", show_alert=True)
        return
    try:
        command = await COMMAND_SERVICE.get_command(command_id)
    except CommandNotFoundError:
        await callback.answer("命令不存在", show_alert=True)
        await _refresh_command_overview(callback, notice="目标命令不存在，列表已刷新。")
        return
    if await _maybe_handle_wx_preview(
        command=command,
        reply_message=callback.message,
        trigger="按钮",
        actor_user=callback.from_user,
        service=COMMAND_SERVICE,
        history_detail_prefix=COMMAND_HISTORY_DETAIL_PREFIX,
        fsm_state=state,
    ):
        await callback.answer("请选择小程序目录")
        return
    await callback.answer("正在执行命令…")
    await _execute_command_definition(
        command=command,
        reply_message=callback.message,
        trigger="按钮",
        actor_user=callback.from_user,
        service=COMMAND_SERVICE,
        history_detail_prefix=COMMAND_HISTORY_DETAIL_PREFIX,
        fsm_state=state,
    )


@router.callback_query(F.data.startswith(COMMAND_EXEC_GLOBAL_PREFIX))
async def on_global_command_execute_callback(callback: CallbackQuery, state: FSMContext) -> None:
    """执行通用命令，入口由 master 配置。"""

    command_id = _extract_command_id(callback.data, COMMAND_EXEC_GLOBAL_PREFIX)
    if command_id is None:
        await callback.answer("命令标识无效", show_alert=True)
        return
    try:
        command = await GLOBAL_COMMAND_SERVICE.get_command(command_id)
    except CommandNotFoundError:
        await callback.answer("通用命令不存在", show_alert=True)
        await _refresh_command_overview(callback, notice="通用命令已被 master 移除。")
        return
    if await _maybe_handle_wx_preview(
        command=command,
        reply_message=callback.message,
        trigger="按钮",
        actor_user=callback.from_user,
        service=GLOBAL_COMMAND_SERVICE,
        history_detail_prefix=COMMAND_HISTORY_DETAIL_GLOBAL_PREFIX,
        fsm_state=state,
    ):
        await callback.answer("请选择小程序目录")
        return
    await callback.answer("正在执行通用命令…")
    await _execute_command_definition(
        command=command,
        reply_message=callback.message,
        trigger="按钮",
        actor_user=callback.from_user,
        service=GLOBAL_COMMAND_SERVICE,
        history_detail_prefix=COMMAND_HISTORY_DETAIL_GLOBAL_PREFIX,
        fsm_state=state,
    )


@router.callback_query(F.data.startswith(WX_PREVIEW_CHOICE_PREFIX))
async def on_wx_preview_choice(callback: CallbackQuery, state: FSMContext) -> None:
    """处理 wx-dev-preview 目录选择。"""

    data = await state.get_data()
    context = data.get("wx_preview") or {}
    raw_idx = (callback.data or "")[len(WX_PREVIEW_CHOICE_PREFIX) :]
    if not raw_idx.isdigit():
        await callback.answer("选择无效", show_alert=True)
        return
    idx = int(raw_idx)
    candidates_data = context.get("candidates") or []
    if idx < 0 or idx >= len(candidates_data):
        await callback.answer("候选不存在", show_alert=True)
        return

    command_id = context.get("command_id")
    scope = context.get("scope") or "project"
    service = GLOBAL_COMMAND_SERVICE if scope == GLOBAL_COMMAND_SCOPE else COMMAND_SERVICE
    history_prefix = context.get("history_prefix") or COMMAND_HISTORY_DETAIL_PREFIX
    trigger = context.get("trigger") or "按钮"
    command_name = (context.get("command_name") or "").strip() or WX_PREVIEW_COMMAND_NAME
    raw_env_overrides = context.get("env_overrides") or {}
    env_overrides = {
        str(key).strip(): str(value).strip()
        for key, value in raw_env_overrides.items()
        if str(key).strip() and str(value).strip()
    }

    try:
        command = await service.get_command(int(command_id))
    except (TypeError, ValueError, CommandNotFoundError):
        await state.clear()
        await callback.answer("命令不存在，请刷新后重试。", show_alert=True)
        return

    candidate_data = candidates_data[idx]
    project_root = Path(candidate_data.get("project_root", "")).expanduser()
    if not project_root.is_dir():
        await state.clear()
        await callback.answer("目录已不存在，请重新触发命令。", show_alert=True)
        if callback.message:
            await callback.message.answer(
                f"所选目录不存在，请重新执行 `{_escape_markdown_text(command_name)}`。",
                parse_mode=_parse_mode_value(),
            )
        return
    app_dir = _resolve_miniprogram_app_dir(project_root)
    if app_dir is None:
        await state.clear()
        await callback.answer("目录缺少有效 app.json，请重新选择。", show_alert=True)
        if callback.message:
            await callback.message.answer(
                f"目录 `{_escape_markdown_text(str(project_root))}` 缺少 app.json，已终止本次操作。",
                parse_mode=_parse_mode_value(),
            )
        return

    command_override = _wrap_wx_preview_command(command, project_root)
    command_override = _apply_command_env_overrides(command_override, env_overrides)
    await state.clear()
    if command_override.name == WX_UPLOAD_COMMAND_NAME:
        await callback.answer("开始执行上传…")
    else:
        await callback.answer("开始生成预览…")
    await _execute_command_definition(
        command=command_override,
        reply_message=callback.message,
        trigger=trigger,
        actor_user=callback.from_user,
        service=service,
        history_detail_prefix=history_prefix,
        fsm_state=state,
    )


@router.callback_query(F.data == WX_PREVIEW_CANCEL)
async def on_wx_preview_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    """取消 wx-dev-preview 交互。"""

    data = await state.get_data()
    command_name = ((data.get("wx_preview") or {}).get("command_name") or "").strip() or WX_PREVIEW_COMMAND_NAME
    await state.clear()
    if callback.message:
        await callback.message.answer(
            f"已取消 `{_escape_markdown_text(command_name)}` 执行。",
            parse_mode=_parse_mode_value(),
        )
    await callback.answer("已取消")


async def _apply_wx_preview_port_and_retry(
    *,
    port: int,
    state: FSMContext,
    reply_message: Message,
    actor_user: Optional[User],
) -> None:
    """保存端口映射并用指定端口重试微信开发命令。"""

    data = await state.get_data()
    context = data.get(WX_PREVIEW_PORT_STATE_KEY) or {}
    command_id = context.get("command_id")
    scope = context.get("scope") or "project"
    trigger = context.get("trigger") or "按钮"
    command_name = (context.get("command_name") or "").strip() or WX_PREVIEW_COMMAND_NAME
    project_root_raw = (context.get("project_root") or "").strip()
    raw_env_overrides = context.get("env_overrides") or {}
    env_overrides = {
        str(key).strip(): str(value).strip()
        for key, value in raw_env_overrides.items()
        if str(key).strip() and str(value).strip()
    }
    if not project_root_raw:
        await state.clear()
        await reply_message.answer(
            f"上下文已失效，请重新执行 `{_escape_markdown_text(command_name)}`。",
            parse_mode=_parse_mode_value(),
        )
        return

    project_root = Path(project_root_raw).expanduser()
    if not project_root.is_dir():
        await state.clear()
        await reply_message.answer(
            f"所选目录已不存在，请重新执行 `{_escape_markdown_text(command_name)}`。",
            parse_mode=_parse_mode_value(),
        )
        return

    if not (1 <= port <= 65535):
        await reply_message.answer("端口号无效，请发送 1-65535 的数字；发送“取消”可终止。")
        return

    service = GLOBAL_COMMAND_SERVICE if scope == GLOBAL_COMMAND_SCOPE else COMMAND_SERVICE
    history_prefix = (
        COMMAND_HISTORY_DETAIL_GLOBAL_PREFIX
        if scope == GLOBAL_COMMAND_SCOPE
        else COMMAND_HISTORY_DETAIL_PREFIX
    )
    try:
        command = await service.get_command(int(command_id))
    except (TypeError, ValueError, CommandNotFoundError):
        await state.clear()
        await reply_message.answer("命令不存在，请刷新后重试。")
        return

    ports_file = CONFIG_DIR_PATH / "wx_devtools_ports.json"
    project_slug_key = PROJECT_NAME or PROJECT_SLUG
    config_note = ""
    try:
        _upsert_wx_devtools_ports_file(
            ports_file=ports_file,
            project_slug=project_slug_key,
            project_root=project_root,
            port=port,
        )
        config_note = f"已写入端口配置：`{_escape_markdown_text(str(ports_file))}`"
    except OSError as exc:
        worker_log.warning(
            "写入 wx_devtools_ports.json 失败：%s",
            exc,
            extra=_session_extra(key="wx_preview_port_write_failed"),
        )
        config_note = "端口配置写入失败，将仅本次使用该端口重试。"

    command_override = _wrap_wx_preview_command(command, project_root)
    command_override = _apply_command_env_overrides(command_override, env_overrides)
    command_retry = CommandDefinition(
        id=command_override.id,
        project_slug=command_override.project_slug,
        name=command_override.name,
        title=command_override.title,
        command=f"PORT={port} {command_override.command}",
        scope=command_override.scope,
        description=command_override.description,
        timeout=command_override.timeout,
        enabled=command_override.enabled,
        created_at=command_override.created_at,
        updated_at=command_override.updated_at,
        aliases=command_override.aliases,
    )

    await state.clear()
    await _answer_with_markdown(
        reply_message,
        "\n".join(
            [
                f"已收到端口：`{port}`",
                config_note,
                ("开始重试上传…" if command_retry.name == WX_UPLOAD_COMMAND_NAME else "开始重试生成预览…"),
            ]
        ),
    )
    await _execute_command_definition(
        command=command_retry,
        reply_message=reply_message,
        trigger=trigger,
        actor_user=actor_user,
        service=service,
        history_detail_prefix=history_prefix,
        fsm_state=state,
    )


@router.callback_query(F.data.startswith(WX_PREVIEW_PORT_USE_PREFIX))
async def on_wx_preview_port_use(callback: CallbackQuery, state: FSMContext) -> None:
    """处理 wx-dev-preview 端口快捷选择。"""

    raw_port = (callback.data or "")[len(WX_PREVIEW_PORT_USE_PREFIX) :].strip()
    port = _parse_numeric_port(raw_port)
    if port is None:
        await callback.answer("端口无效", show_alert=True)
        return
    if callback.message is None:
        await callback.answer("无法定位原消息", show_alert=True)
        return
    await callback.answer(f"使用端口 {port} 重试…")
    await _apply_wx_preview_port_and_retry(
        port=port,
        state=state,
        reply_message=callback.message,
        actor_user=callback.from_user,
    )


@router.callback_query(F.data == WX_PREVIEW_PORT_CANCEL)
async def on_wx_preview_port_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    """取消 wx-dev-preview 端口输入流程。"""

    data = await state.get_data()
    context = data.get(WX_PREVIEW_PORT_STATE_KEY) or {}
    command_name = (context.get("command_name") or "").strip() or WX_PREVIEW_COMMAND_NAME
    await state.clear()
    if callback.message:
        await callback.message.answer(
            f"已取消端口输入，可重新执行 `{_escape_markdown_text(command_name)}`。",
            parse_mode=_parse_mode_value(),
        )
    await callback.answer("已取消")


@router.message(WxPreviewStates.waiting_port)
async def on_wx_preview_port_input(message: Message, state: FSMContext) -> None:
    """处理 wx-dev-preview 端口手动输入。"""

    text = (message.text or "").strip()
    if _is_cancel_text(text):
        await state.clear()
        await message.answer("已取消端口输入。", reply_markup=_build_worker_main_keyboard())
        return
    port = _parse_numeric_port(text)
    if port is None:
        await message.answer("端口号无效，请仅发送 1-65535 的数字；发送“取消”可终止。")
        return
    await _apply_wx_preview_port_and_retry(
        port=port,
        state=state,
        reply_message=message,
        actor_user=message.from_user,
    )


@router.callback_query(F.data == COMMAND_READONLY_CALLBACK)
async def on_command_readonly_callback(callback: CallbackQuery) -> None:
    """提示通用命令只读。"""

    await callback.answer("该命令由 master 统一配置，项目内不可编辑。", show_alert=True)


@router.callback_query(F.data.startswith(COMMAND_EDIT_PREFIX))
async def on_command_edit_callback(callback: CallbackQuery, state: FSMContext) -> None:
    command_id = _extract_command_id(callback.data, COMMAND_EDIT_PREFIX)
    if command_id is None:
        await callback.answer("命令标识无效", show_alert=True)
        return
    try:
        command = await COMMAND_SERVICE.get_command(command_id)
    except CommandNotFoundError:
        await callback.answer("命令不存在", show_alert=True)
        await _refresh_command_overview(callback, notice="命令已不存在。")
        return
    if _is_global_command(command):
        await callback.answer("该命令为通用命令，请到 master 通用命令配置中维护。", show_alert=True)
        return
    await state.update_data(command_id=command_id)
    await state.set_state(CommandEditStates.waiting_choice)
    if callback.message:
        await callback.message.answer(
            f"正在编辑 `{_escape_markdown_text(command.name)}`，请选择要修改的内容：",
            reply_markup=_build_command_edit_keyboard(command),
        )
    await callback.answer("请选择操作")


@router.callback_query(F.data.startswith(COMMAND_FIELD_PREFIX))
async def on_command_field_select(callback: CallbackQuery, state: FSMContext) -> None:
    data = (callback.data or "")[len(COMMAND_FIELD_PREFIX) :]
    field, _, raw_id = data.partition(":")
    if not raw_id.isdigit():
        await callback.answer("字段标识无效", show_alert=True)
        return
    command_id = int(raw_id)
    try:
        command = await COMMAND_SERVICE.get_command(command_id)
    except CommandNotFoundError:
        await callback.answer("命令不存在", show_alert=True)
        await _refresh_command_overview(callback, notice="命令已不存在。")
        return
    if _is_global_command(command):
        await callback.answer("该命令由 master 统一配置，项目内不可编辑。", show_alert=True)
        await _refresh_command_overview(callback)
        return
    prompt_text = build_field_prompt_text(command, field)
    if prompt_text is None:
        await callback.answer("暂不支持该字段", show_alert=True)
        return
    await state.update_data(command_id=command_id, field=field)
    if field == "aliases":
        await state.set_state(CommandEditStates.waiting_aliases)
    else:
        await state.set_state(CommandEditStates.waiting_value)
    if callback.message:
        await callback.message.answer(
            prompt_text,
            reply_markup=_build_command_edit_cancel_keyboard(),
        )
    await callback.answer("请发送新的值")


@router.callback_query(F.data.startswith(COMMAND_TOGGLE_PREFIX))
async def on_command_toggle(callback: CallbackQuery) -> None:
    command_id = _extract_command_id(callback.data, COMMAND_TOGGLE_PREFIX)
    if command_id is None:
        await callback.answer("命令标识无效", show_alert=True)
        return
    try:
        command = await COMMAND_SERVICE.get_command(command_id)
    except CommandNotFoundError:
        await callback.answer("命令不存在", show_alert=True)
        await _refresh_command_overview(callback, notice="命令已不存在。")
        return
    if _is_global_command(command):
        await callback.answer("该命令由 master 维护，项目内不可停用。", show_alert=True)
        return
    updated = await COMMAND_SERVICE.update_command(command_id, enabled=not command.enabled)
    action_text = "已启用" if updated.enabled else "已停用"
    await _refresh_command_overview(callback, notice=f"{updated.name} {action_text}")
    await callback.answer(action_text)


@router.message(CommandCreateStates.waiting_name)
async def on_command_create_name(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if _is_cancel_text(text):
        await state.clear()
        await message.answer("命令创建已取消。", reply_markup=_build_worker_main_keyboard())
        return
    if not CommandService.NAME_PATTERN.match(text):
        await message.answer("名称需以字母开头，可含数字/下划线/短横线，长度 3-64，请重新输入：")
        return
    existing = await COMMAND_SERVICE.resolve_by_trigger(text)
    if existing:
        await message.answer("同名命令或别名已存在，请换一个名称：")
        return
    global_existing = await _resolve_global_command_conflict(text)
    if global_existing:
        await message.answer("该名称已被通用命令占用，请换一个名称：")
        return
    await state.update_data(name=text)
    await state.set_state(CommandCreateStates.waiting_shell)
    await message.answer("请输入需要执行的命令，例如 `./scripts/deploy.sh`：")


@router.message(CommandCreateStates.waiting_shell)
async def on_command_create_shell(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if _is_cancel_text(text):
        await state.clear()
        await message.answer("命令创建已取消。", reply_markup=_build_worker_main_keyboard())
        return
    if not text:
        await message.answer("命令内容不能为空，请重新输入：")
        return
    data = await state.get_data()
    name = data.get("name")
    if not name:
        await state.clear()
        await message.answer("上下文已失效，请重新点击“🆕 新增命令”。")
        return
    title = name
    description = ""
    aliases: tuple[str, ...] = ()
    try:
        created = await COMMAND_SERVICE.create_command(
            name=name,
            title=title,
            command=text,
            description=description,
            aliases=aliases,
        )
    except (ValueError, CommandAlreadyExistsError, CommandAliasConflictError) as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    await message.answer(
        (
            f"命令 `{_escape_markdown_text(created.name)}` 已创建，"
            "标题默认沿用名称，描述与别名可在编辑面板中补齐。"
        ),
        reply_markup=_build_worker_main_keyboard(),
    )
    await _send_command_overview(message)


@router.message(CommandEditStates.waiting_value)
async def on_command_edit_value(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if _is_cancel_text(text):
        await state.clear()
        await message.answer("命令编辑已取消。", reply_markup=_build_worker_main_keyboard())
        return
    data = await state.get_data()
    command_id = data.get("command_id")
    field = data.get("field")
    if not command_id or not field:
        await state.clear()
        await message.answer("上下文已失效，请重新选择命令。")
        return
    updates: dict[str, object] = {}
    if field == "title":
        updates["title"] = text
    elif field == "command":
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
        updated = await COMMAND_SERVICE.update_command(command_id, **updates)
    except (ValueError, CommandAlreadyExistsError, CommandNotFoundError) as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    await message.answer(
        f"命令 `{_escape_markdown_text(updated.name)}` 已更新。",
        reply_markup=_build_worker_main_keyboard(),
    )
    await _send_command_overview(message)


@router.message(CommandEditStates.waiting_aliases)
async def on_command_edit_aliases(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if _is_cancel_text(text):
        await state.clear()
        await message.answer("命令编辑已取消。", reply_markup=_build_worker_main_keyboard())
        return
    data = await state.get_data()
    command_id = data.get("command_id")
    if not command_id:
        await state.clear()
        await message.answer("上下文已失效，请重新选择命令。")
        return
    aliases = _parse_alias_input(text)
    for alias in aliases:
        conflict = await _resolve_global_command_conflict(alias)
        if conflict is not None:
            await message.answer(f"别名 {alias} 已被通用命令占用，请重新输入：")
            return
    try:
        updated_aliases = await COMMAND_SERVICE.replace_aliases(command_id, aliases)
    except (ValueError, CommandAliasConflictError, CommandNotFoundError) as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    alias_label = _command_alias_label(updated_aliases)
    await message.answer(
        f"别名已更新：{alias_label}",
        reply_markup=_build_worker_main_keyboard(),
    )
    await _send_command_overview(message)


async def _dispatch_task_new_command(source_message: Message, actor: Optional[User]) -> None:
    """模拟用户输入 /task_new，让现有命令逻辑复用。"""
    if actor is None:
        raise ValueError("缺少有效的任务创建用户信息")
    bot_instance = current_bot()
    command_text = "/task_new"
    try:
        now = datetime.now(tz=ZoneInfo("UTC"))
    except ZoneInfoNotFoundError:
        now = datetime.now(UTC)
    entities = [
        MessageEntity(type="bot_command", offset=0, length=len(command_text)),
    ]
    synthetic_message_id = _build_internal_synthetic_message_id(source_message.message_id)
    synthetic_message = source_message.model_copy(
        update={
            "message_id": synthetic_message_id,
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
    _mark_text_paste_synthetic_message(int(source_message.chat.id), synthetic_message_id)
    await dp.feed_update(bot_instance, update)


@router.message(F.text == WORKER_CREATE_TASK_BUTTON_TEXT)
async def on_task_create_button(message: Message, state: FSMContext) -> None:
    await state.clear()
    try:
        await _dispatch_task_new_command(message, message.from_user)
    except ValueError:
        await message.answer("无法发起任务创建，请重试或使用 /task_new 命令。")


@router.callback_query(F.data.startswith("task:list_page:"))
async def on_task_list_page(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 5:
        await callback.answer("回调数据异常", show_alert=True)
        return
    _, _, status_token, page_raw, limit_raw = parts
    if callback.message is None:
        await callback.answer("无法定位原始消息", show_alert=True)
        return
    status = None if status_token == "-" else _normalize_status(status_token)
    try:
        page = int(page_raw)
        limit = int(limit_raw)
    except ValueError:
        await callback.answer("分页参数错误", show_alert=True)
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
        await callback.answer("回调数据异常", show_alert=True)
        return
    _, _, status_token, page_raw, limit_raw = parts
    status = None if status_token == "-" else _normalize_status(status_token)
    try:
        page = max(int(page_raw), 1)
        limit = max(1, min(int(limit_raw), 50))
    except ValueError:
        await callback.answer("参数错误", show_alert=True)
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
    await callback.answer("请输入搜索关键词")
    if callback.message:
        await _prompt_task_search_keyword(callback.message)


@router.callback_query(F.data.startswith(f"{TASK_LIST_SEARCH_PAGE_CALLBACK}:"))
async def on_task_list_search_page(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 7:
        await callback.answer("回调数据异常", show_alert=True)
        return
    _, _, encoded_keyword, origin_status_token, origin_page_raw, target_page_raw, limit_raw = parts
    if callback.message is None:
        await callback.answer("无法定位原始消息", show_alert=True)
        return
    keyword = unquote(encoded_keyword)
    origin_status = None if origin_status_token == "-" else _normalize_status(origin_status_token)
    try:
        origin_page = max(int(origin_page_raw), 1)
        page = max(int(target_page_raw), 1)
        limit = max(1, min(int(limit_raw), 50))
    except ValueError:
        await callback.answer("参数错误", show_alert=True)
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
        await callback.answer("回调数据异常", show_alert=True)
        return
    _, _, status_token, page_raw, limit_raw = parts
    if callback.message is None:
        await callback.answer("无法定位原始消息", show_alert=True)
        return
    status = None if status_token == "-" else _normalize_status(status_token)
    try:
        page = max(int(page_raw), 1)
        limit = max(1, min(int(limit_raw), 50))
    except ValueError:
        await callback.answer("参数错误", show_alert=True)
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
    await callback.answer("已返回任务列表")


@router.callback_query(F.data == TASK_LIST_CREATE_CALLBACK)
async def on_task_list_create(callback: CallbackQuery) -> None:
    message = callback.message
    user = callback.from_user
    if message is None or user is None:
        await callback.answer("无法定位会话", show_alert=True)
        return
    await callback.answer()
    await _dispatch_task_new_command(message, user)


@router.message(TaskListSearchStates.waiting_keyword)
async def on_task_list_search_keyword(message: Message, state: FSMContext) -> None:
    raw_text = message.text or ""
    trimmed = raw_text.strip()
    options = [SKIP_TEXT, "取消"]
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

    if resolved == "取消" or resolved == SKIP_TEXT or not trimmed:
        await state.clear()
        await _restore_list()
        await message.answer("已返回任务列表。", reply_markup=_build_worker_main_keyboard())
        return

    if len(trimmed) < SEARCH_KEYWORD_MIN_LENGTH:
        await message.answer(
            f"关键词长度至少 {SEARCH_KEYWORD_MIN_LENGTH} 个字符，请重新输入：",
            reply_markup=_build_description_keyboard(),
        )
        return
    if len(trimmed) > SEARCH_KEYWORD_MAX_LENGTH:
        await message.answer(
            f"关键词长度不可超过 {SEARCH_KEYWORD_MAX_LENGTH} 个字符，请重新输入：",
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
    await message.answer("搜索完成，已展示结果。", reply_markup=_build_worker_main_keyboard())


@router.message(Command("task_show"))
async def on_task_show(message: Message) -> None:
    args = _extract_command_args(message.text)
    if not args:
        await _answer_with_markdown(message, "用法：/task_show TASK_0001")
        return
    task_id = _normalize_task_id(args)
    if not task_id:
        await _answer_with_markdown(message, TASK_ID_USAGE_TIP)
        return
    await _reply_task_detail_message(message, task_id)


@router.message(F.text.regexp(r"^/TASK_[A-Z0-9_]+(?:@[\w_]+)?(?:\s|$)"))
async def on_task_quick_command(message: Message) -> None:
    """处理直接使用 /TASK_XXXX 调用的快捷查询命令。"""
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
        "子任务功能已下线，历史子任务已自动归档。请使用 /task_new 创建独立任务以拆分工作。",
    )


def _prepare_task_new_command_description(
    message: Message,
    *,
    task_type: Optional[str],
    extra: Mapping[str, str],
) -> tuple[Optional[str], list[Mapping[str, str]]]:
    """解析 `/task_new ...` 的结构化/兼容描述参数。"""

    pending_attachments: list[Mapping[str, str]] = []

    def _normalize_command_field(raw_value: Optional[str], label: str) -> Optional[str]:
        normalized = (raw_value or "").strip()
        if not normalized:
            return ""
        if len(normalized) > DESCRIPTION_MAX_LENGTH:
            attachment = _persist_text_paste_as_attachment(message, normalized)
            pending_attachments.append(_serialize_saved_attachment(attachment))
            return _build_overlong_text_placeholder(label)
        return normalized

    normalized_task_type = _normalize_task_type(task_type)
    if normalized_task_type == "defect":
        reproduction = extra.get("reproduction")
        expected_result = extra.get("expected_result")
        if reproduction is not None or expected_result is not None:
            return (
                _build_defect_description(
                    _normalize_command_field(reproduction, DEFECT_REPRODUCTION_LABEL),
                    _normalize_command_field(expected_result, DEFECT_EXPECTED_RESULT_LABEL),
                ),
                pending_attachments,
            )
    if normalized_task_type == "task":
        current_effect = extra.get("current_effect")
        expected_effect = extra.get("expected_effect")
        if current_effect is not None or expected_effect is not None:
            return (
                _build_optimize_description(
                    _normalize_command_field(current_effect, OPTIMIZE_CURRENT_EFFECT_LABEL),
                    _normalize_command_field(expected_effect, OPTIMIZE_EXPECTED_EFFECT_LABEL),
                ),
                pending_attachments,
            )

    description = extra.get("description")
    if description is not None:
        description = _normalize_command_field(description, "任务描述")
    return description, pending_attachments


@router.message(Command("task_new"))
async def on_task_new(message: Message, state: FSMContext) -> None:
    args = _extract_command_args(message.text)
    if args:
        title, extra = parse_structured_text(args)
        title = title.strip()
        if not title:
            await _answer_with_markdown(message, "请提供任务标题，例如：/task_new 修复登录 | type=需求")
            return
        if "priority" in extra:
            await _answer_with_markdown(message, "priority 参数已取消，请直接使用 /task_new 标题 | type=需求")
            return
        status = _normalize_status(extra.get("status")) or TASK_STATUSES[0]
        task_type = _normalize_task_type(extra.get("type"))
        if task_type is None:
            await _answer_with_markdown(
                message,
                "任务类型缺失或无效，请使用 type=需求/缺陷/优化/风险",
            )
            return
        related_task_id: Optional[str] = None
        if task_type == "defect":
            related_raw = (extra.get("related") or extra.get("rel") or "").strip()
            if related_raw:
                normalized_related = _normalize_task_id(related_raw)
                if not normalized_related:
                    await _answer_with_markdown(
                        message,
                        "关联任务 ID 无效，请使用 related=TASK_0001（或 rel=TASK_0001）",
                    )
                    return
                related_task = await TASK_SERVICE.get_task(normalized_related)
                if related_task is None:
                    await _answer_with_markdown(
                        message,
                        f"关联任务 {normalized_related} 不存在，请检查任务编号或改用 FSM 流程选择。",
                    )
                    return
                related_task_id = normalized_related
        description, pending_attachments = _prepare_task_new_command_description(
            message,
            task_type=task_type,
            extra=extra,
        )
        actor = _actor_from_message(message)
        task = await TASK_SERVICE.create_root_task(
            title=title,
            status=status,
            priority=DEFAULT_PRIORITY,
            task_type=task_type,
            tags=(),
            due_date=None,
            description=description,
            related_task_id=related_task_id,
            actor=actor,
        )
        if pending_attachments:
            await _bind_serialized_attachments(task, pending_attachments, actor=actor)
        detail_text, markup = await _render_task_detail(task.id)
        await _answer_with_markdown(message, f"任务已创建：\n{detail_text}", reply_markup=markup)
        return

    await state.clear()
    await state.update_data(
        actor=_actor_from_message(message),
        priority=DEFAULT_PRIORITY,
    )
    await state.set_state(TaskCreateStates.waiting_title)
    await message.answer("请输入任务标题：")


@router.message(TaskCreateStates.waiting_title)
async def on_task_create_title(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if not title:
        await message.answer("标题不能为空，请重新输入：")
        return
    await state.update_data(title=title)
    await state.set_state(TaskCreateStates.waiting_type)
    await message.answer(
        "请选择任务类型（需求 / 缺陷 / 优化 / 风险）：",
        reply_markup=_build_task_type_keyboard(),
    )


@router.message(TaskCreateStates.waiting_type)
async def on_task_create_type(message: Message, state: FSMContext) -> None:
    options = [_format_task_type(task_type) for task_type in TASK_TYPES]
    options.append("取消")
    resolved = _resolve_reply_choice(message.text, options=options)
    candidate = resolved or (message.text or "").strip()
    if resolved == "取消" or candidate == "取消":
        await state.clear()
        await message.answer("已取消创建任务。", reply_markup=_build_worker_main_keyboard())
        return
    task_type = _normalize_task_type(candidate)
    if task_type is None:
        await message.answer(
            "任务类型无效，请从键盘选择或输入需求/缺陷/优化/风险：",
            reply_markup=_build_task_type_keyboard(),
        )
        return
    await state.update_data(task_type=task_type)
    if task_type == "defect":
        await state.update_data(
            related_task_id=None,
            related_page=1,
        )
        await state.set_state(TaskCreateStates.waiting_related_task)
        # 该阶段任务列表使用 InlineKeyboard（选择/翻页）；跳过/取消放入菜单栏保持与后续流程一致。
        await message.answer(
            "请选择关联前置任务，可输入 1 跳过、2 取消创建任务（或在菜单栏点击对应按钮）。",
            reply_markup=_build_related_task_action_keyboard(),
        )
        text, markup = await _build_related_task_select_view(page=1)
        await _answer_with_markdown(message, text, reply_markup=markup)
        return
    if task_type == "task":
        await state.update_data(processed_media_groups=[])
        await state.set_state(TaskCreateStates.waiting_current_effect)
        await message.answer(
            (
                "请输入当前效果（可选），建议描述当前使用体验或存在的问题，支持直接发送图片/文件作为附件。\n"
                "若暂时没有可点击“跳过”按钮或直接发送空消息，发送“取消”可终止。"
            ),
            reply_markup=_build_description_keyboard(),
        )
        return
    await state.update_data(processed_media_groups=[])
    await state.set_state(TaskCreateStates.waiting_description)
    await message.answer(
        (
            "请输入任务描述，建议说明业务背景与预期结果，支持直接发送图片/文件作为附件。\n"
            "若暂时没有可点击“跳过”按钮或直接发送空消息，发送“取消”可终止。"
        ),
        reply_markup=_build_description_keyboard(),
    )


async def _build_related_task_select_view(*, page: int) -> tuple[str, InlineKeyboardMarkup]:
    """构建“选择关联任务”分页视图（最近更新优先）。"""

    limit = TASK_RELATED_PAGE_SIZE
    total = await TASK_SERVICE.count_tasks(status=None, include_archived=False)
    total_pages = max((total + limit - 1) // limit, 1)
    normalized_page = max(1, min(int(page or 1), total_pages))
    offset = (normalized_page - 1) * limit
    tasks = await TASK_SERVICE.list_recent_tasks(limit=limit, offset=offset, include_archived=False)

    lines = [
        "请选择关联前置任务（按更新时间倒序）：",
        f"页码 {normalized_page}/{total_pages} · 每页 {limit} 条 · 总数 {total}",
        "可点击按钮选择，或直接输入 TASK_0001（也支持 /TASK_0001）；也可输入 1 跳过、2 取消创建任务（或在菜单栏点击）。",
    ]
    if not tasks:
        lines.append("当前没有可选任务，可输入 1 跳过继续创建缺陷任务（或在菜单栏点击“跳过”）。")

    rows: list[list[InlineKeyboardButton]] = []
    for task in tasks:
        label = _compose_task_button_label(task)
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"{TASK_RELATED_SELECT_PREFIX}:{task.id}",
                )
            ]
        )

    nav_row: list[InlineKeyboardButton] = []
    if normalized_page > 1:
        nav_row.append(
            InlineKeyboardButton(
                text="⬅️ 上一页",
                callback_data=f"{TASK_RELATED_PAGE_PREFIX}:{normalized_page - 1}",
            )
        )
    if normalized_page < total_pages:
        nav_row.append(
            InlineKeyboardButton(
                text="下一页 ➡️",
                callback_data=f"{TASK_RELATED_PAGE_PREFIX}:{normalized_page + 1}",
            )
        )
    if nav_row:
        rows.append(nav_row)

    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def _advance_task_create_to_description(message: Message, state: FSMContext) -> None:
    """从“关联任务选择”推进到下一输入阶段。"""

    data = await state.get_data()
    task_type = _normalize_task_type(data.get("task_type"))
    await state.update_data(processed_media_groups=[])
    if task_type == "defect":
        await state.set_state(TaskCreateStates.waiting_reproduction)
        await message.answer(
            "请输入复现步骤（可选），可直接发送图片/文件作为附件；若暂无可发送“跳过”继续（仅发送附件也会进入下一步）：",
            reply_markup=_build_description_keyboard(),
        )
        return
    await state.set_state(TaskCreateStates.waiting_description)
    await message.answer(
        (
            "请输入任务描述，建议说明业务背景与预期结果，支持直接发送图片/文件作为附件。\n"
            "若暂时没有可点击“跳过”按钮或直接发送空消息，发送“取消”可终止。"
        ),
        reply_markup=_build_description_keyboard(),
    )


def _format_pending_attachments_for_create_summary(
    pending_attachments: Sequence[Mapping[str, str]],
) -> list[str]:
    """将创建流程中暂存的附件列表格式化为确认摘要文本行（中文）。"""

    if not pending_attachments:
        return ["附件列表：-"]

    # 与 _bind_serialized_attachments() 的行为保持一致：按 path 去重，避免媒体组/重放导致重复展示。
    seen_paths: set[str] = set()
    ordered: list[tuple[str, str, str]] = []
    for item in pending_attachments:
        display_name = (item.get("display_name") or "attachment").strip() or "attachment"
        mime_type = (item.get("mime_type") or "application/octet-stream").strip() or "application/octet-stream"
        path = (item.get("path") or "").strip() or "-"
        if path != "-" and path in seen_paths:
            continue
        if path != "-":
            seen_paths.add(path)
        ordered.append((display_name, mime_type, path))

    lines = ["附件列表："]
    limit = TASK_ATTACHMENT_PREVIEW_LIMIT
    for idx, (display_name, mime_type, path) in enumerate(ordered[:limit], 1):
        lines.append(f"{idx}. {display_name}（{mime_type}）→ {path}")
    if len(ordered) > limit:
        lines.append(f"… 其余 {len(ordered) - limit} 个附件未展开（共 {len(ordered)} 个）")
    return lines


@router.callback_query(F.data.startswith(f"{TASK_RELATED_PAGE_PREFIX}:"))
async def on_task_create_related_page(callback: CallbackQuery, state: FSMContext) -> None:
    """缺陷任务创建：翻页选择关联任务。"""

    if callback.message is None:
        await callback.answer("无法定位消息", show_alert=True)
        return
    current_state = await state.get_state()
    if current_state != TaskCreateStates.waiting_related_task.state:
        await callback.answer("当前不在选择关联任务阶段", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    if len(parts) != 3:
        await callback.answer("回调参数错误", show_alert=True)
        return
    try:
        page = int(parts[2])
    except ValueError:
        await callback.answer("页码参数错误", show_alert=True)
        return
    await state.update_data(related_page=page)
    text, markup = await _build_related_task_select_view(page=page)
    if not await _try_edit_message(callback.message, text, reply_markup=markup):
        await _answer_with_markdown(callback.message, text, reply_markup=markup)
    await callback.answer()


@router.callback_query(F.data.startswith(f"{TASK_RELATED_SELECT_PREFIX}:"))
async def on_task_create_related_select(callback: CallbackQuery, state: FSMContext) -> None:
    """缺陷任务创建：选择关联任务。"""

    if callback.message is None:
        await callback.answer("无法定位消息", show_alert=True)
        return
    current_state = await state.get_state()
    if current_state != TaskCreateStates.waiting_related_task.state:
        await callback.answer("当前不在选择关联任务阶段", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    if len(parts) != 3:
        await callback.answer("回调参数错误", show_alert=True)
        return
    task_id = parts[2]
    task = await TASK_SERVICE.get_task(task_id)
    if task is None:
        await callback.answer("任务不存在或已删除", show_alert=True)
        return
    await state.update_data(related_task_id=task.id)
    await callback.answer("已选择关联任务")
    await callback.message.answer(f"已选择关联任务：/{task.id} {task.title}")
    await _advance_task_create_to_description(callback.message, state)


@router.callback_query(F.data == TASK_RELATED_SKIP_CALLBACK)
async def on_task_create_related_skip(callback: CallbackQuery, state: FSMContext) -> None:
    """缺陷任务创建：跳过关联任务选择。"""

    if callback.message is None:
        await callback.answer("无法定位消息", show_alert=True)
        return
    current_state = await state.get_state()
    if current_state != TaskCreateStates.waiting_related_task.state:
        await callback.answer("当前不在选择关联任务阶段", show_alert=True)
        return
    await state.update_data(related_task_id=None)
    await callback.answer("已跳过")
    await callback.message.answer("已跳过关联任务选择。")
    await _advance_task_create_to_description(callback.message, state)


@router.callback_query(F.data == TASK_RELATED_CANCEL_CALLBACK)
async def on_task_create_related_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    """缺陷任务创建：取消。"""

    await state.clear()
    await callback.answer("已取消创建任务", show_alert=False)
    if callback.message:
        await callback.message.answer("已取消创建任务。", reply_markup=_build_worker_main_keyboard())


@router.message(TaskCreateStates.waiting_related_task)
async def on_task_create_related_task_text(message: Message, state: FSMContext) -> None:
    """缺陷任务创建：处理用户手动输入关联任务编号 / 跳过 / 取消。"""

    # 重要：该阶段菜单栏按钮会带数字前缀（例如 1. 跳过 / 2. 取消创建任务），且用户也可能直接输入 1/2。
    # 使用 _resolve_reply_choice() 统一解析，避免把 “1” 误判为任务编号无效。
    raw_text = message.text or ""
    action_options = [SKIP_TEXT, "取消创建任务"]
    resolved = _resolve_reply_choice(raw_text, options=action_options)
    token = _normalize_choice_token(resolved or raw_text)
    if _is_cancel_message(token):
        await state.clear()
        await message.answer("已取消创建任务。", reply_markup=_build_worker_main_keyboard())
        return
    if _is_skip_message(token):
        await state.update_data(related_task_id=None)
        await message.answer("已跳过关联任务选择。")
        await _advance_task_create_to_description(message, state)
        return
    normalized_task_id = _normalize_task_id(token)
    if not normalized_task_id:
        data = await state.get_data()
        page = int(data.get("related_page", 1) or 1)
        text, markup = await _build_related_task_select_view(page=page)
        await message.answer("任务编号无效，请点击按钮选择或输入 TASK_0001；也可输入 1 跳过、2 取消创建任务。")
        await _answer_with_markdown(message, text, reply_markup=markup)
        return
    task = await TASK_SERVICE.get_task(normalized_task_id)
    if task is None:
        data = await state.get_data()
        page = int(data.get("related_page", 1) or 1)
        text, markup = await _build_related_task_select_view(page=page)
        await message.answer("关联任务不存在，请重新选择或输入正确的任务编号。")
        await _answer_with_markdown(message, text, reply_markup=markup)
        return
    await state.update_data(related_task_id=task.id)
    await message.answer(f"已选择关联任务：/{task.id} {task.title}")
    await _advance_task_create_to_description(message, state)


async def _handle_task_create_first_structured_field(
    message: Message,
    state: FSMContext,
    *,
    field_key: str,
    field_label: str,
    next_state: State,
    next_prompt: str,
    reprompt_text: str,
) -> None:
    """处理 `/task_new` 结构化任务的第一个字段录入。"""

    data = await state.get_data()
    attachment_dir = _attachment_dir_for_message(message)
    processed_groups = set(data.get("processed_media_groups") or [])
    saved_attachments, text_part, processed_groups = await _collect_generic_media_group(
        message,
        attachment_dir,
        processed=processed_groups,
    )
    if message.media_group_id and not saved_attachments and not text_part:
        return
    if message.media_group_id:
        await state.update_data(processed_media_groups=list(processed_groups))
    if saved_attachments:
        pending = list(data.get("pending_attachments") or [])
        pending.extend(_serialize_saved_attachment(item) for item in saved_attachments)
        await state.update_data(pending_attachments=pending)
    raw_text = (text_part or "").strip() or (message.text or "").strip() or (message.caption or "").strip()
    trimmed = raw_text.strip()
    options = [SKIP_TEXT, "取消"]
    resolved = _resolve_reply_choice(trimmed, options=options)
    if resolved == "取消" or _is_cancel_message(resolved):
        await state.clear()
        await message.answer("已取消创建任务。", reply_markup=_build_worker_main_keyboard())
        return
    is_skip = resolved == SKIP_TEXT or _is_skip_message(resolved)
    if is_skip:
        trimmed = ""
    if not trimmed and not is_skip and not saved_attachments:
        await message.answer(reprompt_text, reply_markup=_build_description_keyboard())
        return
    value = trimmed
    if len(value) > DESCRIPTION_MAX_LENGTH:
        attachment = _persist_text_paste_as_attachment(message, value)
        pending = list(data.get("pending_attachments") or [])
        pending.append(_serialize_saved_attachment(attachment))
        await state.update_data(pending_attachments=pending)
        value = _build_overlong_text_placeholder(field_label)
    await state.update_data(**{field_key: value})
    await state.set_state(next_state)
    await message.answer(next_prompt, reply_markup=_build_description_keyboard())


async def _handle_task_create_second_structured_field(
    message: Message,
    state: FSMContext,
    *,
    task_type: str,
    first_key: str,
    second_key: str,
    second_label: str,
    reprompt_text: str,
) -> None:
    """处理 `/task_new` 结构化任务的第二个字段录入，并生成确认摘要。"""

    data = await state.get_data()
    attachment_dir = _attachment_dir_for_message(message)
    processed_groups = set(data.get("processed_media_groups") or [])
    saved_attachments, text_part, processed_groups = await _collect_generic_media_group(
        message,
        attachment_dir,
        processed=processed_groups,
    )
    if message.media_group_id and not saved_attachments and not text_part:
        return
    if message.media_group_id:
        await state.update_data(processed_media_groups=list(processed_groups))
    if saved_attachments:
        pending = list(data.get("pending_attachments") or [])
        pending.extend(_serialize_saved_attachment(item) for item in saved_attachments)
        await state.update_data(pending_attachments=pending)
    raw_text = (text_part or "").strip() or (message.text or "").strip() or (message.caption or "").strip()
    trimmed = raw_text.strip()
    options = [SKIP_TEXT, "取消"]
    resolved = _resolve_reply_choice(trimmed, options=options)
    if resolved == "取消" or _is_cancel_message(resolved):
        await state.clear()
        await message.answer("已取消创建任务。", reply_markup=_build_worker_main_keyboard())
        return
    is_skip = resolved == SKIP_TEXT or _is_skip_message(resolved)
    if is_skip:
        trimmed = ""
    if not trimmed and not is_skip and not saved_attachments:
        await message.answer(reprompt_text, reply_markup=_build_description_keyboard())
        return
    second_value = trimmed
    if len(second_value) > DESCRIPTION_MAX_LENGTH:
        attachment = _persist_text_paste_as_attachment(message, second_value)
        pending = list(data.get("pending_attachments") or [])
        pending.append(_serialize_saved_attachment(attachment))
        await state.update_data(pending_attachments=pending)
        second_value = _build_overlong_text_placeholder(second_label)
    first_value = data.get(first_key)
    description = _build_structured_task_description(task_type, first_value, second_value)
    await state.update_data(**{second_key: second_value, "description": description})
    await state.set_state(TaskCreateStates.waiting_confirm)
    updated_data = await state.get_data()
    summary_lines = await _build_task_create_confirm_summary_lines(
        title=(updated_data.get("title") or "").strip(),
        task_type_code=updated_data.get("task_type"),
        priority=int(updated_data.get("priority", DEFAULT_PRIORITY) or DEFAULT_PRIORITY),
        related_task_id=updated_data.get("related_task_id"),
        description=description,
        pending_attachments=updated_data.get("pending_attachments") or [],
    )
    await message.answer("\n".join(summary_lines), reply_markup=_build_worker_main_keyboard())
    await message.answer("是否创建该任务？", reply_markup=_build_confirm_keyboard())


@router.message(TaskCreateStates.waiting_reproduction)
async def on_task_create_reproduction(message: Message, state: FSMContext) -> None:
    """缺陷创建：录入复现步骤。"""

    await _handle_task_create_first_structured_field(
        message,
        state,
        field_key="reproduction",
        field_label=DEFECT_REPRODUCTION_LABEL,
        next_state=TaskCreateStates.waiting_expected_result,
        next_prompt="请输入期望结果（可选），可直接发送图片/文件作为附件；若暂无可发送“跳过”继续（仅发送附件也会进入下一步）：",
        reprompt_text="复现步骤可选：可继续输入步骤（可同时发送附件），或发送“跳过”继续录入期望结果：",
    )


@router.message(TaskCreateStates.waiting_expected_result)
async def on_task_create_expected_result(message: Message, state: FSMContext) -> None:
    """缺陷创建：录入期望结果并进入确认。"""

    await _handle_task_create_second_structured_field(
        message,
        state,
        task_type="defect",
        first_key="reproduction",
        second_key="expected_result",
        second_label=DEFECT_EXPECTED_RESULT_LABEL,
        reprompt_text="期望结果可选：可继续输入结果（可同时发送附件），或发送“跳过”进入确认创建：",
    )


@router.message(TaskCreateStates.waiting_current_effect)
async def on_task_create_current_effect(message: Message, state: FSMContext) -> None:
    """优化任务创建：录入当前效果。"""

    await _handle_task_create_first_structured_field(
        message,
        state,
        field_key="current_effect",
        field_label=OPTIMIZE_CURRENT_EFFECT_LABEL,
        next_state=TaskCreateStates.waiting_expected_effect,
        next_prompt="请输入期望效果（可选），可直接发送图片/文件作为附件；若暂无可发送“跳过”继续（仅发送附件也会进入下一步）：",
        reprompt_text="当前效果可选：可继续输入现状（可同时发送附件），或发送“跳过”继续录入期望效果：",
    )


@router.message(TaskCreateStates.waiting_expected_effect)
async def on_task_create_expected_effect(message: Message, state: FSMContext) -> None:
    """优化任务创建：录入期望效果并进入确认。"""

    await _handle_task_create_second_structured_field(
        message,
        state,
        task_type="task",
        first_key="current_effect",
        second_key="expected_effect",
        second_label=OPTIMIZE_EXPECTED_EFFECT_LABEL,
        reprompt_text="期望效果可选：可继续输入目标效果（可同时发送附件），或发送“跳过”进入确认创建：",
    )


@router.message(TaskCreateStates.waiting_description)
async def on_task_create_description(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    attachment_dir = _attachment_dir_for_message(message)
    processed_groups = set(data.get("processed_media_groups") or [])
    saved_attachments, text_part, processed_groups = await _collect_generic_media_group(
        message,
        attachment_dir,
        processed=processed_groups,
    )
    # 媒体组会触发多次 handler，若本次调用已被其他消息消费则直接忽略，避免重复推进流程。
    if message.media_group_id and not saved_attachments and not text_part:
        return
    if message.media_group_id:
        await state.update_data(processed_media_groups=list(processed_groups))
    if saved_attachments:
        pending = list(data.get("pending_attachments") or [])
        pending.extend(_serialize_saved_attachment(item) for item in saved_attachments)
        await state.update_data(pending_attachments=pending)
    raw_text = (text_part or "").strip() or (message.text or "").strip() or (message.caption or "").strip()
    trimmed = raw_text.strip()
    options = [SKIP_TEXT, "取消"]
    resolved = _resolve_reply_choice(raw_text, options=options)
    if resolved == "取消":
        await state.clear()
        await message.answer("已取消创建任务。", reply_markup=_build_worker_main_keyboard())
        return
    description: str = data.get("description", "")
    if trimmed and resolved != SKIP_TEXT:
        if len(trimmed) > DESCRIPTION_MAX_LENGTH:
            # 任务描述超长：自动落盘为附件，DB 写入占位文本并继续流程（无需用户手动拆分/重发）。
            attachment = _persist_text_paste_as_attachment(message, trimmed)
            pending = list(data.get("pending_attachments") or [])
            pending.append(_serialize_saved_attachment(attachment))
            await state.update_data(pending_attachments=pending)
            description = _build_overlong_text_placeholder("任务描述")
        else:
            description = trimmed
    await state.update_data(description=description)
    await state.set_state(TaskCreateStates.waiting_confirm)
    data = await state.get_data()
    summary_lines = await _build_task_create_confirm_summary_lines(
        title=(data.get("title") or "").strip(),
        task_type_code=data.get("task_type"),
        priority=int(data.get("priority", DEFAULT_PRIORITY) or DEFAULT_PRIORITY),
        related_task_id=data.get("related_task_id"),
        description=description,
        pending_attachments=data.get("pending_attachments") or [],
    )
    await message.answer("\n".join(summary_lines), reply_markup=_build_worker_main_keyboard())
    await message.answer("是否创建该任务？", reply_markup=_build_confirm_keyboard())


@router.message(TaskCreateStates.waiting_confirm)
async def on_task_create_confirm(message: Message, state: FSMContext) -> None:
    options = ["✅ 确认创建", "❌ 取消"]
    resolved = _resolve_reply_choice(message.text, options=options)
    stripped_token = _strip_number_prefix((message.text or "").strip())
    lowered = stripped_token.lower()
    # 先处理附件追加场景，支持媒体组后续消息继续补充
    attachment_dir = _attachment_dir_for_message(message)
    data = await state.get_data()
    processed_groups = set(data.get("processed_media_groups") or [])
    extra_attachments, text_part, processed_groups = await _collect_generic_media_group(
        message,
        attachment_dir,
        processed=processed_groups,
    )
    # 媒体组会触发多次 handler，若本次调用已被其他消息消费则直接忽略，避免重复追加附件/描述。
    if message.media_group_id and not extra_attachments and not text_part:
        return
    if message.media_group_id:
        await state.update_data(processed_media_groups=list(processed_groups))
    extra_text = _normalize_choice_token(text_part or message.text or "")
    is_cancel = resolved == options[1] or lowered == "取消"
    is_confirm = resolved == options[0] or lowered in {"确认", "确认创建"}
    if extra_attachments or (extra_text and not is_cancel and not is_confirm):
        pending = list(data.get("pending_attachments") or [])
        if extra_attachments:
            pending.extend(_serialize_saved_attachment(item) for item in extra_attachments)
        task_type_code = _normalize_task_type(data.get("task_type"))
        description = data.get("description") or ""
        structured_labels = _get_structured_task_labels(task_type_code)
        parsed_structured = _parse_structured_task_description(task_type_code, description)
        if extra_text and not is_confirm and not is_cancel:
            trimmed_extra = extra_text.strip()
            if trimmed_extra:
                if len(trimmed_extra) > DESCRIPTION_MAX_LENGTH:
                    attachment = _persist_text_paste_as_attachment(message, trimmed_extra)
                    pending.append(_serialize_saved_attachment(attachment))
                    if parsed_structured is not None and structured_labels is not None:
                        placeholder = _build_overlong_text_placeholder(f"补充{structured_labels[1]}")
                    else:
                        placeholder = _build_overlong_text_placeholder("补充任务描述")
                    if parsed_structured is not None and structured_labels is not None:
                        description = _build_structured_task_description(
                            task_type_code,
                            parsed_structured[0],
                            f"{parsed_structured[1]}\n{placeholder}" if parsed_structured[1] and parsed_structured[1] != "-" else placeholder,
                        ) or description
                    else:
                        description = f"{description}\n{placeholder}" if description else placeholder
                else:
                    if parsed_structured is not None and structured_labels is not None:
                        second_value = parsed_structured[1]
                        updated_second_value = (
                            f"{second_value}\n{trimmed_extra}"
                            if second_value and second_value != "-"
                            else trimmed_extra
                        )
                        description = _build_structured_task_description(
                            task_type_code,
                            parsed_structured[0],
                            updated_second_value,
                        ) or description
                    else:
                        description = f"{description}\n{trimmed_extra}" if description else trimmed_extra
        await state.update_data(pending_attachments=pending, description=description)
        updated_lines = await _build_task_create_confirm_summary_lines(
            title=(data.get("title") or "").strip(),
            task_type_code=data.get("task_type"),
            priority=int(data.get("priority", DEFAULT_PRIORITY) or DEFAULT_PRIORITY),
            related_task_id=data.get("related_task_id"),
            description=description,
            pending_attachments=pending,
        )
        update_label = structured_labels[1] if parsed_structured is not None and structured_labels is not None else "描述"
        await message.answer(
            f"已记录补充的{update_label}/附件，请继续选择“确认创建”或“取消”。\n" + "\n".join(updated_lines),
            reply_markup=_build_confirm_keyboard(),
        )
        return
    if is_cancel:
        await state.clear()
        await message.answer("已取消创建任务。", reply_markup=ReplyKeyboardRemove())
        await message.answer("已返回主菜单。", reply_markup=_build_worker_main_keyboard())
        return
    if not is_confirm:
        await message.answer(
            "请选择“确认创建”或“取消”，可直接输入编号或点击键盘按钮：",
            reply_markup=_build_confirm_keyboard(),
        )
        return
    data = await state.get_data()
    title = data.get("title")
    if not title:
        await state.clear()
        await message.answer(
            "创建数据缺失，请重新执行 /task_new。",
            reply_markup=ReplyKeyboardRemove(),
        )
        await message.answer("会话已返回主菜单。", reply_markup=_build_worker_main_keyboard())
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
            "任务类型缺失，请重新执行 /task_new。",
            reply_markup=ReplyKeyboardRemove(),
        )
        await message.answer("会话已返回主菜单。", reply_markup=_build_worker_main_keyboard())
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
        related_task_id=data.get("related_task_id"),
        actor=actor,
    )
    pending_attachments = data.get("pending_attachments") or []
    if pending_attachments:
        await _bind_serialized_attachments(task, pending_attachments, actor=actor)
    await state.clear()
    detail_text, markup = await _render_task_detail(task.id)
    await message.answer("任务已创建。", reply_markup=_build_worker_main_keyboard())
    await _answer_with_markdown(message, f"任务已创建：\n{detail_text}", reply_markup=markup)


@router.message(Command("task_child"))
async def on_task_child(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _answer_with_markdown(
        message,
        "子任务功能已下线，历史子任务已自动归档。请使用 /task_new 创建新的任务。",
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
    await callback.answer("子任务功能已下线，相关按钮已失效，请使用 /task_new 创建任务。", show_alert=True)


@router.callback_query(F.data.startswith("task:desc_edit:"))
async def on_task_desc_edit(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("回调参数错误", show_alert=True)
        return
    _, _, task_id = parts
    task = await TASK_SERVICE.get_task(task_id)
    if task is None:
        await callback.answer("任务不存在", show_alert=True)
        return
    origin_message = callback.message
    if origin_message is None:
        await callback.answer("消息已不存在，请重新开始编辑。", show_alert=True)
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
    """处理任务描述输入阶段的文本或菜单指令。"""

    data = await state.get_data()
    task_id = data.get("task_id")
    if not task_id:
        await state.clear()
        await message.answer("会话已失效，请重新操作。", reply_markup=_build_worker_main_keyboard())
        return

    token = _normalize_choice_token(message.text or "")
    if _is_cancel_message(token):
        await state.clear()
        await message.answer("已取消编辑任务描述。", reply_markup=_build_worker_main_keyboard())
        return

    if token == _normalize_choice_token(TASK_DESC_CLEAR_TEXT):
        await state.update_data(
            new_description="",
            actor=_actor_from_message(message),
        )
        await state.set_state(TaskDescriptionStates.waiting_confirm)
        await _answer_with_markdown(
            message,
            _build_task_desc_confirm_text("（新描述为空，将清空任务描述）"),
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
    pending = list(data.get("pending_attachments") or [])
    actor = _actor_from_message(message)
    if len(trimmed) > DESCRIPTION_MAX_LENGTH:
        # 任务描述超长：自动转为附件，并在 DB 中写入占位文本。
        attachment = _persist_text_paste_as_attachment(message, trimmed)
        pending.append(_serialize_saved_attachment(attachment))
        placeholder = _build_overlong_text_placeholder("任务描述")
        preview_segment = "\n".join([placeholder, "（原文已保存为附件，无需重复发送）"])
        await state.update_data(
            new_description=placeholder,
            pending_attachments=pending,
            actor=actor,
        )
    else:
        preview_segment = trimmed if trimmed else "（新描述为空，将清空任务描述）"
        await state.update_data(
            new_description=trimmed,
            pending_attachments=pending,
            actor=actor,
        )
    await state.set_state(TaskDescriptionStates.waiting_confirm)
    await _answer_with_markdown(
        message,
        _build_task_desc_confirm_text(preview_segment),
        reply_markup=_build_task_desc_confirm_keyboard(),
    )


@router.message(TaskDescriptionStates.waiting_confirm)
async def on_task_desc_confirm_stage_text(message: Message, state: FSMContext) -> None:
    """处理任务描述确认阶段的菜单指令。支持按钮点击、数字编号和直接文本输入。"""

    data = await state.get_data()
    task_id = data.get("task_id")
    if not task_id:
        await state.clear()
        await message.answer("会话已失效，请重新操作。", reply_markup=_build_worker_main_keyboard())
        return

    # 使用 _resolve_reply_choice() 智能解析用户输入，支持数字编号、按钮文本和直接文本
    options = [TASK_DESC_CONFIRM_TEXT, TASK_DESC_RETRY_TEXT, TASK_DESC_CANCEL_TEXT]
    resolved = _resolve_reply_choice(message.text, options=options)
    stripped = _strip_number_prefix((message.text or "").strip()).lower()

    # 处理取消操作
    if resolved == options[2] or _is_cancel_message(resolved) or stripped in {"取消"}:
        await state.clear()
        await message.answer("已取消编辑任务描述。", reply_markup=_build_worker_main_keyboard())
        return

    # 处理重新输入操作
    if resolved == options[1] or stripped in {"重新输入"}:
        task = await TASK_SERVICE.get_task(task_id)
        if task is None:
            await state.clear()
            await message.answer("任务不存在，已结束编辑流程。", reply_markup=_build_worker_main_keyboard())
            return
        await state.update_data(
            new_description=None,
            current_description=task.description or "",
        )
        await state.set_state(TaskDescriptionStates.waiting_content)
        await message.answer("已回到描述输入阶段，请重新输入新的任务描述。", reply_markup=_build_task_desc_input_keyboard())
        await _prompt_task_description_input(
            message,
            current_description=task.description or "",
        )
        return

    # 处理确认更新操作
    if resolved == options[0] or stripped in {"确认", "确认更新"}:
        new_description = data.get("new_description")
        if new_description is None:
            await state.set_state(TaskDescriptionStates.waiting_content)
            await message.answer("描述内容已失效，请重新输入。", reply_markup=_build_task_desc_input_keyboard())
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
            pending_attachments = data.get("pending_attachments") or []
            if pending_attachments:
                await _bind_serialized_attachments(updated, pending_attachments, actor=actor)
        except ValueError as exc:
            await state.clear()
            await message.answer(str(exc), reply_markup=_build_worker_main_keyboard())
            return
        await state.clear()
        await message.answer("任务描述已更新，正在刷新任务详情……", reply_markup=_build_worker_main_keyboard())
        detail_text, markup = await _render_task_detail(updated.id)
        await _answer_with_markdown(
            message,
            f"任务描述已更新：\n{detail_text}",
            reply_markup=markup,
        )
        return

    # 无效输入，提示用户
    await message.answer(
        "当前处于确认阶段，请选择确认、重新输入或取消，可直接输入编号或点击键盘按钮：",
        reply_markup=_build_task_desc_confirm_keyboard(),
    )


@router.callback_query(F.data.startswith("task:desc_"))
async def on_task_desc_legacy_callback(callback: CallbackQuery, state: FSMContext) -> None:
    """兼容旧版内联按钮，提示用户改用菜单按钮。"""

    await callback.answer("任务描述编辑的按钮已移动到菜单栏，请使用菜单操作。", show_alert=True)
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
        preview_segment = data.get("new_description") or "（新描述为空，将清空任务描述）"
        await _answer_with_markdown(
            callback.message,
            _build_task_desc_confirm_text(preview_segment),
            reply_markup=_build_task_desc_confirm_keyboard(),
        )


@router.callback_query(F.data.startswith("task:push_model:"))
async def on_task_push_model(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("回调参数错误", show_alert=True)
        return
    _, _, task_id = parts
    task = await TASK_SERVICE.get_task(task_id)
    if task is None:
        await callback.answer("任务不存在", show_alert=True)
        return
    if task.status not in MODEL_PUSH_ELIGIBLE_STATUSES:
        await callback.answer("当前状态暂不支持推送到模型", show_alert=True)
        return
    actor = _actor_from_callback(callback)
    chat_id = callback.message.chat.id if callback.message else callback.from_user.id
    await state.clear()
    await state.update_data(
        task_id=task_id,
        origin_message=callback.message,
        chat_id=chat_id,
        actor=actor,
        processed_media_groups=[],
    )
    await state.set_state(TaskPushStates.waiting_dispatch_target)
    await callback.answer("请选择处理方式")
    if callback.message:
        await _prompt_push_dispatch_target_input(callback.message)


@router.message(TaskPushStates.waiting_dispatch_target)
async def on_task_push_model_dispatch_target(message: Message, state: FSMContext) -> None:
    """推送到模型：先选择现有 CLI 会话或并行 CLI。"""

    data = await state.get_data()
    task_id = (data.get("task_id") or "").strip()
    if not task_id:
        await state.clear()
        await message.answer("推送会话已失效，请重新点击按钮。", reply_markup=_build_worker_main_keyboard())
        return

    raw_text = message.text or ""
    resolved = _resolve_reply_choice(raw_text, options=[PUSH_TARGET_CURRENT, PUSH_TARGET_PARALLEL, "取消"])
    if resolved == "取消" or _is_cancel_message(raw_text):
        await state.clear()
        await message.answer("已取消推送到模型。", reply_markup=_build_worker_main_keyboard())
        return
    if resolved not in {PUSH_TARGET_CURRENT, PUSH_TARGET_PARALLEL}:
        await message.answer(
            "请选择处理方式：现有 CLI 会话处理 / 新建分支 + 新 CLI 并行处理，发送“取消”可退出。",
            reply_markup=_build_push_dispatch_target_keyboard(),
        )
        return

    task = await TASK_SERVICE.get_task(task_id)
    if task is None:
        await state.clear()
        await message.answer("任务不存在，已取消推送。", reply_markup=_build_worker_main_keyboard())
        return

    await state.update_data(dispatch_target=resolved)
    if resolved == PUSH_TARGET_CURRENT:
        entries = await _list_project_live_sessions()
        if len(entries) == 1 and entries[0].kind == "main":
            await _continue_push_after_existing_session_selected(
                message=message,
                state=state,
                selected_entry_key="main",
            )
            return
        await state.set_state(TaskPushStates.waiting_existing_session)
        await _show_push_existing_session_view(message)
        return

    await _begin_parallel_launch(
        task=task,
        chat_id=data.get("chat_id") or message.chat.id,
        origin_message=data.get("origin_message") or message,
        actor=data.get("actor") or _actor_from_message(message),
        push_mode=None,
        send_mode=PUSH_SEND_MODE_IMMEDIATE,
        supplement=None,
    )


@router.message(TaskPushStates.waiting_existing_session)
async def on_push_existing_session_message(message: Message, state: FSMContext) -> None:
    """现有 CLI 会话选择阶段：允许用户发送“取消”，其余输入提示点击按钮。"""

    raw_text = (message.text or "").strip()
    if _is_cancel_message(raw_text) or _strip_number_prefix(raw_text) == "取消":
        await state.clear()
        await message.answer("已取消推送到模型。", reply_markup=_build_worker_main_keyboard())
        return
    await message.answer("请点击要推送到的现有 CLI 会话，发送“取消”可退出。")


@router.callback_query(F.data == PUSH_EXISTING_SESSION_REFRESH_CALLBACK)
async def on_push_existing_session_refresh_callback(callback: CallbackQuery, state: FSMContext) -> None:
    current_state = await state.get_state()
    data = await state.get_data()
    if current_state != TaskPushStates.waiting_existing_session.state or not (data.get("task_id") or "").strip():
        await state.clear()
        await callback.answer("会话选择已失效，请重新点击推送到模型。", show_alert=True)
        return
    message = callback.message
    if message is None:
        await callback.answer("无法定位原消息", show_alert=True)
        return
    success = await _show_push_existing_session_view(message, prefer_edit=True)
    await callback.answer("已刷新会话列表" if success else "会话列表发送失败", show_alert=not success)


@router.callback_query(F.data == PUSH_EXISTING_SESSION_CANCEL_CALLBACK)
async def on_push_existing_session_cancel_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("已取消推送到模型")
    if callback.message is not None:
        await callback.message.answer("已取消推送到模型。", reply_markup=_build_worker_main_keyboard())


@router.callback_query(F.data == PUSH_EXISTING_SESSION_MAIN_CALLBACK)
async def on_push_existing_session_main_callback(callback: CallbackQuery, state: FSMContext) -> None:
    current_state = await state.get_state()
    data = await state.get_data()
    if current_state != TaskPushStates.waiting_existing_session.state or not (data.get("task_id") or "").strip():
        await state.clear()
        await callback.answer("会话选择已失效，请重新点击推送到模型。", show_alert=True)
        return
    message = callback.message
    if message is None:
        await callback.answer("无法定位原消息", show_alert=True)
        return
    try:
        await _continue_push_after_existing_session_selected(
            message=message,
            state=state,
            selected_entry_key="main",
        )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("已选择主会话")


@router.callback_query(F.data.startswith(PUSH_EXISTING_SESSION_PARALLEL_PREFIX))
async def on_push_existing_session_parallel_callback(callback: CallbackQuery, state: FSMContext) -> None:
    current_state = await state.get_state()
    data = await state.get_data()
    if current_state != TaskPushStates.waiting_existing_session.state or not (data.get("task_id") or "").strip():
        await state.clear()
        await callback.answer("会话选择已失效，请重新点击推送到模型。", show_alert=True)
        return
    message = callback.message
    if message is None:
        await callback.answer("无法定位原消息", show_alert=True)
        return
    task_id = _normalize_task_id((callback.data or "")[len(PUSH_EXISTING_SESSION_PARALLEL_PREFIX) :])
    if not task_id:
        await callback.answer("会话参数错误", show_alert=True)
        return
    try:
        await _continue_push_after_existing_session_selected(
            message=message,
            state=state,
            selected_entry_key=f"parallel:{task_id}",
        )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("已选择并行会话")


@router.message(TaskBatchPushStates.waiting_existing_session)
async def on_task_batch_push_existing_session_message(message: Message, state: FSMContext) -> None:
    raw_text = (message.text or "").strip()
    if _is_cancel_message(raw_text) or _strip_number_prefix(raw_text) == "取消":
        data = await state.get_data()
        await state.clear()
        await _restore_task_batch_push_view(
            target_message=data.get("batch_origin_message"),
            fallback_message=message,
            status=data.get("batch_status"),
            page=int(data.get("batch_page", 1) or 1),
            limit=int(data.get("batch_limit", DEFAULT_PAGE_SIZE) or DEFAULT_PAGE_SIZE),
            selected_task_ids=data.get("batch_task_ids") or [],
            selected_task_order=data.get("batch_task_ids") or [],
        )
        await message.answer("已返回批量勾选列表。", reply_markup=_build_worker_main_keyboard())
        return
    await message.answer("请点击要推送到的现有 CLI 会话，发送“取消”可返回勾选列表。")


@router.callback_query(F.data == TASK_BATCH_PUSH_SESSION_REFRESH_CALLBACK)
async def on_task_batch_push_session_refresh_callback(callback: CallbackQuery, state: FSMContext) -> None:
    current_state = await state.get_state()
    if current_state != TaskBatchPushStates.waiting_existing_session.state:
        await state.clear()
        await callback.answer("批量推送会话选择已失效，请重新开始。", show_alert=True)
        return
    message = callback.message
    if message is None:
        await callback.answer("无法定位原消息", show_alert=True)
        return
    success = await _show_task_batch_push_existing_session_view(message)
    await callback.answer("已刷新会话列表" if success else "会话列表发送失败", show_alert=not success)


@router.callback_query(F.data == TASK_BATCH_PUSH_SESSION_CANCEL_CALLBACK)
async def on_task_batch_push_session_cancel_callback(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    if callback.message is not None:
        await _restore_task_batch_push_view(
            target_message=callback.message,
            fallback_message=callback.message,
            status=data.get("batch_status"),
            page=int(data.get("batch_page", 1) or 1),
            limit=int(data.get("batch_limit", DEFAULT_PAGE_SIZE) or DEFAULT_PAGE_SIZE),
            selected_task_ids=data.get("batch_task_ids") or [],
            selected_task_order=data.get("batch_task_ids") or [],
        )
    await callback.answer("已返回勾选列表")


@router.callback_query(F.data == TASK_BATCH_PUSH_SESSION_MAIN_CALLBACK)
async def on_task_batch_push_session_main_callback(callback: CallbackQuery, state: FSMContext) -> None:
    current_state = await state.get_state()
    if current_state != TaskBatchPushStates.waiting_existing_session.state:
        await state.clear()
        await callback.answer("批量推送会话选择已失效，请重新开始。", show_alert=True)
        return
    message = callback.message
    if message is None:
        await callback.answer("无法定位原消息", show_alert=True)
        return
    entry = await _resolve_session_live_entry("main")
    label = entry.label if entry is not None else "主会话"
    await state.update_data(
        selected_existing_session_key="main",
        batch_session_label=label,
    )
    await state.set_state(TaskBatchPushStates.waiting_choice)
    await callback.answer("已选择主会话")
    await _prompt_push_mode_input(message)


@router.callback_query(F.data.startswith(TASK_BATCH_PUSH_SESSION_PARALLEL_PREFIX))
async def on_task_batch_push_session_parallel_callback(callback: CallbackQuery, state: FSMContext) -> None:
    current_state = await state.get_state()
    if current_state != TaskBatchPushStates.waiting_existing_session.state:
        await state.clear()
        await callback.answer("批量推送会话选择已失效，请重新开始。", show_alert=True)
        return
    message = callback.message
    if message is None:
        await callback.answer("无法定位原消息", show_alert=True)
        return
    task_id = _normalize_task_id((callback.data or "")[len(TASK_BATCH_PUSH_SESSION_PARALLEL_PREFIX) :])
    if not task_id:
        await callback.answer("会话参数错误", show_alert=True)
        return
    entry = await _resolve_session_live_entry(f"parallel:{task_id}")
    if entry is None:
        await callback.answer("并行会话已失效，请刷新后重试。", show_alert=True)
        return
    await state.update_data(
        selected_existing_session_key=entry.key,
        batch_session_label=entry.label,
    )
    await state.set_state(TaskBatchPushStates.waiting_choice)
    await callback.answer("已选择并行会话")
    await _prompt_push_mode_input(message)


@router.message(TaskBatchPushStates.waiting_choice)
async def on_task_batch_push_mode_choice(message: Message, state: FSMContext) -> None:
    """批量推送：统一选择 PLAN/YOLO 后执行排队推送。"""

    data = await state.get_data()
    task_ids = data.get("batch_task_ids") or []
    if not task_ids:
        await state.clear()
        await message.answer("批量推送会话已失效，请重新勾选任务。", reply_markup=_build_worker_main_keyboard())
        return
    raw_text = message.text or ""
    resolved = _resolve_reply_choice(raw_text, options=[PUSH_MODE_PLAN, PUSH_MODE_YOLO, "取消"])
    if resolved == "取消" or _is_cancel_message(raw_text):
        await state.clear()
        await _restore_task_batch_push_view(
            target_message=data.get("batch_origin_message"),
            fallback_message=message,
            status=data.get("batch_status"),
            page=int(data.get("batch_page", 1) or 1),
            limit=int(data.get("batch_limit", DEFAULT_PAGE_SIZE) or DEFAULT_PAGE_SIZE),
            selected_task_ids=task_ids,
            selected_task_order=task_ids,
        )
        await message.answer("已取消批量推送。", reply_markup=_build_worker_main_keyboard())
        return
    normalized = _normalize_choice_token(resolved).upper()
    if normalized not in {PUSH_MODE_PLAN, PUSH_MODE_YOLO}:
        await message.answer(
            "请选择 PLAN 或 YOLO，发送“取消”可返回勾选列表：",
            reply_markup=_build_push_mode_keyboard(),
        )
        return
    if not _is_codex_model():
        await state.clear()
        await _restore_task_batch_push_view(
            target_message=data.get("batch_origin_message"),
            fallback_message=message,
            status=data.get("batch_status"),
            page=int(data.get("batch_page", 1) or 1),
            limit=int(data.get("batch_limit", DEFAULT_PAGE_SIZE) or DEFAULT_PAGE_SIZE),
            selected_task_ids=task_ids,
            selected_task_order=task_ids,
        )
        await message.answer("当前模型不支持批量排队推送，请切换到 Codex 后重试。", reply_markup=_build_worker_main_keyboard())
        return

    await message.answer(
        (
            f"开始批量排队推送 {len(task_ids)} 个任务。\n"
            f"目标会话：{(data.get('batch_session_label') or '主会话').strip()}\n"
            f"模式：{normalized}\n"
            f"发送方式：{PUSH_SEND_MODE_QUEUED_LABEL}"
        ),
        reply_markup=_build_worker_main_keyboard(),
    )
    await _execute_task_batch_push(
        trigger_message=message,
        state=state,
        push_mode=normalized,
    )


@router.message(TaskPushStates.waiting_choice)
async def on_task_push_model_choice(message: Message, state: FSMContext) -> None:
    """推送到模型：处理 PLAN/YOLO 模式选择。"""

    data = await state.get_data()
    task_id = (data.get("task_id") or "").strip()
    if not task_id:
        await state.clear()
        await message.answer("推送会话已失效，请重新点击按钮。", reply_markup=_build_worker_main_keyboard())
        return

    raw_text = message.text or ""
    resolved = _resolve_reply_choice(raw_text, options=[PUSH_MODE_PLAN, PUSH_MODE_YOLO, "取消"])
    if resolved == "取消" or _is_cancel_message(raw_text):
        await state.clear()
        await message.answer(
            "已取消推送到模型。",
            reply_markup=_build_worker_main_keyboard(),
        )
        return

    normalized = _normalize_choice_token(resolved).upper()
    if normalized in {PUSH_MODE_PLAN, PUSH_MODE_YOLO}:
        resolved = normalized

    if resolved not in {PUSH_MODE_PLAN, PUSH_MODE_YOLO}:
        await message.answer(
            "请选择 PLAN 或 YOLO，发送“取消”可退出：",
            reply_markup=_build_push_mode_keyboard(),
        )
        return

    # 选择 PLAN/YOLO 后即刷新一次缓存（仅更新状态，不回写主菜单，避免覆盖流程键盘）。
    await _refresh_worker_plan_mode_state_cache_async(force_probe=True)
    await state.update_data(push_mode=resolved)
    if _is_codex_model():
        await state.set_state(TaskPushStates.waiting_send_mode)
        await _prompt_push_send_mode_input(message, push_mode=resolved)
        return
    await state.set_state(TaskPushStates.waiting_supplement)
    await _prompt_model_supplement_input(message, push_mode=resolved)


@router.message(TaskPushStates.waiting_send_mode)
async def on_task_push_model_send_mode(message: Message, state: FSMContext) -> None:
    """推送到模型：处理立即发送/排队发送方式选择。"""

    data = await state.get_data()
    task_id = (data.get("task_id") or "").strip()
    if not task_id:
        await state.clear()
        await message.answer("推送会话已失效，请重新点击按钮。", reply_markup=_build_worker_main_keyboard())
        return

    raw_text = message.text or ""
    resolved = _resolve_reply_choice(raw_text, options=[PUSH_SEND_MODE_IMMEDIATE_LABEL, PUSH_SEND_MODE_QUEUED_LABEL, "取消"])
    if resolved == "取消" or _is_cancel_message(raw_text):
        await state.clear()
        await message.answer("已取消推送到模型。", reply_markup=_build_worker_main_keyboard())
        return

    if resolved == PUSH_SEND_MODE_QUEUED_LABEL:
        send_mode = PUSH_SEND_MODE_QUEUED
    elif resolved == PUSH_SEND_MODE_IMMEDIATE_LABEL:
        send_mode = PUSH_SEND_MODE_IMMEDIATE
    else:
        await message.answer(
            "请选择立即发送或排队发送，发送“取消”可退出：",
            reply_markup=_build_push_send_mode_keyboard(),
        )
        return

    await state.update_data(send_mode=send_mode)
    push_mode = (data.get("push_mode") or "").strip().upper() or None
    await state.set_state(TaskPushStates.waiting_supplement)
    await _prompt_model_supplement_input(message, push_mode=push_mode, send_mode=send_mode)


@router.callback_query(F.data.startswith("task:push_model_skip:"))
async def on_task_push_model_skip(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("回调参数错误", show_alert=True)
        return
    _, _, task_id = parts
    data = await state.get_data()
    stored_id = data.get("task_id")
    if stored_id and stored_id != task_id:
        task_id = stored_id
    task = await TASK_SERVICE.get_task(task_id)
    if task is None:
        await state.clear()
        await callback.answer("任务不存在", show_alert=True)
        return
    actor = _actor_from_callback(callback)
    chat_id = data.get("chat_id") or (callback.message.chat.id if callback.message else callback.from_user.id)
    origin_message = data.get("origin_message") or callback.message
    push_mode = (data.get("push_mode") or "").strip().upper()
    send_mode = _normalize_push_send_mode(data.get("send_mode"))
    dispatch_target = (data.get("dispatch_target") or "").strip()
    await state.clear()
    if dispatch_target == PUSH_TARGET_PARALLEL:
        await callback.answer("已进入并行分支选择")
        await _begin_parallel_launch(
            task=task,
            chat_id=chat_id,
            origin_message=origin_message,
            actor=actor,
            push_mode=push_mode or None,
            send_mode=send_mode,
            supplement=None,
        )
        return

    try:
        dispatch_context = await _resolve_selected_existing_dispatch_context(data)
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    try:
        success, prompt, session_path = await _push_task_to_model(
            task,
            chat_id=chat_id,
            reply_to=origin_message,
            supplement=None,
            actor=actor,
            push_mode=push_mode or None,
            send_mode=send_mode,
            dispatch_context=dispatch_context,
        )
    except ValueError as exc:
        worker_log.error(
            "推送模板缺失：%s",
            exc,
            extra={"task_id": task_id, "status": task.status},
        )
        await callback.answer("推送失败：缺少模板配置", show_alert=True)
        return
    if not success:
        await callback.answer("推送失败：模型未就绪", show_alert=True)
        return
    await callback.answer("已推送到模型")
    preview_block, preview_parse_mode = _wrap_text_in_code_block(prompt)
    await _send_model_push_preview(
        chat_id,
        preview_block,
        reply_to=origin_message,
        parse_mode=preview_parse_mode,
        reply_markup=_build_worker_main_keyboard(),
    )
    if session_path is not None:
        await _send_session_ack(chat_id, session_path, reply_to=origin_message)


@router.callback_query(F.data.startswith("task:push_model_fill:"))
async def on_task_push_model_fill(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("回调参数错误", show_alert=True)
        return
    _, _, task_id = parts
    task = await TASK_SERVICE.get_task(task_id)
    if task is None:
        await state.clear()
        await callback.answer("任务不存在", show_alert=True)
        return
    actor = _actor_from_callback(callback)
    await state.update_data(
        task_id=task_id,
        origin_message=callback.message,
        chat_id=callback.message.chat.id if callback.message else callback.from_user.id,
        actor=actor,
        # 与任务创建/附件流程保持一致：相册（媒体组）会触发多次回调，需要记录已处理的 group。
        processed_media_groups=[],
    )
    await state.set_state(TaskPushStates.waiting_choice)
    await callback.answer("请选择推送模式：PLAN / YOLO（可发送“取消”退出）")
    if callback.message:
        await _prompt_push_mode_input(callback.message)


def _build_attachment_only_supplement(attachments: Sequence[TelegramSavedAttachment]) -> str:
    """仅发送附件无文字时，为“补充任务描述”生成兜底文案。"""

    if not attachments:
        return "-"
    names = [str(item.display_name or "").strip() for item in attachments]
    names = [name for name in names if name]
    if not names:
        return "见附件"
    limit = TASK_ATTACHMENT_PREVIEW_LIMIT
    shown = names[:limit]
    suffix = f"（共 {len(names)} 个）" if len(names) > limit else ""
    return f"见附件：{'、'.join(shown)}{suffix}"


@router.message(TaskPushStates.waiting_supplement)
async def on_task_push_model_supplement(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    task_id = data.get("task_id")
    if not task_id:
        await state.clear()
        await message.answer("推送会话已失效，请重新点击按钮。", reply_markup=_build_worker_main_keyboard())
        return
    push_mode = (data.get("push_mode") or "").strip().upper()
    send_mode = _normalize_push_send_mode(data.get("send_mode"))
    task = await TASK_SERVICE.get_task(task_id)
    if task is None:
        await state.clear()
        await message.answer("任务不存在，已取消推送。", reply_markup=_build_worker_main_keyboard())
        return
    chat_id = data.get("chat_id") or message.chat.id
    origin_message = data.get("origin_message")
    actor = data.get("actor") or _actor_from_message(message)
    dispatch_target = (data.get("dispatch_target") or "").strip()
    attachment_dir = _attachment_dir_for_message(message)

    processed_groups = set(data.get("processed_media_groups") or [])
    # 复用现有媒体组聚合逻辑：相册会触发多次 handler，这里合并 caption 并确保整组只消费一次。
    saved_attachments, text_part, processed_groups = await _collect_generic_media_group(
        message,
        attachment_dir,
        processed=processed_groups,
    )
    # 媒体组重复回调：该条消息已被其他 handler 消费，避免清空状态/误提示“会话失效”。
    if message.media_group_id and not saved_attachments and not text_part:
        return
    if message.media_group_id:
        await state.update_data(processed_media_groups=list(processed_groups))

    raw_text = text_part or ""
    trimmed = raw_text.strip()
    options = [SKIP_TEXT, "取消"]
    resolved = _resolve_reply_choice(raw_text, options=options)
    if resolved == "取消" or trimmed == "取消":
        await state.clear()
        await message.answer("已取消推送到模型。", reply_markup=_build_worker_main_keyboard())
        return

    supplement: Optional[str] = None
    # Telegram 图片/文件常用 caption 承载文字；若本次仅有附件无文字，则按需求生成“见附件：文件名列表”。
    if trimmed and resolved != SKIP_TEXT:
        if len(trimmed) > DESCRIPTION_MAX_LENGTH:
            # 补充描述超长：自动落盘为文本附件并绑定到任务，提示模型通过附件读取全文。
            text_attachment = _persist_text_paste_as_attachment(message, trimmed)
            saved_attachments = list(saved_attachments)
            saved_attachments.append(text_attachment)
            supplement = _build_overlong_text_placeholder("补充任务描述")
        else:
            supplement = trimmed
    elif saved_attachments:
        supplement = _build_attachment_only_supplement(saved_attachments)

    if saved_attachments:
        serialized = [_serialize_saved_attachment(item) for item in saved_attachments]
        await _bind_serialized_attachments(task, serialized, actor=actor)
    await state.clear()
    if dispatch_target == PUSH_TARGET_PARALLEL:
        await _begin_parallel_launch(
            task=task,
            chat_id=chat_id,
            origin_message=origin_message,
            actor=actor,
            push_mode=push_mode or None,
            send_mode=send_mode,
            supplement=supplement,
        )
        return

    try:
        dispatch_context = await _resolve_selected_existing_dispatch_context(data)
    except ValueError as exc:
        await message.answer(str(exc), reply_markup=_build_worker_main_keyboard())
        return

    try:
        success, prompt, session_path = await _push_task_to_model(
            task,
            chat_id=chat_id,
            reply_to=origin_message,
            supplement=supplement,
            actor=actor,
            push_mode=push_mode or None,
            send_mode=send_mode,
            dispatch_context=dispatch_context,
        )
    except ValueError as exc:
        worker_log.error(
            "推送模板缺失：%s",
            exc,
            extra={"task_id": task_id, "status": task.status if task else None},
        )
        await message.answer("推送失败：缺少模板配置。", reply_markup=_build_worker_main_keyboard())
        return
    if not success:
        await message.answer("推送失败：模型未就绪，请稍后再试。", reply_markup=_build_worker_main_keyboard())
        return
    preview_block, preview_parse_mode = _wrap_text_in_code_block(prompt)
    await _send_model_push_preview(
        chat_id,
        preview_block,
        reply_to=origin_message,
        parse_mode=preview_parse_mode,
        reply_markup=_build_worker_main_keyboard(),
    )
    if session_path is not None:
        await _send_session_ack(chat_id, session_path, reply_to=origin_message)


@router.callback_query(F.data.startswith("task:history:"))
async def on_task_history(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("回调参数错误", show_alert=True)
        return
    _, _, task_id = parts
    message = callback.message
    if message is None:
        await callback.answer("无法定位原消息", show_alert=True)
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
            "任务事件历史发送失败：%s",
            exc,
            extra={"task_id": task_id},
        )
        await callback.answer("历史记录发送失败", show_alert=True)
        return
    _init_task_view_context(sent, history_state)
    await callback.answer("已展示历史记录")
    worker_log.info(
        "任务事件历史已通过代码块消息展示",
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
        await callback.answer("回调参数错误", show_alert=True)
        return
    _, _, task_id, page_raw = parts
    try:
        requested_page = int(page_raw)
    except ValueError:
        await callback.answer("页码无效", show_alert=True)
        return
    message = callback.message
    if message is None:
        await callback.answer("无法定位原消息", show_alert=True)
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
            "历史分页发送失败：%s",
            exc,
            extra={"task_id": task_id, "page": requested_page},
        )
        await callback.answer("切换失败，请稍后重试", show_alert=True)
        return
    chat = getattr(message, "chat", None)
    if chat is not None:
        _clear_task_view(chat.id, message.message_id)
    _init_task_view_context(sent, history_state)
    await callback.answer(f"已展示第 {page}/{total_pages} 页")


@router.callback_query(F.data.startswith(f"{TASK_HISTORY_BACK_CALLBACK}:"))
async def on_task_history_back(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("回调参数错误", show_alert=True)
        return
    _, _, task_id = parts
    message = callback.message
    if message is None:
        await callback.answer("无法定位原消息", show_alert=True)
        return
    try:
        text, markup = await _render_task_detail(task_id)
    except ValueError:
        await callback.answer("任务不存在", show_alert=True)
        return
    detail_state = TaskViewState(kind="detail", data={"task_id": task_id})
    chat = getattr(message, "chat", None)
    if chat is not None:
        _clear_task_view(chat.id, message.message_id)
    if _is_text_too_long_for_telegram(text):
        sent, _edited = await _send_task_detail_as_attachment(
            message,
            task_id=task_id,
            detail_text=text,
            reply_markup=markup,
            prefer_edit=False,
        )
    else:
        sent = await _answer_with_markdown(message, text, reply_markup=markup)
    if sent is not None:
        _init_task_view_context(sent, detail_state)
        await callback.answer("已返回任务详情")
        return
    await callback.answer("返回失败，请稍后重试", show_alert=True)


class TaskSummaryRequestError(Exception):
    """生成摘要流程中的业务异常。"""


async def _request_task_summary(
    task: TaskRecord,
    *,
    actor: Optional[str],
    chat_id: int,
    reply_to: Optional[Message],
) -> tuple[str, bool]:
    """触发摘要请求，必要时自动调整任务状态。"""

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
            raise TaskSummaryRequestError(f"任务状态更新失败：{exc}") from exc
        else:
            current_task = updated
            status_changed = True

    history_text, _ = await _build_history_context_for_model(current_task.id)
    notes = await TASK_SERVICE.list_notes(current_task.id)
    request_id = uuid.uuid4().hex
    prompt = _build_summary_prompt(
        current_task,
        request_id=request_id,
        history_text=history_text,
        notes=notes,
    )

    _remember_chat_active_user(chat_id, _extract_actor_user_id(actor))
    success, session_path = await _dispatch_prompt_to_model(
        chat_id,
        prompt,
        reply_to=reply_to,
        ack_immediately=False,
    )
    if not success:
        raise TaskSummaryRequestError("模型未就绪，摘要生成失败")

    actor_label = actor
    if session_path is not None:
        session_key = str(session_path)
        _bind_session_task(session_key, current_task.id)
        PENDING_SUMMARIES[session_key] = PendingSummary(
            task_id=current_task.id,
            request_id=request_id,
            actor=actor_label,
            session_key=session_key,
            session_path=session_path,
            created_at=time.monotonic(),
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
            await _answer_with_markdown(message, "请提供任务 ID，例如：/task_note TASK_0001 内容")
            return
        normalized_task_id = _normalize_task_id(task_id)
        if not normalized_task_id:
            await _answer_with_markdown(message, TASK_ID_USAGE_TIP)
            return
        content = parts[1].strip() if len(parts) > 1 else extra.get("content", "").strip()
        if not content:
            await _answer_with_markdown(message, "备注内容不能为空")
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
        await _answer_with_markdown(message, f"备注已添加：\n{detail_text}", reply_markup=markup)
        return

    await state.clear()
    await state.set_state(TaskNoteStates.waiting_task_id)
    await message.answer("请输入任务 ID：")


@router.message(TaskNoteStates.waiting_task_id)
async def on_note_task_id(message: Message, state: FSMContext) -> None:
    task_id_raw = (message.text or "").strip()
    if not task_id_raw:
        await message.answer("任务 ID 不能为空，请重新输入：")
        return
    task_id = _normalize_task_id(task_id_raw)
    if not task_id:
        await message.answer(TASK_ID_USAGE_TIP)
        return
    task = await TASK_SERVICE.get_task(task_id)
    if task is None:
        await message.answer("任务不存在，请重新输入有效的 ID：")
        return
    await state.update_data(task_id=task_id)
    await state.set_state(TaskNoteStates.waiting_content)
    await message.answer("请输入备注内容：")


@router.message(TaskNoteStates.waiting_content)
async def on_note_content(message: Message, state: FSMContext) -> None:
    content = (message.text or "").strip()
    if not content:
        await message.answer("备注内容不能为空，请重新输入：")
        return
    data = await state.get_data()
    task_id = data.get("task_id")
    if not task_id:
        await state.clear()
        await message.answer("数据缺失，备注添加失败，请重新执行 /task_note")
        return
    await TASK_SERVICE.add_note(
        task_id,
        note_type="misc",
        content=content,
        actor=_actor_from_message(message),
    )
    await state.clear()
    detail_text, markup = await _render_task_detail(task_id)
    await _answer_with_markdown(message, f"备注已添加：\n{detail_text}", reply_markup=markup)


def _build_attachment_prompt(task_id: str) -> str:
    return (
        "请发送要绑定的附件（图片/文件/视频等），将自动落地并关联到任务。\n"
        "- 输入“取消”可退出\n"
        f"- 当前任务：{task_id}\n"
        "- 支持多种类型，发送后会返回本地相对路径以便模型读取"
    )


async def _start_attachment_collection(
    message: Message,
    state: FSMContext,
    task_id: str,
) -> None:
    await state.clear()
    await state.update_data(task_id=task_id, processed_media_groups=[])
    await state.set_state(TaskAttachmentStates.waiting_files)
    await _answer_with_markdown(message, _build_attachment_prompt(task_id), reply_markup=_build_worker_main_keyboard())


@router.message(Command("attach"))
async def on_attach_command(message: Message, state: FSMContext) -> None:
    args = _extract_command_args(message.text)
    if not args:
        await _answer_with_markdown(message, "请提供任务 ID，例如：/attach TASK_0001")
        return
    normalized_task_id = _normalize_task_id(args)
    if not normalized_task_id:
        await _answer_with_markdown(message, TASK_ID_USAGE_TIP)
        return
    task = await TASK_SERVICE.get_task(normalized_task_id)
    if task is None:
        await _answer_with_markdown(message, "任务不存在，请检查任务编码。")
        return
    await _start_attachment_collection(message, state, task.id)


@router.callback_query(F.data.startswith("task:attach:"))
async def on_task_attach_callback(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("回调参数错误", show_alert=True)
        return
    _, _, task_id = parts
    task = await TASK_SERVICE.get_task(task_id)
    if task is None:
        await callback.answer("任务不存在", show_alert=True)
        return
    if callback.message is None:
        await callback.answer("无法定位原消息", show_alert=True)
        return
    await _start_attachment_collection(callback.message, state, task.id)
    await callback.answer()


@router.message(TaskAttachmentStates.waiting_files)
async def on_task_attach_files(message: Message, state: FSMContext) -> None:
    raw_text = (message.text or "").strip()
    if raw_text == "取消":
        await state.clear()
        await message.answer("已取消附件绑定。", reply_markup=_build_worker_main_keyboard())
        return
    data = await state.get_data()
    task_id = data.get("task_id")
    if not task_id:
        await state.clear()
        await message.answer("任务上下文丢失，请重新执行 /attach。", reply_markup=_build_worker_main_keyboard())
        return
    task = await TASK_SERVICE.get_task(task_id)
    if task is None:
        await state.clear()
        await message.answer("任务不存在，请重新执行 /attach。", reply_markup=_build_worker_main_keyboard())
        return
    attachment_dir = _attachment_dir_for_message(message)
    processed_groups = set(data.get("processed_media_groups") or [])
    saved, text_part, processed_groups = await _collect_generic_media_group(
        message,
        attachment_dir,
        processed=processed_groups,
    )
    # 媒体组会触发多次 handler，若本次调用已被其他消息消费则直接忽略，避免重复绑定/误报无附件。
    if message.media_group_id and not saved and not text_part:
        return
    if message.media_group_id:
        await state.update_data(processed_media_groups=list(processed_groups))
    if not saved:
        await message.answer("未检测到附件，请发送图片/文件等，或输入“取消”退出。")
        return
    actor = _actor_from_message(message)
    serialized = [_serialize_saved_attachment(item) for item in saved]
    bound = await _bind_serialized_attachments(task, serialized, actor=actor)
    await state.clear()
    detail_text, markup = await _render_task_detail(task.id)
    lines = ["附件已绑定到任务：", f"- 任务：{task.id}"]
    for idx, item in enumerate(bound, 1):
        display = _escape_markdown_text(item.display_name)
        mime = _escape_markdown_text(item.mime_type)
        path_text = _escape_markdown_text(item.path)
        lines.append(f"{idx}. {display}（{mime}）→ {path_text}")
    lines.append("如需继续添加，可再次使用 /attach <task_id>。")
    await _answer_with_markdown(
        message,
        "\n".join(lines) + f"\n\n{detail_text}",
        reply_markup=markup,
    )


@router.message(Command("task_update"))
async def on_task_update(message: Message) -> None:
    args = _extract_command_args(message.text)
    if not args:
        await _answer_with_markdown(
            message,
            "用法：/task_update TASK_0001 status=test | priority=2 | description=调研内容",
        )
        return
    body, extra = parse_structured_text(args)
    parts = body.split(" ", 1)
    task_id = parts[0].strip() if parts and parts[0].strip() else extra.get("id")
    if not task_id:
        await _answer_with_markdown(message, "请提供任务 ID")
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
            await _answer_with_markdown(message, "优先级需要为数字 1-5")
            return
        priority = max(1, min(priority, 5))
    pending_attachments: list[Mapping[str, str]] = []
    description = extra.get("description")
    if description is not None:
        description = description.strip()
        if len(description) > DESCRIPTION_MAX_LENGTH:
            # /task_update 场景：描述超长自动转附件并写入占位，避免命令被长度限制卡住。
            attachment = _persist_text_paste_as_attachment(message, description)
            pending_attachments.append(_serialize_saved_attachment(attachment))
            description = _build_overlong_text_placeholder("任务描述")
    task_type = None
    if "type" in extra:
        task_type = _normalize_task_type(extra.get("type"))
        if task_type is None:
            await _answer_with_markdown(
                message,
                "任务类型无效，请填写 type=需求/缺陷/优化/风险",
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
        await _answer_with_markdown(message, "请提供需要更新的字段，例如 status=test")
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
        if pending_attachments:
            await _bind_serialized_attachments(updated, pending_attachments, actor=actor)
    except ValueError as exc:
        await _answer_with_markdown(message, str(exc))
        return
    detail_text, markup = await _render_task_detail(updated.id)
    await _answer_with_markdown(message, f"任务已更新：\n{detail_text}", reply_markup=markup)


@router.message(Command("task_delete"))
async def on_task_delete(message: Message) -> None:
    args = _extract_command_args(message.text)
    if not args:
        await _answer_with_markdown(message, "用法：/task_delete TASK_0001 [restore=yes]")
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
    action = "已恢复" if restore else "已归档"
    detail_text, markup = await _render_task_detail(updated.id)
    await _answer_with_markdown(message, f"任务{action}：\n{detail_text}", reply_markup=markup)


@router.callback_query(F.data.startswith("task:status:"))
async def on_status_callback(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("回调参数错误", show_alert=True)
        return
    _, _, task_id, status_value = parts
    status = _normalize_status(status_value)
    if status is None:
        await callback.answer("无效的状态", show_alert=True)
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
    message = callback.message
    if updated.status == "done":
        # done 是长链路场景：只要状态写库成功，立即返回成功提示；
        # 详情按钮刷新与并行清理全部放到后台，避免用户感知到“点击后卡住”。
        asyncio.create_task(_finalize_done_status_update_safely(message, updated))
        await _answer_callback_safely(callback, "状态已更新")
        return
    detail_text, markup = await _render_task_detail(updated.id)
    if message is None:
        await callback.answer("无法定位原消息", show_alert=True)
        return
    detail_state = TaskViewState(kind="detail", data={"task_id": updated.id})
    if _is_text_too_long_for_telegram(detail_text):
        sent, edited = await _send_task_detail_as_attachment(
            message,
            task_id=updated.id,
            detail_text=detail_text,
            reply_markup=markup,
            prefer_edit=True,
        )
        if edited:
            _set_task_view_context(message, detail_state)
            await callback.answer("状态已更新")
            return
        if sent is not None:
            _init_task_view_context(sent, detail_state)
            await callback.answer("状态已更新")
            return
        await callback.answer("状态更新但消息刷新失败", show_alert=True)
        return
    if await _try_edit_message(message, detail_text, reply_markup=markup):
        _set_task_view_context(message, detail_state)
        await callback.answer("状态已更新")
        return
    sent = await _answer_with_markdown(message, detail_text, reply_markup=markup)
    if sent is not None:
        _init_task_view_context(sent, detail_state)
        await callback.answer("状态已更新")
        return
    await callback.answer("状态更新但消息刷新失败", show_alert=True)


@router.callback_query(F.data.startswith("task:summary:"))
async def on_task_summary_request(callback: CallbackQuery) -> None:
    """请求模型生成任务摘要。"""

    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("回调参数错误", show_alert=True)
        return
    _, _, task_id = parts
    task = await TASK_SERVICE.get_task(task_id)
    if task is None:
        await callback.answer("任务不存在", show_alert=True)
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
    await callback.answer("已请求模型生成摘要")
    if callback.message:
        lines = ["已向模型发送摘要请求，请等待回复。"]
        if status_changed:
            lines.append("任务状态已自动更新为“测试”。")
        await callback.message.answer(
            "\n".join(lines),
            reply_markup=_build_worker_main_keyboard(),
        )


@router.message(
    F.text.lower().startswith("/task_summary_request_")
    | F.text.lower().startswith("/tasksummaryrequest")
)
async def on_task_summary_command(message: Message) -> None:
    """命令式触发任务摘要生成。"""

    raw_text = (message.text or "").strip()
    if not raw_text:
        await message.answer("请提供任务 ID，例如：/task_summary_request_TASK_0001")
        return
    token = raw_text.split()[0]
    command_part, _, _bot = token.partition("@")
    lowered = command_part.lower()
    prefix = next(
        (alias for alias in SUMMARY_COMMAND_ALIASES if lowered.startswith(alias)),
        None,
    )
    if prefix is None:
        await message.answer("请提供任务 ID，例如：/task_summary_request_TASK_0001")
        return
    task_segment = command_part[len(prefix) :].strip()
    if not task_segment:
        await message.answer("请提供任务 ID，例如：/task_summary_request_TASK_0001")
        return
    normalized_task_id = _normalize_task_id(task_segment)
    if not normalized_task_id:
        await message.answer(TASK_ID_USAGE_TIP)
        return
    task = await TASK_SERVICE.get_task(normalized_task_id)
    if task is None:
        await message.answer("任务不存在", reply_markup=_build_worker_main_keyboard())
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
    lines = ["已向模型发送摘要请求，请等待回复。"]
    if status_changed:
        lines.append("任务状态已自动更新为“测试”。")
    await message.answer("\n".join(lines), reply_markup=_build_worker_main_keyboard())


@router.callback_query(F.data.startswith("task:bug_report:"))
async def on_task_bug_report(callback: CallbackQuery, state: FSMContext) -> None:
    """从任务详情发起“报告缺陷（创建缺陷任务）”流程。"""

    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("回调参数错误", show_alert=True)
        return
    _, _, task_id = parts
    task = await TASK_SERVICE.get_task(task_id)
    if task is None:
        await callback.answer("任务不存在", show_alert=True)
        return
    await state.clear()
    reporter = _actor_from_callback(callback)
    await state.update_data(
        origin_task_id=task.id,
        reporter=reporter,
        title="",
        reproduction="",
        expected_result="",
        pending_attachments=[],
        processed_media_groups=[],
    )
    await state.set_state(TaskDefectReportStates.waiting_title)
    await callback.answer("请输入缺陷标题")
    if callback.message:
        await callback.message.answer(
            _build_defect_report_intro(task),
            reply_markup=_build_task_desc_cancel_keyboard(),
        )


@router.message(TaskDefectReportStates.waiting_title)
async def on_task_defect_report_title(message: Message, state: FSMContext) -> None:
    """报告缺陷：处理缺陷任务标题输入。"""

    title = (message.text or "").strip()
    if _is_cancel_message(title):
        await state.clear()
        await message.answer("已取消创建缺陷任务。", reply_markup=_build_worker_main_keyboard())
        return
    if not title:
        await message.answer("缺陷标题不能为空，请重新输入：", reply_markup=_build_task_desc_cancel_keyboard())
        return
    await state.update_data(
        title=title,
        processed_media_groups=[],
    )
    await state.set_state(TaskDefectReportStates.waiting_reproduction)
    await message.answer(
        "请输入复现步骤（可选），可直接发送图片/文件作为附件；若暂无可发送“跳过”继续（仅发送附件也会进入下一步）：",
        reply_markup=_build_description_keyboard(),
    )


@router.message(TaskDefectReportStates.waiting_reproduction)
async def on_task_defect_report_reproduction(message: Message, state: FSMContext) -> None:
    """报告缺陷：处理复现步骤输入，并暂存附件。"""

    data = await state.get_data()
    attachment_dir = _attachment_dir_for_message(message)
    processed_groups = set(data.get("processed_media_groups") or [])
    saved_attachments, text_part, processed_groups = await _collect_generic_media_group(
        message,
        attachment_dir,
        processed=processed_groups,
    )
    # 媒体组会触发多次 handler，若本次调用已被其他消息消费则直接忽略，避免重复追加附件。
    if message.media_group_id and not saved_attachments and not text_part:
        return
    if message.media_group_id:
        await state.update_data(processed_media_groups=list(processed_groups))
    if saved_attachments:
        pending = list(data.get("pending_attachments") or [])
        pending.extend(_serialize_saved_attachment(item) for item in saved_attachments)
        await state.update_data(pending_attachments=pending)
    raw_text = (text_part or "").strip() or (message.text or "").strip() or (message.caption or "").strip()
    trimmed = raw_text.strip()
    # 复现步骤非必填：用户可选择“跳过”继续录入期望结果。
    options = [SKIP_TEXT, "取消"]
    resolved = _resolve_reply_choice(trimmed, options=options)
    if resolved == "取消" or _is_cancel_message(resolved):
        await state.clear()
        await message.answer("已取消创建缺陷任务。", reply_markup=_build_worker_main_keyboard())
        return
    is_skip = resolved == SKIP_TEXT or _is_skip_message(resolved)
    if is_skip:
        trimmed = ""
    if not trimmed and not is_skip and not saved_attachments:
        # 用户既没输入文字也没发送附件时，继续提示可补充复现步骤或跳过。
        await message.answer(
            "复现步骤可选：可继续输入步骤（可同时发送附件），或发送“跳过”继续录入期望结果：",
            reply_markup=_build_description_keyboard(),
        )
        return
    reproduction = trimmed
    if len(reproduction) > DESCRIPTION_MAX_LENGTH:
        # 复现步骤超长：自动落盘为附件，写入占位文本并继续流程。
        attachment = _persist_text_paste_as_attachment(message, reproduction)
        pending = list(data.get("pending_attachments") or [])
        pending.append(_serialize_saved_attachment(attachment))
        await state.update_data(pending_attachments=pending)
        reproduction = _build_overlong_text_placeholder("复现步骤")
    await state.update_data(reproduction=reproduction)
    await state.set_state(TaskDefectReportStates.waiting_expected_result)
    await message.answer(
        "请输入期望结果（可选），可直接发送图片/文件作为附件；若暂无可发送“跳过”继续（仅发送附件也会进入下一步）：",
        reply_markup=_build_description_keyboard(),
    )


@router.message(TaskDefectReportStates.waiting_expected_result)
async def on_task_defect_report_expected_result(message: Message, state: FSMContext) -> None:
    """报告缺陷：处理期望结果输入，并进入确认阶段。"""

    data = await state.get_data()
    attachment_dir = _attachment_dir_for_message(message)
    processed_groups = set(data.get("processed_media_groups") or [])
    saved_attachments, text_part, processed_groups = await _collect_generic_media_group(
        message,
        attachment_dir,
        processed=processed_groups,
    )
    # 媒体组会触发多次 handler，若本次调用已被其他消息消费则直接忽略，避免重复追加附件。
    if message.media_group_id and not saved_attachments and not text_part:
        return
    if message.media_group_id:
        await state.update_data(processed_media_groups=list(processed_groups))
    if saved_attachments:
        pending = list(data.get("pending_attachments") or [])
        pending.extend(_serialize_saved_attachment(item) for item in saved_attachments)
        await state.update_data(pending_attachments=pending)
    raw_text = (text_part or "").strip() or (message.text or "").strip() or (message.caption or "").strip()
    trimmed = raw_text.strip()
    options = [SKIP_TEXT, "取消"]
    resolved = _resolve_reply_choice(trimmed, options=options)
    if resolved == "取消" or _is_cancel_message(resolved):
        await state.clear()
        await message.answer("已取消创建缺陷任务。", reply_markup=_build_worker_main_keyboard())
        return
    is_skip = resolved == SKIP_TEXT or _is_skip_message(resolved)
    if is_skip:
        trimmed = ""
    if not trimmed and not is_skip and not saved_attachments:
        await message.answer(
            "期望结果可选：可继续输入结果（可同时发送附件），或发送“跳过”进入确认创建：",
            reply_markup=_build_description_keyboard(),
        )
        return
    expected_result = trimmed
    if len(expected_result) > DESCRIPTION_MAX_LENGTH:
        # 期望结果超长：自动落盘为附件，写入占位文本并继续流程。
        attachment = _persist_text_paste_as_attachment(message, expected_result)
        pending = list(data.get("pending_attachments") or [])
        pending.append(_serialize_saved_attachment(attachment))
        await state.update_data(pending_attachments=pending)
        expected_result = _build_overlong_text_placeholder("期望结果")
    await state.update_data(expected_result=expected_result)
    await state.set_state(TaskDefectReportStates.waiting_confirm)
    data = await state.get_data()
    origin_task_id = data.get("origin_task_id")
    origin_task = await TASK_SERVICE.get_task(origin_task_id) if origin_task_id else None
    summary_lines = _build_defect_confirm_summary_lines(
        title=(data.get("title") or "").strip(),
        origin_task=origin_task,
        origin_task_id=origin_task_id,
        reproduction=data.get("reproduction"),
        expected_result=expected_result,
        pending_attachments=data.get("pending_attachments") or [],
    )
    await message.answer("\n".join(summary_lines), reply_markup=_build_worker_main_keyboard())
    await message.answer("是否创建该缺陷任务？", reply_markup=_build_confirm_keyboard())


@router.message(TaskDefectReportStates.waiting_confirm)
async def on_task_defect_report_confirm(message: Message, state: FSMContext) -> None:
    """报告缺陷：确认创建缺陷任务。"""

    options = ["✅ 确认创建", "❌ 取消"]
    resolved = _resolve_reply_choice(message.text, options=options)
    stripped_token = _strip_number_prefix((message.text or "").strip())
    lowered = stripped_token.lower()

    # 支持确认阶段继续补充附件/文本
    attachment_dir = _attachment_dir_for_message(message)
    data = await state.get_data()
    processed_groups = set(data.get("processed_media_groups") or [])
    extra_attachments, text_part, processed_groups = await _collect_generic_media_group(
        message,
        attachment_dir,
        processed=processed_groups,
    )
    if message.media_group_id and not extra_attachments and not text_part:
        return
    if message.media_group_id:
        await state.update_data(processed_media_groups=list(processed_groups))

    extra_text = _normalize_choice_token(text_part or message.text or "")
    is_cancel = resolved == options[1] or lowered == "取消"
    is_confirm = resolved == options[0] or lowered in {"确认", "确认创建"}

    if extra_attachments or (extra_text and not is_cancel and not is_confirm):
        pending = list(data.get("pending_attachments") or [])
        if extra_attachments:
            pending.extend(_serialize_saved_attachment(item) for item in extra_attachments)
        expected_result = data.get("expected_result") or ""
        if extra_text and not is_confirm and not is_cancel:
            trimmed_extra = extra_text.strip()
            if trimmed_extra:
                if len(trimmed_extra) > DESCRIPTION_MAX_LENGTH:
                    attachment = _persist_text_paste_as_attachment(message, trimmed_extra)
                    pending.append(_serialize_saved_attachment(attachment))
                    placeholder = _build_overlong_text_placeholder("补充期望结果")
                    expected_result = f"{expected_result}\n{placeholder}" if expected_result else placeholder
                else:
                    expected_result = f"{expected_result}\n{trimmed_extra}" if expected_result else trimmed_extra
        # 若是媒体组，统一使用合并后的文本，避免遗漏 caption
        if text_part and not extra_text:
            trimmed_part = text_part.strip()
            if trimmed_part:
                if len(trimmed_part) > DESCRIPTION_MAX_LENGTH:
                    attachment = _persist_text_paste_as_attachment(message, trimmed_part)
                    pending.append(_serialize_saved_attachment(attachment))
                    placeholder = _build_overlong_text_placeholder("补充期望结果")
                    expected_result = f"{expected_result}\n{placeholder}" if expected_result else placeholder
                else:
                    expected_result = f"{expected_result}\n{trimmed_part}" if expected_result else trimmed_part
        await state.update_data(pending_attachments=pending, expected_result=expected_result)
        data = await state.get_data()
        origin_task_id = data.get("origin_task_id")
        origin_task = await TASK_SERVICE.get_task(origin_task_id) if origin_task_id else None
        updated_lines = _build_defect_confirm_summary_lines(
            title=(data.get("title") or "").strip(),
            origin_task=origin_task,
            origin_task_id=origin_task_id,
            reproduction=data.get("reproduction"),
            expected_result=expected_result,
            pending_attachments=pending,
        )
        await message.answer(
            "已记录补充的期望结果/附件，请继续选择“确认创建”或“取消”。\n" + "\n".join(updated_lines),
            reply_markup=_build_confirm_keyboard(),
        )
        return

    if is_cancel:
        await state.clear()
        await message.answer("已取消创建缺陷任务。", reply_markup=ReplyKeyboardRemove())
        await message.answer("已返回主菜单。", reply_markup=_build_worker_main_keyboard())
        return

    if not is_confirm:
        await message.answer(
            "请选择“确认创建”或“取消”，可直接输入编号或点击键盘按钮：",
            reply_markup=_build_confirm_keyboard(),
        )
        return

    data = await state.get_data()
    origin_task_id = data.get("origin_task_id")
    title = (data.get("title") or "").strip()
    reproduction = (data.get("reproduction") or "").strip()
    expected_result = (data.get("expected_result") or "").strip()
    description = _build_defect_description(reproduction, expected_result)
    reporter = data.get("reporter") or _actor_from_message(message)
    # 复现步骤与期望结果均可为空，仅校验关键上下文与标题。
    if not origin_task_id or not title:
        await state.clear()
        await message.answer("会话已失效，请重新操作。", reply_markup=_build_worker_main_keyboard())
        return
    origin_task = await TASK_SERVICE.get_task(origin_task_id)
    if origin_task is None:
        await state.clear()
        await message.answer("触发任务不存在，已取消创建缺陷任务。", reply_markup=_build_worker_main_keyboard())
        return

    defect_task = await TASK_SERVICE.create_root_task(
        title=title,
        status=TASK_STATUSES[0],
        priority=DEFAULT_PRIORITY,
        task_type="defect",
        tags=(),
        due_date=None,
        description=description,
        related_task_id=origin_task.id,
        actor=reporter,
    )
    pending_attachments = data.get("pending_attachments") or []
    if pending_attachments:
        await _bind_serialized_attachments(defect_task, pending_attachments, actor=reporter)

    # 在触发任务上留下“报告缺陷”历史记录，便于追溯新创建的缺陷任务。
    await _log_task_action(
        origin_task.id,
        action="bug_report",
        actor=reporter,
        new_value=description[:HISTORY_DISPLAY_VALUE_LIMIT],
        payload={
            "has_reproduction": False,
            "has_logs": False,
            "created_defect_task_id": defect_task.id,
            "defect_title": title,
            "defect_task_id": defect_task.id,
            "reporter": reporter,
        },
    )

    await state.clear()
    detail_text, markup = await _render_task_detail(defect_task.id)
    await message.answer("缺陷任务已创建。", reply_markup=_build_worker_main_keyboard())
    await _answer_with_markdown(message, f"缺陷任务详情：\n{detail_text}", reply_markup=markup)


@router.message(TaskBugReportStates.waiting_description)
async def on_task_bug_description(message: Message, state: FSMContext) -> None:
    """处理缺陷描述输入。"""

    if _is_cancel_message(message.text):
        await state.clear()
        await message.answer("已取消缺陷上报。", reply_markup=_build_worker_main_keyboard())
        return
    data = await state.get_data()
    processed_groups = set(data.get("processed_media_groups") or [])
    task_id = data.get("task_id")
    if not task_id:
        await state.clear()
        await message.answer("任务信息缺失，流程已终止。", reply_markup=_build_worker_main_keyboard())
        return
    task = await TASK_SERVICE.get_task(task_id)
    if task is None:
        await state.clear()
        await message.answer("任务不存在，已取消缺陷上报。", reply_markup=_build_worker_main_keyboard())
        return
    actor = data.get("reporter") or _actor_from_message(message)
    attachment_dir = _attachment_dir_for_message(message)
    saved_attachments, text_part = await _collect_bug_media_group(message, attachment_dir)
    media_group_id = message.media_group_id
    if media_group_id:
        async with BUG_MEDIA_GROUP_LOCK:
            if media_group_id in BUG_MEDIA_GROUP_PROCESSED:
                return
            BUG_MEDIA_GROUP_PROCESSED.add(media_group_id)
        processed_groups.add(media_group_id)
        await state.update_data(processed_media_groups=list(processed_groups))
    if saved_attachments:
        serialized = [_serialize_saved_attachment(item) for item in saved_attachments]
        await _bind_serialized_attachments(task, serialized, actor=actor)
    content = _collect_message_payload(message, saved_attachments, text_override=text_part)
    if not content:
        await message.answer(
            "缺陷描述不能为空，请重新输入：",
            reply_markup=_build_description_keyboard(),
        )
        return
    await state.update_data(
        description=content,
        reporter=actor,
    )
    await state.set_state(TaskBugReportStates.waiting_reproduction)
    await message.answer(_build_bug_repro_prompt(), reply_markup=_build_description_keyboard())


@router.message(TaskBugReportStates.waiting_reproduction)
async def on_task_bug_reproduction(message: Message, state: FSMContext) -> None:
    """处理复现步骤输入。"""

    if _is_cancel_message(message.text):
        await state.clear()
        await message.answer("已取消缺陷上报。", reply_markup=_build_worker_main_keyboard())
        return
    options = [SKIP_TEXT, "取消"]
    resolved = _resolve_reply_choice(message.text or "", options=options)
    reproduction = ""
    data = await state.get_data()
    processed_groups = set(data.get("processed_media_groups") or [])
    task_id = data.get("task_id")
    attachment_dir = _attachment_dir_for_message(message)
    saved_attachments, text_part = await _collect_bug_media_group(message, attachment_dir)
    media_group_id = message.media_group_id
    if media_group_id:
        async with BUG_MEDIA_GROUP_LOCK:
            if media_group_id in BUG_MEDIA_GROUP_PROCESSED:
                return
            BUG_MEDIA_GROUP_PROCESSED.add(media_group_id)
        processed_groups.add(media_group_id)
        await state.update_data(processed_media_groups=list(processed_groups))
    if saved_attachments and task_id:
        task = await TASK_SERVICE.get_task(task_id)
        if task:
            actor = data.get("reporter") or _actor_from_message(message)
            serialized = [_serialize_saved_attachment(item) for item in saved_attachments]
            await _bind_serialized_attachments(task, serialized, actor=actor)
    if resolved not in {SKIP_TEXT, "取消"}:
        reproduction = _collect_message_payload(message, saved_attachments, text_override=text_part)
    await state.update_data(reproduction=reproduction)
    await state.set_state(TaskBugReportStates.waiting_logs)
    await message.answer(_build_bug_log_prompt(), reply_markup=_build_description_keyboard())


@router.message(TaskBugReportStates.waiting_logs)
async def on_task_bug_logs(message: Message, state: FSMContext) -> None:
    """处理日志信息输入。"""

    if _is_cancel_message(message.text):
        await state.clear()
        await message.answer("已取消缺陷上报。", reply_markup=_build_worker_main_keyboard())
        return
    options = [SKIP_TEXT, "取消"]
    resolved = _resolve_reply_choice(message.text or "", options=options)
    data = await state.get_data()
    processed_groups = set(data.get("processed_media_groups") or [])
    task_id = data.get("task_id")
    if not task_id:
        await state.clear()
        await message.answer("任务信息缺失，流程已终止。", reply_markup=_build_worker_main_keyboard())
        return
    task = await TASK_SERVICE.get_task(task_id)
    if task is None:
        await state.clear()
        await message.answer("任务不存在，已取消缺陷上报。", reply_markup=_build_worker_main_keyboard())
        return
    actor = data.get("reporter") or _actor_from_message(message)
    attachment_dir = _attachment_dir_for_message(message)
    saved_attachments, text_part = await _collect_bug_media_group(message, attachment_dir)
    media_group_id = message.media_group_id
    if media_group_id:
        async with BUG_MEDIA_GROUP_LOCK:
            if media_group_id in BUG_MEDIA_GROUP_PROCESSED:
                return
            BUG_MEDIA_GROUP_PROCESSED.add(media_group_id)
        processed_groups.add(media_group_id)
        await state.update_data(processed_media_groups=list(processed_groups))
    logs = ""
    if resolved not in {SKIP_TEXT, "取消"}:
        logs = _collect_message_payload(message, saved_attachments, text_override=text_part)
    if saved_attachments:
        serialized = [_serialize_saved_attachment(item) for item in saved_attachments]
        await _bind_serialized_attachments(task, serialized, actor=actor)
    description = data.get("description", "")
    reproduction = data.get("reproduction", "")
    reporter = actor
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
        f"请确认以下缺陷信息：\n{preview}",
        reply_markup=_build_bug_confirm_keyboard(),
    )


@router.message(TaskBugReportStates.waiting_confirm)
async def on_task_bug_confirm(message: Message, state: FSMContext) -> None:
    """确认并写入缺陷记录。"""

    if _is_cancel_message(message.text):
        await state.clear()
        await message.answer("已取消缺陷上报。", reply_markup=_build_worker_main_keyboard())
        return
    resolved = _resolve_reply_choice(message.text or "", options=["✅ 确认提交", "❌ 取消"])
    normalized = _normalize_choice_token(message.text or "")
    is_cancel = resolved == "❌ 取消" or normalized == "取消"
    is_confirm = resolved == "✅ 确认提交"
    data = await state.get_data()
    task_id = data.get("task_id")
    if not task_id:
        await state.clear()
        await message.answer("任务信息缺失，流程已终止。", reply_markup=_build_worker_main_keyboard())
        return
    task = await TASK_SERVICE.get_task(task_id)
    if task is None:
        await state.clear()
        await message.answer("已取消缺陷上报。", reply_markup=_build_worker_main_keyboard())
        return
    attachment_dir = _attachment_dir_for_message(message)
    processed_groups = set(data.get("processed_media_groups") or [])
    extra_attachments, text_part = await _collect_bug_media_group(message, attachment_dir)
    media_group_id = message.media_group_id
    if media_group_id:
        async with BUG_MEDIA_GROUP_LOCK:
            if media_group_id in BUG_MEDIA_GROUP_PROCESSED:
                return
            BUG_MEDIA_GROUP_PROCESSED.add(media_group_id)
        processed_groups.add(media_group_id)
        await state.update_data(processed_media_groups=list(processed_groups))
    reporter = data.get("reporter") or _actor_from_message(message)
    if extra_attachments:
        serialized = [_serialize_saved_attachment(item) for item in extra_attachments]
        await _bind_serialized_attachments(task, serialized, actor=reporter)
    if is_cancel:
        await state.clear()
        await message.answer("已取消缺陷上报。", reply_markup=_build_worker_main_keyboard())
        return
    if extra_attachments or (normalized and not is_confirm):
        # 用户继续补充附件或文字，刷新预览后等待确认
        updated_logs = data.get("logs", "")
        if normalized and not is_confirm:
            updated_logs = f"{updated_logs}\n{normalized}" if updated_logs else normalized
        # 若是媒体组，统一使用合并后的文本，避免遗漏 caption
        if text_part and not normalized:
            updated_logs = f"{updated_logs}\n{text_part}" if updated_logs else text_part
        await state.update_data(logs=updated_logs)
        description = data.get("description", "")
        reproduction = data.get("reproduction", "")
        preview = _build_bug_preview_text(
            task=task,
            description=description,
            reproduction=reproduction,
            logs=updated_logs,
            reporter=reporter,
        )
        await message.answer(
            f"已记录补充的附件/日志，请再次确认：\n{preview}",
            reply_markup=_build_bug_confirm_keyboard(),
        )
        return
    if not is_confirm:
        await message.answer("请回复“✅ 确认提交”或输入“取消”。", reply_markup=_build_bug_confirm_keyboard())
        return
    description = data.get("description", "")
    reproduction = data.get("reproduction", "")
    logs = data.get("logs", "")
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
        await callback.answer("回调参数错误", show_alert=True)
        return
    _, _, task_id = parts
    await state.clear()
    await state.update_data(task_id=task_id)
    await state.set_state(TaskNoteStates.waiting_content)
    await callback.answer("请输入备注内容")
    await callback.message.answer("请输入备注内容：")


@router.callback_query(F.data.startswith("task:add_child:"))
async def on_add_child_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("子任务功能已下线", show_alert=True)
    if callback.message:
        await callback.message.answer(
            "子任务功能已下线，历史子任务已自动归档。请使用 /task_new 创建新的任务。",
            reply_markup=_build_worker_main_keyboard(),
        )


@router.callback_query(F.data.startswith("task:list_children:"))
async def on_list_children_callback(callback: CallbackQuery) -> None:
    await callback.answer("子任务功能已下线", show_alert=True)
    if callback.message:
        await callback.message.answer(
            "子任务功能已下线，历史子任务已自动归档。请使用 /task_new 创建新的任务。",
            reply_markup=_build_worker_main_keyboard(),
        )


@router.callback_query(F.data.startswith("task:detail:"))
async def on_task_detail_callback(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("回调参数错误", show_alert=True)
        return
    _, _, task_id = parts
    message = callback.message
    if message is None:
        await callback.answer("无法定位原消息", show_alert=True)
        return
    try:
        detail_text, markup = await _render_task_detail(task_id)
    except ValueError:
        await callback.answer("任务不存在", show_alert=True)
        return
    await callback.answer()
    failure_markup = _build_task_detail_failure_keyboard(task_id)
    detail_state = TaskViewState(kind="detail", data={"task_id": task_id})
    chat = getattr(message, "chat", None)
    base_state = _peek_task_view(chat.id, message.message_id) if chat else None
    if base_state is None:
        if _is_text_too_long_for_telegram(detail_text):
            sent, _edited = await _send_task_detail_as_attachment(
                message,
                task_id=task_id,
                detail_text=detail_text,
                reply_markup=markup,
                prefer_edit=False,
            )
        else:
            sent = await _answer_with_markdown(message, detail_text, reply_markup=markup)
        if sent is not None:
            _init_task_view_context(sent, detail_state)
        else:
            # 兜底：给出可行动入口，避免用户卡死在“打不开详情”的状态。
            await message.answer(
                "\n".join(
                    [
                        "⚠️ 任务详情显示失败。",
                        f"任务ID: {task_id}",
                        "可能原因：内容过长、格式异常或网络波动。",
                        "你可以点击“重试”，或直接“删除（归档）（可恢复）”。",
                    ]
                ),
                reply_markup=failure_markup,
            )
        return
    if _is_text_too_long_for_telegram(detail_text):
        sent, edited = await _send_task_detail_as_attachment(
            message,
            task_id=task_id,
            detail_text=detail_text,
            reply_markup=markup,
            prefer_edit=True,
        )
        if edited:
            _push_detail_view(message, task_id)
            return
        if sent is not None:
            _init_task_view_context(sent, detail_state)
            return
        await message.answer(
            "\n".join(
                [
                    "⚠️ 任务详情显示失败。",
                    f"任务ID: {task_id}",
                    "可能原因：内容过长、格式异常或网络波动。",
                    "你可以点击“重试”，或直接“删除（归档）（可恢复）”。",
                ]
            ),
            reply_markup=failure_markup,
        )
        return
    if await _try_edit_message(message, detail_text, reply_markup=markup):
        _push_detail_view(message, task_id)
        return
    sent = await _answer_with_markdown(message, detail_text, reply_markup=markup)
    if sent is not None:
        _init_task_view_context(sent, detail_state)
    else:
        await message.answer(
            "\n".join(
                [
                    "⚠️ 任务详情显示失败。",
                    f"任务ID: {task_id}",
                    "可能原因：内容过长、格式异常或网络波动。",
                    "你可以点击“重试”，或直接“删除（归档）（可恢复）”。",
                ]
            ),
            reply_markup=failure_markup,
        )


async def _fallback_task_detail_back(callback: CallbackQuery) -> None:
    """当视图栈缺失时，回退到旧的 /task_list 触发方式。"""

    message = callback.message
    user = callback.from_user
    if message is None or user is None:
        await callback.answer("无法定位会话", show_alert=True)
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
    synthetic_message_id = _build_internal_synthetic_message_id(message.message_id)
    synthetic_message = message.model_copy(
        update={
            "message_id": synthetic_message_id,
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
    _mark_text_paste_synthetic_message(int(message.chat.id), synthetic_message_id)
    await dp.feed_update(bot, update)


@router.callback_query(F.data == TASK_DETAIL_BACK_CALLBACK)
async def on_task_detail_back(callback: CallbackQuery) -> None:
    message = callback.message
    if message is None:
        await callback.answer("无法定位会话", show_alert=True)
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
    except Exception as exc:  # pragma: no cover - 极端情况下进入兜底
        worker_log.warning(
            "恢复任务视图失败：%s",
            exc,
            extra={"chat": message.chat.id, "message": message.message_id},
        )
        await _fallback_task_detail_back(callback)
        return
    if await _try_edit_message(message, text, reply_markup=markup):
        await callback.answer("已返回任务列表")
        return
    _clear_task_view(chat.id, message.message_id)
    sent = await _answer_with_markdown(message, text, reply_markup=markup)
    if sent is not None:
        cloned_state = TaskViewState(kind=prev_state.kind, data=dict(prev_state.data))
        _init_task_view_context(sent, cloned_state)
        await callback.answer("已返回任务列表")
        return
    await _fallback_task_detail_back(callback)


@router.callback_query(F.data.startswith(f"{TASK_DETAIL_DELETE_PROMPT_CALLBACK}:"))
async def on_task_detail_delete_prompt(callback: CallbackQuery) -> None:
    """任务详情：点击“删除（归档）”后展示二次确认（仅更新键盘，避免超长文本导致 edit_text 失败）。"""

    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("回调参数错误", show_alert=True)
        return
    _, _, task_id = parts
    task = await TASK_SERVICE.get_task(task_id)
    if task is None:
        await callback.answer("任务不存在", show_alert=True)
        return
    message = callback.message
    if message is None:
        await callback.answer("无法定位原消息", show_alert=True)
        return

    try:
        await message.edit_reply_markup(reply_markup=_build_task_delete_confirm_keyboard(task_id))
    except TelegramBadRequest as exc:
        worker_log.info(
            "更新删除确认键盘失败：%s",
            exc,
            extra={"task_id": task_id, **_session_extra()},
        )
        await message.answer(
            f"⚠️ 即将删除（归档）任务 {task_id}，确认吗？（可恢复）",
            reply_markup=_build_task_delete_confirm_keyboard(task_id),
        )
    await callback.answer("请确认是否删除（归档）")


@router.callback_query(F.data.startswith(f"{TASK_DETAIL_DELETE_CONFIRM_CALLBACK}:"))
async def on_task_detail_delete_confirm(callback: CallbackQuery) -> None:
    """任务详情：确认删除（归档）并给出恢复提示。"""

    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("回调参数错误", show_alert=True)
        return
    _, _, task_id = parts
    actor = _actor_from_callback(callback)
    try:
        updated = await TASK_SERVICE.delete_task(task_id, actor=actor)
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    message = callback.message
    if message is None:
        await callback.answer("已归档（可恢复）")
        return

    # 删除成功后，恢复详情页的操作按钮，避免停留在确认态。
    try:
        _detail_text, markup = await _render_task_detail(updated.id)
        await message.edit_reply_markup(reply_markup=markup)
    except Exception as exc:  # pragma: no cover - 兜底保护
        worker_log.warning(
            "归档后刷新任务详情键盘失败：%s",
            exc,
            extra={"task_id": task_id, **_session_extra()},
        )

    await message.answer(
        "\n".join(
            [
                f"✅ 已归档任务 /{updated.id}（可恢复）。",
                f"恢复方式：/task_delete {updated.id} restore=yes",
            ]
        ),
        reply_markup=_build_worker_main_keyboard(),
    )
    await callback.answer("已归档（可恢复）")


@router.callback_query(F.data.startswith("task:toggle_archive:"))
async def on_toggle_archive(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("回调参数错误", show_alert=True)
        return
    _, _, task_id = parts
    task = await TASK_SERVICE.get_task(task_id)
    if task is None:
        await callback.answer("任务不存在", show_alert=True)
        return
    updated = await TASK_SERVICE.update_task(
        task_id,
        actor=_actor_from_message(callback.message),
        archived=not task.archived,
    )
    detail_text, markup = await _render_task_detail(updated.id)
    message = callback.message
    if message is None:
        await callback.answer("无法定位原消息", show_alert=True)
        return
    detail_state = TaskViewState(kind="detail", data={"task_id": updated.id})
    if _is_text_too_long_for_telegram(detail_text):
        sent, edited = await _send_task_detail_as_attachment(
            message,
            task_id=updated.id,
            detail_text=detail_text,
            reply_markup=markup,
            prefer_edit=True,
        )
        if edited:
            _set_task_view_context(message, detail_state)
            await callback.answer("已切换任务状态")
            return
        if sent is not None:
            _init_task_view_context(sent, detail_state)
            await callback.answer("已切换任务状态")
            return
        await callback.answer("状态已切换但消息刷新失败", show_alert=True)
        return
    if await _try_edit_message(message, detail_text, reply_markup=markup):
        _set_task_view_context(message, detail_state)
        await callback.answer("已切换任务状态")
        return
    sent = await _answer_with_markdown(message, detail_text, reply_markup=markup)
    if sent is not None:
        _init_task_view_context(sent, detail_state)
        await callback.answer("已切换任务状态")
        return
    await callback.answer("状态已切换但消息刷新失败", show_alert=True)


@router.callback_query(F.data.startswith("task:refresh:"))
async def on_refresh_callback(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("回调参数错误", show_alert=True)
        return
    _, _, task_id = parts
    message = callback.message
    if message is None:
        await callback.answer("无法定位原消息", show_alert=True)
        return
    try:
        detail_text, markup = await _render_task_detail(task_id)
    except ValueError:
        await callback.answer("任务不存在", show_alert=True)
        return
    detail_state = TaskViewState(kind="detail", data={"task_id": task_id})
    if _is_text_too_long_for_telegram(detail_text):
        sent, edited = await _send_task_detail_as_attachment(
            message,
            task_id=task_id,
            detail_text=detail_text,
            reply_markup=markup,
            prefer_edit=True,
        )
        if edited:
            _set_task_view_context(message, detail_state)
            await callback.answer("已刷新")
            return
        if sent is not None:
            _init_task_view_context(sent, detail_state)
            await callback.answer("已刷新")
            return
        await callback.answer("刷新失败", show_alert=True)
        return
    if await _try_edit_message(message, detail_text, reply_markup=markup):
        _set_task_view_context(message, detail_state)
        await callback.answer("已刷新")
        return
    sent = await _answer_with_markdown(message, detail_text, reply_markup=markup)
    if sent is not None:
        _init_task_view_context(sent, detail_state)
        await callback.answer("已刷新")
        return
    await callback.answer("刷新失败", show_alert=True)


@router.callback_query(F.data.startswith("task:edit:"))
async def on_edit_callback(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("回调参数错误", show_alert=True)
        return
    _, _, task_id = parts
    task = await TASK_SERVICE.get_task(task_id)
    if task is None:
        await callback.answer("任务不存在", show_alert=True)
        return
    await state.clear()
    await state.update_data(task_id=task_id, actor=_actor_from_message(callback.message))
    await state.set_state(TaskEditStates.waiting_field_choice)
    await callback.answer("请选择需要编辑的字段")
    await callback.message.answer("请选择需要修改的字段：", reply_markup=_build_edit_field_keyboard())


@router.message(TaskEditStates.waiting_field_choice)
async def on_edit_field_choice(message: Message, state: FSMContext) -> None:
    options = ["标题", "优先级", "类型", "描述", "状态", "取消"]
    resolved = _resolve_reply_choice(message.text, options=options)
    choice = resolved or (message.text or "").strip()
    mapping = {
        "标题": "title",
        "优先级": "priority",
        "类型": "task_type",
        "描述": "description",
    }
    if choice == "取消":
        await state.clear()
        await message.answer("已取消编辑", reply_markup=_build_worker_main_keyboard())
        return
    field = mapping.get(choice)
    if choice == "状态":
        await state.clear()
        await message.answer("请使用任务详情中的状态按钮进行切换。", reply_markup=_build_worker_main_keyboard())
        return
    if field is None:
        await message.answer("暂不支持该字段，请重新选择：", reply_markup=_build_edit_field_keyboard())
        return
    if field == "description":
        data = await state.get_data()
        task_id = data.get("task_id")
        if not task_id:
            await state.clear()
            await message.answer("任务信息缺失，已取消编辑。", reply_markup=_build_worker_main_keyboard())
            return
        task = await TASK_SERVICE.get_task(task_id)
        if task is None:
            await state.clear()
            await message.answer("任务不存在，已取消编辑。", reply_markup=_build_worker_main_keyboard())
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
        await message.answer("请输入新的优先级（1-5）：", reply_markup=_build_priority_keyboard())
    elif field == "task_type":
        await message.answer(
            "请选择新的任务类型（需求 / 缺陷 / 优化 / 风险）：",
            reply_markup=_build_task_type_keyboard(),
        )
    else:
        await message.answer("请输入新的值：", reply_markup=_build_worker_main_keyboard())


@router.message(TaskEditStates.waiting_new_value)
async def on_edit_new_value(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    task_id = data.get("task_id")
    field = data.get("field")
    if not task_id or not field:
        await state.clear()
        await message.answer("数据缺失，已取消编辑。", reply_markup=_build_worker_main_keyboard())
        return
    raw_text = message.text or ""
    text = raw_text.strip()
    resolved_task_type: Optional[str] = None
    if field == "task_type":
        task_type_options = [_format_task_type(task_type) for task_type in TASK_TYPES]
        task_type_options.append("取消")
        resolved_task_type = _resolve_reply_choice(raw_text, options=task_type_options)
        if resolved_task_type == "取消":
            await state.clear()
            await message.answer("已取消编辑", reply_markup=_build_worker_main_keyboard())
            return
    elif text == "取消":
        await state.clear()
        await message.answer("已取消编辑", reply_markup=_build_worker_main_keyboard())
        return

    update_kwargs: dict[str, Any] = {}
    pending_attachments: list[Mapping[str, str]] = []
    if field == "priority":
        priority_options = [str(i) for i in range(1, 6)]
        priority_options.append(SKIP_TEXT)
        resolved_priority = _resolve_reply_choice(raw_text, options=priority_options)
        if resolved_priority == SKIP_TEXT:
            await message.answer("优先级请输入 1-5 的数字：", reply_markup=_build_priority_keyboard())
            return
        candidate = resolved_priority or text
        try:
            value = int(candidate)
        except ValueError:
            await message.answer("优先级请输入 1-5 的数字：", reply_markup=_build_priority_keyboard())
            return
        value = max(1, min(value, 5))
        update_kwargs["priority"] = value
    elif field == "description":
        if len(text) > DESCRIPTION_MAX_LENGTH:
            # 描述超长：自动转为附件并写入占位文本，避免编辑流程被长度限制打断。
            attachment = _persist_text_paste_as_attachment(message, text)
            pending_attachments.append(_serialize_saved_attachment(attachment))
            update_kwargs["description"] = _build_overlong_text_placeholder("任务描述")
        else:
            update_kwargs["description"] = text
    elif field == "task_type":
        candidate = resolved_task_type or text
        task_type = _normalize_task_type(candidate)
        if task_type is None:
            await message.answer(
                "任务类型无效，请重新输入需求/缺陷/优化/风险：",
                reply_markup=_build_task_type_keyboard(),
            )
            return
        update_kwargs["task_type"] = task_type
    else:
        if not text:
            await message.answer("标题不能为空，请重新输入：", reply_markup=_build_worker_main_keyboard())
            return
        update_kwargs["title"] = text
    await state.clear()
    try:
        actor = _actor_from_message(message)
        updated = await TASK_SERVICE.update_task(
            task_id,
            actor=actor,
            title=update_kwargs.get("title"),
            priority=update_kwargs.get("priority"),
            task_type=update_kwargs.get("task_type"),
            description=update_kwargs.get("description"),
        )
        if pending_attachments:
            await _bind_serialized_attachments(updated, pending_attachments, actor=actor)
    except ValueError as exc:
        await message.answer(str(exc), reply_markup=_build_worker_main_keyboard())
        return
    detail_text, markup = await _render_task_detail(updated.id)
    await _answer_with_markdown(message, f"任务已更新：\n{detail_text}", reply_markup=markup)


@router.message(
    F.photo | F.document | F.video | F.audio | F.voice | F.animation | F.video_note
)
async def on_media_message(message: Message) -> None:
    """处理带附件的普通消息，将附件下载并拼接提示词。"""

    _auto_record_chat_id(message.chat.id)
    if await _handle_request_input_custom_text_message(message):
        return
    text_part = (message.caption or message.text or "").strip()

    if message.media_group_id:
        await _enqueue_media_group_message(message, text_part)
        return

    attachment_dir = _attachment_dir_for_message(message)
    attachments = await _collect_saved_attachments(message, attachment_dir)
    if not attachments and not text_part:
        await message.answer("未检测到可处理的附件或文字内容。")
        return
    prompt = _build_prompt_with_attachments(text_part, attachments)
    await _handle_prompt_dispatch(message, prompt)


@router.message(CommandStart())
async def on_start(m: Message):
    # 首次收到消息时自动记录 chat_id 到 state 文件
    _auto_record_chat_id(m.chat.id)

    await m.answer(
        (
            f"Hello, {m.from_user.full_name}！\n"
            "直接发送问题就能与模型对话，\n"
            "或使用任务功能来组织需求与执行记录。\n\n"
            "主菜单已准备好，祝你使用愉快！"
        ),
        reply_markup=_build_worker_main_keyboard(),
    )
    worker_log.info("收到 /start，chat_id=%s", m.chat.id, extra=_session_extra())
    if ENV_ISSUES:
        await m.answer(_format_env_issue_message())

@router.message(F.text)
async def on_text(m: Message, state: FSMContext):
    # 首次收到消息时自动记录 chat_id 到 state 文件
    _auto_record_chat_id(m.chat.id)

    if await _handle_request_input_custom_text_message(m):
        return

    raw_text = m.text or ""
    prompt = raw_text.strip()
    if not prompt:
        return await m.answer("请输入非空提示词")
    prefix_token = CHAT_PARALLEL_BRANCH_PREFIX_INPUTS.get(m.chat.id)
    if prefix_token:
        session = PARALLEL_LAUNCH_SESSIONS.get(prefix_token)
        if session is None:
            _clear_parallel_branch_prefix_input(chat_id=m.chat.id)
            await m.answer("分支前缀输入会话已失效，请重新发起并行创建。", reply_markup=_build_worker_main_keyboard())
            return
        if _is_cancel_message(prompt):
            session.branch_prefix = DEFAULT_PARALLEL_BRANCH_PREFIX
        else:
            normalized_prefix = normalize_parallel_branch_prefix(prompt)
            if not normalized_prefix:
                await m.answer(
                    f"分支前缀无效，请重新输入（例如 Sprint001）；发送“取消”将使用默认前缀 {DEFAULT_PARALLEL_BRANCH_PREFIX}。",
                    reply_markup=_build_parallel_branch_prefix_input_keyboard(),
                )
                return
            session.branch_prefix = normalized_prefix
        await _start_parallel_launch_session(session, trigger_message=session.origin_message or m)
        return
    prefixed_task_id, prefixed_prompt = _extract_task_prefixed_prompt(prompt)
    if prefixed_task_id and prefixed_prompt:
        session = await _get_active_parallel_session_for_task(prefixed_task_id)
        if session is None:
            await m.answer(f"/{prefixed_task_id} 当前没有活动中的并行会话。", reply_markup=_build_worker_main_keyboard())
            return
        await _handle_prompt_dispatch(
            m,
            prefixed_prompt,
            dispatch_context=_parallel_dispatch_context_from_session(session),
        )
        return
    reply_target = _consume_parallel_reply_target(m.chat.id)
    if reply_target:
        reply_task_id = _normalize_task_id(reply_target.get("task_id"))
        dispatch_context = reply_target.get("dispatch_context")
        if _is_cancel_message(prompt):
            await m.answer(
                f"已取消 /{reply_task_id} 回复模式。",
                reply_markup=_build_worker_main_keyboard(),
            )
            return
        if not isinstance(dispatch_context, ParallelDispatchContext):
            session = await _get_active_parallel_session_for_task(reply_task_id or "")
            if session is None:
                await m.answer(f"/{reply_task_id} 的并行会话已失效，请重新点击回复按钮。", reply_markup=_build_worker_main_keyboard())
                return
            dispatch_context = _parallel_dispatch_context_from_session(session)
        await _handle_prompt_dispatch(
            m,
            prompt,
            dispatch_context=dispatch_context,
        )
        return
    task_id_candidate = _normalize_task_id(prompt)
    if task_id_candidate:
        await _reply_task_detail_message(m, task_id_candidate)
        return
    if await _handle_command_trigger_message(m, prompt, state):
        return
    if prompt.startswith("/"):
        return
    await _handle_prompt_dispatch(m, prompt)


async def ensure_telegram_connectivity(bot: Bot, timeout: float = 30.0):
    """启动前校验 Telegram 连通性，便于快速定位代理/网络问题"""
    try:
        if hasattr(asyncio, "timeout"):
            async with asyncio.timeout(timeout):
                me = await bot.get_me()
        else:
            me = await asyncio.wait_for(bot.get_me(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise RuntimeError(f"在 {timeout} 秒内未能与 Telegram 成功握手") from exc
    except TelegramNetworkError as exc:
        raise RuntimeError("Telegram 网络请求失败，请检查代理或网络策略") from exc
    except ClientError as exc:
        raise RuntimeError("无法连接到代理或 Telegram，请检查代理配置") from exc
    else:
        worker_log.info(
            "Telegram 连接正常，Bot=%s (id=%s)",
            me.username,
            me.id,
            extra=_session_extra(),
        )
        _record_worker_identity(me.username, me.id)
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
                "设置 Bot 命令失败：%s",
                exc,
                extra={**_session_extra(), "scope": label},
            )
        else:
            worker_log.info(
                "Bot 命令已同步",
                extra={**_session_extra(), "scope": label},
            )


async def _ensure_worker_menu_button(bot: Bot) -> None:
    """确保 worker 侧聊天菜单按钮文本为任务列表入口。"""
    try:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonCommands(text=WORKER_MENU_BUTTON_TEXT),
        )
    except TelegramBadRequest as exc:
        worker_log.warning(
            "设置聊天菜单失败：%s",
            exc,
            extra=_session_extra(),
        )
    else:
        worker_log.info(
            "聊天菜单已同步",
            extra={**_session_extra(), "text": WORKER_MENU_BUTTON_TEXT},
        )

async def main():
    global _bot, CHAT_LONG_POLL_LOCK
    # 初始化长轮询锁
    CHAT_LONG_POLL_LOCK = asyncio.Lock()
    _bot = build_bot()
    try:
        await ensure_telegram_connectivity(_bot)
    except Exception as exc:
        worker_log.error("Telegram 连通性检查失败：%s", exc, extra=_session_extra())
        if _bot:
            await _bot.session.close()
        raise SystemExit(1)
    try:
        await TASK_SERVICE.initialize()
    except Exception as exc:
        worker_log.error("任务数据库初始化失败：%s", exc, extra=_session_extra())
        if _bot:
            await _bot.session.close()
        raise SystemExit(1)
    try:
        await COMMAND_SERVICE.initialize()
    except Exception as exc:
        worker_log.error("命令数据库初始化失败：%s", exc, extra=_session_extra())
        if _bot:
            await _bot.session.close()
        raise SystemExit(1)
    try:
        await PARALLEL_SESSION_STORE.initialize()
    except Exception as exc:
        worker_log.error("并行会话数据库初始化失败：%s", exc, extra=_session_extra())
        if _bot:
            await _bot.session.close()
        raise SystemExit(1)
    try:
        await _reconcile_codex_trusted_paths()
    except Exception as exc:  # noqa: BLE001
        worker_log.warning("Codex trusted 路径启动对账失败：%s", exc, extra=_session_extra())
    try:
        await _ensure_primary_workdir_codex_trust()
    except Exception as exc:  # noqa: BLE001
        worker_log.warning("主项目目录 Codex trusted 检查失败：%s", exc, extra=_session_extra())
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
