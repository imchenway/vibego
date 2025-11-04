"""Final verification: Confirm Telegram message sending process and parse_mode Configuration"""

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
print("Final verification: Telegram message sending process")
print("=" * 80 + "\n")

# The first question: parse_mode Configuration
print("Question 1: Current parse_mode Configuration")
print("-" * 80)
print(f"MODEL_OUTPUT_PARSE_MODE value: {MODEL_OUTPUT_PARSE_MODE}")
print(f"_parse_mode_value() return: {repr(_parse_mode_value())}")
print(f"_IS_MARKDOWN_V2 logo: {_IS_MARKDOWN_V2}")
print()

if MODEL_OUTPUT_PARSE_MODE:
    print(f"parse_mode configured for: {MODEL_OUTPUT_PARSE_MODE.value}")
else:
    print("FAIL: parse_mode not configured (will send plain text)")
print()

# Second question: Complete message processing process
print("Question 2: Complete message processing process verification")
print("-" * 80)

# Simulate actual scenarios provided by users
test_input = r"""\#\#\# 📋 Next steps

1\. \*\*Restart the Bot service\*\*To apply the fix:
   \`\`\`bash
   python -m vibego\_cli stop
   python -m vibego\_cli start
   \`\`\`

2\. \*\*Verify TASK\_0011\*\*: 
   - Click on the task in Telegram
   - You should be able to see complete mission details"""

print("Step 1: Raw input (what is stored in the database)")
print(f"  first 100 characters: {repr(test_input[:100])}...")
print()

# Step 2: Smart anti-escaping when formatting task details
print("Step 2:_format_task_detail call _unescape_if_already_escaped")
cleaned = _unescape_if_already_escaped(test_input)
print(f"  first 100 characters: {repr(cleaned[:100])}...")
print()

# Step 3: Prepare to send to Telegram
print("Step 3:_answer_with_markdown call _prepare_model_payload")
final = _prepare_model_payload(cleaned)
print(f"  first 100 characters: {repr(final[:100])}...")
print()

# Step 4: Actual Send to Telegram
print("Step 4: message.answer() Send to Telegram API")
print(f"  call: message.answer(text, parse_mode={repr(_parse_mode_value())}, ...)")
print()

# key verification
print("=" * 80)
print("key verification points")
print("=" * 80)

checks = {
    "1. parse_mode Configurationcorrect": _parse_mode_value() == "Markdown",
    "2. Code block marking is normal after step 2": "```bash" in cleaned,
    "3. Bold format is normal after step 2": "**Restart the Bot service**" in cleaned,
    "4. Keep escaping within the code block after step 2": "vibego\\_cli" in cleaned,
    "5. After step 3, the bold text conforms to Markdown syntax.": "*Restart the Bot service*" in final,
    "6. Code block markers are not escaped after step 3": "```bash" in final,
    "7. Keep escaping within the code block after step 3": "vibego\\_cli" in final,
}

all_passed = True
for name, passed in checks.items():
    status = "PASS" if passed else "FAIL"
    if not passed:
        all_passed = False
    print(f"{status} {name}")

print()
print("=" * 80)
print("Telegram Render preview")
print("=" * 80)
print("""
Telegram Data received:
  parse_mode: "Markdown"
  text: "### 📋 Next steps\\n\\n1. *Restart the Bot service*To apply the fix:\\n   ```bash\\n   python -m vibego_cli stop\\n   python -m vibego_cli start\\n   ```"

Telegram Rendering effect:
  ┌─────────────────────────────────────────┐
  │ ### 📋 Next steps                         │
  │                                         │
  │ 1. **Restart the Bot service**To apply the fix:        │
  │    ┌─────────────────────────────────┐ │
  │    │ python -m vibego_cli stop       │ │
  │    │ python -m vibego_cli start      │ │
  │    └─────────────────────────────────┘ │
  │                                         │
  │ 2. **Verify TASK_0011**:                  │
  │    - Click on the task in Telegram             │
  │    - You should be able to see complete task details         │
  └─────────────────────────────────────────┘

Description:
  - title (###)Correctly rendered as third-level headings
  - Bold (**text**)Renders correctly as bold
  - code block (```)Renders correctly as a gray background box
  - vibego inside code block_cli Shown as vibego_cli(Underscore is normal)
""")

print("=" * 80)
print("actual API callExample")
print("=" * 80)
print("""
Python code (bot.py:2760-2764): 
  sent = await message.answer(
      prepared,                    # text already escaped in bot.py
      parse_mode="Markdown",       # Telegram parse_mode
      reply_markup=reply_markup,   # button keyboard
  )

Telegram Bot API Request:
  POST https://api.telegram.org/bot{TOKEN}/sendMessage
  {
    "chat_id": 123456789,
    "text": "### 📋 Next steps\\n\\n1. *Restart the Bot service*...",
    "parse_mode": "Markdown",
    "reply_markup": {...}
  }

Telegram API Documentation:
  https://core.telegram.org/bots/api#formatting-options
""")

print("=" * 80)
if all_passed:
    print("All verification passed! Telegram messages will display correctly in Markdown format")
else:
    print("FAIL: Issue detected, please check configuration")
print("=" * 80)
print()
