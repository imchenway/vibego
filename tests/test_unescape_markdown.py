"""测试智能反转义功能，特别是代码块保护场景。"""

import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from bot import (
    _is_already_escaped,
    _unescape_markdown_v2,
    _unescape_if_already_escaped,
)


def test_is_already_escaped():
    """测试预转义检测功能。"""
    print("=" * 60)
    print("测试 1: 预转义检测")
    print("=" * 60)

    test_cases = [
        # (输入, 期望结果, 描述)
        (r"\*\*粗体\*\*", True, "连续转义模式"),
        (r"\#\#\# 标题", True, "标题转义"),
        (r"这是\*\*粗体\*\*文本", True, "包含转义的普通文本"),
        (r"python -m vibego\_cli stop", True, "包含转义下划线"),
        ("正常文本", False, "无转义字符"),
        ("hello_world", False, "普通下划线"),
        ("**粗体**", False, "未转义的粗体"),
        ("短文本", False, "太短的文本"),
        (r"\*", True, "单个转义字符也应识别为已转义"),
    ]

    passed = 0
    failed = 0

    for text, expected, desc in test_cases:
        result = _is_already_escaped(text)
        status = "✅" if result == expected else "❌"
        if result == expected:
            passed += 1
        else:
            failed += 1
        print(f"{status} {desc}")
        print(f"   输入: {repr(text)}")
        print(f"   期望: {expected}, 实际: {result}")
        print()

    print(f"通过: {passed}/{passed + failed}")
    print()
    return failed == 0


def test_unescape_markdown_v2():
    """测试基础反转义功能。"""
    print("=" * 60)
    print("测试 2: 基础反转义")
    print("=" * 60)

    test_cases = [
        # (输入, 期望输出, 描述)
        (r"\*\*粗体\*\*", "**粗体**", "粗体反转义"),
        (r"\#\#\# 标题", "### 标题", "标题反转义"),
        (r"列表\:\n\- 项目1\n\- 项目2", "列表:\n- 项目1\n- 项目2", "列表反转义"),
        (r"代码 \`code\`", "代码 `code`", "行内代码反转义"),
        (r"链接 \[text\]\(url\)", "链接 [text](url)", "链接反转义"),
        (r"python \-m vibego\_cli", "python -m vibego_cli", "命令反转义"),
        ("正常文本", "正常文本", "无需反转义"),
    ]

    passed = 0
    failed = 0

    for input_text, expected, desc in test_cases:
        result = _unescape_markdown_v2(input_text)
        status = "✅" if result == expected else "❌"
        if result == expected:
            passed += 1
        else:
            failed += 1
        print(f"{status} {desc}")
        print(f"   输入: {repr(input_text)}")
        print(f"   期望: {repr(expected)}")
        print(f"   实际: {repr(result)}")
        print()

    print(f"通过: {passed}/{passed + failed}")
    print()
    return failed == 0


def test_code_block_protection():
    """测试代码块保护场景（最重要的测试）。"""
    print("=" * 60)
    print("测试 3: 代码块保护（核心功能）")
    print("=" * 60)

    test_cases = [
        # (输入, 期望输出, 描述)
        (
            r"正常文本\*\*粗体\*\* `code_with\_underscore` 继续文本",
            r"正常文本**粗体** `code_with\_underscore` 继续文本",
            "单行代码块内下划线保护",
        ),
        (
            r"\#\#\# 标题\n\n```python\nprint('hello\_world')\n```\n\n继续\*\*文本\*\*",
            r"### 标题\n\n```python\nprint('hello\_world')\n```\n\n继续**文本**",
            "多行代码块保护",
        ),
        (
            r"使用 `vibego\_cli` 命令",
            r"使用 `vibego\_cli` 命令",
            "行内代码中的转义保持不变",
        ),
        (
            r"```bash\npython -m vibego\_cli stop\npython -m vibego\_cli start\n```",
            r"```bash\npython -m vibego\_cli stop\npython -m vibego\_cli start\n```",
            "代码块内的命令完整保护",
        ),
        (
            r"\*\*步骤\*\*\:\n\n```bash\nls -la\n```\n\n\*\*结果\*\*\: 成功",
            r"**步骤**:\n\n```bash\nls -la\n```\n\n**结果**: 成功",
            "代码块前后文本都反转义",
        ),
        (
            r"`interface\{\}` 是 Go 的语法",
            r"`interface\{\}` 是 Go 的语法",
            "行内代码中的大括号保护",
        ),
        (
            r"配置 `\{\"key\": \"value\"\}` 格式",
            r"配置 `\{\"key\": \"value\"\}` 格式",
            "行内代码中的 JSON 保护",
        ),
    ]

    passed = 0
    failed = 0

    for input_text, expected, desc in test_cases:
        result = _unescape_if_already_escaped(input_text)
        status = "✅" if result == expected else "❌"
        if result == expected:
            passed += 1
        else:
            failed += 1
        print(f"{status} {desc}")
        print(f"   输入: {repr(input_text)}")
        print(f"   期望: {repr(expected)}")
        print(f"   实际: {repr(result)}")
        if result != expected:
            print(f"   差异: 期望与实际不匹配")
        print()

    print(f"通过: {passed}/{passed + failed}")
    print()
    return failed == 0


