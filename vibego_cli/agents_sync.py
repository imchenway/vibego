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
class NativeSkillSyncStatus:
    """单个 native skill 目录的同步结果。"""

    path: Path
    status: str
    skill_count: int

    def to_dict(self) -> dict[str, str | int]:
        """转换为可 JSON 序列化的字典。"""

        return {
            "path": str(self.path),
            "status": self.status,
            "skill_count": self.skill_count,
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
    native_skill_statuses: Mapping[str, NativeSkillSyncStatus]
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
            "native_skill_targets": {
                key: status.to_dict() for key, status in self.native_skill_statuses.items()
            },
        }


def _utc_timestamp() -> str:
    """返回统一的 UTC 时间戳，便于日志与 manifest 对齐。"""

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _env_flag(env: Mapping[str, str], name: str) -> bool:
    """按常见真值解析环境变量开关。"""

    return str(env.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


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


def _candidate_template_files(source_root: Path) -> list[Path]:
    """返回 AGENTS 模板候选路径，兼容源码目录与 pipx data-files 安装形态。"""

    candidates = [source_root / "AGENTS-template.md"]
    # pipx/setuptools data-files 会把 AGENTS-template.md 放在 venv 根目录，
    # 但 Python 包根目录是 venv/lib/pythonX.Y/site-packages。这里复用旧 shell 脚本的 ../../.. 兜底语义。
    if source_root.name == "site-packages" and len(source_root.parents) >= 3:
        candidates.append(source_root.parents[2] / "AGENTS-template.md")
    if virtual_env := os.environ.get("VIRTUAL_ENV"):
        candidates.append(Path(virtual_env).expanduser() / "AGENTS-template.md")
    return candidates


def _candidate_skills_dirs(source_root: Path) -> list[Path]:
    """返回内置 skills 候选目录，兼容源码目录、site-packages 与 venv 根目录。"""

    candidates = [source_root / "vibego_cli" / "data" / "skills"]
    for site_packages in sorted((source_root / "lib").glob("python*/site-packages")):
        candidates.append(site_packages / "vibego_cli" / "data" / "skills")
    return candidates


def _first_existing_file(candidates: list[Path], label: str) -> Path:
    """从候选路径中选择第一个存在的文件，否则给出包含全部候选的错误。"""

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    joined = "；".join(str(path) for path in candidates)
    raise AgentsSyncError(f"{label}不存在，已检查：{joined}")


def _first_existing_skills_dir(candidates: list[Path]) -> tuple[Path, list[Path]]:
    """选择首个存在的 skills 目录；没有内置 skill 也是合法状态。"""

    first_existing: Path | None = None
    for candidate in candidates:
        if candidate.is_dir():
            skill_files = sorted(candidate.glob("*/SKILL.md"))
            if skill_files:
                return candidate, skill_files
            if first_existing is None:
                first_existing = candidate
    if first_existing is not None:
        return first_existing, []
    if not candidates:
        raise AgentsSyncError("未配置 skills 目录候选路径")
    return candidates[0], []


def _validate_source_root(source_root: Path) -> tuple[Path, Path, list[Path]]:
    """校验源目录必须包含模板，内置 skill 允许为空。"""

    template_file = _first_existing_file(_candidate_template_files(source_root), "AGENTS 模板")
    skills_dir, skill_files = _first_existing_skills_dir(_candidate_skills_dirs(source_root))
    return template_file, skills_dir, skill_files


def _extract_skill_index(skill_file: Path) -> tuple[str, str]:
    """从 SKILL.md frontmatter 提取索引信息，不读取正文到 AGENTS。"""

    text = skill_file.read_text(encoding="utf-8")
    skill_name = skill_file.parent.name
    description = ""
    if text.startswith("---\n"):
        _, frontmatter, _ = text.split("---", 2)
        for raw_line in frontmatter.splitlines():
            line = raw_line.strip()
            if line.startswith("name:"):
                skill_name = line.removeprefix("name:").strip().strip('"').strip("'") or skill_name
            elif line.startswith("description:"):
                description = line.removeprefix("description:").strip().strip('"').strip("'")
    return skill_name, description


def render_builtin_skills(skills_dir: Path) -> str:
    """把内置 skills 渲染成 AGENTS managed block 的索引。"""

    skill_files = sorted(Path(skills_dir).expanduser().glob("*/SKILL.md"))
    if not skill_files:
        return ""

    lines = [
        "# Vibego 内置 Skills",
        "",
        "以下技能包由 vibego 在同步 AGENTS 时自动注入索引，与本文件其他全局规约同级生效。",
        "当用户需求命中某个 skill 的 name/description 时，必须先读取对应 SKILL.md 全文，再执行该 skill 的规则。",
        "AGENTS 只保留索引，不常驻注入完整 skill 正文，避免提示词膨胀。",
        "",
    ]
    for skill_file in skill_files:
        skill_name, description = _extract_skill_index(skill_file)
        lines.extend(
            [
                f"## Skill: {skill_name}",
                "",
                f"<!-- vibego-skill-source: {skill_file} -->",
                "",
                "```yaml",
                f"name: {skill_name}",
                f"description: {description}",
                "```",
                "",
                "- 命中该 skill 时，先读取上方 vibego-skill-source 指向的 SKILL.md 全文。",
                "- 若读取失败，必须 fail-closed，不得凭索引内容继续执行。",
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
    include_skill_index: bool,
) -> str:
    """生成可替换的 AGENTS managed block。"""

    body = template_file.read_text(encoding="utf-8").rstrip()
    if include_skill_index:
        skills_block = render_builtin_skills(skills_dir)
        if skills_block:
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
    home = Path(env.get("HOME") or Path.home()).expanduser()
    return {
        "codex": Path(env.get("CODEX_AGENTS_FILE") or home / ".codex" / "AGENTS.md").expanduser(),
        "claude": Path(env.get("CLAUDE_AGENTS_FILE") or home / ".claude" / "CLAUDE.md").expanduser(),
        "gemini": Path(env.get("GEMINI_AGENTS_FILE") or home / ".gemini" / "GEMINI.md").expanduser(),
        "vibego": Path(env.get("VIBEGO_AGENTS_FILE") or config_root / "AGENTS.md").expanduser(),
    }


def default_native_skill_targets(env: Mapping[str, str] | None = None) -> dict[str, Path]:
    """解析默认 native skill 安装目录。"""

    env = env or os.environ
    home = Path(env.get("HOME") or Path.home()).expanduser()
    return {
        "codex": Path(
            env.get("VIBEGO_CODEX_SKILLS_DIR")
            or env.get("CODEX_SKILLS_DIR")
            or home / ".codex" / "skills"
        ).expanduser(),
        "agents": Path(env.get("VIBEGO_AGENTS_SKILLS_DIR") or home / ".agents" / "skills").expanduser(),
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


def _replace_tree_atomically(source_dir: Path, target_dir: Path) -> None:
    """用 staging 目录原子替换目标目录。"""

    target_dir = Path(target_dir).expanduser()
    parent = target_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = parent / f".{target_dir.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}"
    previous = parent / f".{target_dir.name}.prev-{os.getpid()}-{uuid.uuid4().hex}"
    try:
        shutil.copytree(source_dir, staging)
        if target_dir.exists():
            target_dir.rename(previous)
        try:
            staging.rename(target_dir)
        except OSError:
            if previous.exists() and not target_dir.exists():
                previous.rename(target_dir)
            raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(previous, ignore_errors=True)


def publish_native_skills(
    skills_dir: Path,
    targets: Mapping[str, Path],
    *,
    removed_skill_names: set[str] | None = None,
) -> dict[str, NativeSkillSyncStatus]:
    """发布当前内置 skills，并清理由上一版 Vibego 管理但已移除的投影。"""

    skill_dirs = sorted(path for path in Path(skills_dir).expanduser().iterdir() if (path / "SKILL.md").is_file())
    removed_skill_names = removed_skill_names or set()

    statuses: dict[str, NativeSkillSyncStatus] = {}
    for key, target_root in targets.items():
        target_root = Path(target_root).expanduser()
        cleaned = False
        for skill_name in sorted(removed_skill_names):
            stale_target = target_root / skill_name
            if stale_target.is_symlink() or stale_target.is_file():
                stale_target.unlink()
                cleaned = True
            elif stale_target.is_dir():
                shutil.rmtree(stale_target)
                cleaned = True
        for skill_dir in skill_dirs:
            _replace_tree_atomically(skill_dir, target_root / skill_dir.name)
        statuses[key] = NativeSkillSyncStatus(
            path=target_root,
            status="updated" if skill_dirs else ("cleaned" if cleaned else "unchanged"),
            skill_count=len(skill_dirs),
        )
    return statuses


def _publish_override_copy(
    *,
    config_root: Path,
    source_root: Path,
    template_file: Path,
    skills_dir: Path,
    skill_count: int,
    target_keys: list[str],
    native_skill_targets: Mapping[str, Path],
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
        staging_skills_dir = staging_root / "vibego_cli" / "data" / "skills"
        if skills_dir.is_dir():
            shutil.copytree(skills_dir, staging_skills_dir)
        else:
            staging_skills_dir.mkdir(parents=True)
        (staging_root / MANIFEST_NAME).write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "source_root": str(source_root),
                    "template_file": str(override_template),
                    "skills_dir": str(override_skills_dir),
                    "skill_count": skill_count,
                    "synced_at": synced_at,
                    "native_skill_targets": {
                        key: str(Path(path).expanduser())
                        for key, path in native_skill_targets.items()
                    },
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
    native_skill_targets = default_native_skill_targets(env)
    synced_at = _utc_timestamp()
    previous_skills_dir = config_root / "agents" / "current" / "vibego_cli" / "data" / "skills"
    previous_skill_names = {
        path.parent.name for path in previous_skills_dir.glob("*/SKILL.md")
    }
    current_skill_names = {path.parent.name for path in skill_files}

    override_root, override_template, override_skills_dir = _publish_override_copy(
        config_root=config_root,
        source_root=resolved_source,
        template_file=template_file,
        skills_dir=skills_dir,
        skill_count=len(skill_files),
        target_keys=list(target_map.keys()),
        native_skill_targets=native_skill_targets,
        synced_at=synced_at,
    )
    native_skill_statuses = publish_native_skills(
        override_skills_dir,
        native_skill_targets,
        removed_skill_names=previous_skill_names - current_skill_names,
    )

    block = _render_managed_block(
        template_file=override_template,
        skills_dir=override_skills_dir,
        source_root=resolved_source,
        synced_at=synced_at,
        include_skill_index=_env_flag(env, "VIBEGO_AGENTS_LEGACY_SKILL_INDEX"),
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
        native_skill_statuses=native_skill_statuses,
        skill_count=len(skill_files),
        synced_at=synced_at,
    )
