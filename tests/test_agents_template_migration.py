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
    assert "## plan 阶段" not in template_text
    assert "## develop 阶段" not in template_text
    assert "PLAN-> develop" not in template_text
    assert "vibe -> design -> develop" not in template_text


def test_agents_template_removes_brainstorming_stage_gate_and_task_reset() -> None:
    """AGENTS 模板不应保留阶段确认门禁、确认复用和任务回退逻辑。"""

    template_text = _read_text("AGENTS-template.md")

    assert "需求、方案、行为变更、复杂设计：使用 superpowers:brainstorming" in template_text
    assert "任何任务进入方案、计划、实现或验证等下一阶段前，必须先使用 superpowers:brainstorming" not in template_text
    assert "先通过 superpowers:brainstorming 明确目标、范围、关键约束、验收口径和下一阶段" not in template_text
    assert "只有用户明确确认方案或确认进入下一阶段后，才结束交互问答" not in template_text
    assert "若当前线程已完成同一目标的 brainstorming 并获得用户确认" not in template_text
    assert "修改类动作完成并完成验证/交付后，必须视为该任务闭环" not in template_text
    assert "即使仍在同一会话内，也必须回到新的交互式起点" not in template_text
    assert "不得把上一任务的确认跨任务复用" not in template_text
    assert "明确说“按推荐做 / 开始修复 / 直接实现”时，可以进入实现" not in template_text


def test_agents_template_routes_openai_product_design_with_gates() -> None:
    """AGENTS 模板应把 Product Design 作为按需产品设计路由，而不是替换现有前端链路。"""

    template_text = _read_text("AGENTS-template.md")

    assert "若当前环境已提供 OpenAI Product Design" in template_text
    assert "不会因本模板自动安装" in template_text
    assert "缺失时不得假装可用" in template_text
    assert "产品体验、UX 研究、用户流程审计、视觉方向探索" in template_text
    assert "原型/重设计/URL 克隆" in template_text
    assert "截图/Figma/ImageGen 到可交互原型" in template_text
    assert "必须先确认 brief" in template_text
    assert "缺少视觉目标时先生成 3 个方向并等待用户选择" in template_text
    assert "不得从文字 brief 直接实现" in template_text
    assert "落地代码仍遵守 superpowers:test-driven-development、frontend-skill、impeccable、accessibility" in template_text
    assert "Product Design 仅负责产品设计前置、视觉探索、原型化和视觉 QA" in template_text
    assert "若产出需要视觉化交付，仍遵守本仓 HTML 图交付规则" in template_text


def test_agents_template_keeps_runtime_entry_prompts_out() -> None:
    """AGENTS 模板不应内置具体运行入口文案，入口差异由发送侧前缀处理。"""

    template_text = _read_text("AGENTS-template.md")

    assert "平台或入口侧要求由发送侧提示词前缀或运行时适配层注入" in template_text
    assert "Telegram" not in template_text
    assert "Vibego" not in template_text
    assert "vibego" not in template_text


def test_agents_template_does_not_keep_extra_reply_style_prompts() -> None:
    """AGENTS 模板不应保留用户明确不要的额外回复风格提示。"""

    template_text = _read_text("AGENTS-template.md")

    assert "## Reply contract" not in template_text
    assert "任务编码：-" not in template_text
    assert "任务名称：-" not in template_text
    assert "本次使用的skill：-" not in template_text
    assert "本次修改的影响功能点：-" not in template_text
    assert "待用户重启服务或待执行脚本：-" not in template_text
    assert "默认先结论，少噪音" not in template_text
    assert "现象 -> 影响 -> 根因 -> 修法 -> 验证" not in template_text


def test_agents_template_forbids_optional_commentary() -> None:
    """AGENTS 模板应明确禁止发送可选评论。"""

    template_text = _read_text("AGENTS-template.md")

    assert "DO NOT send optional commentary" in template_text


