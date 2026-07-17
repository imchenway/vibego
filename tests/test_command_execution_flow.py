import os
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("BOT_TOKEN", "TEST_TOKEN")

import bot
from command_center import CommandDefinition


class _StubCommandService:
    """记录命令执行历史调用，避免依赖真实数据库。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def record_history(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})
        return SimpleNamespace(id=321)


class _DummyReplyMessage:
    """仅保留命令执行链路需要的 chat 上下文。"""

    def __init__(self, chat_id: int = 42) -> None:
        self.chat = SimpleNamespace(id=chat_id)


class _DummyEditableMessage:
    """模拟 Telegram 已发送消息，记录后续 edit_text 更新行为。"""

    def __init__(self, chat, events: list[tuple], message_id: int) -> None:
        self.chat = chat
        self.events = events
        self.message_id = message_id

    async def edit_text(self, text: str, *, parse_mode=None, reply_markup=None):
        kind = "auto-retry-edit" if "自动重试 1 次" in text else "result-edit"
        self.events.append((kind, text, reply_markup, self.message_id))


def _build_command_answer_spy(events: list[tuple]):
    """构造命令消息发送桩：执行中返回可编辑消息，便于断言同消息更新。"""

    async def fake_answer_with_markdown(message, text: str, *, reply_markup=None):
        if "命令执行中" in text:
            kind = "progress-send"
        elif "自动重试 1 次" in text:
            kind = "auto-retry-send"
        else:
            kind = "result-send"
        message_id = len(events) + 1
        events.append((kind, text, reply_markup, message_id))
        return _DummyEditableMessage(message.chat, events, message_id)

    return fake_answer_with_markdown


class _DummyFsmState:
    """记录端口人工兜底状态，便于确认自动重试是否短路人工流程。"""

    def __init__(self) -> None:
        self.states: list[object] = []
        self.data: list[dict] = []
        self.cleared = 0

    async def clear(self) -> None:
        self.cleared += 1

    async def set_state(self, state) -> None:
        self.states.append(state)

    async def update_data(self, **kwargs) -> None:
        self.data.append(kwargs)


class _DummyBot:
    """记录图片/文件发送顺序，并可按需制造失败。"""

    def __init__(self, events: list[tuple], *, fail_photo: bool = False) -> None:
        self.events = events
        self.fail_photo = fail_photo

    async def send_photo(self, chat_id: int, photo, caption: str | None = None):
        self.events.append(("photo", chat_id, caption, type(photo).__name__))
        if self.fail_photo:
            raise RuntimeError("photo send failed")

    async def send_document(self, chat_id: int, document, caption: str | None = None, **kwargs):
        self.events.append(("document", chat_id, caption, type(document).__name__, kwargs))


def _build_preview_command() -> CommandDefinition:
    """构造最小可执行的通用预览命令对象。"""

    return CommandDefinition(
        id=15,
        project_slug="__global__",
        scope="global",
        name=bot.WX_PREVIEW_COMMAND_NAME,
        title="生成微信开发预览二维码",
        command='echo "preview"',
        description="",
        timeout=600,
        enabled=True,
        aliases=(),
    )


def _build_auto_preview_command() -> CommandDefinition:
    """构造最小可执行的自动预览命令对象。"""

    return CommandDefinition(
        id=17,
        project_slug="__global__",
        scope="global",
        name=bot.WX_AUTO_PREVIEW_COMMAND_NAME,
        title="微信手机自动预览",
        command='echo "auto-preview"',
        description="",
        timeout=600,
        enabled=True,
        aliases=(),
    )


def _build_remote_debug_command() -> CommandDefinition:
    """构造最小可执行的自动真机调试命令对象。"""

    return CommandDefinition(
        id=18,
        project_slug="__global__",
        scope="global",
        name=bot.WX_REMOTE_DEBUG_COMMAND_NAME,
        title="启动微信自动真机调试",
        command='echo "remote-debug"',
        description="",
        timeout=600,
        enabled=True,
        aliases=(),
    )


def _build_project_preview_command(project_root: Path, *, retry_marker: bool = False) -> CommandDefinition:
    """构造带小程序目录的预览命令对象。"""

    retry_prefix = "WX_DEVTOOLS_AUTO_PORT_RETRY=1 " if retry_marker else ""
    return CommandDefinition(
        id=16,
        project_slug="hyphamall",
        scope="global",
        name=bot.WX_PREVIEW_COMMAND_NAME,
        title="生成微信开发预览二维码",
        command=f'{retry_prefix}PROJECT_PATH="{project_root}" echo preview',
        description="",
        timeout=600,
        enabled=True,
        aliases=(),
    )


@pytest.mark.asyncio
async def test_execute_command_success_hides_output_preview_and_detail_buttons(monkeypatch):
    """成功态只反馈结果，不把 stdout 诊断噪音推给 Telegram 用户。"""

    events: list[tuple] = []
    reply_message = _DummyReplyMessage()
    service = _StubCommandService()

    async def fake_run_shell_command(command: str, timeout: int):
        return (
            0,
            "\n".join(
                [
                    "[信息] 手机自动预览，项目：/tmp/mini，端口：64701",
                    "INFO_OUTPUT: {\"previewInfo\": \"ok\"}",
                    "[完成] 手机自动预览已触发，请在微信开发者工具中确认。",
                ]
            ),
            "",
            0.42,
        )

    monkeypatch.setattr(bot, "_run_shell_command", fake_run_shell_command)
    monkeypatch.setattr(bot, "_answer_with_markdown", _build_command_answer_spy(events))

    await bot._execute_command_definition(
        command=_build_preview_command(),
        reply_message=reply_message,
        trigger="按钮",
        actor_user=None,
        service=service,
        history_detail_prefix=bot.COMMAND_HISTORY_DETAIL_GLOBAL_PREFIX,
        fsm_state=None,
    )

    assert [item[0] for item in events] == ["progress-send", "result-edit"]
    summary_text = events[-1][1]
    assert "状态：✅ 成功" in summary_text
    assert "标准输出摘要" not in summary_text
    assert "标准错误摘要" not in summary_text
    assert "INFO_OUTPUT" not in summary_text
    assert "手机自动预览已触发" not in summary_text
    assert "退出码" not in summary_text
    assert "如需完整输出" not in summary_text
    assert events[-1][2] is None
    assert events[-1][3] == events[0][3]
    assert service.calls[-1]["kwargs"]["status"] == "success"
    assert "INFO_OUTPUT" in service.calls[-1]["kwargs"]["output"]


@pytest.mark.asyncio
async def test_execute_command_sends_summary_before_qr_photo(monkeypatch, tmp_path: Path):
    """摘要消息应先发送，二维码图片应作为最后一条 Telegram 消息发送。"""

    events: list[tuple] = []
    reply_message = _DummyReplyMessage()
    service = _StubCommandService()
    photo_path = tmp_path / "wx-preview.jpg"
    photo_path.write_bytes(b"fake qr")

    async def fake_run_shell_command(command: str, timeout: int):
        return (
            0,
            "\n".join(
                [
                    "[完成] 预览二维码已生成",
                    f"TG_PHOTO_FILE: {photo_path}",
                ]
            ),
            "",
            1.23,
        )

    monkeypatch.setattr(bot, "_run_shell_command", fake_run_shell_command)
    monkeypatch.setattr(bot, "_answer_with_markdown", _build_command_answer_spy(events))
    monkeypatch.setattr(bot, "current_bot", lambda: _DummyBot(events))

    await bot._execute_command_definition(
        command=_build_preview_command(),
        reply_message=reply_message,
        trigger="按钮",
        actor_user=None,
        service=service,
        history_detail_prefix=bot.COMMAND_HISTORY_DETAIL_GLOBAL_PREFIX,
        fsm_state=None,
    )

    assert [item[0] for item in events] == ["progress-send", "result-edit", "photo"]
    assert "二维码图片已发送" not in events[1][1]
    assert "标准输出摘要" not in events[1][1]
    assert "TG_PHOTO_FILE" not in events[1][1]
    assert events[1][2] is None
    assert events[1][3] == events[0][3]


@pytest.mark.asyncio
async def test_execute_command_falls_back_to_document_when_photo_send_fails(monkeypatch, tmp_path: Path):
    """图片发送失败时应在摘要之后降级为文件发送，避免二维码彻底丢失。"""

    events: list[tuple] = []
    reply_message = _DummyReplyMessage()
    service = _StubCommandService()
    photo_path = tmp_path / "wx-preview.jpg"
    photo_path.write_bytes(b"fake qr")

    async def fake_run_shell_command(command: str, timeout: int):
        return (
            0,
            "\n".join(
                [
                    "[完成] 预览二维码已生成",
                    f"TG_PHOTO_FILE: {photo_path}",
                ]
            ),
            "",
            1.23,
        )

    monkeypatch.setattr(bot, "_run_shell_command", fake_run_shell_command)
    monkeypatch.setattr(bot, "_answer_with_markdown", _build_command_answer_spy(events))
    monkeypatch.setattr(bot, "current_bot", lambda: _DummyBot(events, fail_photo=True))

    await bot._execute_command_definition(
        command=_build_preview_command(),
        reply_message=reply_message,
        trigger="按钮",
        actor_user=None,
        service=service,
        history_detail_prefix=bot.COMMAND_HISTORY_DETAIL_GLOBAL_PREFIX,
        fsm_state=None,
    )

    assert [item[0] for item in events] == ["progress-send", "result-edit", "photo", "document"]
    assert "降级为文件" in (events[-1][2] or "")


@pytest.mark.asyncio
async def test_execute_command_failure_keeps_output_preview_and_detail_buttons(monkeypatch):
    """失败态仍保留 stdout/stderr 摘要和详情入口，避免削弱排障能力。"""

    events: list[tuple] = []
    reply_message = _DummyReplyMessage()
    service = _StubCommandService()

    async def fake_run_shell_command(command: str, timeout: int):
        return (7, "stdout diagnostics", "stderr diagnostics", 0.31)

    monkeypatch.setattr(bot, "_run_shell_command", fake_run_shell_command)
    monkeypatch.setattr(bot, "_answer_with_markdown", _build_command_answer_spy(events))

    await bot._execute_command_definition(
        command=_build_preview_command(),
        reply_message=reply_message,
        trigger="按钮",
        actor_user=None,
        service=service,
        history_detail_prefix=bot.COMMAND_HISTORY_DETAIL_GLOBAL_PREFIX,
        fsm_state=None,
    )

    summary_text = events[-1][1]
    summary_markup = events[-1][2]
    assert [item[0] for item in events] == ["progress-send", "result-edit"]
    assert "状态：⚠️ 失败" in summary_text
    assert "退出码：7" in summary_text
    assert "标准输出摘要" in summary_text
    assert "stdout diagnostics" in summary_text
    assert "标准错误摘要" in summary_text
    assert "stderr diagnostics" in summary_text
    assert "如需完整输出" in summary_text
    assert summary_markup is not None
    button_texts = [button.text for row in summary_markup.inline_keyboard for button in row]
    assert "🔎 查询详情" in button_texts
    assert "🧾 最近执行" in button_texts
    assert events[-1][3] == events[0][3]


@pytest.mark.asyncio
async def test_execute_wx_auto_preview_compile_failure_is_reported_as_failure(monkeypatch):
    """自动预览前置编译校验失败时，Telegram 不能显示成功态。"""

    events: list[tuple] = []
    reply_message = _DummyReplyMessage()
    service = _StubCommandService()

    async def fake_run_shell_command(command: str, timeout: int):
        return (
            10,
            "[信息] 手机自动预览前置编译校验，项目：/tmp/mini",
            "[错误] 自动预览前置编译校验失败，微信开发者工具 CLI 退出码：10\nwxml 编译错误",
            0.31,
        )

    monkeypatch.setattr(bot, "_run_shell_command", fake_run_shell_command)
    monkeypatch.setattr(bot, "_answer_with_markdown", _build_command_answer_spy(events))

    await bot._execute_command_definition(
        command=_build_auto_preview_command(),
        reply_message=reply_message,
        trigger="按钮",
        actor_user=None,
        service=service,
        history_detail_prefix=bot.COMMAND_HISTORY_DETAIL_GLOBAL_PREFIX,
        fsm_state=None,
    )

    summary_text = events[-1][1]
    summary_markup = events[-1][2]
    assert [item[0] for item in events] == ["progress-send", "result-edit"]
    assert "状态：⚠️ 失败" in summary_text
    assert "退出码：10" in summary_text
    assert "自动预览前置编译校验失败" in summary_text
    assert "wxml 编译错误" in summary_text
    assert "状态：✅ 成功" not in summary_text
    assert summary_markup is not None
    button_texts = [button.text for row in summary_markup.inline_keyboard for button in row]
    assert "🔎 查询详情" in button_texts
    assert service.calls[-1]["kwargs"]["status"] == "failed"
    assert service.calls[-1]["kwargs"]["exit_code"] == 10


@pytest.mark.asyncio
async def test_execute_wx_remote_debug_success_shows_verified_device_evidence(monkeypatch):
    """真机调试成功态应展示连接与运行时探测证据，而不是只看退出码。"""

    events: list[tuple] = []
    reply_message = _DummyReplyMessage()
    service = _StubCommandService()

    async def fake_run_shell_command(command: str, timeout: int):
        return (
            0,
            'VIBEGO_WX_REMOTE_DEBUG_RESULT:{"status":"success","project":"/tmp/mini",'
            '"platform":"ios","system":"iOS 18.5","connectionEvidence":"Tool.onRemoteDebugConnected"}',
            "",
            2.31,
        )

    monkeypatch.setattr(bot, "_run_shell_command", fake_run_shell_command)
    monkeypatch.setattr(bot, "_answer_with_markdown", _build_command_answer_spy(events))

    await bot._execute_command_definition(
        command=_build_remote_debug_command(),
        reply_message=reply_message,
        trigger="按钮",
        actor_user=None,
        service=service,
        history_detail_prefix=bot.COMMAND_HISTORY_DETAIL_GLOBAL_PREFIX,
        fsm_state=None,
    )

    summary_text = events[-1][1]
    assert "状态：✅ 成功" in summary_text
    assert "真机已连接且运行时探测通过" in summary_text
    assert "项目：`/tmp/mini`" in summary_text
    assert r"连接证据：`Tool\.onRemoteDebugConnected`" in summary_text
    assert "平台：`ios`" in summary_text
    assert "系统：`iOS 18\\.5`" in summary_text
    assert "标准输出摘要" not in summary_text
    assert service.calls[-1]["kwargs"]["status"] == "success"


@pytest.mark.asyncio
async def test_execute_wx_remote_debug_exit_zero_without_evidence_fails_closed(monkeypatch):
    """执行器退出 0 但缺少结构化连接证据时，Telegram 不得误报成功。"""

    events: list[tuple] = []
    reply_message = _DummyReplyMessage()
    service = _StubCommandService()

    async def fake_run_shell_command(command: str, timeout: int):
        return (0, "[完成] 已触发真机调试", "", 0.31)

    monkeypatch.setattr(bot, "_run_shell_command", fake_run_shell_command)
    monkeypatch.setattr(bot, "_answer_with_markdown", _build_command_answer_spy(events))

    await bot._execute_command_definition(
        command=_build_remote_debug_command(),
        reply_message=reply_message,
        trigger="按钮",
        actor_user=None,
        service=service,
        history_detail_prefix=bot.COMMAND_HISTORY_DETAIL_GLOBAL_PREFIX,
        fsm_state=None,
    )

    summary_text = events[-1][1]
    assert "状态：⚠️ 失败" in summary_text
    assert "缺少已验证的真机连接与运行时探测证据" in summary_text
    assert "状态：✅ 成功" not in summary_text
    assert service.calls[-1]["kwargs"]["status"] == "failed"


@pytest.mark.asyncio
async def test_execute_command_result_falls_back_to_new_message_when_edit_fails(monkeypatch):
    """执行中消息无法编辑时，应降级发送新的结果消息，避免用户看不到结果。"""

    events: list[tuple] = []
    reply_message = _DummyReplyMessage()
    service = _StubCommandService()

    async def fake_run_shell_command(command: str, timeout: int):
        return (0, "ok", "", 0.11)

    async def fake_try_edit_message(message, text: str, *, reply_markup=None):
        events.append(("result-edit-attempt", text, reply_markup, getattr(message, "message_id", None)))
        return False

    monkeypatch.setattr(bot, "_run_shell_command", fake_run_shell_command)
    monkeypatch.setattr(bot, "_answer_with_markdown", _build_command_answer_spy(events))
    monkeypatch.setattr(bot, "_try_edit_message", fake_try_edit_message)

    await bot._execute_command_definition(
        command=_build_preview_command(),
        reply_message=reply_message,
        trigger="按钮",
        actor_user=None,
        service=service,
        history_detail_prefix=bot.COMMAND_HISTORY_DETAIL_GLOBAL_PREFIX,
        fsm_state=None,
    )

    assert [item[0] for item in events] == ["progress-send", "result-edit-attempt", "result-send"]
    assert "状态：✅ 成功" in events[-1][1]


@pytest.mark.asyncio
async def test_execute_wx_preview_auto_retries_once_with_current_ide_port(monkeypatch, tmp_path: Path):
    """端口不匹配时，应自动改用 IDE 当前端口重试一次，并避免直接进入人工端口流程。"""

    events: list[tuple] = []
    calls: list[str] = []
    reply_message = _DummyReplyMessage()
    service = _StubCommandService()
    fsm_state = _DummyFsmState()
    project_root = tmp_path / "mini"
    project_root.mkdir()
    (project_root / "app.json").write_text("{}", encoding="utf-8")
    config_root = tmp_path / "vibego-config"

    async def fake_run_shell_command(command: str, timeout: int):
        calls.append(command)
        if len(calls) == 1:
            return (
                255,
                f"[信息] 生成预览，项目：{project_root}，版本：test，端口：64701，输出：/tmp/qr.jpg",
                "✖ IDE server has started on http://127.0.0.1:34724 and must be restarted on port 64701 first",
                0.11,
            )
        return (0, "[完成] 预览二维码已生成：/tmp/qr.jpg", "", 0.22)

    monkeypatch.setattr(bot, "CONFIG_DIR_PATH", config_root)
    monkeypatch.setattr(bot, "PROJECT_NAME", "hyphamall")
    monkeypatch.setattr(bot, "PROJECT_SLUG", "hyphamall")
    monkeypatch.setattr(bot, "_run_shell_command", fake_run_shell_command)
    monkeypatch.setattr(bot, "_answer_with_markdown", _build_command_answer_spy(events))

    await bot._execute_command_definition(
        command=_build_project_preview_command(project_root),
        reply_message=reply_message,
        trigger="按钮",
        actor_user=None,
        service=service,
        history_detail_prefix=bot.COMMAND_HISTORY_DETAIL_GLOBAL_PREFIX,
        fsm_state=fsm_state,
    )

    assert len(calls) == 2
    assert "PORT=34724" in calls[1]
    assert "WX_DEVTOOLS_AUTO_PORT_RETRY=1" in calls[1]
    assert [item[0] for item in events] == ["progress-send", "auto-retry-edit", "result-edit"]
    assert events[1][3] == events[0][3]
    assert events[2][3] == events[0][3]
    assert fsm_state.states == []
    ports_file = config_root / "wx_devtools_ports.json"
    data = json.loads(ports_file.read_text(encoding="utf-8"))
    assert data["projects"]["hyphamall"] == 34724
    assert data["paths"][str(project_root.resolve())] == 34724


@pytest.mark.asyncio
async def test_execute_wx_preview_auto_retry_marker_falls_back_to_manual_port(monkeypatch, tmp_path: Path):
    """已自动重试过的命令再次端口失败时，不应继续递归重试。"""

    events: list[tuple] = []
    calls: list[str] = []
    reply_message = _DummyReplyMessage()
    service = _StubCommandService()
    fsm_state = _DummyFsmState()
    project_root = tmp_path / "mini"
    project_root.mkdir()
    (project_root / "app.json").write_text("{}", encoding="utf-8")

    async def fake_run_shell_command(command: str, timeout: int):
        calls.append(command)
        return (
            255,
            f"[信息] 生成预览，项目：{project_root}，版本：test，端口：64701，输出：/tmp/qr.jpg",
            "✖ IDE server has started on http://127.0.0.1:34724 and must be restarted on port 64701 first",
            0.11,
        )

    monkeypatch.setattr(bot, "_run_shell_command", fake_run_shell_command)
    monkeypatch.setattr(bot, "_answer_with_markdown", _build_command_answer_spy(events))
    monkeypatch.setattr(bot, "_suggest_wx_devtools_ports", lambda: ([34724], True, None))

    await bot._execute_command_definition(
        command=_build_project_preview_command(project_root, retry_marker=True),
        reply_message=reply_message,
        trigger="按钮",
        actor_user=None,
        service=service,
        history_detail_prefix=bot.COMMAND_HISTORY_DETAIL_GLOBAL_PREFIX,
        fsm_state=fsm_state,
    )

    assert len(calls) == 1
    assert fsm_state.states == [bot.WxPreviewStates.waiting_port]
    assert [item[0] for item in events] == ["progress-send", "result-edit"]
    assert events[-1][2] is not None
    assert "端口配置不匹配" in events[-1][1]
    assert events[-1][3] == events[0][3]


def test_select_wx_devtools_auto_retry_port_requires_unique_missing_port_candidate(monkeypatch, tmp_path: Path):
    """端口缺失时，只有本机候选端口唯一才允许自动重试，避免多端口误判。"""

    command = _build_project_preview_command(tmp_path)
    stderr = "[错误] 未配置微信开发者工具 IDE 服务端口，无法生成预览二维码。"

    monkeypatch.setattr(bot, "_suggest_wx_devtools_ports", lambda: ([12605], True, None))
    assert bot._select_wx_devtools_auto_retry_port(command, 2, stderr) == (
        12605,
        "端口配置缺失，已自动使用本机唯一候选端口",
    )

    monkeypatch.setattr(bot, "_suggest_wx_devtools_ports", lambda: ([12605, 64701], True, None))
    assert bot._select_wx_devtools_auto_retry_port(command, 2, stderr) == (None, None)


def test_select_wx_remote_debug_auto_retry_port_uses_existing_recovery_rules(tmp_path: Path):
    """自动真机调试应复用 IDE 当前端口的单次高置信恢复规则。"""

    project = tmp_path / "mini"
    command = CommandDefinition(
        id=19,
        project_slug="hyphamall",
        scope="global",
        name=bot.WX_REMOTE_DEBUG_COMMAND_NAME,
        title="启动微信自动真机调试",
        command=f'PROJECT_PATH="{project}" echo remote-debug',
        description="",
        timeout=600,
        enabled=True,
        aliases=(),
    )
    stderr = "IDE server has started on http://127.0.0.1:34724 and must be restarted on port 64701 first"
    assert bot._select_wx_devtools_auto_retry_port(command, 255, stderr) == (
        34724,
        "端口不匹配，已自动改用 IDE 当前端口",
    )


def test_select_wx_preview_auto_retry_port_skips_when_script_already_retried(tmp_path: Path):
    command = _build_project_preview_command(tmp_path)
    stderr = "\n".join(
        [
            "VIBEGO_WX_PORT_RETRY_USED=1",
            "IDE server has started on http://127.0.0.1:34724 and must be restarted on port 64701 first",
        ]
    )

    assert bot._select_wx_devtools_auto_retry_port(command, 255, stderr) == (None, None)
