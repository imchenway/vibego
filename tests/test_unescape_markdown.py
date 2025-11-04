"""Test smart anti-escaping capabilities, especially code block protection scenarios."""

import sys
from pathlib import Path

# Add project root directory to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from bot import (
    _is_already_escaped,
    _unescape_markdown_v2,
    _unescape_if_already_escaped,
)


def test_is_already_escaped():
    """Test pre-escape detection functionality."""
    print("=" * 60)
    print("Test 1: Pre-escape detection")
    print("=" * 60)

    test_cases = [
        # (enter, expected result, describe)
        (r"\*\*bold\*\*", True, "Continuous escape mode"),
        (r"\#\#\# title", True, "titleescape"),
        (r"This is\*\*bold\*\*text", True, "Includeescapeof ordinarytext"),
        (r"python -m vibego\_cli stop", True, "Contains escaped underscores"),
        ("normal text", False, "No escape characters"),
        ("hello_world", False, "plain underscore"),
        ("**Bold**", False, "unescapedBold"),
        ("short text", False, "text too short"),
        (r"\*", True, "Single escaped characters should also be recognized as escaped"),
    ]

    passed = 0
    failed = 0

    for text, expected, desc in test_cases:
        result = _is_already_escaped(text)
        status = "PASS" if result == expected else "FAIL"
        if result == expected:
            passed += 1
        else:
            failed += 1
        print(f"{status} {desc}")
        print(f"   enter: {repr(text)}")
        print(f"   expect: {expected}, actual: {result}")
        print()

    print(f"pass: {passed}/{passed + failed}")
    print()
    return failed == 0


def test_unescape_markdown_v2():
    """Test basic anti-escaping functionality."""
    print("=" * 60)
    print("Test 2: Basic anti-escaping")
    print("=" * 60)

    test_cases = [
        # (enter, expectoutput, describe)
        (r"\*\*bold\*\*", "**Bold**", "Boldoppositeescape"),
        (r"\#\#\# title", "### title", "titleoppositeescape"),
        (r"List\:\n\- Project 1\n\- Project 2", "list:\n- Item 1\n- Item 2", "listoppositeescape"),
        (r"code \`code\`", "code `code`", "Inside the industrycodeoppositeescape"),
        (r"Link \[text\]\(url\)", "Link [text](url)", "Linkoppositeescape"),
        (r"python \-m vibego\_cli", "python -m vibego_cli", "command escaping"),
        ("normal text", "normal text", "No need to unescape"),
    ]

    passed = 0
    failed = 0

    for input_text, expected, desc in test_cases:
        result = _unescape_markdown_v2(input_text)
        status = "PASS" if result == expected else "FAIL"
        if result == expected:
            passed += 1
        else:
            failed += 1
        print(f"{status} {desc}")
        print(f"   enter: {repr(input_text)}")
        print(f"   expect: {repr(expected)}")
        print(f"   actual: {repr(result)}")
        print()

    print(f"pass: {passed}/{passed + failed}")
    print()
    return failed == 0


def test_code_block_protection():
    """Test the code block protection scenario (the most important test)."""
    print("=" * 60)
    print("Test 3: codeBlock protection (core feature)")
    print("=" * 60)

    test_cases = [
        # (enter, expectoutput, describe)
        (
            r"normal text\*\*bold\*\* `code_with\_underscore` continuetext",
            r"normal text**Bold** `code_with\_underscore` continuetext",
            "Underline protection within single-line code block",
        ),
        (
            r"\#\#\# title\n\n```python\nprint('hello\_world')\n```\n\ncontinue\*\*text\*\*",
            r"### title\n\n```python\nprint('hello\_world')\n```\n\ncontinue**text**",
            "Multi-line code block protection",
        ),
        (
            r"use `vibego\_cli` Order",
            r"use `vibego\_cli` Order",
            "Inline code segments remain escaped",
        ),
        (
            r"```bash\npython -m vibego\_cli stop\npython -m vibego\_cli start\n```",
            r"```bash\npython -m vibego\_cli stop\npython -m vibego\_cli start\n```",
            "Commands inside code blocks remain untouched",
        ),
        (
            r"\*\*step\*\*\:\n\n```bash\nls -la\n```\n\n\*\*result\*\*\: success",
            r"**step**:\n\n```bash\nls -la\n```\n\n**result**: success",
            "Text surrounding code blocks is left unchanged",
        ),
        (
            r"`interface\{\}` is the syntax of Go",
            r"`interface\{\}` is the syntax of Go",
            "Braces remain quoted inside inline code",
        ),
        (
            r"Configuration `\{\"key\": \"value\"\}` Format",
            r"Configuration `\{\"key\": \"value\"\}` Format",
            "JSON braces remain protected inside inline code",
        ),
    ]

    passed = 0
    failed = 0

    for input_text, expected, desc in test_cases:
        result = _unescape_if_already_escaped(input_text)
        status = "PASS" if result == expected else "FAIL"
        if result == expected:
            passed += 1
        else:
            failed += 1
        print(f"{status} {desc}")
        print(f"   enter: {repr(input_text)}")
        print(f"   expect: {repr(expected)}")
        print(f"   actual: {repr(result)}")
        if result != expected:
            print(f"   difference: expectDoes not match actual")
        print()

    print(f"pass: {passed}/{passed + failed}")
    print()
    return failed == 0


