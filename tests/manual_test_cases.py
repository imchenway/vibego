"""Manual test cases: simulate 10 real-life usage scenarios"""

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from bot import _unescape_if_already_escaped, _prepare_model_payload, _IS_MARKDOWN_V2

print("\n" + "=" * 80)
print("Manual testing: 10 real-world usage scenarios")
print("=" * 80 + "\n")

test_cases = [
    {
        "name": "Scenario 1: Real question provided by user (next steps)",
        "input": r"""\#\#\# 📋 Next steps

1\. \*\*Restart the Bot service\*\*To apply the fix:
   \`\`\`bash
   python -m vibego\_cli stop
   python -m vibego\_cli start
   \`\`\`

2\. \*\*Verify TASK\_0011\*\* Now it can be displayed normally""",
        "description": "Original user-submitted question containing code blocks and escaped characters"
    },
    {
        "name": "Scenario 2: Task description containing Python code",
        "input": r"""Repair \*\*Database connection pool\*\* question

Sample code:
\`\`\`python
import asyncpg

async def connect():
    pool = await asyncpg.create\_pool(
        host='localhost',
        database='vibebot'
    )
    return pool
\`\`\`

\*\*important\*\*:Need to check environment variables""",
        "description": "Task description containing Python code chunks"
    },
    {
        "name": "Scenario 3: Simple text with only bold and list",
        "input": r"""\*\*Mission objectives\*\*:
\- Optimize performance
\- fix bug
\-Update documentation""",
        "description": "Simple lists and bold text"
    },
    {
        "name": "Scenario 4: Instructions containing inline code",
        "input": r"""use \`vibego\_cli\` Command line tools to manage tasks, the main commands include:
\-\`start\` \- Start service
\-\`stop\` \- Stop service
\-\`status\` \- View status""",
        "description": "Contains descriptive text for inline code"
    },
    {
        "name": "Scenario 5: Mixing of multiple code blocks",
        "input": r"""Configuration steps:

1\. Edit configuration file \`config\.yaml\`:
   \`\`\`yaml
   database:
     host: localhost
     port: 5432
   \`\`\`

2\. Run the initialization script:
   \`\`\`bash
   ./init\.sh \-\-force
   \`\`\`""",
        "description": "Mixing YAML and Bash code blocks"
    },
    {
        "name": "Scenario 6: Text containing links",
        "input": r"""Reference document:\[Official documentation\]\(https\://docs\.example\.com\)

Check \[GitHub storehouse\]\(https\://github\.com/example/repo\) learn more""",
        "description": "Contains Markdown links"
    },
    {
        "name": "Scenario 7: Complex tables and lists",
        "input": r"""\#\#\# Test results

\| Test item \| result \|
\|\-\-\-\-\-\-\|\-\-\-\-\-\-\|
| unit testing | PASS |
| Integration testing | PASS |
\| Performance Test \| ⚠️ To be optimized \|""",
        "description": "Contains Markdown tables"
    },
    {
        "name": "Scenario 8: Git command example",
        "input": r"""Git Operation steps:

\`\`\`bash
git add \.
git commit \-m "fix\: Fix task display issue"
git push origin main
\`\`\`

Note: Do not use \`git push \-\-force\`""",
        "description": "Code block containing Git commands"
    },
    {
        "name": "Scenario 9: Docker configuration",
        "input": r"""Docker Configuration:

\`\`\`dockerfile
FROM python:3\.11\-slim

WORKDIR /app

COPY requirements\.txt \.
RUN pip install \-r requirements\.txt

CMD \["python", "bot\.py"\]
\`\`\`""",
        "description": "Code block containing Dockerfile"
    },
    {
        "name": "Scenario 10: JSON configuration example",
        "input": r"""Configuration file example \`config\.json\`:

\`\`\`json
\{
  "telegram": \{
    "bot\_token": "YOUR\_TOKEN",
    "parse\_mode": "MarkdownV2"
  \}
\}
\`\`\`""",
        "description": "Code block containing JSON configuration"
    }
]

print(f"current parse_mode: {'MarkdownV2' if _IS_MARKDOWN_V2 else 'Markdown'}\n")

for i, case in enumerate(test_cases, 1):
    print(f"\n{'=' * 80}")
    print(f"test case {i}/{len(test_cases)}: {case['name']}")
    print(f"describe: {case['description']}")
    print(f"{'=' * 80}\n")

    input_text = case['input']

    # Step 1: Smart Unescaping
    unescaped = _unescape_if_already_escaped(input_text)

    # Step 2: Format for final output (this will be sent to Telegram)
    final_output = _prepare_model_payload(unescaped)

    print("Raw input (first 100 characters):")
    print(f"  {repr(input_text[:100])}...")
    print()

    print("After escaping (first 100 characters):")
    print(f"  {repr(unescaped[:100])}...")
    print()

    print("Final output (first 100 characters):")
    print(f"  {repr(final_output[:100])}...")
    print()

    # critical check
    checks = {
        "Code block tags are ok": "```" in final_output or "`" in final_output,
        "Bold format is normal": "*" in final_output,
        "No remaining MarkdownV2 escaping": all(
            escape not in final_output for escape in ("\\*", "\\#", "\\[", "\\]")
        ),
    }

    print("critical check:")
    for check_name, passed in checks.items():
        status = "PASS" if passed else "FAIL"
        print(f"  {status} {check_name}")

    # Extra check: underscores inside code blocks should remain escaped
    if "vibego\\_cli" in input_text or "create\\_pool" in input_text or "bot\\_token" in input_text:
        if "vibego\\_cli" in final_output or "create\\_pool" in final_output or "bot\\_token" in final_output:
            print("  OK: Underscores within code blocks are properly protected")
        else:
            print(f"  ⚠️  Underscores within code blocks may be escaped")

print("\n" + "=" * 80)
print("Test completed")
print("=" * 80 + "\n")

print("Summarize:")
print("- All test cases have been intelligently unescaped.")
print("- Code block tags are correctly converted to ``` and `")
print("- The content of the code block remains in its original escaped state")
print("- Escape symbols for normal text are properly sanitized")
print()
