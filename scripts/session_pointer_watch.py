#!/usr/bin/env python3
"""Capture the session JSONL path generated after a worker launch and lock it.

This helper watches the Codex/Claude session directory after the tmux worker
starts. The first rollout file that matches the configured working directory is
recorded into both the pointer file (used by the worker for streaming) and a
dedicated lock file so subsequent bindings never drift to another CLI session.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path
from threading import Event, Lock
from typing import Iterable, Optional

try:
    from watchdog.events import FileSystemEvent, FileSystemEventHandler
    from watchdog.observers import Observer
except Exception:  # pragma: no cover - watchdog is an optional dependency fallback
    Observer = None
    FileSystemEventHandler = object  # type: ignore[assignment]


log = logging.getLogger("session_pointer_watch")


def _resolve(path: str) -> Path:
    return Path(path).expanduser().resolve()


def _read_session_cwd(path: Path) -> Optional[str]:
    """Read first JSON line and return payload.cwd when available."""

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
    payload = data.get("payload")
    if isinstance(payload, dict):
        raw = payload.get("cwd")
        if isinstance(raw, str):
            return raw
    return None


def _iter_candidate_files(roots: Iterable[Path], glob: str) -> Iterable[Path]:
    for root in roots:
        if not root.exists():
            continue
        try:
            real_root = root.resolve()
        except OSError:
            real_root = root
        yield from real_root.glob(f"**/{glob}")


class _RolloutCapture(FileSystemEventHandler):
    """Handle filesystem events and pick the first rollout that matches criteria."""

    def __init__(
        self,
        *,
        pattern: str,
        baseline: set[str],
        start_wall: float,
        start_monotonic: float,
        target_cwd: Optional[str],
        timeout: float,
        poll_interval: float,
    ) -> None:
        self._pattern = pattern
        self._baseline = baseline
        self._start_wall = start_wall
        self._target_cwd = target_cwd
        self._deadline = start_monotonic + timeout
        self._poll_interval = poll_interval
        self._chosen: Optional[Path] = None
        self._event = Event()
        self._lock = Lock()

    def _consider(self, candidate: Path) -> None:
        if candidate.is_dir():
            return
        name = candidate.name
        if not fnmatch(name, self._pattern):
            return
        try:
            real_path = candidate.resolve()
        except OSError:
            real_path = candidate
        real_key = str(real_path)
        if real_key in self._baseline:
            return
        try:
            stat = real_path.stat()
        except OSError:
            return
        if stat.st_mtime + 0.01 < self._start_wall:
            # Ignore historical files.
            return

        if self._target_cwd:
            # Wait until the JSON header is flushed and matches our CWD.
            deadline = time.monotonic() + self._poll_interval * 10
            while time.monotonic() < deadline:
                cwd = _read_session_cwd(real_path)
                if cwd is None:
                    time.sleep(self._poll_interval)
                    continue
                if cwd == self._target_cwd:
                    break
                log.debug("Skip rollout with mismatched cwd=%s", cwd)
                return
        with self._lock:
            if self._chosen is None:
                self._chosen = real_path
                self._event.set()

    # The following methods are only called when watchdog is available.
    def on_created(self, event: FileSystemEvent) -> None:  # type: ignore[override]
        if getattr(event, "is_directory", False):
            return
        self._consider(Path(event.src_path))

    def on_moved(self, event: FileSystemEvent) -> None:  # type: ignore[override]
        if getattr(event, "is_directory", False):
            return
        self._consider(Path(event.dest_path))

    def poll_until_found(self, roots: Iterable[Path]) -> Optional[Path]:
        while time.monotonic() < self._deadline:
            remaining = self._deadline - time.monotonic()
            if remaining <= 0:
                break
            wait_time = min(self._poll_interval, remaining)
            if self._event.wait(timeout=wait_time):
                break
            for candidate in _iter_candidate_files(roots, self._pattern):
                self._consider(candidate)
                if self._event.is_set():
                    break
            if self._event.is_set():
                break
        return self._chosen


def _write_pointer(pointer: Path, session_path: Path) -> None:
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(str(session_path), encoding="utf-8")


def _write_lock(lock_file: Path, *, session_path: Path, tmux_session: str, project: str, workdir: str, method: str) -> None:
    payload = {
        "session_path": str(session_path),
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "tmux_session": tmux_session,
        "project": project,
        "workdir": workdir,
        "method": method,
    }
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Capture newly created Codex/Claude rollout JSONL and write pointer+lock files.")
    parser.add_argument("--pointer", required=True, help="Path to current_session.txt pointer file")
    parser.add_argument("--lock", required=True, help="Path to persistent lock metadata JSON")
    parser.add_argument("--session-root", required=False, help="Primary sessions root directory")
    parser.add_argument("--additional-root", action="append", default=[], help="Extra directories to monitor for rollouts")
    parser.add_argument("--glob", default="rollout-*.jsonl", help="Glob pattern for rollout files")
    parser.add_argument("--workdir", default="", help="Model working directory, used to filter sessions")
    parser.add_argument("--tmux-session", default="", help="tmux session name for diagnostics")
    parser.add_argument("--project", default="", help="Project slug for diagnostics")
    parser.add_argument("--timeout", type=float, default=180.0, help="Maximum seconds to wait for a new rollout")
    parser.add_argument("--poll", type=float, default=0.5, help="Polling interval when watchdog is unavailable")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    pointer_path = _resolve(args.pointer)
    lock_path = _resolve(args.lock)
    roots: list[Path] = []
    if args.session_root:
        roots.append(_resolve(args.session_root))
    pointer_parent = pointer_path.parent
    if pointer_parent not in roots:
        roots.append(pointer_parent)
    for raw in args.additional_root:
        resolved = _resolve(raw)
        if resolved not in roots:
            roots.append(resolved)

    baseline = {str(path.resolve()) for path in _iter_candidate_files(roots, args.glob)}
    start_wall = time.time()
    start_monotonic = time.monotonic()

    capture = _RolloutCapture(
        pattern=args.glob,
        baseline=baseline,
        start_wall=start_wall,
        start_monotonic=start_monotonic,
        target_cwd=args.workdir or None,
        timeout=args.timeout,
        poll_interval=max(args.poll, 0.1),
    )

    observer: Optional[Observer] = None
    method = "watchdog"
    if Observer is not None:
        observer = Observer()
        for root in roots:
            if root.exists():
                observer.schedule(capture, str(root), recursive=True)
        observer.start()
    else:  # pragma: no cover - watchdog is optional
        log.warning("watchdog not available, falling back to polling")
        method = "polling"

    try:
        session_path = capture.poll_until_found(roots)
    finally:
        if observer is not None:
            observer.stop()
            observer.join(timeout=5)

    if session_path is None:
        log.error(
            "Failed to detect rollout file within timeout %.1fs (tmux session=%s, project=%s)",
            args.timeout,
            args.tmux_session or "-",
            args.project or "-",
        )
        return 1

    _write_pointer(pointer_path, session_path)
    _write_lock(
        lock_path,
        session_path=session_path,
        tmux_session=args.tmux_session,
        project=args.project,
        workdir=args.workdir,
        method=method,
    )

    log.info("Recorded session pointer -> %s", session_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
