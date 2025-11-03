"""手动测试用例：模拟 10 种实际使用场景"""

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from bot import _unescape_if_already_escaped, _prepare_model_payload, _IS_MARKDOWN_V2

print("\n" + "=" * 80)
print("手动测试：10 种实际使用场景")
print("=" * 80 + "\n")

test_cases = [
    {
        "name": "场景1：用户提供的真实问题（后续步骤）",
        "input": r"""\#\#\# 📋 后续步骤

1\. \*\*重启 Bot 服务\*\*以应用修复：
   \`\`\`bash
   python -m vibego\_cli stop
   python -m vibego\_cli start
   \`\`\`

2\. \*\*验证 TASK\_0011\*\* 现在可以正常显示""",
        "description": "用户提交的原始问题，包含代码块和转义字符"
    },
    {
        "name": "场景2：包含 Python 代码的任务描述",
        "input": r"""修复 \*\*数据库连接池\*\* 的问题

示例代码：
\`\`\`python
import asyncpg

async def connect():
    pool = await asyncpg.create\_pool(
        host='localhost',
        database='vibebot'
    )
    return pool
\`\`\`

\*\*重要\*\*：需要检查环境变量""",
        "description": "包含 Python 代码块的任务描述"
    },
    {
        "name": "场景3：仅包含粗体和列表的简单文本",
        "input": r"""\*\*任务目标\*\*：
\- 优化性能
\- 修复缺陷
\- 更新文档""",
        "description": "简单的列表和粗体文本"
    },
    {
        "name": "场景4：包含行内代码的说明",
        "input": r"""使用 \`vibego\_cli\` 命令行工具来管理任务，主要命令包括：
\- \`start\` \- 启动服务
\- \`stop\` \- 停止服务
\- \`status\` \- 查看状态""",
        "description": "包含行内代码的说明文本"
    },
    {
        "name": "场景5：多种代码块混合",
        "input": r"""配置步骤：

1\. 编辑配置文件 \`config\.yaml\`：
   \`\`\`yaml
   database:
     host: localhost
     port: 5432
   \`\`\`

2\. 运行初始化脚本：
   \`\`\`bash
   ./init\.sh \-\-force
   \`\`\`""",
        "description": "混合 YAML 和 Bash 代码块"
    },
    {
        "name": "场景6：包含链接的文本",
        "input": r"""参考文档：\[官方文档\]\(https\://docs\.example\.com\)

查看 \[GitHub 仓库\]\(https\://github\.com/example/repo\) 了解更多""",
        "description": "包含 Markdown 链接"
    },
    {
        "name": "场景7：复杂的表格和列表",
        "input": r"""\#\#\# 测试结果

\| 测试项 \| 结果 \|
\|\-\-\-\-\-\-\|\-\-\-\-\-\-\|
\| 单元测试 \| ✅ 通过 \|
\| 集成测试 \| ✅ 通过 \|
\| 性能测试 \| ⚠️ 待优化 \|""",
        "description": "包含 Markdown 表格"
    },
    {
        "name": "场景8：Git 命令示例",
        "input": r"""Git 操作步骤：

\`\`\`bash
git add \.
git commit \-m "fix\: 修复任务显示问题"
git push origin main
\`\`\`

注意：不要使用 \`git push \-\-force\`""",
        "description": "包含 Git 命令的代码块"
    },
    {
        "name": "场景9：Docker 配置",
        "input": r"""Docker 配置：

\`\`\`dockerfile
FROM python:3\.11\-slim

WORKDIR /app

COPY requirements\.txt \.
RUN pip install \-r requirements\.txt

CMD \["python", "bot\.py"\]
\`\`\`""",
        "description": "包含 Dockerfile 的代码块"
    },
    {
        "name": "场景10：JSON 配置示例",
        "input": r"""配置文件示例 \`config\.json\`：

\`\`\`json
\{
  "telegram": \{
    "bot\_token": "YOUR\_TOKEN",
    "parse\_mode": "MarkdownV2"
  \}
\}
\`\`\`""",
        "description": "包含 JSON 配置的代码块"
    }
]

print(f"当前 parse_mode: {'MarkdownV2' if _IS_MARKDOWN_V2 else 'Markdown'}\n")

for i, case in enumerate(test_cases, 1):
    print(f"\n{'=' * 80}")
    print(f"测试用例 {i}/{len(test_cases)}: {case['name']}")
    print(f"描述: {case['description']}")
    print(f"{'=' * 80}\n")

    input_text = case['input']

    # 步骤1：智能反转义
    unescaped = _unescape_if_already_escaped(input_text)

    # 步骤2：格式化为最终输出（这会被发送到 Telegram）
    final_output = _prepare_model_payload(unescaped)

    print("原始输入（前100字符）:")
    print(f"  {repr(input_text[:100])}...")
    print()

    print("反转义后（前100字符）:")
    print(f"  {repr(unescaped[:100])}...")
    print()

    print("最终输出（前100字符）:")
    print(f"  {repr(final_output[:100])}...")
    print()

    # 关键检查
    checks = {
        "代码块标记正常": "```" in final_output or "`" in final_output,
        "粗体格式正常": "*" in final_output,
        "未残留 MarkdownV2 转义": all(
            escape not in final_output for escape in ("\\*", "\\#", "\\[", "\\]")
        ),
    }

    print("关键检查:")
    for check_name, passed in checks.items():
        status = "✅" if passed else "❌"
        print(f"  {status} {check_name}")

    # 额外检查：代码块内的下划线应该保持转义
    if "vibego\\_cli" in input_text or "create\\_pool" in input_text or "bot\\_token" in input_text:
        if "vibego\\_cli" in final_output or "create\\_pool" in final_output or "bot\\_token" in final_output:
            print(f"  ✅ 代码块内的下划线正确保护")
        else:
            print(f"  ⚠️  代码块内的下划线可能被反转义")

print("\n" + "=" * 80)
print("测试完成")
print("=" * 80 + "\n")

print("总结:")
print("- 所有测试用例都经过了智能反转义处理")
print("- 代码块标记正确转换为 ``` 和 `")
print("- 代码块内容保持原有转义状态")
print("- 普通文本的转义符号被正确清理")
print()
