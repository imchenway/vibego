from __future__ import annotations

import json
import os
import sys
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


def test_codex_session_binder_records_recent_bound_session_index(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """绑定成功后应只记录最近会话的路径索引，不复制 JSONL 内容。"""

    workdir = tmp_path / "repo"
    workdir.mkdir()
    root = tmp_path / "sessions"
    pointer = tmp_path / "logs" / "current_session.txt"
    recent_sessions = tmp_path / "logs" / "recent_sessions.json"
    marker = "vibego-session-bind-token:test-worker"
    worker_session = root / "2026" / "06" / "01" / "rollout-worker.jsonl"
    _write_codex_rollout(worker_session, cwd=str(workdir), marker=marker)
    worker_session.write_text(
        worker_session.read_text(encoding="utf-8") + "SECRET_SHOULD_NOT_BE_COPIED\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "session_binder.py",
            "--pointer",
            str(pointer),
            "--session-root",
            str(root),
            "--glob",
            "rollout-*.jsonl",
            "--cwd",
            str(workdir),
            "--boot-ts-ms",
            "0",
            "--timeout",
            "0.1",
            "--required-marker",
            marker,
            "--recent-sessions-file",
            str(recent_sessions),
            "--project-slug",
            "demo-project",
        ],
    )

    assert session_binder.main() == 0

    entries = json.loads(recent_sessions.read_text(encoding="utf-8"))
    assert len(entries) == 1
    assert entries[0]["session_id"] == worker_session.stem
    assert entries[0]["jsonl_path"] == str(worker_session)
    assert entries[0]["cwd"] == str(workdir)
    assert entries[0]["project_slug"] == "demo-project"
    assert "SECRET_SHOULD_NOT_BE_COPIED" not in recent_sessions.read_text(encoding="utf-8")


def test_recent_sessions_index_keeps_latest_three_and_deduplicates(tmp_path: Path) -> None:
    """最近会话索引只保留 3 条，并按 session_id/jsonl_path 去重。"""

    recent_sessions = tmp_path / "recent_sessions.json"
    entries = [
        {
            "session_id": f"rollout-{idx}",
            "jsonl_path": str(tmp_path / f"rollout-{idx}.jsonl"),
            "cwd": str(tmp_path),
            "project_slug": "demo",
            "bound_at": f"2026-06-01T00:00:0{idx}Z",
        }
        for idx in range(4)
    ]
    for entry in entries:
        session_binder._update_recent_sessions_index(  # noqa: SLF001
            recent_sessions,
            entry,
            limit=3,
        )

    stored = json.loads(recent_sessions.read_text(encoding="utf-8"))
    assert [item["session_id"] for item in stored] == [
        "rollout-3",
        "rollout-2",
        "rollout-1",
    ]

    duplicate = dict(entries[2])
    duplicate["bound_at"] = "2026-06-01T00:01:00Z"
    session_binder._update_recent_sessions_index(  # noqa: SLF001
        recent_sessions,
        duplicate,
        limit=3,
    )

    stored = json.loads(recent_sessions.read_text(encoding="utf-8"))
    assert [item["session_id"] for item in stored] == [
        "rollout-2",
        "rollout-3",
        "rollout-1",
    ]
