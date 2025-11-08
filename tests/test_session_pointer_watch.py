import json
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import pytest

import scripts.session_pointer_watch as session_pointer_watch


def _write_rollout(path: Path, cwd_value: str) -> None:
    payload = {"payload": {"cwd": cwd_value}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


PlanFactory = Optional[Callable[[Callable[..., None], Path, str], None]]


def _run_session_watch(tmp_path: Path, *, create_plan: PlanFactory, timeout: float = 0.6, poll: float = 0.1) -> dict:
    pointer = tmp_path / "pointer.txt"
    lock = tmp_path / "lock.json"
    sessions_dir = tmp_path / "sessions"
    workdir = tmp_path / "project"
    sessions_dir.mkdir()
    workdir.mkdir()
    cwd_value = str(workdir.resolve())

    threads: list[threading.Thread] = []

    def spawn(delay: float, filename: str, *, match: bool = True) -> None:
        def worker() -> None:
            time.sleep(delay)
            _write_rollout(sessions_dir / filename, cwd_value if match else f"{cwd_value}-other")
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        threads.append(thread)

    original_observer = session_pointer_watch.Observer
    session_pointer_watch.Observer = None  # Force polling mode to avoid external dependency

    try:
        if create_plan is not None:
            create_plan(spawn, sessions_dir, cwd_value)

        argv = [
            "--pointer",
            str(pointer),
            "--lock",
            str(lock),
            "--session-root",
            str(sessions_dir),
            "--glob",
            "rollout-*.jsonl",
            "--workdir",
            cwd_value,
            "--tmux-session",
            "vibe-test",
            "--project",
            "demo",
            "--timeout",
            str(timeout),
            "--poll",
            str(poll),
        ]
        exit_code = session_pointer_watch.main(argv)
    finally:
        session_pointer_watch.Observer = original_observer
        for thread in threads:
            thread.join(timeout=1)

    pointer_value = pointer.read_text(encoding="utf-8").strip() if pointer.exists() else ""
    lock_payload = json.loads(lock.read_text(encoding="utf-8")) if lock.exists() else None
    return {
        "exit_code": exit_code,
        "pointer": pointer_value,
        "lock": lock_payload,
        "cwd": cwd_value,
    }


def test_pointer_written_when_rollout_appears(tmp_path):
    result = _run_session_watch(
        tmp_path,
        create_plan=lambda spawn, *_: spawn(0.1, "rollout-new.jsonl"),
    )
    assert result["exit_code"] == 0
    assert result["pointer"].endswith("/sessions/rollout-new.jsonl")
    assert result["lock"]["session_path"] == result["pointer"]


def test_no_rollout_leaves_pointer_empty(tmp_path):
    result = _run_session_watch(tmp_path, create_plan=None, timeout=0.2)
    assert result["exit_code"] == 1
    assert result["pointer"] == ""
    assert result["lock"] is None


def test_mismatch_rollout_is_ignored_until_match(tmp_path):
    def plan(spawn, *_):
        spawn(0.05, "rollout-bad.jsonl", match=False)
        spawn(0.2, "rollout-good.jsonl", match=True)

    result = _run_session_watch(tmp_path, create_plan=plan, timeout=0.5)
    assert result["exit_code"] == 0
    assert result["pointer"].endswith("rollout-good.jsonl")
    assert result["lock"]["session_path"].endswith("rollout-good.jsonl")


def test_pointer_overwritten_with_locked_session(tmp_path):
    def plan(spawn, sessions_dir, cwd):
        old = sessions_dir / "rollout-old.jsonl"
        _write_rollout(old, cwd)
        pointer = tmp_path / "pointer.txt"
        pointer.write_text(str(old), encoding="utf-8")
        spawn(0.1, "rollout-fresh.jsonl", match=True)

    result = _run_session_watch(tmp_path, create_plan=plan, timeout=0.5)
    assert result["pointer"].endswith("rollout-fresh.jsonl")
    assert result["lock"]["session_path"].endswith("rollout-fresh.jsonl")
