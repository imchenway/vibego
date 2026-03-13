from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_TOKEN", "TEST_TOKEN")

import bot


def _setup_tmux_send_line_mocks(monkeypatch):
    """统一替换 tmux 相关依赖，便于断言 send-keys 调用序列。"""

    monkeypatch.setattr(bot, "tmux_bin", lambda: "tmux")
    monkeypatch.setattr(bot, "_tmux_cmd", lambda *args: list(args))
    monkeypatch.setattr(subprocess, "call", lambda *args, **kwargs: 0)
    monkeypatch.setattr(subprocess, "check_output", lambda *args, **kwargs: "0")


def test_tmux_send_line_sends_delayed_second_enter_when_enabled(monkeypatch):
    """开启兜底后，应在延迟窗口后补发一次 Enter。"""

    _setup_tmux_send_line_mocks(monkeypatch)
    monkeypatch.setattr(bot, "TMUX_SEND_LINE_DOUBLE_ENTER_ENABLED", True)
    monkeypatch.setattr(bot, "TMUX_SEND_LINE_DOUBLE_ENTER_DELAY_SECONDS", 2.0)
    monkeypatch.setattr(bot, "_is_claudecode_model", lambda: False)

    sleep_calls: list[float] = []
    sent_calls: list[list[str]] = []

    monkeypatch.setattr(time, "sleep", lambda seconds: sleep_calls.append(seconds))
    monkeypatch.setattr(
        subprocess,
        "check_call",
        lambda cmd, *args, **kwargs: sent_calls.append(cmd) or 0,
    )

    bot.tmux_send_line("demo", "hello")

    enter_calls = [cmd for cmd in sent_calls if cmd[-1] == "C-m"]
    assert len(enter_calls) == 2
    assert 2.0 in sleep_calls


def test_tmux_send_line_disables_second_enter_when_flag_off(monkeypatch):
    """关闭兜底后，不再补发第二次 Enter（即使 ClaudeCode 分支）。"""

    _setup_tmux_send_line_mocks(monkeypatch)
    monkeypatch.setattr(bot, "TMUX_SEND_LINE_DOUBLE_ENTER_ENABLED", False)
    monkeypatch.setattr(bot, "TMUX_SEND_LINE_DOUBLE_ENTER_DELAY_SECONDS", 2.0)
    monkeypatch.setattr(bot, "_is_claudecode_model", lambda: True)

    sleep_calls: list[float] = []
    sent_calls: list[list[str]] = []

    monkeypatch.setattr(time, "sleep", lambda seconds: sleep_calls.append(seconds))
    monkeypatch.setattr(
        subprocess,
        "check_call",
        lambda cmd, *args, **kwargs: sent_calls.append(cmd) or 0,
    )

    bot.tmux_send_line("demo", "hello")

    enter_calls = [cmd for cmd in sent_calls if cmd[-1] == "C-m"]
    assert len(enter_calls) == 1
    assert 2.0 not in sleep_calls


