#!/usr/bin/env python3
"""监听 Codex/Claude JSONL 会话文件并在首次生成时绑定 session。

此脚本在后台运行：
1. 周期性扫描会话目录，只要发现符合条件（同 CWD、满足启动时间）的 rollout 文件，
   就会把绝对路径写入 pointer 文件；
2. 同时可选地把 sessionId（即文件 stem）写入独立文件，方便其它进程引用；
3. 成功绑定一次后立即退出，确保单次 worker 生命周期只使用同一个 session。
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Set


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="绑定首个可用的 Codex/Claude 会话文件")
    parser.add_argument("--pointer", required=True, help="current_session.txt 路径")
    parser.add_argument(
        "--session-root",
        action="append",
        default=[],
        help="JSONL 搜索根目录，可多次指定",
    )
    parser.add_argument(
        "--glob",
        default="rollout-*.jsonl",
        help="会话文件匹配模式，默认 rollout-*.jsonl",
    )
    parser.add_argument("--cwd", default="", help="期望匹配的 MODEL_WORKDIR，留空则不校验")
    parser.add_argument(
        "--boot-ts-ms",
        type=float,
        default=0.0,
        help="worker 启动时间戳 (ms)，只接受晚于该时间的文件",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=0.5,
        help="轮询间隔（秒）",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=600.0,
        help="最大等待时长（秒），0 表示一直等待",
    )
    parser.add_argument(
        "--session-id-file",
        default="",
        help="写入 sessionId（文件 stem ）的路径",
    )
    parser.add_argument(
        "--extra-root",
        action="append",
        default=[],
        help="额外搜索目录，可多次指定",
    )
    parser.add_argument(
        "--log",
        default="",
        help="可选的日志文件路径，仅用于调试信息",
    )
    return parser.parse_args()


def _normalize_path(path: str) -> Path:
    """展开 ~ / 环境变量，统一返回 Path。"""

    expanded = Path(path).expanduser()
    try:
        return expanded.resolve()
    except OSError:
        return expanded


def _dedup_roots(candidates: Sequence[str]) -> List[Path]:
    """去重合法目录，保持原有顺序。"""

    normalized: List[Path] = []
    seen: Set[str] = set()
    for raw in candidates:
        if not raw:
            continue
        path = _normalize_path(raw)
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(path)
    return normalized


def _iter_rollouts(roots: Iterable[Path], pattern: str) -> Iterable[Path]:
    """遍历所有候选 rollout 文件。"""

    for root in roots:
        if not root.exists():
            continue
        try:
            iterator = root.rglob(pattern)
        except OSError:
            continue
        for candidate in iterator:
            if candidate.is_file():
                yield candidate


def _read_session_cwd(path: Path) -> Optional[str]:
    """读取 JSONL 首行的 cwd 字段用于过滤。"""

    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            first_line = fh.readline()
    except OSError:
        return None
    if not first_line:
        return None
    try:
        data = json.loads(first_line)
    except json.JSONDecodeError:
        return None
    payload = data.get("payload") if isinstance(data, dict) else None
    if not isinstance(payload, dict):
        return None
    cwd = payload.get("cwd")
    return cwd if isinstance(cwd, str) else None


def _select_latest_session(
    roots: Sequence[Path],
    pattern: str,
    target_cwd: str,
    boot_ts_ms: float,
) -> Optional[Path]:
    """从候选目录中挑选满足 CWD + 启动时间过滤的最新文件。"""

    latest_path: Optional[Path] = None
    latest_mtime = -1.0
    for candidate in _iter_rollouts(roots, pattern):
        try:
            mtime = candidate.stat().st_mtime
        except OSError:
            continue
        if boot_ts_ms > 0 and (mtime * 1000.0) < boot_ts_ms:
            continue
        if target_cwd:
            cwd = _read_session_cwd(candidate)
            if cwd != target_cwd:
                continue
        if mtime > latest_mtime:
            latest_mtime = mtime
            latest_path = candidate
    return latest_path


def _write_text(path: Path, content: str) -> None:
    """原子写入文本，必要时创建父目录。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _append_log(log_file: str, message: str) -> None:
    """将调试信息附加写入日志文件。"""

    if not log_file:
        return
    try:
        with open(log_file, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except OSError:
        pass


def main() -> int:
    args = _parse_args()
    pointer = _normalize_path(args.pointer)
    pointer.parent.mkdir(parents=True, exist_ok=True)

    session_roots = list(args.session_root or [])
    session_roots.extend(args.extra_root or [])
    # 指针文件所在目录及其 sessions 子目录也加入搜索范围
    pointer_dir = str(pointer.parent)
    session_roots.append(pointer_dir)
    session_roots.append(str(Path(pointer_dir) / "sessions"))
    roots = _dedup_roots(session_roots)

    session_id_path = Path(args.session_id_file).expanduser() if args.session_id_file else None
    target_cwd = args.cwd.strip()
    strict_boot_ts = float(args.boot_ts_ms or 0.0)

    _append_log(args.log, f"[binder] start, pointer={pointer}, roots={roots}, cwd={target_cwd!r}, boot_ts={strict_boot_ts}")

    # 若启动前已存在有效指针，直接退出
    try:
        existing = pointer.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        existing = ""
    if existing:
        bound = Path(existing)
        if bound.exists():
            if session_id_path is not None:
                _write_text(session_id_path, bound.stem)
            _append_log(args.log, "[binder] pointer already bound, exit early")
            return 0

    start = time.monotonic()
    poll_interval = max(args.poll_interval, 0.1)
    timeout = max(args.timeout, 0.0)

    while True:
        candidate = _select_latest_session(roots, args.glob, target_cwd, strict_boot_ts)
        if candidate is not None:
            _write_text(pointer, str(candidate))
            if session_id_path is not None:
                _write_text(session_id_path, candidate.stem)
            _append_log(args.log, f"[binder] bind session -> {candidate}")
            return 0

        if timeout and (time.monotonic() - start) >= timeout:
            _append_log(args.log, "[binder] timeout reached, stop watching")
            return 1

        time.sleep(poll_interval)


if __name__ == "__main__":
    raise SystemExit(main())
