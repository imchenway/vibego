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
async def test_run_upgrade_pipeline_success(monkeypatch: pytest.MonkeyPatch):
    """所有步骤成功时应输出完成提示。"""

    bot = DummyUpgradeBot()
    calls = []

    async def fake_step(*args, **kwargs):
        calls.append(args)
        return 0, ["ok"]

    monkeypatch.setattr(master, "_run_single_upgrade_step", fake_step)
    await master._run_upgrade_pipeline(bot, chat_id=1, message_id=10)
    assert len(calls) == len(master._UPGRADE_COMMANDS)
    assert bot.edits, "应至少更新一次状态"
    assert "升级流程完成" in bot.edits[-1][2]


@pytest.mark.asyncio
async def test_run_upgrade_pipeline_failure(monkeypatch: pytest.MonkeyPatch):
    """任一步骤返回非零退出码时应推送失败信息。"""

    bot = DummyUpgradeBot()

    async def fake_step(command, description, step_index, total_steps, bot_obj, chat_id, message_id):
        if step_index == 2:
            return 9, ["boom"]
        return 0, [f"{description}-ok"]

    monkeypatch.setattr(master, "_run_single_upgrade_step", fake_step)
    await master._run_upgrade_pipeline(bot, chat_id=1, message_id=10)
    assert bot.edits, "应推送失败信息"
    assert "升级流程失败" in bot.edits[-1][2]