def test_tmux_send_line_second_enter_failure_does_not_raise(monkeypatch):
    """补发 Enter 失败只记日志，不应覆盖首发成功结果。"""

    _setup_tmux_send_line_mocks(monkeypatch)
    monkeypatch.setattr(bot, "TMUX_SEND_LINE_DOUBLE_ENTER_ENABLED", True)
    monkeypatch.setattr(bot, "TMUX_SEND_LINE_DOUBLE_ENTER_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(bot, "_is_claudecode_model", lambda: False)
    monkeypatch.setattr(time, "sleep", lambda *_args, **_kwargs: None)

    enter_count = {"value": 0}

    def fake_check_call(cmd, *args, **kwargs):
        if cmd[-1] == "C-m":
            enter_count["value"] += 1
            if enter_count["value"] == 2:
                raise subprocess.CalledProcessError(1, cmd, "fallback failed")
        return 0

    monkeypatch.setattr(subprocess, "check_call", fake_check_call)

    # 不应抛错
    bot.tmux_send_line("demo", "hello")
    assert enter_count["value"] == 2


def test_tmux_queue_line_uses_tab_without_double_enter(monkeypatch):
    """排队发送应使用 Tab 提交，且不补发第二次 Enter。"""

    _setup_tmux_send_line_mocks(monkeypatch)
    monkeypatch.setattr(bot, "TMUX_SEND_LINE_DOUBLE_ENTER_ENABLED", True)
    monkeypatch.setattr(bot, "TMUX_SEND_LINE_DOUBLE_ENTER_DELAY_SECONDS", 2.0)
    monkeypatch.setattr(bot, "_is_claudecode_model", lambda: False)

    sleep_calls: list[float] = []
    sent_calls: list[list[str]] = []

    monkeypatch.setattr(time, "sleep", lambda seconds: sleep_calls.append(seconds))
    monkeypatch.setattr(
        subprocess,
        "check_call",
        lambda cmd, *args, **kwargs: sent_calls.append(cmd) or 0,
    )

    bot.tmux_queue_line("demo", "hello")

    tab_calls = [cmd for cmd in sent_calls if cmd[-1] == "Tab"]
    enter_calls = [cmd for cmd in sent_calls if cmd[-1] == "C-m"]
    assert len(tab_calls) == 1
    assert enter_calls == []
    assert 2.0 not in sleep_calls


def test_dispatch_prompt_tmux_error_suggests_manual_enter(monkeypatch, tmp_path: Path):
    """tmux 推送失败时，应给出“手动按 Enter”提示。"""

    pointer = tmp_path / "pointer.txt"
    session_file = tmp_path / "rollout.jsonl"
    session_file.write_text("", encoding="utf-8")
    pointer.write_text(str(session_file), encoding="utf-8")

    monkeypatch.setattr(bot, "CODEX_SESSION_FILE_PATH", str(pointer))
    monkeypatch.setattr(bot, "CODEX_WORKDIR", "")
    monkeypatch.setattr(bot, "SESSION_BIND_STRICT", True)
    monkeypatch.setattr(bot, "SESSION_POLL_TIMEOUT", 0)

    replies: list[str] = []

    async def fake_reply(chat_id: int, text: str, **kwargs):
        replies.append(text)
        return None

    def fake_tmux_send_line(_session: str, _prompt: str) -> None:
        raise subprocess.CalledProcessError(1, ["tmux", "-u", "send-keys"], "failure")

    monkeypatch.setattr(bot, "_reply_to_chat", fake_reply)
    monkeypatch.setattr(bot, "tmux_send_line", fake_tmux_send_line)

    ok, session_path = asyncio.run(
        bot._dispatch_prompt_to_model(
            9527,
            "pwd",
            reply_to=None,
            ack_immediately=False,
        )
    )

    assert not ok
    assert session_path is None
    assert replies and "手动按 Enter" in replies[-1]


def test_dispatch_prompt_tmux_queue_error_suggests_manual_tab(monkeypatch, tmp_path: Path):
    """排队发送失败时，应给出“手动按 Tab”提示。"""

    pointer = tmp_path / "pointer.txt"
    session_file = tmp_path / "rollout.jsonl"
    session_file.write_text("", encoding="utf-8")
    pointer.write_text(str(session_file), encoding="utf-8")

    monkeypatch.setattr(bot, "CODEX_SESSION_FILE_PATH", str(pointer))
    monkeypatch.setattr(bot, "CODEX_WORKDIR", "")
    monkeypatch.setattr(bot, "SESSION_BIND_STRICT", True)
    monkeypatch.setattr(bot, "SESSION_POLL_TIMEOUT", 0)

    replies: list[str] = []

    async def fake_reply(chat_id: int, text: str, **kwargs):
        replies.append(text)
        return None

    def fake_tmux_queue_line(_session: str, _prompt: str) -> None:
        raise subprocess.CalledProcessError(1, ["tmux", "-u", "send-keys"], "failure")

    monkeypatch.setattr(bot, "_reply_to_chat", fake_reply)
    monkeypatch.setattr(bot, "tmux_queue_line", fake_tmux_queue_line)

    ok, session_path = asyncio.run(
        bot._dispatch_prompt_to_model(
            9528,
            "pwd",
            reply_to=None,
            ack_immediately=False,
            send_mode=bot.PUSH_SEND_MODE_QUEUED,
        )
    )

    assert not ok
    assert session_path is None
    assert replies and "手动按 Tab" in replies[-1]
