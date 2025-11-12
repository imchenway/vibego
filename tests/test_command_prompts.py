import pytest

from command_center.models import CommandDefinition
from command_center.prompts import build_field_prompt_text


def _make_command(**overrides):
    """构造默认命令对象，便于测试。"""

    base = {
        "id": 1,
        "project_slug": "demo",
        "scope": "project",
        "name": "deploy",
        "title": "部署",
        "command": "./deploy.sh",
        "description": "",
        "timeout": 120,
        "aliases": (),
    }
    base.update(overrides)
    return CommandDefinition(**base)


@pytest.mark.parametrize(
    ("field", "overrides", "expected_fragments"),
    [
        ("command", {"command": "echo 1"}, ["当前指令", "echo 1"]),
        ("command", {"command": "line1\nline2"}, ["line1", "line2"]),
        ("command", {"command": ""}, ["当前指令", "（当前为空）"]),
        ("title", {"title": "原始标题"}, ["当前标题：原始标题"]),
        ("title", {"title": ""}, ["当前标题：", "（当前为空）"]),
        ("description", {"description": "多行\n描述"}, ["当前描述", "多行", "描述"]),
        ("description", {"description": ""}, ["当前描述", "（当前为空）"]),
        ("timeout", {"timeout": 300}, ["当前超时：300 秒"]),
        ("timeout", {"timeout": None}, ["当前超时：- 秒"]),
        ("aliases", {"aliases": ("one", "two")}, ["当前别名：one, two"]),
        ("aliases", {"aliases": ()}, ["当前别名：", "（无别名）"]),
        ("command", {"command": "python script && exit"}, ["python script && exit"]),
    ],
)
def test_build_field_prompt_text_covers_all_fields(field, overrides, expected_fragments):
    command = _make_command(**overrides)
    text = build_field_prompt_text(command, field)
    assert text, "字段提示应返回可展示文本"
    for fragment in expected_fragments:
        assert fragment in text
    assert "发送“取消”可终止当前操作。" in text


def test_build_field_prompt_text_returns_none_on_unknown_field():
    command = _make_command()
    assert build_field_prompt_text(command, "unknown") is None
