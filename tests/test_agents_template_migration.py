"""AGENTS 模板文件名迁移测试。"""

import os
from pathlib import Path

os.environ.setdefault("BOT_TOKEN", "TEST_TOKEN")

import bot


ROOT = Path(__file__).resolve().parents[1]


def _read_text(rel_path: str) -> str:
    """读取仓库中文件内容。"""

    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_enforced_notice_keeps_user_requirement_header() -> None:
    """强制规约文案应保留用户需求分隔头。"""

    assert "以下是用户需求描述：" in bot.ENFORCED_AGENTS_NOTICE
    assert "当前根目录 AGENTS-template.md" not in bot.ENFORCED_AGENTS_NOTICE


def test_enforced_notice_adds_user_requirement_header_before_prompt() -> None:
    """强制规约文案应在用户正文前标明 Telegram 来源与 HTML 附件口径。"""

    injected = bot._prepend_enforced_agents_notice("pwd")
    lines = injected.splitlines()

    assert lines[-4] == "以下是用户需求描述："
    assert "请求来源：vibego Telegram worker / 移动端。" in lines[-3]
    assert "不需要 PNG" in lines[-3]
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
    assert 'DEFAULT_AGENTS_TEMPLATE="$(select_agents_template_file "$ROOT_DIR/AGENTS-template.md" "start-tmux")"' in start_tmux_text
    assert 'AGENTS_TEMPLATE_FILE="${VIBEGO_AGENTS_TEMPLATE:-$DEFAULT_AGENTS_TEMPLATE}"' in start_tmux_text
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


def test_agents_template_uses_global_skill_kernel_without_plan_develop_phases() -> None:
    """AGENTS 模板应收敛为 skill router kernel，不再内置 PLAN/develop 长阶段提示词。"""

    template_text = _read_text("AGENTS-template.md")

    assert "# Global Agent Kernel" in template_text
    assert "## Skill routing" in template_text
    assert "superpowers:brainstorming" in template_text
    assert "superpowers:systematic-debugging" in template_text
    assert "superpowers:test-driven-development" in template_text
    assert "superpowers:verification-before-completion" in template_text
    assert "vibe-diagram" in template_text
    assert "frontend-skill" in template_text
    assert "impeccable" in template_text
    assert "accessibility" in template_text
    assert "不要自行执行 git commit/push/merge/revert" in template_text
    assert "证据不足写“待确认/推断”" in template_text
    assert "任务编码：- ; 任务名称：- ;" in template_text
    assert "## plan 阶段" not in template_text
    assert "## develop 阶段" not in template_text
    assert "PLAN-> develop" not in template_text
    assert "vibe -> design -> develop" not in template_text


def test_agents_template_does_not_keep_extra_reply_style_prompts() -> None:
    """AGENTS 模板不应保留用户明确不要的额外回复风格提示。"""

    template_text = _read_text("AGENTS-template.md")

    assert "## Reply contract" in template_text
    assert "默认先结论，少噪音" not in template_text
    assert "现象 -> 影响 -> 根因 -> 修法 -> 验证" not in template_text


def test_agents_template_prefers_html_visual_communication_for_non_trivial_tasks() -> None:
    """AGENTS 模板应把 HTML 图形沟通提升为几乎所有实质会话的默认协议。"""

    template_text = _read_text("AGENTS-template.md")

    assert "## Visual and frontend contract" in template_text
    assert "几乎所有需要解释、判断、设计、排障、复盘、代码逻辑说明或交付验收的会话都应优先触发 vibe-diagram" in template_text
    assert "一图胜千言" in template_text
    assert "AGENTS 只判断何时触发，具体制图规则以 vibe-diagram 为准" in template_text
    assert "HTML-only" in template_text
    assert "所有实质内容写入项目内单文件 HTML" in template_text
    assert "Telegram 来源只输出项目内 `.html/.htm` 路径" in template_text


def test_agents_template_requires_html_first_interaction_contract() -> None:
    """AGENTS 模板应把全程 HTML 交互作为默认，而不是只在非琐碎画图任务中触发。"""

    template_text = _read_text("AGENTS-template.md")

    assert "## HTML-first interaction contract" in template_text
    assert "默认所有实质沟通都使用单文件 HTML 与用户交互" in template_text
    assert "聊天通道默认只做交付信封" in template_text
    assert "分析、设计、排障、方案、决策、验收、总结、代码逻辑说明、证据链、风险、回滚、测试矩阵" in template_text
    assert "不要把 HTML 只限定为非琐碎任务" in template_text
    assert "本节只定义何时调用 vibe-diagram 做图形化表达，不得把 HTML 限定在这些场景" in template_text
    assert "阻塞性澄清问题、极短确认、简单命令结果、用户明确不要 HTML" in template_text
    assert "docs 做长期沉淀；HTML 是主交互界面" in template_text


def test_agents_template_compresses_text_after_html_delivery() -> None:
    """生成 HTML 图后，AGENTS 应要求聊天回复只保留交付信封和下一步。"""

    template_text = _read_text("AGENTS-template.md")

    assert "生成 HTML 后聊天只给链接/路径和下一步" in template_text
    assert "验证摘要" not in template_text
    assert "分析、证据链、测试矩阵和风险回滚写入 HTML 或 docs" in template_text
