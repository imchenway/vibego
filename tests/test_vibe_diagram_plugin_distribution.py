"""vibe-diagram Codex plugin / marketplace distribution contract."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_SKILL_ROOT = ROOT / "vibego_cli" / "data" / "skills" / "vibe-diagram"
PLUGIN_ROOT = ROOT / "plugins" / "vibe-diagram"
PLUGIN_SKILL_ROOT = PLUGIN_ROOT / "skills" / "vibe-diagram"
MARKETPLACE_FILE = ROOT / ".agents" / "plugins" / "marketplace.json"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _relative_files(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_vibe_diagram_plugin_manifest_exposes_skill_package() -> None:
    """Codex marketplace install needs a real plugin manifest, not only a native skill folder."""

    manifest_path = PLUGIN_ROOT / ".codex-plugin" / "plugin.json"
    assert manifest_path.is_file()
    manifest = _json(manifest_path)
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert manifest["name"] == "vibe-diagram"
    assert manifest["version"] == project["version"]
    assert manifest["description"]
    assert manifest["skills"] == "./skills/"
    assert manifest["author"]["name"] == "Hypha"
    assert manifest["license"] == "LicenseRef-Proprietary"
    assert manifest["interface"]["displayName"] == "Vibe Diagram"
    assert manifest["interface"]["category"] == "Developer Tools"
    assert "Interactive" in manifest["interface"]["capabilities"]
    assert "Read" in manifest["interface"]["capabilities"]
    assert "Write" in manifest["interface"]["capabilities"]
    assert not any("[TODO:" in json.dumps(value) for value in manifest.values())


def test_vibe_diagram_plugin_skill_tree_mirrors_builtin_skill_source() -> None:
    """The distributable plugin must not drift from the built-in vibe-diagram skill source."""

    assert PLUGIN_SKILL_ROOT.is_dir()
    source_files = _relative_files(SOURCE_SKILL_ROOT)
    plugin_files = _relative_files(PLUGIN_SKILL_ROOT)

    assert source_files
    assert plugin_files == source_files


def test_vibe_diagram_native_skill_display_name_matches_invocation_name() -> None:
    """Native skill UI should show the callable skill name, not a localized title."""

    openai_metadata = (SOURCE_SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")

    assert 'display_name: "vibe-diagram"' in openai_metadata
    assert "Vibe 图形表达" not in openai_metadata


def test_repo_marketplace_exposes_vibe_diagram_plugin_entry() -> None:
    """A repo marketplace lets other users add the repo then install the plugin by name."""

    assert MARKETPLACE_FILE.is_file()
    marketplace = _json(MARKETPLACE_FILE)

    assert marketplace["name"] == "vibego"
    assert marketplace["interface"]["displayName"] == "Vibego"
    entries = {entry["name"]: entry for entry in marketplace["plugins"]}
    entry = entries["vibe-diagram"]
    assert entry == {
        "name": "vibe-diagram",
        "source": {
            "source": "local",
            "path": "./plugins/vibe-diagram",
        },
        "policy": {
            "installation": "AVAILABLE",
            "authentication": "ON_INSTALL",
        },
        "category": "Developer Tools",
    }


def test_bumpversion_updates_vibe_diagram_plugin_version() -> None:
    """The plugin version should move with vibego releases so marketplace users can upgrade."""

    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["bumpversion"]
    filenames = {item["filename"] for item in config["files"]}
    assert "plugins/vibe-diagram/.codex-plugin/plugin.json" in filenames


def test_readme_documents_vibe_diagram_distribution_and_trigger_scope() -> None:
    """README should explain how to install/update vibe-diagram without reviving broad HTML triggers."""

    readme_text = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "Codex Skill / Plugin 与 vibe-diagram" in readme_text
    assert "明确要求画图、图形化、HTML 图" in readme_text
    assert "复杂技术/业务逻辑、关系结构或状态流转可视化" in readme_text
    assert "行为/故障为什么默认生成 HTML 图" in readme_text
    assert "为什么没反应 / 为什么失败 / 为什么没生效 / 为什么走错 / 为什么变慢 / 为什么不一致" in readme_text
    assert "解释具体对象、代码、文件更新、diff、模块、页面、接口、配置、数据、功能入口或运行结果" in readme_text
    assert "只在纯概念定义、翻译改写、一句话答案、简单命令或用户明确不要图时" in readme_text
    assert "安装升级说明、轻量决策也可简洁文本" in readme_text
    assert "tabs / role=tablist 只用于同一图型的多个候选布局" in readme_text
    assert "不得把追问、步骤、发布说明或普通章节导航追加成按钮" in readme_text
    assert "codex plugin marketplace add /path/to/vibego" in readme_text
    assert "codex plugin add vibe-diagram@vibego" in readme_text
    assert "codex plugin marketplace upgrade vibego" in readme_text
    assert "vibego agents-sync --json" in readme_text
    assert "默认所有实质沟通都使用单文件 HTML" not in readme_text
    assert "用户问“为什么 / 怎么做 / 需要怎么做”也属于实质沟通" not in readme_text
