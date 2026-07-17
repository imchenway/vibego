from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from vibego_cli.agents_sync import (
    AgentsSyncError,
    sync_agents,
    validate_agents_override_root,
)


ROOT = Path(__file__).resolve().parents[1]


def _make_source_root(
    tmp_path: Path,
    *,
    template_text: str = "# 模板 v1\n",
    skill_name: str | None = None,
) -> Path:
    """构造最小 AGENTS 源目录，模拟仓库或新版安装包。"""

    source = tmp_path / "source"
    skills_dir = source / "vibego_cli" / "data" / "skills"
    skills_dir.mkdir(parents=True)
    (source / "AGENTS-template.md").write_text(template_text, encoding="utf-8")
    if skill_name:
        skill_dir = skills_dir / skill_name
        references_dir = skill_dir / "references"
        references_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {skill_name}\ndescription: sample capability\n---\n\n# 示例能力\n",
            encoding="utf-8",
        )
        (references_dir / "example.md").write_text("# example reference\n", encoding="utf-8")
    return source


def _make_targets(tmp_path: Path) -> dict[str, Path]:
    """构造隔离目标文件，避免测试写入真实 home。"""

    return {
        "codex": tmp_path / "home" / ".codex" / "AGENTS.md",
        "claude": tmp_path / "home" / ".claude" / "CLAUDE.md",
        "gemini": tmp_path / "home" / ".gemini" / "GEMINI.md",
        "vibego": tmp_path / "config" / "AGENTS.md",
    }


