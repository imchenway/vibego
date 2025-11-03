"""统一日志配置工具。

提供 master / worker 共用的 logging 配置，确保写入同一文件。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from logging.handlers import WatchedFileHandler
from pathlib import Path
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(agent)s] [%(project)s] [%(model)s] [%(session)s] : %(message)s"
_CONFIGURED = False


class ContextLoggerAdapter(logging.LoggerAdapter):
    """支持 per-call extra 覆盖的 LoggerAdapter。"""

    def process(self, msg: str, kwargs: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
        """合并默认 extra 与调用者提供的 extra，避免上下文丢失。"""

        provided: Optional[Dict[str, Any]] = kwargs.pop("extra", None)
        merged: Dict[str, Any] = dict(self.extra)
        if provided:
            merged.update(provided)
        kwargs["extra"] = merged
        return msg, kwargs


def _default_config_root() -> Path:
    """按照环境变量与 XDG 规范解析配置根目录。"""

    override = os.environ.get("MASTER_CONFIG_ROOT") or os.environ.get("VIBEGO_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    xdg_base = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_base).expanduser() if xdg_base else Path.home() / ".config"
    return base / "vibego"


def _resolve_log_file() -> Path:
    """根据环境变量确定日志文件位置。"""
    candidate = os.environ.get("LOG_FILE")
    default_path = _default_config_root() / "logs/vibe.log"
    target = Path(candidate).expanduser() if candidate else default_path
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _determine_level(level_name: str) -> int:
    """解析日志等级字符串，无法识别时回退为 INFO。"""

    level = getattr(logging, level_name.upper(), None)
    if isinstance(level, int):
        return level
    return logging.INFO


def _resolve_timezone() -> ZoneInfo:
    """从环境变量解析日志时区，默认为上海时区。"""

    tz_name = os.environ.get("LOG_TIMEZONE", "Asia/Shanghai").strip()
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("Asia/Shanghai")


class _TimezoneFormatter(logging.Formatter):
    """将日志时间统一格式化为指定时区。"""

    def __init__(self, *args: Any, timezone: ZoneInfo, **kwargs: Any) -> None:
        """保存目标时区并初始化基础 Formatter。"""
        super().__init__(*args, **kwargs)
        self._timezone = timezone

    def formatTime(self, record: logging.LogRecord, datefmt: Optional[str] = None) -> str:
        """按照预设时区格式化日志时间。"""
        dt = datetime.fromtimestamp(record.created, tz=self._timezone)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.strftime("%Y-%m-%d %H:%M:%S")


def configure_base_logger(*, level_env: str | None = None, stderr_env: str | None = None) -> logging.Logger:
    """初始化基础 logger，仅执行一次。"""
    global _CONFIGURED
    logger = logging.getLogger("vibe")
    if _CONFIGURED:
        return logger

    level_name = "INFO"
    if level_env:
        level_name = os.environ.get(level_env, level_name)
    level_name = os.environ.get("LOG_LEVEL", level_name)
    logger.setLevel(_determine_level(level_name))

    timezone = _resolve_timezone()
    formatter = _TimezoneFormatter(LOG_FORMAT, timezone=timezone)
    file_handler = WatchedFileHandler(_resolve_log_file(), encoding="utf-8")
    file_handler.setFormatter(formatter)

    logger.handlers.clear()
    logger.addHandler(file_handler)

    enable_stderr = True
    if stderr_env:
        enable_stderr = os.environ.get(stderr_env, "1") != "0"
    if os.environ.get("LOG_STDERR") is not None:
        enable_stderr = os.environ.get("LOG_STDERR") != "0"
    if enable_stderr:
        console = logging.StreamHandler()
        console.setFormatter(_TimezoneFormatter(LOG_FORMAT, timezone=timezone))
        logger.addHandler(console)

    logger.propagate = False
    _CONFIGURED = True
    return logger


def create_logger(
    agent: str,
    *,
    project: str = "-",
    model: str = "-",
    session: str = "-",
    level_env: str | None = None,
    stderr_env: str | None = None,
) -> ContextLoggerAdapter:
    """创建带上下文的 LoggerAdapter。"""

    base = configure_base_logger(level_env=level_env, stderr_env=stderr_env)
    extra = {
        "agent": agent or "-",
        "project": project or "-",
        "model": model or "-",
        "session": session or "-",
    }
    return ContextLoggerAdapter(base, extra)


def enrich(logger: ContextLoggerAdapter, **kwargs: Any) -> ContextLoggerAdapter:
    """返回带额外上下文的新 LoggerAdapter。"""

    merged: Dict[str, Any] = {**getattr(logger, "extra", {}), **kwargs}
    return ContextLoggerAdapter(logger.logger, merged)
