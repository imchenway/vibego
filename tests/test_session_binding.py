import asyncio
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot


@pytest.mark.asyncio
async def test_await_session_path_strict_waits(tmp_path):
    """严格模式下应等待 pointer 写入后再返回。"""
    pointer = tmp_path / "current_session.txt"
    pointer.write_text("", encoding="utf-8")
    session_file = tmp_path / "sessions" / "rollout-new.jsonl"
    session_file.parent.mkdir()
    session_file.write_text("{}", encoding="utf-8")

    async def _delayed_bind() -> None:
        await asyncio.sleep(0.05)
        pointer.write_text(str(session_file), encoding="utf-8")

    task = asyncio.create_task(_delayed_bind())
    result = await bot._await_session_path(
        pointer,
        target_cwd=None,
        poll=0.01,
        strict=True,
        max_wait=1.0,
    )
    await task
    assert result == session_file


@pytest.mark.asyncio
async def test_await_session_path_strict_timeout(tmp_path):
    """超过超时仍未绑定时需返回 None 供上层提示用户重试。"""
    pointer = tmp_path / "current_session.txt"
    pointer.write_text("", encoding="utf-8")
    result = await bot._await_session_path(
        pointer,
        target_cwd=None,
        poll=0.01,
        strict=True,
        max_wait=0.05,
    )
    assert result is None
