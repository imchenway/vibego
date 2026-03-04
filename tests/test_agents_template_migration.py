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
