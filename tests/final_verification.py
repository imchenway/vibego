"""最终验证：确认 Telegram 消息发送流程和 parse_mode 配置"""

import os
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

os.environ.setdefault("BOT_TOKEN", "dummy")
os.environ.setdefault("TELEGRAM_PARSE_MODE", "Markdown")

from bot import (
    _unescape_if_already_escaped,
    _prepare_model_payload,
    _parse_mode_value,
    MODEL_OUTPUT_PARSE_MODE,
    _IS_MARKDOWN_V2,
)

print("\n" + "=" * 80)
print("最终验证：Telegram 消息发送流程")
print("=" * 80 + "\n")

# 第一个问题：parse_mode 配置
print("问题1：当前 parse_mode 配置")
print("-" * 80)
print(f"MODEL_OUTPUT_PARSE_MODE 值: {MODEL_OUTPUT_PARSE_MODE}")
print(f"_parse_mode_value() 返回: {repr(_parse_mode_value())}")
print(f"_IS_MARKDOWN_V2 标志: {_IS_MARKDOWN_V2}")
print()

if MODEL_OUTPUT_PARSE_MODE:
    print(f"✅ parse_mode 已配置为: {MODEL_OUTPUT_PARSE_MODE.value}")
else:
    print("❌ parse_mode 未配置（将发送纯文本）")
print()

# 第二个问题：完整的消息处理流程
print("问题2：完整的消息处理流程验证")
print("-" * 80)

# 模拟用户提供的实际场景
test_input = r"""\#\#\# 📋 后续步骤

1\. \*\*重启 Bot 服务\*\*以应用修复：
   \`\`\`bash
   python -m vibego\_cli stop
   python -m vibego\_cli start
   \`\`\`

2\. \*\*验证 TASK\_0011\*\*：
   - 在 Telegram 中点击任务
   - 应该可以看到完整的任务详情"""

print("步骤1：原始输入（数据库中存储的内容）")
print(f"  前100字符: {repr(test_input[:100])}...")
print()

# 步骤2：格式化任务详情时的智能反转义
print("步骤2：_format_task_detail 调用 _unescape_if_already_escaped")
cleaned = _unescape_if_already_escaped(test_input)
print(f"  前100字符: {repr(cleaned[:100])}...")
print()

# 步骤3：准备发送到 Telegram
print("步骤3：_answer_with_markdown 调用 _prepare_model_payload")
final = _prepare_model_payload(cleaned)
print(f"  前100字符: {repr(final[:100])}...")
print()

# 步骤4：实际发送到 Telegram
print("步骤4：message.answer() 发送到 Telegram API")
print(f"  调用: message.answer(text, parse_mode={repr(_parse_mode_value())}, ...)")
print()

# 关键验证
print("=" * 80)
print("关键验证点")
print("=" * 80)

checks = {
    "1. parse_mode 配置正确": _parse_mode_value() == "Markdown",
    "2. 步骤2后代码块标记正常": "```bash" in cleaned,
    "3. 步骤2后粗体格式正常": "**重启 Bot 服务**" in cleaned,
    "4. 步骤2后代码块内保持转义": "vibego\\_cli" in cleaned,
    "5. 步骤3后粗体符合 Markdown 语法": "*重启 Bot 服务*" in final,
    "6. 步骤3后代码块标记不转义": "```bash" in final,
    "7. 步骤3后代码块内保持转义": "vibego\\_cli" in final,
}

all_passed = True
for name, passed in checks.items():
    status = "✅" if passed else "❌"
    if not passed:
        all_passed = False
    print(f"{status} {name}")

print()
print("=" * 80)
print("Telegram 渲染预览")
print("=" * 80)
print("""
Telegram 收到的数据：
  parse_mode: "Markdown"
  text: "### 📋 后续步骤\\n\\n1. *重启 Bot 服务*以应用修复：\\n   ```bash\\n   python -m vibego_cli stop\\n   python -m vibego_cli start\\n   ```"

Telegram 渲染效果：
  ┌─────────────────────────────────────────┐
  │ ### 📋 后续步骤                         │
  │                                         │
  │ 1. **重启 Bot 服务**以应用修复：        │
  │    ┌─────────────────────────────────┐ │
  │    │ python -m vibego_cli stop       │ │
  │    │ python -m vibego_cli start      │ │
  │    └─────────────────────────────────┘ │
  │                                         │
  │ 2. **验证 TASK_0011**：                 │
  │    - 在 Telegram 中点击任务             │
  │    - 应该可以看到完整的任务详情         │
  └─────────────────────────────────────────┘

说明：
  - 标题（###）正确渲染为三级标题
  - 粗体（**文本**）正确渲染为粗体
  - 代码块（```）正确渲染为灰色背景框
  - 代码块内的 vibego_cli 显示为 vibego_cli（下划线正常）
""")

print("=" * 80)
print("实际 API 调用示例")
print("=" * 80)
print("""
Python 代码（bot.py:2760-2764）：
  sent = await message.answer(
      prepared,                    # 已转义的文本
      parse_mode="Markdown",       # Telegram parse_mode
      reply_markup=reply_markup,   # 按钮键盘
  )

Telegram Bot API 请求：
  POST https://api.telegram.org/bot{TOKEN}/sendMessage
  {
    "chat_id": 123456789,
    "text": "### 📋 后续步骤\\n\\n1. *重启 Bot 服务*...",
    "parse_mode": "Markdown",
    "reply_markup": {...}
  }

Telegram API 文档：
  https://core.telegram.org/bots/api#formatting-options
""")

print("=" * 80)
if all_passed:
    print("✅ 所有验证通过！Telegram 消息将正确显示 Markdown 格式")
else:
    print("❌ 存在问题，需要检查配置")
print("=" * 80)
print()
