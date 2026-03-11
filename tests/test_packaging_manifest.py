from __future__ import annotations

from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def _load_pyproject() -> dict:
    """读取项目打包清单，供回归测试复用。"""

    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_parallel_runtime_is_packaged_for_distribution() -> None:
    """parallel_runtime 被 bot.py 直接依赖，发布包必须显式收录。"""

    data = _load_pyproject()
    py_modules = set(data["tool"]["setuptools"]["py-modules"])

    assert "parallel_runtime" in py_modules


def test_codex_trust_is_packaged_for_distribution() -> None:
    """codex_trust 被 master.py / bot.py 直接依赖，发布包必须显式收录。"""

    data = _load_pyproject()
    py_modules = set(data["tool"]["setuptools"]["py-modules"])

    assert "codex_trust" in py_modules
