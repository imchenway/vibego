#!/usr/bin/env bash
# Test the logic of the dependency check function
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MASTER_CONFIG_ROOT="${MASTER_CONFIG_ROOT:-$HOME/.config/vibego}"
RUNTIME_DIR="${VIBEGO_RUNTIME_ROOT:-$MASTER_CONFIG_ROOT/runtime}"
VENV_DIR="$RUNTIME_DIR/.venv"

echo "=== Test dependency check function ==="
echo "ROOT_DIR: $ROOT_DIR"
echo "RUNTIME_DIR: $RUNTIME_DIR"
echo ""

# from start.shExtract check_deps_installedfunction to test
check_deps_installed() {
  if [[ ! -d "$VENV_DIR" ]]; then
    echo "ERROR: Virtual environment does not exist"
    return 1
  fi

  if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    echo "ERROR: Python interpreter not found in virtual environment"
    return 1
  fi

  if ! "$VENV_DIR/bin/python" -c "import aiogram, aiohttp, aiosqlite" 2>/dev/null; then
    echo "ERROR: Required dependencies are missing or broken"
    return 1
  fi

  echo "OK: Dependency check passed"
  return 0
}

# Test 1: Check the current environment
echo "[Test 1] Check the current virtual environment status:"
if check_deps_installed; then
  echo "Result: dependencies are complete, pip install will be skipped when restarting"
else
  echo "Result: Dependencies are missing, pip install will be executed when restarting"
fi
echo ""

# Test 2: Check the virtual environment directory
echo "[Test 2] Virtual environment directory check:"
if [[ -d "$VENV_DIR" ]]; then
  echo "OK: Virtual environment directory exists: $VENV_DIR"
  ls -lh "$VENV_DIR/bin/python" 2>/dev/null || echo "ERROR: Python interpreter does not exist"
else
  echo "ERROR: Virtual environment directory does not exist: $VENV_DIR"
fi
echo ""

# Test 3: Check critical dependencies
echo "[Test 3] Key dependency package check:"
if [[ -x "$VENV_DIR/bin/python" ]]; then
  for pkg in aiogram aiohttp aiosqlite; do
    if "$VENV_DIR/bin/python" -c "import $pkg" 2>/dev/null; then
      echo "OK: $pkg installed"
    else
      echo "ERROR: $pkg not installed"
    fi
  done
else
  echo "WARN: Unable to check (Python interpreter unavailable)"
fi
echo ""

echo "=== Test completed ==="