def test_agents_template_scopes_vibe_diagram_to_explicit_visual_or_logic_visualization() -> None:
    """AGENTS 模板只应在明确视觉化或复杂逻辑可视化时触发 vibe-diagram。"""

    template_text = _read_text("AGENTS-template.md")

    assert "## Visual and frontend contract" in template_text
    assert "明确要求画图、图形化、HTML 图" in template_text
    assert "需要把复杂技术/业务逻辑、关系结构或状态流转可视化" in template_text
    assert "使用 vibe-diagram" in template_text
    assert "一图胜千言" in template_text
    assert "纯概念定义、翻译改写、一句话答案、简单命令" in template_text
    assert "平台或入口侧要求由发送侧提示词前缀或运行时适配层注入" in template_text
    assert "几乎所有需要解释、判断、设计、排障、复盘、代码逻辑说明或交付验收的会话都应优先触发 vibe-diagram" not in template_text


def test_agents_template_uses_html_only_when_visual_delivery_is_needed() -> None:
    """AGENTS 模板不应把普通实质沟通强制改成 HTML。"""

    template_text = _read_text("AGENTS-template.md")

    assert "## HTML / visual delivery contract" in template_text
    assert "用户明确要求 HTML/图形化" in template_text
    assert "确实需要用图表达复杂关系" in template_text
    assert "生成或更新项目内单文件 HTML" in template_text
    assert "纯概念定义、翻译改写、一句话答案、简单命令" in template_text
    assert "docs 做长期沉淀；HTML 是主交互界面" in template_text
    assert "默认所有实质沟通都使用单文件 HTML 与用户交互" not in template_text
    assert "聊天通道默认只做交付信封" not in template_text
    assert "不要把 HTML 只限定为非琐碎任务" not in template_text
    assert "本节只定义何时调用 vibe-diagram 做图形化表达，不得把 HTML 限定在这些场景" not in template_text


def test_agents_template_routes_runtime_why_questions_to_vibe_diagram() -> None:
    """现场行为/故障成因类为什么应默认触发 HTML 图，而概念问答仍可文本回答。"""

    template_text = _read_text("AGENTS-template.md")

    assert "HTML-first 实质沟通、原因解释、方案建议、修复说明、验收收口：使用 vibe-diagram" not in template_text
    assert "除阻塞性澄清、极短确认、简单命令结果或用户明确不要 HTML 外" not in template_text
    assert "概念为什么" in template_text
    assert "行为/故障为什么" in template_text
    assert "为什么没反应" in template_text
    assert "为什么失败" in template_text
    assert "为什么没生效" in template_text
    assert "为什么走错" in template_text
    assert "为什么变慢" in template_text
    assert "为什么不一致" in template_text
    assert "默认使用 vibe-diagram 生成单文件 HTML 图" in template_text
    assert "完整逻辑、调用链、状态流转、数据口径、前后差异、根因链路、证据链" in template_text
    assert "普通为什么/怎么做追问默认简洁文本回答" not in template_text


def test_agents_template_routes_concrete_object_explanations_to_vibe_diagram() -> None:
    """解释具体对象、文件更新或 diff 时应默认用图，而不是退回纯文本表格。"""

    template_text = _read_text("AGENTS-template.md")

    assert "解释具体对象、代码、文件更新、diff、模块、页面、接口、配置、数据、功能入口或运行结果时" in template_text
    assert "默认使用 vibe-diagram 生成单文件 HTML 图" in template_text
    assert "只在纯概念定义、翻译改写、一句话答案、简单命令或用户明确不要图时" in template_text
    assert "逐个解释文件作用默认简洁文本" not in template_text


def test_agents_template_compresses_text_after_html_delivery() -> None:
    """生成 HTML 图后，AGENTS 应要求聊天回复只保留交付信封和下一步。"""

    template_text = _read_text("AGENTS-template.md")

    assert "生成 HTML 后聊天只给链接/路径和下一步" in template_text
    assert "链接文字必须使用 HTML 内部 `<h1>` 主标题" in template_text
    assert "不要写成固定的“打开 HTML”" in template_text
    assert "验证摘要" not in template_text
    assert "分析、证据链、测试矩阵和风险回滚写入 HTML 或 docs" in template_text
