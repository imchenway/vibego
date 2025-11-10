import os
import sys

ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("BOT_TOKEN", "TEST_TOKEN")
os.environ.setdefault("MODEL_WORKDIR", "/tmp")

import bot  # noqa: E402


def test_strip_completion_header_removes_prefix():
    base = "Hello world"
    prefixed = f"{bot.MODEL_COMPLETION_PREFIX}\n\n{base}"
    assert bot._strip_completion_header(prefixed) == base


def test_strip_completion_header_returns_original_when_missing():
    text = "No prefix content"
    assert bot._strip_completion_header(text) == text


def test_hash_variants_identify_same_message_with_and_without_prefix():
    base = "Result payload"
    prefixed = f"{bot.MODEL_COMPLETION_PREFIX}\n\n{base}"
    without_prefix = base
    prefixed_hashes = bot._hash_delivery_variants(prefixed)
    base_hashes = bot._hash_delivery_variants(without_prefix)
    assert base_hashes.issubset(prefixed_hashes)
