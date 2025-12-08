"""验证命令字符串长度与空值校验。"""
from pathlib import Path

import pytest

from command_center.service import CommandService


@pytest.fixture
def service(tmp_path: Path) -> CommandService:
    """构造临时 CommandService，使用临时数据库路径。"""
    return CommandService(tmp_path / "commands.db", "proj")


@pytest.mark.parametrize(
    ("value", "should_pass"),
    [
        ("a", True),  # 最小非空
        ("x" * 1024, True),  # 边界：正好 1024
        ("x" * 1025, False),  # 超过上限
        ("", False),  # 为空
        ("   ", False),  # 空白
        ("echo hi", True),  # 正常指令
        (" \nabc\n ", True),  # 去除首尾空白后有效
        ("x" * 512 + "\n", True),  # 去除结尾换行后长度仍合法
        ("\t", False),  # 制表符去空后为空
        ("x" * 1023 + " ", True),  # 去除尾空后 1023
    ],
)
def test_sanitize_command_limits(service: CommandService, value: str, should_pass: bool) -> None:
    """覆盖指令字段的长度与空值边界场景。"""
    if should_pass:
        assert service._sanitize_command(value) == value.strip()
    else:
        with pytest.raises(ValueError):
            service._sanitize_command(value)
