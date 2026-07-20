from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROJECTION_PATH = ROOT / "vibe_diagram_projection.json"
BUILTIN_SKILL = ROOT / "vibego_cli" / "data" / "skills" / "vibe-diagram"
PLUGIN_ROOT = ROOT / "plugins" / "vibe-diagram"
PLUGIN_SKILL = PLUGIN_ROOT / "skills" / "vibe-diagram"
CATALOG_PATH = ROOT / ".agents" / "plugins" / "marketplace.json"


def _files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _canonical_tree_digest(skill_root: Path) -> str:
    digest = hashlib.sha256()
    for relative, payload in _files(skill_root).items():
        if relative in {"agents/openai.yaml", "update.json"}:
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(hashlib.sha256(payload).digest())
    return digest.hexdigest()


def test_vibego_projection_is_exact_and_auditable() -> None:
    projection = json.loads(PROJECTION_PATH.read_text(encoding="utf-8"))
    assert projection == {
        "schema_version": 1,
        "source": {
            "repository": "vibe-diagram",
            "version": "0.1.3",
            "tree_sha256": "8488c39673b7205483056ef48ed5223128d166387b8fa2ee353a50dd4ec49ca6",
        },
        "targets": {
            "builtin_skill": "vibego_cli/data/skills/vibe-diagram",
            "codex_plugin": "plugins/vibe-diagram",
            "marketplace_catalog": ".agents/plugins/marketplace.json",
        },
        "host_overrides": [],
        "adapter_extras": ["agents/openai.yaml"],
    }

    builtin_files = _files(BUILTIN_SKILL)
    plugin_files = _files(PLUGIN_SKILL)
    assert builtin_files == plugin_files
    assert set(builtin_files) == set(plugin_files)
    assert "agents/openai.yaml" in builtin_files

    update = json.loads((BUILTIN_SKILL / "update.json").read_text(encoding="utf-8"))
    assert update["version"] == projection["source"]["version"]
    assert update["tree_sha256"] == projection["source"]["tree_sha256"]
    assert _canonical_tree_digest(BUILTIN_SKILL) == projection["source"]["tree_sha256"]

    plugin = json.loads(
        (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    assert plugin["name"] == "vibe-diagram"
    assert plugin["version"] == projection["source"]["version"]

    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    entries = catalog["plugins"]
    assert len(entries) == 1
    assert entries[0]["name"] == "vibe-diagram"
    assert entries[0]["source"] == {
        "source": "local",
        "path": "./plugins/vibe-diagram",
    }


def test_packaging_declares_the_projected_skill_tree() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for pattern in (
        "data/skills/*/SKILL.md",
        "data/skills/*/assets/contracts/*/*",
        "data/skills/*/assets/templates/*/*.html",
        "data/skills/*/contracts/*.json",
        "data/skills/*/references/*.md",
        "data/skills/*/agents/*.yaml",
        "data/skills/*/scripts/*.py",
    ):
        assert pattern in pyproject
    assert "recursive-include vibego_cli/data/skills *" in manifest
    assert "当前发行包携带受控投影的 `vibe-diagram` 内置 skill" in readme
    assert "不会在安装或构建时自动写入本机 native skill 目录" in readme
