"""FSM states for command preset creation and editing."""
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class CommandPresetStates(StatesGroup):
    """Command management wizard states."""

    waiting_name = State()
    waiting_command = State()
    waiting_workdir = State()
    waiting_confirmation_flag = State()
    waiting_final_confirmation = State()
