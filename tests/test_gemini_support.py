from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timezone

import pytest

# 确保可以直接 import bot.py / master.py（与现有测试保持一致）
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_TOKEN", "test-token")

import bot  # noqa: E402
from scripts import session_binder  # noqa: E402


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


@pytest.mark.parametrize(
    "raw_content,json_payload,cursor,backtrack,expected_cursor,expected_texts,expected_offsets",
    [
        # 1) 空文件：无消息
        ("", None, 0, 20, 0, [], []),
        # 2) 非法 JSON：解析失败
        ("{", None, 0, 20, 0, [], []),
        # 3) 缺少 messages：视为无事件
        (None, {"sessionId": "s1"}, 0, 20, 0, [], []),
        # 4) messages 不是 list：视为无事件
        (None, {"messages": "oops"}, 0, 20, 0, [], []),
        # 5) 只有 user：不回推，但游标应前进
        (None, {"messages": [{"type": "user", "content": "hi"}]}, 0, 20, 1, [], []),
        # 6) gemini 空内容：不回推，但游标应前进
        (None, {"messages": [{"type": "gemini", "content": ""}]}, 0, 20, 1, [], []),
        # 7) gemini 空白内容：不回推
        (None, {"messages": [{"type": "gemini", "content": "   "}]}, 0, 20, 1, [], []),
        # 8) gemini 正常文本：回推 1 条（offset=1）
        (None, {"messages": [{"type": "gemini", "content": "ok", "id": "m1"}]}, 0, 20, 1, ["ok"], [1]),
        # 9) assistant 兼容：回推 1 条
        (None, {"messages": [{"type": "assistant", "content": "hi", "id": "m2"}]}, 0, 20, 1, ["hi"], [1]),
        # 10) 游标异常过大：回退最近 N 条，避免跳过新输出
        (
            None,
            {"messages": [{"type": "user", "content": "a"}, {"type": "gemini", "content": "b", "id": "m3"}]},
            999,
            1,
            2,
            ["b"],
            [2],
        ),
    ],
)
def test_read_session_events_gemini_variants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw_content: str | None,
    json_payload: dict | None,
    cursor: int,
    backtrack: int,
    expected_cursor: int,
    expected_texts: list[str],
    expected_offsets: list[int],
) -> None:
    """Gemini session-*.json：仅回推 type=gemini/assistant 的 content。"""

    monkeypatch.setattr(bot, "GEMINI_SESSION_INITIAL_BACKTRACK_MESSAGES", backtrack)
    bot.SESSION_OFFSETS.clear()

    session_path = tmp_path / "session-2025-12-31T00-00-00000000.json"
    if raw_content is not None:
        session_path.write_text(raw_content, encoding="utf-8")
    else:
        session_path.write_text(json.dumps(json_payload, ensure_ascii=False), encoding="utf-8")

    bot.SESSION_OFFSETS[str(session_path)] = cursor
    new_cursor, events = bot._read_session_events(session_path)

    assert new_cursor == expected_cursor
    assert [e.text for e in events] == expected_texts
    assert [e.offset for e in events] == expected_offsets


def test_initial_session_offset_gemini_backtracks_messages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Gemini 初始化偏移应按 messages 数回退，避免漏掉刚生成的输出。"""

    monkeypatch.setattr(bot, "GEMINI_SESSION_INITIAL_BACKTRACK_MESSAGES", 3)
    session_path = tmp_path / "session-2025-12-31T00-00-abcdef12.json"
    payload = {"messages": [{"type": "user", "content": "u"}] * 10}
    session_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    assert bot._initial_session_offset(session_path) == 7


def test_session_binder_selects_latest_gemini_session_by_project_hash(tmp_path: Path) -> None:
    """session_binder：应按 projectHash 过滤并选择最新 mtime 的 session 文件。"""

    real_workdir = tmp_path / "realproj"
    real_workdir.mkdir()
    link_workdir = tmp_path / "linkproj"
    link_workdir.symlink_to(real_workdir, target_is_directory=True)

    expected_hash = _sha256_hex(str(real_workdir))
    other_hash = _sha256_hex(str(tmp_path / "otherproj"))

    root = tmp_path / "gemini_tmp"
    ok_dir = root / expected_hash / "chats"
    bad_dir = root / other_hash / "chats"
    ok_dir.mkdir(parents=True)
    bad_dir.mkdir(parents=True)

    file_old = ok_dir / "session-2025-12-31T00-00-aaaa1111.json"
    file_new = ok_dir / "session-2025-12-31T00-00-bbbb2222.json"
    file_bad = bad_dir / "session-2025-12-31T00-00-cccc3333.json"

    file_old.write_text(json.dumps({"projectHash": expected_hash, "startTime": "2025-12-31T00:00:01.000Z"}), encoding="utf-8")
    file_new.write_text(json.dumps({"projectHash": expected_hash, "startTime": "2025-12-31T00:00:02.000Z"}), encoding="utf-8")
    file_bad.write_text(json.dumps({"projectHash": other_hash, "startTime": "2025-12-31T00:00:03.000Z"}), encoding="utf-8")

    # 通过 mtime 控制“最新”选择：bad 虽更新，但应被 projectHash 过滤掉
    os.utime(file_old, (1, 1))
    os.utime(file_new, (3, 3))
    os.utime(file_bad, (5, 5))

    selected = session_binder._select_latest_session(  # noqa: SLF001 - 测试内部函数
        roots=[root],
        pattern="session-*.json",
        target_cwd=str(link_workdir),
        boot_ts_ms=0.0,
    )
    assert selected == file_new


def test_session_binder_gemini_boot_ts_prefers_start_time(tmp_path: Path) -> None:
    """session_binder：Gemini 过滤应优先使用 startTime，避免旧会话因 mtime 更新被误选。"""

    workdir = tmp_path / "proj"
    workdir.mkdir()
    project_hash = _sha256_hex(str(workdir))

    root = tmp_path / "gemini_tmp"
    chats = root / project_hash / "chats"
    chats.mkdir(parents=True)

    file_old = chats / "session-2025-12-30T00-00-aaaa1111.json"
    file_new = chats / "session-2025-12-31T00-00-bbbb2222.json"

    file_old.write_text(json.dumps({"projectHash": project_hash, "startTime": "2025-12-30T00:00:00.000Z"}), encoding="utf-8")
    file_new.write_text(json.dumps({"projectHash": project_hash, "startTime": "2025-12-31T00:00:00.000Z"}), encoding="utf-8")

    # mtime 都设置得很新，但 boot_ts_ms 只允许 2025-12-31 之后的 startTime
    os.utime(file_old, (10, 10))
    os.utime(file_new, (11, 11))

    boot_ts_ms = datetime(2025, 12, 31, 0, 0, 0, tzinfo=timezone.utc).timestamp() * 1000.0
    selected = session_binder._select_latest_session(  # noqa: SLF001 - 测试内部函数
        roots=[root],
        pattern="session-*.json",
        target_cwd=str(workdir),
        boot_ts_ms=boot_ts_ms,
    )
    assert selected == file_new