def test_edge_cases():
    """测试边界情况。"""
    print("=" * 60)
    print("测试 4: 边界情况")
    print("=" * 60)

    test_cases = [
        # (输入, 期望输出, 描述)
        ("", "", "空字符串"),
        ("   ", "   ", "仅空格"),
        (None, None, "None 值"),  # 需要处理 None
        ("正常文本无需处理", "正常文本无需处理", "未检测到预转义"),
        (r"\*", "*", "单个转义字符同样需要反转义"),
        (r"```\n\n```", r"```\n\n```", "空代码块"),
        (r"`单独的反引号", r"`单独的反引号", "不匹配的反引号"),
        (r"混合 **未转义** 和 \*\*已转义\*\*", r"混合 **未转义** 和 **已转义**", "混合转义状态"),
    ]

    passed = 0
    failed = 0

    for input_text, expected, desc in test_cases:
        try:
            if input_text is None:
                # _unescape_if_already_escaped 应该处理 None
                result = _unescape_if_already_escaped(input_text)
            else:
                result = _unescape_if_already_escaped(input_text)
            status = "✅" if result == expected else "❌"
            if result == expected:
                passed += 1
            else:
                failed += 1
            print(f"{status} {desc}")
            print(f"   输入: {repr(input_text)}")
            print(f"   期望: {repr(expected)}")
            print(f"   实际: {repr(result)}")
            print()
        except Exception as e:
            failed += 1
            print(f"❌ {desc}")
            print(f"   输入: {repr(input_text)}")
            print(f"   错误: {e}")
            print()

    print(f"通过: {passed}/{passed + failed}")
    print()
    return failed == 0


def test_real_world_example():
    """测试真实场景示例（来自用户提供的问题）。"""
    print("=" * 60)
    print("测试 5: 真实场景示例")
    print("=" * 60)

    # 用户提供的问题示例
    input_text = r"""\#\#\# 📋 后续步骤

1\. \*\*重启 Bot 服务\*\*以应用修复：
   \`\`\`bash
   python -m vibego\_cli stop
   python -m vibego\_cli start
   \`\`\`

2\. \*\*验证 TASK\_0011\*\* 现在可以正常显示：
   - 在 Telegram 中点击任务列表中的 TASK\_0011
   - 应该可以看到完整的任务详情，不再显示错误"""

    expected_output = r"""### 📋 后续步骤

1. **重启 Bot 服务**以应用修复：
   ```bash
   python -m vibego\_cli stop
   python -m vibego\_cli start
   ```

2. **验证 TASK_0011** 现在可以正常显示：
   - 在 Telegram 中点击任务列表中的 TASK_0011
   - 应该可以看到完整的任务详情，不再显示错误"""

    result = _unescape_if_already_escaped(input_text)

    if result == expected_output:
        print("✅ 真实场景测试通过")
        print("   问题已修复：代码块内的 vibego_cli 命令保持转义")
        print("   普通文本的转义符号已清理")
        print()
        return True
    else:
        print("❌ 真实场景测试失败")
        print(f"   输入长度: {len(input_text)}")
        print(f"   期望长度: {len(expected_output)}")
        print(f"   实际长度: {len(result)}")
        print()
        print("差异详情:")
        print("=" * 60)
        print("期望输出:")
        print(expected_output)
        print("=" * 60)
        print("实际输出:")
        print(result)
        print("=" * 60)
        return False


def test_performance():
    """测试性能（可选）。"""
    print("=" * 60)
    print("测试 6: 性能测试")
    print("=" * 60)

    import time

    # 模拟大文本
    large_text = r"\*\*标题\*\*\n" * 1000 + r"```python\ncode\n```" * 100

    start = time.time()
    for _ in range(100):
        _unescape_if_already_escaped(large_text)
    elapsed = time.time() - start

    print(f"✅ 处理 100 次大文本耗时: {elapsed:.3f} 秒")
    print(f"   平均每次: {elapsed / 100 * 1000:.2f} 毫秒")
    print(f"   文本大小: {len(large_text)} 字符")
    print()

    # 性能阈值：平均每次处理应该在 10ms 以内
    if elapsed / 100 < 0.01:
        print("✅ 性能测试通过")
        return True
    else:
        print("⚠️  性能测试警告：处理速度较慢")
        return True  # 不算失败，只是警告


def main():
    """运行所有测试。"""
    print("\n")
    print("╔" + "=" * 58 + "╗")
    print("║" + " " * 15 + "智能反转义功能测试套件" + " " * 15 + "║")
    print("╚" + "=" * 58 + "╝")
    print("\n")

    results = {
        "预转义检测": test_is_already_escaped(),
        "基础反转义": test_unescape_markdown_v2(),
        "代码块保护": test_code_block_protection(),
        "边界情况": test_edge_cases(),
        "真实场景": test_real_world_example(),
        "性能测试": test_performance(),
    }

    print("\n")
    print("=" * 60)
    print("测试总结")
    print("=" * 60)

    passed_count = sum(1 for passed in results.values() if passed)
    total_count = len(results)

    for name, passed in results.items():
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"{status} - {name}")

    print("=" * 60)
    print(f"总计: {passed_count}/{total_count} 通过")
    print("=" * 60)

    if passed_count == total_count:
        print("\n🎉 所有测试通过！代码块保护功能正常工作。\n")
        return 0
    else:
        print(f"\n⚠️  有 {total_count - passed_count} 个测试失败，需要修复。\n")
        return 1


if __name__ == "__main__":
    exit(main())
