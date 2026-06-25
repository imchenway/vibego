from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_html_visual_skill_pack_exists_and_is_packaged() -> None:
    """HTML 图形表达 skill 必须作为 vibego 内置资源随包发布。"""

    skill_file = ROOT / "vibego_cli" / "data" / "skills" / "html-visual-communication" / "SKILL.md"
    skill_text = skill_file.read_text(encoding="utf-8")
    pyproject_text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    manifest_text = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    frontmatter = skill_text.split("---", 2)[1]

    assert "name: html-visual-communication" in skill_text
    assert "description:" in skill_text
    assert "description: Use when" in frontmatter
    assert "单文件 HTML" in skill_text
    assert "直接发送 `.html` 文件" in skill_text
    assert "必须作为文件附件发送" in skill_text
    assert "禁止只发送 Markdown 链接" in skill_text
    assert "Telegram 中必须看到文件卡片" in skill_text
    assert "系统架构图" in skill_text
    assert "BPMN-light" in skill_text
    assert "禁止把业务流程图画成密集表格" in skill_text
    assert "事件圆点" in skill_text
    assert "决策菱形" in skill_text
    assert "任何图都禁止文字被节点、连线、标签或背景层遮挡" in skill_text
    assert "顶部标题必须克制" in skill_text
    assert "顶部描述最多一行" in skill_text
    assert "箭头必须短、直、少交叉" in skill_text
    assert "连线不得穿过节点正文" in skill_text
    assert "发现遮挡或箭头混乱必须重排，不得交付" in skill_text
    assert "## 自动路由规则" in skill_text
    assert "路由冲突优先级" in skill_text
    assert "业务架构图 / 领域地图" in skill_text
    assert "状态 / 数据模型图" in skill_text
    assert "技术设计图" in skill_text
    assert "需求 / 决策沟通图" in skill_text
    assert "用户提到业务能力、领域、对象、规则、价值链" in skill_text
    assert "用户提到状态、状态流转、生命周期、实体关系、表结构" in skill_text
    assert "用户提到 API、数据库、模块、契约、部署、回滚" in skill_text
    assert "系统架构图规则" in skill_text
    assert "业务架构图规则" in skill_text
    assert "业务流程图规则" in skill_text
    assert "代码时序图规则" in skill_text
    assert "状态 / 数据模型图规则" in skill_text
    assert "故障排查图规则" in skill_text
    assert "页面设计稿规则" in skill_text
    assert "技术设计与需求决策图规则" in skill_text
    assert "## AGENTS 配合协议" in skill_text
    assert "当 AGENTS 要求默认通过 HTML 图沟通时" in skill_text
    assert "功能迭代 / 开发设计" in skill_text
    assert "前后差异对比图" in skill_text
    assert "缺陷排查 / 故障分析" in skill_text
    assert "高亮根因节点" in skill_text
    assert "设计定稿 / 方案确认" in skill_text
    assert "每个 HTML 图都必须能脱离聊天记录独立阅读" in skill_text
    assert "data/skills/*/SKILL.md" in pyproject_text
    assert "data/skills/*/agents/*.yaml" in pyproject_text
    assert "recursive-include vibego_cli/data/skills" in manifest_text


def test_sync_agents_block_embeds_builtin_html_visual_skill(tmp_path: Path) -> None:
    """同步全局 AGENTS 时，应把 vibego 内置 skill 注入到同一个受管块。"""

    target = tmp_path / "AGENTS.md"
    env = os.environ.copy()
    env.update(
        {
            "PYTHON_EXEC": sys.executable,
            "TARGET_AGENTS_FILE": str(target),
        }
    )

    subprocess.run(
        [
            "bash",
            "-lc",
            (
                "set -euo pipefail; "
                "source scripts/models/common.sh; "
                'sync_agents_block "$TARGET_AGENTS_FILE" AGENTS-template.md >/dev/null'
            ),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
        env=env,
    )

    synced_text = target.read_text(encoding="utf-8")

    assert "<!-- vibego-agents:start -->" in synced_text
    assert "# Vibego 内置 Skills" in synced_text
    assert "## Skill: html-visual-communication" in synced_text
    assert "当用户要求画系统架构图、业务流程图、代码时序图、故障排查图、页面设计稿" in synced_text
    assert "最终必须直接发送 `.html` 文件" in synced_text
    assert "必须作为文件附件发送" in synced_text
    assert "## 自动路由规则" in synced_text
    assert "业务架构图 / 领域地图" in synced_text
    assert "状态 / 数据模型图" in synced_text
    assert "技术设计图" in synced_text
    assert "## AGENTS 配合协议" in synced_text
    assert "高亮根因节点" in synced_text
    assert "任何图都禁止文字被节点、连线、标签或背景层遮挡" in synced_text
    assert "箭头必须短、直、少交叉" in synced_text
    assert "<!-- vibego-agents:end -->" in synced_text
