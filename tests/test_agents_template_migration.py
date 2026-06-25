"""AGENTS 模板文件名迁移测试。"""

from pathlib import Path

import bot


ROOT = Path(__file__).resolve().parents[1]


def _read_text(rel_path: str) -> str:
    """读取仓库中文件内容。"""

    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_enforced_notice_points_to_agents_md() -> None:
    """强制规约文案应要求读取当前根目录 AGENTS.md。"""

    assert "当前根目录 AGENTS.md" in bot.ENFORCED_AGENTS_NOTICE
    assert "当前根目录 AGENTS-template.md" not in bot.ENFORCED_AGENTS_NOTICE


def test_enforced_notice_adds_user_requirement_header_before_prompt() -> None:
    """强制规约文案应在 PLAN 提示后引出“用户需求描述”，并与正文保留一行空行。"""

    injected = bot._prepend_enforced_agents_notice("pwd")
    lines = injected.splitlines()

    assert lines[-4] == "如未特殊指定模式，则默认进入 PLAN 模式。"
    assert lines[-3] == "以下是用户需求描述："
    assert lines[-2] == ""
    assert lines[-1] == "pwd"


def test_shell_defaults_use_agents_template() -> None:
    """启动脚本默认模板路径应切换为 AGENTS-template.md。"""

    run_bot_text = _read_text("scripts/run_bot.sh")
    start_tmux_text = _read_text("scripts/start_tmux_codex.sh")
    common_text = _read_text("scripts/models/common.sh")

    assert 'DEFAULT_AGENTS_TEMPLATE="$SOURCE_ROOT/AGENTS-template.md"' in run_bot_text
    assert 'CANDIDATE_VENV_TEMPLATE="$VENV_ROOT_FROM_SOURCE/AGENTS-template.md"' in run_bot_text
    assert '[[ -n "${VIRTUAL_ENV:-}" && -f "$VIRTUAL_ENV/AGENTS-template.md" ]]' in run_bot_text
    assert 'AGENTS_TEMPLATE_FILE="${VIBEGO_AGENTS_TEMPLATE:-$ROOT_DIR/AGENTS-template.md}"' in start_tmux_text
    assert 'local template="${2:-$ROOT_DIR/AGENTS-template.md}"' in common_text


def test_packaging_lists_agents_template() -> None:
    """打包清单应包含 AGENTS-template.md。"""

    pyproject_text = _read_text("pyproject.toml")
    manifest_text = _read_text("MANIFEST.in")

    assert '"" = ["AGENTS-template.md", "AGENTS-en.md"]' in pyproject_text
    assert "include AGENTS-template.md" in manifest_text
    assert "include AGENTS.md" not in manifest_text


def test_readme_links_point_to_agents_template() -> None:
    """中英文 README 内模板链接应指向新文件名。"""

    readme_cn = _read_text("README.md")
    readme_en = _read_text("README-en.md")

    assert "[AGENTS-template.md](AGENTS-template.md)" in readme_cn
    assert "仓库根目录的 `AGENTS-template.md`" in readme_cn
    assert "[AGENTS-template.md](AGENTS-template.md)" in readme_en
    assert "repository `AGENTS-template.md`" in readme_en


def test_agents_template_requires_comet_for_complex_workflows() -> None:
    """AGENTS 模板应要求所有用户任务默认优先使用 Comet。"""

    template_text = _read_text("AGENTS-template.md")

    assert "## Comet 自动调用规则" in template_text
    assert "必须优先启用 `comet` skill" in template_text
    assert "所有用户任务默认必须走 Comet 工作流" in template_text
    assert "不因为任务看起来简单而跳过 Comet" in template_text
    assert "不可因自动启用 Comet 而跳过用户决策点" in template_text


def test_agents_template_requires_claudecode_like_communication() -> None:
    """AGENTS 模板应固化接近 Claude Code 的清晰表达风格。"""

    template_text = _read_text("AGENTS-template.md")

    assert "## Claude Code 风格表达规则" in template_text
    assert "先给结论，再给原因、修法和验证方式" in template_text
    assert "少用术语、少贴过程日志、少堆文件清单" in template_text


def test_agents_template_prefers_html_visual_communication_for_non_trivial_tasks() -> None:
    """AGENTS 模板应把 HTML 图形沟通作为非琐碎任务的默认沟通协议。"""

    template_text = _read_text("AGENTS-template.md")

    assert "## HTML 图形沟通默认协议" in template_text
    assert "非琐碎任务默认优先使用 `html-visual-communication` skill" in template_text
    assert "AGENTS 负责判断何时触发；`html-visual-communication` 负责具体制图约束" in template_text
    assert "功能迭代或开发设计：必须先用 HTML 图展示现状、目标方案、前后差异、影响面、测试矩阵、风险与回滚" in template_text
    assert "缺陷排查：必须用 HTML 图展示现象、影响、证据链、可疑节点、已确认根因、高亮根因节点、修法、验证与回滚" in template_text
    assert "系统/功能理解：必须用 HTML 图展示组件、依赖、中间件、DB、MQ/队列语义、关键调用链或业务规则" in template_text
    assert "用户明确要求不要画图、只要一句话、或任务是琐碎格式转换时，可不生成 HTML" in template_text
