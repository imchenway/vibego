"""aiogram FSM state definitions."""
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class TaskCreateStates(StatesGroup):
    """States for the guided task creation flow."""

    waiting_title = State()
    waiting_type = State()
    waiting_description = State()
    waiting_confirm = State()


class TaskEditStates(StatesGroup):
    """States for editing existing tasks."""

    waiting_task_id = State()
    waiting_field_choice = State()
    waiting_new_value = State()
    waiting_confirm = State()


class TaskNoteStates(StatesGroup):
    """States for appending notes to a task."""

    waiting_task_id = State()
    waiting_content = State()
    waiting_type = State()


class TaskBugReportStates(StatesGroup):
    """States for the defect reporting flow."""

    waiting_description = State()
    waiting_reproduction = State()
    waiting_logs = State()
    waiting_confirm = State()


class TaskDescriptionStates(StatesGroup):
    """States for editing task descriptions."""

    waiting_content = State()
    waiting_confirm = State()


class TaskPushStates(StatesGroup):
    """States for sending supplemental task updates."""

    waiting_choice = State()
    waiting_supplement = State()


class TaskListSearchStates(StatesGroup):
    """States for searching within the task list."""

    waiting_keyword = State()


class ProjectDeleteStates(StatesGroup):
    """States for confirming master project deletion."""

    confirming = State()
