from __future__ import annotations

import json
import os
from pathlib import Path

from scripts import session_binder


def _write_codex_rollout(path: Path, *, cwd: str, marker: str = "") -> None:
    """写入最小 Codex JSONL 会话元数据，便于验证 binder 过滤逻辑。"""

    payload = {
        "timestamp": "2026-05-11T00:00:00.000Z",
        "type": "session_meta",
        "payload": {
            "id": path.stem.removeprefix("rollout-"),
            "timestamp": "2026-05-11T00:00:00.000Z",
            "cwd": cwd,
            "source": "cli",
            "base_instructions": {"text": f"base instructions\n{marker}"},
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")


def test_codex_session_binder_rejects_newer_same_cwd_session_without_marker(tmp_path: Path) -> None:
    """同 CWD 但缺少 worker marker 的新会话，不应污染 pointer 绑定。"""

    workdir = tmp_path / "repo"
    workdir.mkdir()
    root = tmp_path / "sessions"
    marker = "vibego-session-bind-token:test-worker"
    worker_session = root / "2026" / "05" / "11" / "rollout-worker.jsonl"
    codex_app_session = root / "2026" / "05" / "11" / "rollout-codex-app.jsonl"

    _write_codex_rollout(worker_session, cwd=str(workdir), marker=marker)
    _write_codex_rollout(codex_app_session, cwd=str(workdir), marker="")
    os.utime(worker_session, (10, 10))
    os.utime(codex_app_session, (20, 20))

    selected = session_binder._select_latest_session(  # noqa: SLF001
        roots=[root],
        pattern="rollout-*.jsonl",
        target_cwd=str(workdir),
        boot_ts_ms=0.0,
        required_marker=marker,
    )

    assert selected == worker_session


def test_codex_session_binder_returns_none_when_only_same_cwd_session_lacks_marker(tmp_path: Path) -> None:
    """只有同 CWD 的 Codex App 会话时，严格 marker 模式应 fail-closed。"""

    workdir = tmp_path / "repo"
    workdir.mkdir()
    root = tmp_path / "sessions"
    marker = "vibego-session-bind-token:test-worker"
    codex_app_session = root / "2026" / "05" / "11" / "rollout-codex-app.jsonl"

    _write_codex_rollout(codex_app_session, cwd=str(workdir), marker="")

    selected = session_binder._select_latest_session(  # noqa: SLF001
        roots=[root],
        pattern="rollout-*.jsonl",
        target_cwd=str(workdir),
        boot_ts_ms=0.0,
        required_marker=marker,
    )

    assert selected is None
