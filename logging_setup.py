"""Unified logging configuration helpers shared between master and worker."""

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
    """LoggerAdapter that allows per-call extra overrides."""

    def process(self, msg: str, kwargs: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
        """Merge default extras with user-provided extras to preserve context."""

        provided: Optional[Dict[str, Any]] = kwargs.pop("extra", None)
        merged: Dict[str, Any] = dict(self.extra)
        if provided:
            merged.update(provided)
        kwargs["extra"] = merged
        return msg, kwargs


def _default_config_root() -> Path:
    """Resolve the configuration root based on environment variables and XDG conventions."""

    override = os.environ.get("MASTER_CONFIG_ROOT") or os.environ.get("VIBEGO_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    xdg_base = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_base).expanduser() if xdg_base else Path.home() / ".config"
    return base / "vibego"


def _resolve_log_file() -> Path:
    """Determine the log file path using environment variables when available."""
    candidate = os.environ.get("LOG_FILE")
    default_path = _default_config_root() / "logs/vibe.log"
    target = Path(candidate).expanduser() if candidate else default_path
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _determine_level(level_name: str) -> int:
    """Resolve a log level string, falling back to INFO if unknown."""

    level = getattr(logging, level_name.upper(), None)
    if isinstance(level, int):
        return level
    return logging.INFO


def _resolve_timezone() -> ZoneInfo:
    """Parse the log timezone from environment variables, defaulting to Asia/Shanghai."""

    tz_name = os.environ.get("LOG_TIMEZONE", "Asia/Shanghai").strip()
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("Asia/Shanghai")


class _TimezoneFormatter(logging.Formatter):
    """Format log timestamps using a specific timezone."""

    def __init__(self, *args: Any, timezone: ZoneInfo, **kwargs: Any) -> None:
        """Store the target timezone and initialise the base formatter."""
        super().__init__(*args, **kwargs)
        self._timezone = timezone

    def formatTime(self, record: logging.LogRecord, datefmt: Optional[str] = None) -> str:
        """Format the timestamp according to the configured timezone."""
        dt = datetime.fromtimestamp(record.created, tz=self._timezone)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.strftime("%Y-%m-%d %H:%M:%S")


def configure_base_logger(*, level_env: str | None = None, stderr_env: str | None = None) -> logging.Logger:
    """Initialise the base logger (idempotent)."""
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
    """Create a LoggerAdapter with context enrichments."""

    base = configure_base_logger(level_env=level_env, stderr_env=stderr_env)
    extra = {
        "agent": agent or "-",
        "project": project or "-",
        "model": model or "-",
        "session": session or "-",
    }
    return ContextLoggerAdapter(base, extra)


def enrich(logger: ContextLoggerAdapter, **kwargs: Any) -> ContextLoggerAdapter:
    """Return a new LoggerAdapter with extended context."""

    merged: Dict[str, Any] = {**getattr(logger, "extra", {}), **kwargs}
    return ContextLoggerAdapter(logger.logger, merged)
