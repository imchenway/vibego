"""vibego CLI 初始化与启动工具。

该包提供 `vibego` 命令的核心实现，封装了配置目录管理、
依赖自检以及 master 服务启动/停止逻辑。"""

from __future__ import annotations

__all__ = ["main", "__version__"]

__version__ = "1.5.0"

from .main import main  # noqa: E402
