import sys
import os
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

import pytest

os.environ.setdefault("BOT_TOKEN", "dummy")
os.environ.setdefault("TELEGRAM_PARSE_MODE", "Markdown")

from bot import _prepare_model_payload, _prepare_model_payload_variants  # noqa: E402


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("**bold** text", "*bold* text"),
        ("__italic__", "_italic_"),
        ("`code_snippet`", "`code_snippet`"),
        ("普通文本 (test)", "普通文本 (test)"),
        ("raw *literal* star", "raw *literal* star"),
        ("multiline\nline2", "multiline\nline2"),
    ],
)
def test_prepare_model_payload_markdown(source: str, expected: str) -> None:
    assert _prepare_model_payload(source) == expected


def test_prepare_model_payload_preserves_checklist() -> None:
    text = "- [ ] 第一项\n- [ ] 第二项"
    assert _prepare_model_payload(text) == text


def test_prepare_model_payload_handles_list_numbers() -> None:
    text = "1. 选择 A\n2. 选择 B"
    assert _prepare_model_payload(text) == text


def test_prepare_model_payload_variants_returns_single_payload() -> None:
    text = "纯文本内容"
    primary, fallback = _prepare_model_payload_variants(text)
    assert primary == "纯文本内容"
    assert fallback is None
