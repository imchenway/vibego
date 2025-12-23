from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_TOKEN", "dummy-token")
os.environ.setdefault("TELEGRAM_PARSE_MODE", "Markdown")

import bot  # noqa: E402


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        # 任务编码/命令：下划线需要转义，避免触发斜体实体解析失败
        ("任务编码：/TASK_0027", r"任务编码：/TASK\_0027"),
        (
            "点击生成任务摘要：/task_summary_request_TASK_0027",
            r"点击生成任务摘要：/task\_summary\_request\_TASK\_0027",
        ),
        # 行内代码块内的内容不应被改写（避免出现多余反斜杠）
        ("命令：`/TASK_0027`", "命令：`/TASK_0027`"),
        # 兼容模型输出的 * item 列表符号，避免误解析为加粗实体起始
        ("* item\n  * indented\n* item2", "- item\n  - indented\n- item2"),
        # 行内出现 ``` 常见于“举例说明”，需要转义避免被误判为代码块起始
        ("例如半截代码块 ``` 会导致解析失败", r"例如半截代码块 \`\`\` 会导致解析失败"),
        # fence 代码块行若包含语言标记，统一移除，提升 legacy Markdown 兼容性
        ("```bash\necho hi\n```", "```\necho hi\n```"),
        # fence 未闭合时，自动追加闭合标记，避免整条消息解析失败
        ("示例：\n```bash\necho hi\n", "示例：\n```\necho hi\n```"),
        # 未配对的格式标记：转义最后一个，避免 can't parse entities
        ("注意：*重要", r"注意：\*重要"),
        ("变量名：_value", r"变量名：\_value"),
        ("这里有 `未闭合代码", r"这里有 \`未闭合代码"),
        # 代码块内容不应被改写（列表符号/下划线等保持原样）
        ("```\n* item\n/TASK_0027\n```", "```\n* item\n/TASK_0027\n```"),
        # 标准 Markdown 语法的归一化仍应保持可读性
        ("**bold** text", "*bold* text"),
    ],
)
def test_prepare_model_payload_sanitizes_markdown_legacy(source: str, expected: str) -> None:
    assert bot._prepare_model_payload(source) == expected

