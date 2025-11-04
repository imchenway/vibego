"""vibego CLI bootstrap utilities.

The package exposes the core implementation behind the ``vibego`` command,
including configuration directory management, dependency checks, and master
service lifecycle helpers."""

from __future__ import annotations

__all__ = ["main", "__version__"]

__version__ = "0.2.58"

from .main import main  # noqa: E402
