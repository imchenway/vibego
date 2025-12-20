import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import master


@pytest.fixture(autouse=True)
def restore_manager():
    """每个用例后还原全局 MANAGER，避免互相影响。"""

    original = master.MANAGER
    yield
    master.MANAGER = original


@pytest.fixture(autouse=True)
def reset_upgrade_state():
    """隔离升级相关的全局锁与任务。"""

    master._UPGRADE_TASK = None
    master._UPGRADE_STATE_LOCK = asyncio.Lock()
    yield
    task = master._UPGRADE_TASK
    if task and not task.done():
        task.cancel()
    master._UPGRADE_TASK = None
    master._UPGRADE_STATE_LOCK = asyncio.Lock()


class DummyBot:
    """简化版 Bot，用于记录发送的消息。"""

    def __init__(self) -> None:
        self.messages = []

    async def send_message(self, chat_id: int, text: str, **kwargs) -> None:
        self.messages.append((chat_id, text, kwargs))


class DummyUpgradeBot(DummyBot):
    """用于模拟升级过程中的 bot 行为。"""

    def __init__(self) -> None:
        super().__init__()
        self.edits = []

    async def edit_message_text(self, chat_id: int, message_id: int, text: str, **kwargs) -> None:
        self.edits.append((chat_id, message_id, text, kwargs))


class DummyMessage:
    """模拟 aiogram Message，仅保留测试所需接口。"""

    def __init__(self, chat_id: int) -> None:
        self.chat = SimpleNamespace(id=chat_id)
        self.from_user = SimpleNamespace(id=chat_id, username=None)
        self.text = "/upgrade"
        self.replies = []
        self.bot = DummyUpgradeBot()

    async def answer(self, text: str, **kwargs):
        self.replies.append((text, kwargs))
        # 模拟 aiogram 返回的 Message 对象
        return SimpleNamespace(message_id=len(self.replies))


@pytest.fixture
def update_state_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """为每个用例隔离 update_state.json 位置。"""

    state_path = tmp_path / "update_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(master, "UPDATE_STATE_PATH", state_path)
    return state_path


