"""验证完整的处理流程"""

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from bot import _unescape_if_already_escaped, _prepare_model_payload

print("\n" + "=" * 80)
print("验证：智能反转义 + 格式化流程")
print("=" * 80 + "\n")

# 测试用户提供的真实场景
original_input = r"""\#\#\# 📋 后续步骤

1\. \*\*重启 Bot 服务\*\*以应用修复：
   \`\`\`bash
   python -m vibego\_cli stop
   \`\`\`"""

print("第1步：原始输入（存储在数据库中的内容）")
print("-" * 80)
print(original_input)
print()

# 模拟 _format_task_detail 的处理
print("第2步：智能反转义（_unescape_if_already_escaped）")
print("-" * 80)
cleaned = _unescape_if_already_escaped(original_input)
print(cleaned)
print()

print("第3步：准备发送到 Telegram（_prepare_model_payload）")
print("-" * 80)
final = _prepare_model_payload(cleaned)
print(final)
print()

print("=" * 80)
print("关键检查")
print("=" * 80)

checks = {
    "步骤2后：代码块标记正常": "```bash" in cleaned,
    "步骤2后：粗体格式正常": "**重启 Bot 服务**" in cleaned,
    "步骤2后：代码块内保持转义": "vibego\\_cli" in cleaned,
    "步骤3后：代码块标记保持": "```bash" in final,
    "步骤3后：粗体符合 Markdown": "*重启 Bot 服务*" in final,
    "步骤3后：代码块内下划线被保护": "vibego\\_cli" in final,
}

for name, passed in checks.items():
    status = "✅" if passed else "❌"
    print(f"{status} {name}")

print()
print("=" * 80)
print("说明")
print("=" * 80)
print("""
预期行为：
1. 步骤2（反转义）：清理外部的转义字符，保护代码块内容
   - 代码块标记：\`\`\`bash → ```bash ✅
   - 粗体文本：\*\*文本\*\* → **文本** ✅
   - 代码块内：vibego\_cli → vibego\_cli ✅ （保持不变）

2. 步骤3（格式化）：转换为 Telegram Markdown 格式
   - 代码块标记：```bash → ```bash ✅
   - 粗体文本：**文本** → *文本* ✅
   - 代码块内：vibego\_cli → vibego\_cli ✅（代码块内容不变）

注意：Telegram 在渲染时会：
- 识别 \`\`\` 并显示为代码块
- 代码块内的 vibego\_cli 显示为 vibego_cli （下划线正常显示）
""")
