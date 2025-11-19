"""命令字段提示与预览构建工具。"""
from __future__ import annotations

from typing import Optional
import textwrap

from command_center.models import CommandDefinition


def _indent_block(content: str) -> str:
    """缩进多行文本，便于展示。"""

    if not content:
        return "    （当前为空）"
    return textwrap.indent(content, "    ")


def build_field_preview_text(command: CommandDefinition, field: str) -> str:
    """根据字段类型生成当前值预览文本。"""

    if field == "command":
        header = "当前指令："
        body = _indent_block(command.command or "")
        return f"{header}\n{body}"
    if field == "title":
        return f"当前标题：{command.title or '（当前为空）'}"
    if field == "description":
        if command.description:
            return f"当前描述：\n{_indent_block(command.description)}"
        return "当前描述： （当前为空）"
    if field == "timeout":
        timeout_value = command.timeout if command.timeout is not None else "-"
        return f"当前超时：{timeout_value} 秒"
    if field == "aliases":
        alias_text = ", ".join(command.aliases) if command.aliases else "（无别名）"
        return f"当前别名：{alias_text}"
    return ""


def build_field_prompt_text(
    command: CommandDefinition,
    field: str,
    *,
    include_cancel_hint: bool = True,
) -> Optional[str]:
    """拼装包含当前值预览与交互提示的完整文本。"""

    prompt_map = {
        "title": "请输入新的命令标题：",
        "command": "请输入新的执行指令（可包含参数）：",
        "description": "请输入新的命令描述（可留空）：",
        "timeout": "请输入新的超时时间（单位秒，5-3600）：",
        "aliases": "请输入全部别名，以逗号或空格分隔，发送 - 可清空：",
    }
    prompt = prompt_map.get(field)
    if prompt is None:
        return None
    preview = build_field_preview_text(command, field)
    lines: list[str] = []
    if preview:
        lines.append(preview)
        lines.append("")
    lines.append(prompt)
    if include_cancel_hint:
        lines.append("发送“取消”可终止当前操作。")
    return "\n".join(lines)
