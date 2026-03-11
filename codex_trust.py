"""Codex trusted 目录共享工具。

该模块仅负责 `~/.codex/config.toml` 的纯文件读写逻辑，
供 worker（bot.py）与 master（master.py）共同复用。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python<3.11 兼容兜底
    import tomli as tomllib  # type: ignore[no-redef]


CODEX_CONFIG_PATH = Path(
    os.environ.get("CODEX_CONFIG_PATH", str(Path.home() / ".codex" / "config.toml"))
).expanduser()


@dataclass(frozen=True)
class CodexTrustEnsureResult:
    """描述一次 trusted 自动配置的结果。"""

    path: Path
    previous_trust_level: Optional[str]
    changed: bool


def resolve_path(path: Path | str) -> Path:
    """统一展开用户目录与环境变量，避免写入错路径。"""

    if isinstance(path, Path):
        return path.expanduser()
    return Path(os.path.expanduser(os.path.expandvars(path))).expanduser()


def codex_project_table_header(path: Path) -> str:
    """返回 Codex config.toml 中 projects.<path> 的标准表头。"""

    return f"[projects.{json.dumps(str(path))}]"


def find_codex_project_table_bounds(text: str, path: Path) -> tuple[int, int] | None:
    """查找指定 projects.<path> 表在文本中的起止范围。"""

    pattern = re.compile(
        rf'^\[projects\.(["\']){re.escape(str(path))}\1\]\s*$',
        re.MULTILINE,
    )
    match = pattern.search(text)
    if match is None:
        return None
    start = match.start()
    next_header = re.compile(r"^\[", re.MULTILINE).search(text, match.end())
    end = next_header.start() if next_header is not None else len(text)
    return start, end


def read_codex_project_trust_level(config_path: Path, project_path: Path) -> Optional[str]:
    """读取指定目录当前的 trust_level；解析失败时按缺失处理。"""

    if not config_path.exists():
        return None
    try:
        raw = config_path.read_text(encoding="utf-8")
        data = tomllib.loads(raw) if raw.strip() else {}
    except (OSError, tomllib.TOMLDecodeError):
        return None
    projects = data.get("projects")
    if not isinstance(projects, dict):
        return None
    project = projects.get(str(project_path))
    if not isinstance(project, dict):
        return None
    trust_level = project.get("trust_level")
    return trust_level if isinstance(trust_level, str) else None


def upsert_codex_project_trust_level_text(text: str, project_path: Path, trust_level: str) -> str:
    """在 config.toml 文本中新增或更新指定目录的 trust_level。"""

    bounds = find_codex_project_table_bounds(text, project_path)
    line = f'trust_level = "{trust_level}"'
    if bounds is None:
        base = text.rstrip()
        suffix = "" if not base else "\n\n"
        return f"{base}{suffix}{codex_project_table_header(project_path)}\n{line}\n"
    start, end = bounds
    section = text[start:end]
    if re.search(r"^\s*trust_level\s*=", section, re.MULTILINE):
        section = re.sub(
            r'^\s*trust_level\s*=\s*["\'][^"\']*["\']\s*$',
            line,
            section,
            count=1,
            flags=re.MULTILINE,
        )
    else:
        section = section.rstrip("\n") + f"\n{line}\n"
    return text[:start] + section + text[end:]


def remove_codex_project_table_text(text: str, project_path: Path) -> str:
    """从 config.toml 文本中移除指定目录对应的表。"""

    bounds = find_codex_project_table_bounds(text, project_path)
    if bounds is None:
        return text
    start, end = bounds
    cleaned = (text[:start] + text[end:]).strip("\n")
    return f"{cleaned}\n" if cleaned else ""


def write_codex_config_text(config_path: Path, text: str) -> None:
    """原子写回 config.toml，降低中途写坏的风险。"""

    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = config_path.with_suffix(config_path.suffix + ".tmp")
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(config_path)


def ensure_codex_project_trust(
    project_path: Path | str,
    *,
    config_path: Path | None = None,
) -> CodexTrustEnsureResult:
    """确保指定目录已被 Codex 标记为 trusted。"""

    normalized = resolve_path(project_path)
    target_config = resolve_path(config_path or CODEX_CONFIG_PATH)
    raw = target_config.read_text(encoding="utf-8") if target_config.exists() else ""
    current = read_codex_project_trust_level(target_config, normalized)
    changed = current != "trusted"
    if changed:
        raw = upsert_codex_project_trust_level_text(raw, normalized, "trusted")
        write_codex_config_text(target_config, raw)
    return CodexTrustEnsureResult(
        path=normalized,
        previous_trust_level=current,
        changed=changed,
    )
