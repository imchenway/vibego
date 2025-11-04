#!/usr/bin/env python3
"""Log writer for tmux pipe-pane.

Features:
- Stream stdin to a target log file while keeping the primary file under a
  configurable size (default 20 MB).
- When the threshold is exceeded, archive the current file with a timestamp
  suffix and create a fresh primary log.
- Periodically delete archived logs that exceed the retention window
  (default 24 hours).

Environment variables:
- MODEL_LOG_MAX_BYTES: maximum size of the primary log file, default 20971520 (20 MB).
- MODEL_LOG_RETENTION_SECONDS: retention period for archived files, default 86400 seconds (24 h).
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from pathlib import Path

BUFFER_SIZE = 8192
DEFAULT_MAX_BYTES = 20 * 1024 * 1024
DEFAULT_RETENTION_SECONDS = 24 * 60 * 60


def _env_int(name: str, default: int) -> int:
    """Read an integer environment variable with graceful fallback."""

    raw = os.environ.get(name)
    if raw is None:
        return default
    raw = raw.strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def cleanup_archives(base_path: Path, retention_seconds: int) -> None:
    """Remove archived logs that exceed the retention period."""

    cutoff = time.time() - retention_seconds
    pattern = f"{base_path.stem}-*.log"
    for candidate in base_path.parent.glob(pattern):
        try:
            stat = candidate.stat()
        except FileNotFoundError:
            continue
        if stat.st_mtime < cutoff:
            try:
                candidate.unlink()
            except FileNotFoundError:
                continue


def rotate_log(base_path: Path) -> Path:
    """Archive the current primary log and return the archive path."""

    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    archive_name = f"{base_path.stem}-{timestamp}.log"
    archive_path = base_path.with_name(archive_name)

    suffix = 1
    while archive_path.exists():
        archive_path = base_path.with_name(f"{base_path.stem}-{timestamp}-{suffix}.log")
        suffix += 1

    try:
        base_path.rename(archive_path)
    except FileNotFoundError:
        # External processes may have removed the file; skip archival in that case.
        return archive_path
    return archive_path


def main() -> int:
    """Run the logging loop, handling rotation and archive cleanup."""

    if len(sys.argv) != 2:
        sys.stderr.write("Usage: log_writer.py <log_file>\n")
        return 1

    log_path = Path(sys.argv[1]).expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    max_bytes = _env_int("MODEL_LOG_MAX_BYTES", DEFAULT_MAX_BYTES)
    retention_seconds = _env_int("MODEL_LOG_RETENTION_SECONDS", DEFAULT_RETENTION_SECONDS)

    def open_log_file() -> tuple[int, object]:
        """Open the log file and return the current size together with the handle."""

        fp = log_path.open("ab", buffering=0)
        try:
            current_size = fp.tell()
        except OSError:
            current_size = log_path.stat().st_size if log_path.exists() else 0
        return current_size, fp

    current_size, fp = open_log_file()

    if current_size > max_bytes:
        fp.close()
        rotate_log(log_path)
        cleanup_archives(log_path, retention_seconds)
        current_size, fp = open_log_file()

    stdin = sys.stdin.buffer
    while True:
        chunk = stdin.read(BUFFER_SIZE)
        if not chunk:
            break

        if current_size + len(chunk) > max_bytes:
            fp.close()
            rotate_log(log_path)
            cleanup_archives(log_path, retention_seconds)
            current_size, fp = open_log_file()

        fp.write(chunk)
        current_size += len(chunk)

    fp.close()
    cleanup_archives(log_path, retention_seconds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