@pytest.fixture
def upgrade_report_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离升级报告路径，避免污染真实配置目录。"""

    report_path = tmp_path / "upgrade_report.json"
    monkeypatch.setattr(master, "_UPGRADE_REPORT_PATH", report_path)
    return report_path


@pytest.mark.asyncio
async def test_ensure_update_state_without_latest(monkeypatch: pytest.MonkeyPatch, update_state_path: Path):
    """无可用版本时仅记录 last_check。"""

    async def fake_fetch():
        return None

    monkeypatch.setattr(master, "_fetch_latest_version", fake_fetch)
    state = await master._ensure_update_state(force=True)
    assert "last_check" in state
    assert "latest_version" not in state
    # 确保状态已写入文件
    written = json.loads(update_state_path.read_text(encoding="utf-8"))
    assert "last_check" in written


@pytest.mark.asyncio
async def test_ensure_update_state_with_new_version(monkeypatch: pytest.MonkeyPatch, update_state_path: Path):
    """检测到新版本时重置已通知列表。"""

    update_state_path.write_text(
        json.dumps({"latest_version": "1.0.19", "notified_chat_ids": [1, 2]}, ensure_ascii=False),
        encoding="utf-8",
    )

    async def fake_fetch():
        return "9.9.9"

    monkeypatch.setattr(master, "_fetch_latest_version", fake_fetch)
    state = await master._ensure_update_state(force=True)
    assert state["latest_version"] == "9.9.9"
    assert state["notified_chat_ids"] == []


@pytest.mark.asyncio
async def test_maybe_notify_update_single_chat(monkeypatch: pytest.MonkeyPatch, update_state_path: Path):
    """同一 chat 仅提醒一次。"""

    state = {
        "latest_version": "9.9.9",
        "notified_chat_ids": [],
        "last_check": master._utcnow().isoformat(),
    }
    update_state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    async def fake_fetch():
        return "9.9.9"

    monkeypatch.setattr(master, "_fetch_latest_version", fake_fetch)
    bot = DummyBot()
    notified = await master._maybe_notify_update(bot, chat_id=100, force_check=False)
    assert notified is True
    assert len(bot.messages) == 1

    notified_again = await master._maybe_notify_update(bot, chat_id=100, force_check=False)
    assert notified_again is False
    assert len(bot.messages) == 1


@pytest.mark.asyncio
async def test_maybe_notify_update_multiple_chats(monkeypatch: pytest.MonkeyPatch, update_state_path: Path):
    """不同 chat 均会收到同一版本的提醒。"""

    state = {
        "latest_version": "8.0.0",
        "notified_chat_ids": [],
        "last_check": master._utcnow().isoformat(),
    }
    update_state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    async def fake_fetch():
        return "8.0.0"

    monkeypatch.setattr(master, "_fetch_latest_version", fake_fetch)
    bot = DummyBot()
    await master._maybe_notify_update(bot, chat_id=1, force_check=False)
    await master._maybe_notify_update(bot, chat_id=2, force_check=False)
    assert {chat_id for chat_id, *_ in bot.messages} == {1, 2}


@pytest.mark.asyncio
async def test_maybe_notify_update_skips_old_version(update_state_path: Path):
    """当前版本不落后时不提醒。"""

    state = {
        "latest_version": master.__version__,
        "notified_chat_ids": [],
    }
    update_state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    bot = DummyBot()
    notified = await master._maybe_notify_update(bot, chat_id=1, state=state)
    assert notified is False
    assert bot.messages == []


@pytest.mark.asyncio
async def test_notify_update_to_targets(monkeypatch: pytest.MonkeyPatch, update_state_path: Path):
    """批量通知会遍历所有目标。"""

    state = {
        "latest_version": "7.0.0",
        "notified_chat_ids": [],
        "last_check": master._utcnow().isoformat(),
    }
    update_state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    async def fake_fetch():
        return "7.0.0"

    monkeypatch.setattr(master, "_fetch_latest_version", fake_fetch)
    bot = DummyBot()
    await master._notify_update_to_targets(bot, [11, 22], force_check=False)
    assert len(bot.messages) == 2


@pytest.mark.asyncio
async def test_cmd_upgrade_authorized(monkeypatch: pytest.MonkeyPatch):
    """授权用户执行 /upgrade 时会启动后台流水线并发送提示。"""

    message = DummyMessage(chat_id=999)
    triggered = asyncio.Event()

    async def fake_pipeline(bot, chat_id, message_id):
        triggered.set()

    monkeypatch.setattr(master, "_run_upgrade_pipeline", fake_pipeline)
    master.MANAGER = SimpleNamespace(is_authorized=lambda _: True)
    await master.cmd_upgrade(message)
    await asyncio.wait_for(triggered.wait(), timeout=1)
    assert message.replies, "应至少回复一条消息"
    assert "已收到升级指令" in message.replies[0][0]


@pytest.mark.asyncio
async def test_cmd_upgrade_unauthorized(monkeypatch: pytest.MonkeyPatch):
    """未授权用户无法执行 /upgrade。"""

    message = DummyMessage(chat_id=321)
    master.MANAGER = SimpleNamespace(is_authorized=lambda _: False)
    await master.cmd_upgrade(message)
    assert message.replies[0][0] == "未授权。"


@pytest.mark.asyncio
async def test_cmd_upgrade_rejects_parallel_requests(monkeypatch: pytest.MonkeyPatch):
    """并发触发时只有第一个请求会被受理。"""

    message = DummyMessage(chat_id=1)
    start_event = asyncio.Event()
    finish_event = asyncio.Event()

    async def fake_pipeline(bot, chat_id, message_id):
        start_event.set()
        await finish_event.wait()

    monkeypatch.setattr(master, "_run_upgrade_pipeline", fake_pipeline)
    master.MANAGER = SimpleNamespace(is_authorized=lambda _: True)

    await master.cmd_upgrade(message)
    await asyncio.wait_for(start_event.wait(), timeout=1)

    second = DummyMessage(chat_id=1)
    await master.cmd_upgrade(second)
    assert "已有升级任务" in second.replies[-1][0]

    finish_event.set()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_run_upgrade_pipeline_success(monkeypatch: pytest.MonkeyPatch, upgrade_report_path: Path):
    """pipx 成功后应记录报告并安排自动重启。"""

    bot = DummyUpgradeBot()
    calls = []

    async def fake_step(*args, **kwargs):
        calls.append(args)
        return 0, ["ok"]

    spawned = {}
    def fake_spawn(command, delay):
        spawned["args"] = (command, delay)
        return SimpleNamespace(pid=123)

    recorded = {}
    def fake_persist(chat_id, lines, elapsed, command, delay):
        recorded["args"] = (chat_id, lines, elapsed, command, delay)

    monkeypatch.setattr(master, "_run_single_upgrade_step", fake_step)
    monkeypatch.setattr(master, "_spawn_detached_restart", fake_spawn)
    monkeypatch.setattr(master, "_persist_upgrade_report", fake_persist)
    monkeypatch.setattr(master, "_UPGRADE_RESTART_COMMAND", "echo restart")
    monkeypatch.setattr(master, "_UPGRADE_RESTART_DELAY", 0.1)

    await master._run_upgrade_pipeline(bot, chat_id=1, message_id=10)
    assert len(calls) == len(master._UPGRADE_COMMANDS)
    assert bot.edits, "应至少更新一次状态"
    assert "升级流程完成" in bot.edits[-1][2]
    assert recorded["args"][0] == 1
    assert spawned["args"][0] == "echo restart"


@pytest.mark.asyncio
async def test_run_upgrade_pipeline_failure(monkeypatch: pytest.MonkeyPatch):
    """任一步骤返回非零退出码时应推送失败信息。"""

    bot = DummyUpgradeBot()

    async def fake_step(command, description, step_index, total_steps, bot_obj, chat_id, message_id):
        if step_index == 1:
            return 9, ["boom"]
        return 0, [f"{description}-ok"]

    monkeypatch.setattr(master, "_run_single_upgrade_step", fake_step)
    await master._run_upgrade_pipeline(bot, chat_id=1, message_id=10)
    assert bot.edits, "应推送失败信息"
    assert "升级流程失败" in bot.edits[-1][2]


@pytest.mark.asyncio
async def test_run_upgrade_pipeline_without_restart(monkeypatch: pytest.MonkeyPatch):
    """未配置自动重启命令时，仅提示完成而不写报告。"""

    bot = DummyUpgradeBot()

    async def fake_step(*args, **kwargs):
        return 0, ["ok"]

    recorded = {}

    def fake_persist(*args, **kwargs):
        recorded["called"] = True

    monkeypatch.setattr(master, "_run_single_upgrade_step", fake_step)
    monkeypatch.setattr(master, "_persist_upgrade_report", fake_persist)
    monkeypatch.setattr(master, "_UPGRADE_RESTART_COMMAND", "")

    await master._run_upgrade_pipeline(bot, chat_id=1, message_id=10)
    assert bot.edits, "应提示完成"
    assert "未配置自动重启命令" in bot.edits[-1][2]
    assert "called" not in recorded


@pytest.mark.asyncio
async def test_run_single_upgrade_step_uses_devnull_stdin(monkeypatch: pytest.MonkeyPatch):
    """升级子进程 stdin 应显式连接 /dev/null，避免后台环境描述符缺失。"""

    recorded = {}

    class FakeStdout:
        def __init__(self):
            self._lines = [b"line1\n", b"line2\n"]

        async def readline(self):
            return self._lines.pop(0) if self._lines else b""

    class FakeProcess:
        def __init__(self):
            self.stdout = FakeStdout()

        async def wait(self):
            return 0

    async def fake_create(command, **kwargs):  # type: ignore[override]
        recorded.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_create)
    bot = DummyUpgradeBot()
    returncode, lines = await master._run_single_upgrade_step(
        "echo 1",
        "升级 vibego 包",
        1,
        1,
        bot,
        1,
        1,
    )
    assert returncode == 0
    assert lines == ["line1", "line2"]
    assert recorded["stdin"] is asyncio.subprocess.DEVNULL
    assert recorded["stdout"] is asyncio.subprocess.PIPE
    assert recorded["stderr"] is asyncio.subprocess.STDOUT


def test_spawn_detached_restart_uses_devnull(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """后台重启命令同样需要绑定 /dev/null，同时输出需落盘便于排查。"""

    captured = {}

    def fake_popen(args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(pid=222)

    monkeypatch.setattr(master.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(master, "_UPGRADE_RESTART_LOG_PATH", tmp_path / "upgrade_restart.log")
    proc = master._spawn_detached_restart("echo hi", 0.1)
    assert proc.pid == 222
    assert captured["stdin"] is master.subprocess.DEVNULL
    assert captured["stdout"] is not master.subprocess.DEVNULL
    assert captured["stderr"] is not master.subprocess.DEVNULL


def test_persist_upgrade_report_records_versions(upgrade_report_path: Path):
    """写入升级报告时应记录旧/新版本。"""

    lines = [
        "其他输出",
        "upgraded package vibego from 1.1.13 to 1.1.14 (location: /Users/david/.local/pipx/venvs/vibego)",
    ]
    master._persist_upgrade_report(
        chat_id=1,
        lines=lines,
        elapsed=6.2,
        restart_command="echo restart",
        restart_delay=2.0,
    )
    payload = json.loads(upgrade_report_path.read_text(encoding="utf-8"))
    assert payload["old_version"] == "1.1.13"
    assert payload["new_version"] == "1.1.14"


@pytest.mark.asyncio
async def test_notify_upgrade_report(monkeypatch: pytest.MonkeyPatch, upgrade_report_path: Path):
    """启动时若存在升级报告应推送摘要并清理文件。"""

    payload = {
        "chat_id": 777,
        "log_tail": ["line1", "line2"],
        "elapsed": 3.5,
        "restart_command": "echo restart",
        "restart_delay": 1.0,
        "recorded_at": "2025-11-12T10:00:00+00:00",
        "old_version": "1.1.13",
        "new_version": "1.1.14",
    }
    upgrade_report_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    bot = DummyBot()
    await master._notify_upgrade_report(bot)
    assert bot.messages, "应推送升级摘要"
    lines = bot.messages[0][1].splitlines()
    assert lines[0].startswith("✅ 升级流程完成")
    assert lines[1] == "📦 旧版本 1.1.13 -> 新版本 1.1.14"
    assert lines[2].startswith("🚀 master 已重新上线")
    assert not upgrade_report_path.exists()
