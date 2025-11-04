"""Verify complete processing flow"""

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from bot import _unescape_if_already_escaped, _prepare_model_payload

print("\n" + "=" * 80)
print("Validation: smart escaping + Formatting process")
print("=" * 80 + "\n")

# Test real-life scenarios provided by users
original_input = r"""\#\#\# 📋 Next steps

1\. \*\*Restart the Bot service\*\*To apply the fix:
   \`\`\`bash
   python -m vibego\_cli stop
   \`\`\`"""

print("Step 1: Raw input (what is stored in the database)")
print("-" * 80)
print(original_input)
print()

# simulation _format_task_detail processing
print("Step 2: Smart Unescaping (_unescape_if_already_escaped)")
print("-" * 80)
cleaned = _unescape_if_already_escaped(original_input)
print(cleaned)
print()

print("Step 3: Prepare to send to Telegram (_prepare_model_payload)")
print("-" * 80)
final = _prepare_model_payload(cleaned)
print(final)
print()

print("=" * 80)
print("critical check")
print("=" * 80)

checks = {
    "After step 2: Code block tags are ok": "```bash" in cleaned,
    "After step 2: Bold formatting is OK": "**Restart the Bot service**" in cleaned,
    "After step 2: Keep escaping within code blocks": "vibego\\_cli" in cleaned,
    "After Step 3: Code block tags remain": "```bash" in final,
    "After Step 3: Bold conforms to Markdown": "*Restart the Bot service*" in final,
    "After step 3: Underlines within code blocks are protected": "vibego\\_cli" in final,
}

for name, passed in checks.items():
    status = "PASS" if passed else "FAIL"
    print(f"{status} {name}")

print()
print("=" * 80)
print("illustrate")
print("=" * 80)
print("""
Expected behavior:
1. Step 2 (anti-escaping): Clean up external escape characters and protect the code block content
   - Code block tag: \`\`\`bash → ```bash (ok)
   - Bold text:\*\*text\*\* → **text** (ok)
   - Within the code block: vibego\_cli → vibego\_cli (ok - remains unchanged)

2. Step 3 (Formatting): Convert to Telegram Markdown format
   - Code block markers:```bash → ```bash (ok)
   - Boldtext: **text** → *text* (ok)
   - Within the code block: vibego\_cli → vibego\_cli (ok - code block remains unchanged)

Note: Telegram will:
- identify \`\`\` and displayed as a code block
- vibego\ inside code block_cli Shown as vibego_cli (The underline is displayed normally)
""")
