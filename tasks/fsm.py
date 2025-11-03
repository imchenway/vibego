"""aiogram FSM 状态定义。"""
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class TaskCreateStates(StatesGroup):
    """任务创建引导流程的状态集。"""

    waiting_title = State()
    waiting_type = State()
    waiting_description = State()
    waiting_confirm = State()


class TaskEditStates(StatesGroup):
    """任务编辑流程的状态集。"""

    waiting_task_id = State()
    waiting_field_choice = State()
    waiting_new_value = State()
    waiting_confirm = State()


class TaskNoteStates(StatesGroup):
    """任务备注追加流程的状态集。"""

    waiting_task_id = State()
    waiting_content = State()
    waiting_type = State()


class TaskBugReportStates(StatesGroup):
    """缺陷报告流程状态。"""

    waiting_description = State()
    waiting_reproduction = State()
    waiting_logs = State()
    waiting_confirm = State()


class TaskDescriptionStates(StatesGroup):
    """任务描述编辑流程状态。"""

    waiting_content = State()
    waiting_confirm = State()


class TaskPushStates(StatesGroup):
    """任务推送补充信息流程的状态集。"""

    waiting_choice = State()
    waiting_supplement = State()


class TaskListSearchStates(StatesGroup):
    """任务列表搜索流程的状态集。"""

    waiting_keyword = State()


class ProjectDeleteStates(StatesGroup):
    """Master 项目删除确认流程的状态定义。"""

    confirming = State()