def test_edge_cases():
    """Test edge cases."""
    print("=" * 60)
    print("Test 4: boundary case")
    print("=" * 60)

    test_cases = [
        # (enter, expectoutput, describe)
        ("", "", "empty string"),
        ("   ", "   ", "spaces only"),
        (None, None, "None value"),  # Needs to be processed None
        ("normal textNo processing required", "normal textNo processing required", "Pre-escaping not detected"),
        (r"\*", "*", "Single escape characters also need to be unescaped"),
        (r"```\n\n```", r"```\n\n```", "empty code block"),
        (r"`Backticks alone", r"`Backticks alone", "unmatched backtick"),
        (r"mix **unescaped** and \*\*Escaped\*\*", r"mix **unescaped** and **escaped**", "mixescapestate"),
    ]

    passed = 0
    failed = 0

    for input_text, expected, desc in test_cases:
        try:
            if input_text is None:
                # _unescape_if_already_escaped Should handle None
                result = _unescape_if_already_escaped(input_text)
            else:
                result = _unescape_if_already_escaped(input_text)
            status = "PASS" if result == expected else "FAIL"
            if result == expected:
                passed += 1
            else:
                failed += 1
            print(f"{status} {desc}")
            print(f"   enter: {repr(input_text)}")
            print(f"   expect: {repr(expected)}")
            print(f"   actual: {repr(result)}")
            print()
        except Exception as e:
            failed += 1
            print(f"FAIL {desc}")
            print(f"   enter: {repr(input_text)}")
            print(f"   mistake: {e}")
            print()

    print(f"pass: {passed}/{passed + failed}")
    print()
    return failed == 0


def test_real_world_example():
    """Test real-life scenario examples (from user-provided questions)."""
    print("=" * 60)
    print("Test 5: Real scenario examples")
    print("=" * 60)

    # Examples of questions provided by users
    input_text = r"""\#\#\# 📋 Follow-up steps

1\. \*\*Restart the Bot service\*\*To apply the fix:
   \`\`\`bash
   python -m vibego\_cli stop
   python -m vibego\_cli start
   \`\`\`

2\. \*\*Verify TASK\_0011\*\* Now it displays normally:
   - Click TASK\ in the task list in Telegram_0011
   - You should be able to see the complete task details and no more mistakes will be displayed."""

    expected_output = r"""### 📋 Follow-up steps

1. **Restart the Bot service**To apply the fix:
   ```bash
   python -m vibego\_cli stop
   python -m vibego\_cli start
   ```

2. **Verify TASK_0011** Now it displays normally:
   - Click TASK in the task list in Telegram_0011
   - You should be able to see the complete task details and no more mistakes will be displayed."""

    result = _unescape_if_already_escaped(input_text)

    if result == expected_output:
        print("Real scenario test passed")
        print("   Issue fixed: vibego inside code block_cli OrderKeepescape")
        print("   ordinarytextofescapeSymbols cleaned")
        print()
        return True
    else:
        print("FAIL Real scenario test failed")
        print(f"   enterlength: {len(input_text)}")
        print(f"   expectlength: {len(expected_output)}")
        print(f"   actuallength: {len(result)}")
        print()
        print("differenceDetails:")
        print("=" * 60)
        print("expectoutput:")
        print(expected_output)
        print("=" * 60)
        print("actualoutput:")
        print(result)
        print("=" * 60)
        return False


def test_performance():
    """Test performance (optional)."""
    print("=" * 60)
    print("Test 6: Performance testing")
    print("=" * 60)

    import time

    # Simulate large text
    large_text = r"\*\*title\*\*\n" * 1000 + r"```python\ncode\n```" * 100

    start = time.time()
    for _ in range(100):
        _unescape_if_already_escaped(large_text)
    elapsed = time.time() - start

    print(f"Processing large text 100 times took {elapsed:.3f} seconds")
    print(f"   average every time: {elapsed / 100 * 1000:.2f} Millisecond")
    print(f"   textsize: {len(large_text)} character")
    print()

    # Performance threshold value: average every time processing should be within 10ms
    if elapsed / 100 < 0.01:
        print("Performance test passed")
        return True
    else:
        print("⚠️  Performance testingWarning: Slow processing")
        return True  # Not a failure, just a warning


def main():
    """Run all tests."""
    print("\n")
    print("╔" + "=" * 58 + "╗")
    print("║" + " " * 15 + "Smart anti-escaping functional test suite" + " " * 15 + "║")
    print("╚" + "=" * 58 + "╝")
    print("\n")

    results = {
        "Pre-escape detection": test_is_already_escaped(),
        "Basic anti-escaping": test_unescape_markdown_v2(),
        "codeblock protection": test_code_block_protection(),
        "boundary case": test_edge_cases(),
        "real scene": test_real_world_example(),
        "Performance testing": test_performance(),
    }

    print("\n")
    print("=" * 60)
    print("Test summary")
    print("=" * 60)

    passed_count = sum(1 for passed in results.values() if passed)
    total_count = len(results)

    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"{status} - {name}")

    print("=" * 60)
    print(f"total: {passed_count}/{total_count} pass")
    print("=" * 60)

    if passed_count == total_count:
        print("\n🎉 Placehavetest pass!codeblock protectionFunction works fine.\n")
        return 0
    else:
        print(f"\n⚠️  have {total_count - passed_count} A test failed and needs to be fixed. \n")
        return 1


if __name__ == "__main__":
    exit(main())