def test_sync_agents_writes_override_and_targets(tmp_path: Path) -> None:
    """同步命令应写入 override，并把同一 managed block 同步到各模型目标文件。"""

    source = _make_source_root(tmp_path)
    config_root = tmp_path / "config"
    targets = _make_targets(tmp_path)
    env = {"HOME": str(tmp_path / "home")}

    result = sync_agents(source_root=source, config_root=config_root, targets=targets, env=env)

    override_root = config_root / "agents" / "current"
    assert result.override_root == override_root
    assert (override_root / "AGENTS-template.md").read_text(encoding="utf-8") == "# 模板 v1\n"
    override_skills_dir = override_root / "vibego_cli" / "data" / "skills"
    assert override_skills_dir.is_dir()
    assert list(override_skills_dir.iterdir()) == []
    assert not (tmp_path / "home" / ".codex" / "skills").exists()
    assert not (tmp_path / "home" / ".agents" / "skills").exists()
    assert set(result.native_skill_statuses.keys()) == {"codex", "agents"}
    assert result.skill_count == 0
    assert {status.status for status in result.native_skill_statuses.values()} == {"unchanged"}
    manifest = json.loads((override_root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_root"] == str(source)
    assert manifest["template_file"] == str(override_root / "AGENTS-template.md")
    assert manifest["skills_dir"] == str(override_root / "vibego_cli" / "data" / "skills")
    assert set(manifest["native_skill_targets"].keys()) == {"codex", "agents"}
    assert set(manifest["targets"].keys()) == set(targets.keys())

    for target in targets.values():
        text = target.read_text(encoding="utf-8")
        assert "<!-- vibego-agents:start -->" in text
        assert "<!-- vibego-agents:end -->" in text
        assert "# 模板 v1" in text
        assert "# Vibego 内置 Skills" not in text
        assert "## Skill: sample-skill" not in text
        assert "description: sample capability" not in text
        assert "命中该 skill 时，先读取上方 vibego-skill-source 指向的 SKILL.md 全文" not in text
        assert "# 示例能力" not in text


def test_sync_agents_can_render_legacy_skill_index_when_enabled(tmp_path: Path) -> None:
    """显式 legacy 开关打开时，仍可写入旧 AGENTS skill 索引。"""

    source = _make_source_root(tmp_path, skill_name="sample-skill")
    config_root = tmp_path / "config"
    targets = _make_targets(tmp_path)
    env = {
        "HOME": str(tmp_path / "home"),
        "VIBEGO_AGENTS_LEGACY_SKILL_INDEX": "1",
    }

    sync_agents(source_root=source, config_root=config_root, targets=targets, env=env)

    for target in targets.values():
        text = target.read_text(encoding="utf-8")
        assert "# Vibego 内置 Skills" in text
        assert "## Skill: sample-skill" in text
        assert "description: sample capability" in text
        assert "命中该 skill 时，先读取上方 vibego-skill-source 指向的 SKILL.md 全文" in text
        assert "# 示例能力" not in text


def test_sync_agents_removes_native_skill_that_was_removed_from_source(tmp_path: Path) -> None:
    """源目录删除曾由 Vibego 管理的 skill 后，同步应清理其 native 投影。"""

    source = _make_source_root(tmp_path, skill_name="sample-skill")
    config_root = tmp_path / "config"
    targets = _make_targets(tmp_path)
    env = {"HOME": str(tmp_path / "home")}

    sync_agents(source_root=source, config_root=config_root, targets=targets, env=env)
    codex_skill = tmp_path / "home" / ".codex" / "skills" / "sample-skill"
    agents_skill = tmp_path / "home" / ".agents" / "skills" / "sample-skill"
    assert codex_skill.is_dir()
    assert agents_skill.is_dir()

    shutil.rmtree(source / "vibego_cli" / "data" / "skills" / "sample-skill")
    result = sync_agents(source_root=source, config_root=config_root, targets=targets, env=env)

    assert not codex_skill.exists()
    assert not agents_skill.exists()
    assert result.skill_count == 0
    assert {status.status for status in result.native_skill_statuses.values()} == {"cleaned"}


def test_sync_agents_preserves_user_content_and_replaces_managed_block(tmp_path: Path) -> None:
    """已有用户内容不得被覆盖；重复同步只能替换 managed block，不能追加重复块。"""

    source = _make_source_root(tmp_path, template_text="# 模板 v1\n")
    config_root = tmp_path / "config"
    targets = {"codex": tmp_path / "home" / ".codex" / "AGENTS.md"}
    targets["codex"].parent.mkdir(parents=True)
    targets["codex"].write_text("# 用户自定义\n请保留这段。\n", encoding="utf-8")

    sync_agents(source_root=source, config_root=config_root, targets=targets)
    first_text = targets["codex"].read_text(encoding="utf-8")
    assert first_text.startswith("# 用户自定义\n请保留这段。")
    assert (targets["codex"].with_suffix(".md.vibego.bak")).exists()

    (source / "AGENTS-template.md").write_text("# 模板 v2\n", encoding="utf-8")
    sync_agents(source_root=source, config_root=config_root, targets=targets)
    second_text = targets["codex"].read_text(encoding="utf-8")
    assert second_text.startswith("# 用户自定义\n请保留这段。")
    assert "# 模板 v1" not in second_text
    assert "# 模板 v2" in second_text
    assert second_text.count("<!-- vibego-agents:start -->") == 1


def test_sync_agents_resolves_last_successful_source_root(tmp_path: Path) -> None:
    """不传 source-root 时，应优先使用上次成功记录，便于 Master 按钮一键同步。"""

    source = _make_source_root(tmp_path)
    config_root = tmp_path / "config"
    targets = {"vibego": config_root / "AGENTS.md"}

    sync_agents(source_root=source, config_root=config_root, targets=targets)
    (config_root / "agents" / "current").rename(config_root / "agents" / "old-current")

    result = sync_agents(source_root=None, config_root=config_root, targets=targets)

    assert result.source_root == source
    assert (config_root / "agents" / "current" / "AGENTS-template.md").exists()


def test_validate_agents_override_root_fails_closed_when_manifest_points_to_broken_copy(tmp_path: Path) -> None:
    """只要 override manifest 存在但内容损坏，启动脚本就应能 fail-closed。"""

    override_root = tmp_path / "config" / "agents" / "current"
    override_root.mkdir(parents=True)
    (override_root / "manifest.json").write_text("{}", encoding="utf-8")
    (override_root / "AGENTS-template.md").write_text("# ok\n", encoding="utf-8")

    with pytest.raises(AgentsSyncError, match="skills"):
        validate_agents_override_root(override_root)


def test_agents_sync_cli_json_uses_env_targets(tmp_path: Path) -> None:
    """CLI smoke 使用临时 env 目标，不能污染真实本机 AGENTS。"""

    source = _make_source_root(tmp_path)
    config_root = tmp_path / "config"
    home = tmp_path / "home"
    env = os.environ.copy()
    env.update(
        {
            "VIBEGO_CONFIG_DIR": str(config_root),
            "CODEX_AGENTS_FILE": str(home / ".codex" / "AGENTS.md"),
            "CLAUDE_AGENTS_FILE": str(home / ".claude" / "CLAUDE.md"),
            "GEMINI_AGENTS_FILE": str(home / ".gemini" / "GEMINI.md"),
            "PYTHONPATH": str(ROOT),
        }
    )

    completed = subprocess.run(
        [sys.executable, "-m", "vibego_cli", "agents-sync", "--source-root", str(source), "--json"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["override_root"] == str(config_root / "agents" / "current")
    assert (home / ".codex" / "AGENTS.md").exists()


def test_default_global_commands_include_agents_sync_button() -> None:
    """AGENTS 同步也应出现在项目命令管理的通用命令列表里。"""

    from command_center.defaults import DEFAULT_GLOBAL_COMMANDS

    command = next((item for item in DEFAULT_GLOBAL_COMMANDS if item["name"] == "agents-sync"), None)

    assert command is not None
    command_text = str(command["command"])
    assert "vibego_cli agents-sync" in command_text
    assert "PYTHONPATH" in command_text
    assert "MODEL_WORKDIR" in command_text
    assert command["timeout"] >= 60


def test_sync_agents_reads_template_from_pipx_venv_root_when_package_root_lacks_data_file(tmp_path: Path) -> None:
    """pipx 安装形态下即使不含内置 skill，也应按 venv 根目录模板完成同步。"""

    venv_root = tmp_path / "pipx" / "venvs" / "vibego"
    package_root = venv_root / "lib" / "python3.11" / "site-packages"
    package_root.mkdir(parents=True)
    (venv_root / "AGENTS-template.md").write_text("# pipx venv 模板\n", encoding="utf-8")
    targets = {"vibego": tmp_path / "config" / "AGENTS.md"}

    result = sync_agents(source_root=package_root, config_root=tmp_path / "config", targets=targets)

    assert result.source_root == package_root.resolve()
    assert result.skill_count == 0
    assert "pipx venv 模板" in targets["vibego"].read_text(encoding="utf-8")
    assert (result.override_root / "AGENTS-template.md").read_text(encoding="utf-8") == "# pipx venv 模板\n"


def test_sync_agents_prefers_packaged_skills_over_empty_source_directory(tmp_path: Path) -> None:
    """源码候选为空时，仍应继续查找 pipx/site-packages 中实际存在的 skill。"""

    venv_root = tmp_path / "pipx" / "venvs" / "vibego"
    (venv_root / "vibego_cli" / "data" / "skills").mkdir(parents=True)
    packaged_skill = (
        venv_root
        / "lib"
        / "python3.11"
        / "site-packages"
        / "vibego_cli"
        / "data"
        / "skills"
        / "sample-skill"
    )
    packaged_skill.mkdir(parents=True)
    (packaged_skill / "SKILL.md").write_text(
        "---\nname: sample-skill\ndescription: sample capability\n---\n",
        encoding="utf-8",
    )
    (venv_root / "AGENTS-template.md").write_text("# pipx venv 模板\n", encoding="utf-8")
    env = {"HOME": str(tmp_path / "home")}

    result = sync_agents(
        source_root=venv_root,
        config_root=tmp_path / "config",
        targets={"vibego": tmp_path / "config" / "AGENTS.md"},
        env=env,
    )

    assert result.skill_count == 1
    assert (tmp_path / "home" / ".codex" / "skills" / "sample-skill" / "SKILL.md").is_file()
