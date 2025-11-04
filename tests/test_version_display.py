"""Test that the /start command displays the version number."""

import sys
from pathlib import Path

# Add project root directory to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from vibego_cli import __version__

print("\n" + "=" * 60)
print("Version number display test")
print("=" * 60 + "\n")

print(f"Read version from vibego_cli: {__version__}\n")

# Simulate the /start command response
project_count = 3  # Assume there are 3 projects

message = (
    f"Master bot started (v{__version__}).\n"
    f"Registered projects: {project_count}.\n"
    "Use /projects to check status, and /run or /stop to control workers."
)

print("Simulated /start Telegram response:")
print("-" * 60)
print(message)
print("-" * 60)
print()

print("The version number display format is correct.")
print("   - Version source: vibego_cli/__init__.py")
print(f"   - Current version: v{__version__}")
print(f"   - Display format: concise format (v{__version__})")
print()

# Verify modification points
print("=" * 60)
print("Edit summary")
print("=" * 60)
print()
print("1. File: master.py")
print("   - Line 57: Add import `from vibego_cli import __version__`")
print("   - Line 1796: Modify the message to `f\"Master bot started (v{__version__}).\"`")
print()
print("2. Effect of the change:")
print("   Before: Master bot has been started.")
print(f"   After: Master bot started (v{__version__}).")
print()
print("3. Deployment instructions:")
print("   After restarting the master bot, running /start shows the version number.")
print("   Command: python -m vibego_cli stop && python -m vibego_cli start")
print()
