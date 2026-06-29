"""AGENTS 与内置 skills 本机同步服务。

该模块供 CLI 与 Master Bot 复用，避免同步逻辑散落在 shell 与 Telegram handler 中。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Optional

from . import config

VIBEGO_AGENTS_MARKER_START = "<!-- vibego-agents:start -->"
VIBEGO_AGENTS_MARKER_END = "<!-- vibego-agents:end -->"
SOURCE_ROOT_RECORD_NAME = "source_root.json"
MANIFEST_NAME = "manifest.json"


class AgentsSyncError(RuntimeError):
    """AGENTS/Skills 同步失败时抛出的领域异常。"""


@dataclass(frozen=True)
class TargetSyncStatus:
    """单个目标 AGENTS 文件的同步结果。"""

    path: Path
    status: str
    backup_path: Optional[Path] = None

    def to_dict(self) -> dict[str, str | None]:
        """转换为可 JSON 序列化的字典。"""

        return {
            "path": str(self.path),
            "status": self.status,
            "backup_path": str(self.backup_path) if self.backup_path else None,
        }


@dataclass(frozen=True)
class AgentsSyncResult:
    """AGENTS/Skills 同步的总结果。"""

    source_root: Path
    override_root: Path
    template_file: Path
    skills_dir: Path
    manifest_file: Path
    target_statuses: Mapping[str, TargetSyncStatus]
    skill_count: int
    synced_at: str

    def to_dict(self) -> dict[str, object]:
        """转换为 CLI JSON 输出结构。"""

        return {
            "ok": True,
            "source_root": str(self.source_root),
            "override_root": str(self.override_root),
            "template_file": str(self.template_file),
            "skills_dir": str(self.skills_dir),
            "manifest_file": str(self.manifest_file),
            "skill_count": self.skill_count,
            "synced_at": self.synced_at,
            "targets": {key: status.to_dict() for key, status in self.target_statuses.items()},
        }


def _utc_timestamp() -> str:
    """返回统一的 UTC 时间戳，便于日志与 manifest 对齐。"""

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _expand_path(path: str | Path) -> Path:
    """展开用户目录并尽量解析为绝对路径。"""

    return Path(path).expanduser().resolve()


def _source_record_path(config_root: Path) -> Path:
    """返回上次成功同步源目录的记录文件。"""

    return config_root / "agents" / SOURCE_ROOT_RECORD_NAME


def _load_recorded_source_root(config_root: Path) -> Optional[Path]:
    """读取上次成功同步的源目录；文件损坏时 fail-closed。"""

    record_path = _source_record_path(config_root)
    if not record_path.exists():
        return None
    try:
        payload = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AgentsSyncError(f"source_root 记录无法读取：{record_path} ({exc})") from exc
    source_root = str(payload.get("source_root") or "").strip()
    if not source_root:
        raise AgentsSyncError(f"source_root 记录缺少 source_root：{record_path}")
    return _expand_path(source_root)


def resolve_source_root(
    source_root: str | Path | None = None,
    *,
    config_root: Path | None = None,
    package_root: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    """按显式参数、环境变量、历史记录、安装包顺序解析同步源目录。"""

    config_root = _expand_path(config_root or config.CONFIG_ROOT)
    package_root = _expand_path(package_root or config.PACKAGE_ROOT)
    env = env or os.environ

    if source_root is not None:
        return _expand_path(source_root)

    env_source = env.get("VIBEGO_AGENTS_SOURCE_ROOT") or env.get("MASTER_AGENTS_SOURCE_ROOT")
    if env_source:
        return _expand_path(env_source)

    recorded = _load_recorded_source_root(config_root)
    if recorded is not None:
        return recorded

    return package_root


def _validate_source_root(source_root: Path) -> tuple[Path, Path, list[Path]]:
    """校验源目录必须同时包含模板与至少一个内置 skill。"""

    template_file = source_root / "AGENTS-template.md"
    skills_dir = source_root / "vibego_cli" / "data" / "skills"
    if not template_file.is_file():
        raise AgentsSyncError(f"AGENTS 模板不存在：{template_file}")
    if not skills_dir.is_dir():
        raise AgentsSyncError(f"skills 目录不存在：{skills_dir}")
    skill_files = sorted(skills_dir.glob("*/SKILL.md"))
    if not skill_files:
        raise AgentsSyncError(f"skills 目录未发现 SKILL.md：{skills_dir}")
    return template_file, skills_dir, skill_files


def render_builtin_skills(skills_dir: Path) -> str:
    """把内置 skills 渲染成 AGENTS managed block 的静态内容。"""

    skill_files = sorted(Path(skills_dir).expanduser().glob("*/SKILL.md"))
    if not skill_files:
        raise AgentsSyncError(f"skills 目录未发现 SKILL.md：{skills_dir}")

    lines = [
        "# Vibego 内置 Skills",
        "",
        "以下技能包由 vibego 在同步 AGENTS 时自动注入，与本文件其他全局规约同级生效。",
        "当用户需求命中某个 skill 的 description 或正文触发词时，必须执行该 skill 的规则。",
        "",
    ]
    for skill_file in skill_files:
        skill_name = skill_file.parent.name
        skill_text = skill_file.read_text(encoding="utf-8").strip()
        lines.extend(
            [
                f"## Skill: {skill_name}",
                "",
                f"<!-- vibego-skill-source: {skill_file} -->",
                "",
                skill_text,
                "",
            ]
        )
    return "\n".join(lines).rstrip()


def _render_managed_block(
    *,
    template_file: Path,
    skills_dir: Path,
    source_root: Path,
    synced_at: str,
) -> str:
    """生成可替换的 AGENTS managed block。"""

    body = template_file.read_text(encoding="utf-8").rstrip()
    skills_block = render_builtin_skills(skills_dir)
    body = body + "\n\n" + skills_block
    return "\n".join(
        [
            VIBEGO_AGENTS_MARKER_START,
            f"<!-- vibego-source: {template_file} -->",
            f"<!-- vibego-source-root: {source_root} -->",
            f"<!-- vibego-synced-at-utc: {synced_at} -->",
            "",
            body,
            VIBEGO_AGENTS_MARKER_END,
            "",
        ]
    )


def update_managed_block(
    target_file: Path,
    block: str,
    *,
    marker_start: str = VIBEGO_AGENTS_MARKER_START,
    marker_end: str = VIBEGO_AGENTS_MARKER_END,
) -> TargetSyncStatus:
    """替换或追加目标文件中的 vibego managed block，并保留用户自定义内容。"""

    target_file = Path(target_file).expanduser()
    target_file.parent.mkdir(parents=True, exist_ok=True)
    backup_path: Optional[Path] = None
    status = "created"

    if target_file.exists():
        text = target_file.read_text(encoding="utf-8")
        pattern = re.compile(re.escape(marker_start) + r".*?" + re.escape(marker_end), re.DOTALL)
        match = pattern.search(text)
        if match:
            new_text = text[: match.start()] + block + text[match.end() :]
            status = "updated"
        else:
            backup_path = Path(str(target_file) + ".vibego.bak")
            if not backup_path.exists():
                shutil.copy2(target_file, backup_path)
            new_text = text.rstrip() + "\n\n" + block if text.strip() else block
            status = "appended"
    else:
        new_text = block

    if not new_text.endswith("\n"):
        new_text += "\n"
    target_file.write_text(new_text, encoding="utf-8")
    return TargetSyncStatus(path=target_file, status=status, backup_path=backup_path)


def default_agents_targets(
    *,
    config_root: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Path]:
    """解析默认同步目标，支持测试或运维通过环境变量重定向。"""

    config_root = _expand_path(config_root or config.CONFIG_ROOT)
    env = env or os.environ
    home = Path.home()
    return {
        "codex": Path(env.get("CODEX_AGENTS_FILE") or home / ".codex" / "AGENTS.md").expanduser(),
        "claude": Path(env.get("CLAUDE_AGENTS_FILE") or home / ".claude" / "CLAUDE.md").expanduser(),
        "gemini": Path(env.get("GEMINI_AGENTS_FILE") or home / ".gemini" / "GEMINI.md").expanduser(),
        "vibego": Path(env.get("VIBEGO_AGENTS_FILE") or config_root / "AGENTS.md").expanduser(),
    }


def validate_agents_override_root(override_root: Path) -> tuple[Path, Path]:
    """校验启动脚本可用的 override 根目录；损坏时必须 fail-closed。"""

    override_root = Path(override_root).expanduser()
    manifest_file = override_root / MANIFEST_NAME
    template_file = override_root / "AGENTS-template.md"
    skills_dir = override_root / "vibego_cli" / "data" / "skills"
    if not manifest_file.is_file():
        raise AgentsSyncError(f"override manifest 不存在：{manifest_file}")
    if not template_file.is_file():
        raise AgentsSyncError(f"override AGENTS 模板不存在：{template_file}")
    if not skills_dir.is_dir():
        raise AgentsSyncError(f"override skills 目录不存在：{skills_dir}")
    if not list(skills_dir.glob("*/SKILL.md")):
        raise AgentsSyncError(f"override skills 目录未发现 SKILL.md：{skills_dir}")
    return template_file, skills_dir


def _write_source_record(config_root: Path, source_root: Path, synced_at: str) -> None:
    """记录最近一次成功使用的源目录，供 Master 按钮后续复用。"""

    record_path = _source_record_path(config_root)
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(
        json.dumps(
            {
                "source_root": str(source_root),
                "synced_at": synced_at,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _publish_override_copy(
    *,
    config_root: Path,
    source_root: Path,
    template_file: Path,
    skills_dir: Path,
    skill_count: int,
    target_keys: list[str],
    synced_at: str,
) -> tuple[Path, Path, Path]:
    """复制模板与 skills 到稳定 override 目录，并写入 manifest。"""

    agents_root = config_root / "agents"
    current_root = agents_root / "current"
    staging_root = agents_root / f".current.tmp-{os.getpid()}-{uuid.uuid4().hex}"
    previous_root = agents_root / f".current.prev-{os.getpid()}-{uuid.uuid4().hex}"
    override_template = current_root / "AGENTS-template.md"
    override_skills_dir = current_root / "vibego_cli" / "data" / "skills"
    manifest_file = current_root / MANIFEST_NAME

    agents_root.mkdir(parents=True, exist_ok=True)
    try:
        staging_root.mkdir(parents=True, exist_ok=True)
        shutil.copy2(template_file, staging_root / "AGENTS-template.md")
        shutil.copytree(skills_dir, staging_root / "vibego_cli" / "data" / "skills")
        (staging_root / MANIFEST_NAME).write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "source_root": str(source_root),
                    "template_file": str(override_template),
                    "skills_dir": str(override_skills_dir),
                    "skill_count": skill_count,
                    "synced_at": synced_at,
                    "targets": {key: None for key in target_keys},
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        if current_root.exists():
            current_root.rename(previous_root)
        try:
            staging_root.rename(current_root)
        except OSError:
            if previous_root.exists() and not current_root.exists():
                previous_root.rename(current_root)
            raise
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)
        shutil.rmtree(previous_root, ignore_errors=True)

    validate_agents_override_root(current_root)
    return current_root, override_template, override_skills_dir


def sync_agents(
    *,
    source_root: str | Path | None = None,
    config_root: Path | None = None,
    targets: Mapping[str, Path] | None = None,
    env: Mapping[str, str] | None = None,
) -> AgentsSyncResult:
    """同步最新 AGENTS-template 与 skills 到本机 override 和各模型目标文件。"""

    config_root = _expand_path(config_root or config.CONFIG_ROOT)
    env = env or os.environ
    resolved_source = resolve_source_root(source_root, config_root=config_root, env=env)
    template_file, skills_dir, skill_files = _validate_source_root(resolved_source)
    target_map = dict(targets or default_agents_targets(config_root=config_root, env=env))
    synced_at = _utc_timestamp()

    override_root, override_template, override_skills_dir = _publish_override_copy(
        config_root=config_root,
        source_root=resolved_source,
        template_file=template_file,
        skills_dir=skills_dir,
        skill_count=len(skill_files),
        target_keys=list(target_map.keys()),
        synced_at=synced_at,
    )

    block = _render_managed_block(
        template_file=override_template,
        skills_dir=override_skills_dir,
        source_root=resolved_source,
        synced_at=synced_at,
    )
    target_statuses = {
        key: update_managed_block(Path(target_path), block)
        for key, target_path in target_map.items()
    }
    _write_source_record(config_root, resolved_source, synced_at)

    return AgentsSyncResult(
        source_root=resolved_source,
        override_root=override_root,
        template_file=override_template,
        skills_dir=override_skills_dir,
        manifest_file=override_root / MANIFEST_NAME,
        target_statuses=target_statuses,
        skill_count=len(skill_files),
        synced_at=synced_at,
    )
