#!/usr/bin/env python3
"""面向 tmux pipe-pane 的日志写入器。

功能：
- 将 stdin 写入指定日志文件，确保主文件大小不超过阈值（默认 20MB）。
- 超过阈值时将现有文件按时间戳归档，并创建新的主文件。
- 定期清理超过保留时间（默认 24 小时）的归档文件。

通过环境变量控制：
- MODEL_LOG_MAX_BYTES：主日志文件最大字节数，默认 20971520 (20MB)
- MODEL_LOG_RETENTION_SECONDS：归档文件保留时长，默认 86400 秒 (24h)
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
    """读取整数型环境变量，解析失败时返回默认值。"""

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
    """删除超过保留时间的归档日志。"""

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
    """将当前主日志按时间戳归档，并返回归档后的路径。"""

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
        # 文件可能已被外部删除，此时无需归档
        return archive_path
    return archive_path


def main() -> int:
    """执行日志写入循环，处理滚动与归档清理。"""

    if len(sys.argv) != 2:
        sys.stderr.write("Usage: log_writer.py <log_file>\n")
        return 1

    log_path = Path(sys.argv[1]).expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    max_bytes = _env_int("MODEL_LOG_MAX_BYTES", DEFAULT_MAX_BYTES)
    retention_seconds = _env_int("MODEL_LOG_RETENTION_SECONDS", DEFAULT_RETENTION_SECONDS)

    def open_log_file() -> tuple[int, object]:
        """打开日志文件并返回当前大小及文件句柄。"""

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
