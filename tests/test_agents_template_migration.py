"""AGENTS 模板文件名迁移测试。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read_text(rel_path: str) -> str:
    """读取仓库中文件内容。"""

    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_telegram_prompt_prefix_runtime_contract_is_removed() -> None:
    """worker 运行时代码不得再定义或注入 Telegram 入模前缀。"""

    bot_text = _read_text("bot.py")

    assert "ENFORCED_AGENTS_NOTICE" not in bot_text
    assert "TELEGRAM_SOURCE_CONTEXT_NOTICE" not in bot_text
    assert "_prepend_enforced_agents_notice" not in bot_text
    assert "以下是用户需求描述：" not in bot_text
    assert "请求来源：vibego Telegram worker / 移动端。" not in bot_text


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
