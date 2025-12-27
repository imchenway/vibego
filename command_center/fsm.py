"""命令管理流程使用的 aiogram FSM 状态。"""
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class CommandCreateStates(StatesGroup):
    """命令创建引导各阶段。"""

    waiting_name = State()
    waiting_shell = State()


class CommandEditStates(StatesGroup):
    """命令编辑流程各阶段。"""

    waiting_choice = State()
    waiting_value = State()
    waiting_aliases = State()


class WxPreviewStates(StatesGroup):
    """wx-dev-preview 交互流程。"""

    waiting_choice = State()
    waiting_port = State()
