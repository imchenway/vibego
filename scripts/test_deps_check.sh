#!/usr/bin/env bash
# 测试依赖检查函数的逻辑
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MASTER_CONFIG_ROOT="${MASTER_CONFIG_ROOT:-$HOME/.config/vibego}"
RUNTIME_DIR="${VIBEGO_RUNTIME_ROOT:-$MASTER_CONFIG_ROOT/runtime}"
VENV_DIR="$RUNTIME_DIR/.venv"

echo "=== 测试依赖检查函数 ==="
echo "ROOT_DIR: $ROOT_DIR"
echo "RUNTIME_DIR: $RUNTIME_DIR"
echo ""

# 从start.sh提取check_deps_installed函数进行测试
check_deps_installed() {
  if [[ ! -d "$VENV_DIR" ]]; then
    echo "❌ 虚拟环境不存在"
    return 1
  fi

  if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    echo "❌ 虚拟环境Python解释器缺失"
    return 1
  fi

  if ! "$VENV_DIR/bin/python" -c "import aiogram, aiohttp, aiosqlite" 2>/dev/null; then
    echo "❌ 关键依赖包缺失或损坏"
    return 1
  fi

  echo "✅ 依赖检查通过"
  return 0
}

# 测试1：检查当前环境
echo "【测试1】检查当前虚拟环境状态："
if check_deps_installed; then
  echo "结果：依赖完整，重启时将跳过pip install"
else
  echo "结果：依赖缺失，重启时将执行pip install"
fi
echo ""

# 测试2：检查虚拟环境目录
echo "【测试2】虚拟环境目录检查："
if [[ -d "$VENV_DIR" ]]; then
  echo "✅ 虚拟环境目录存在：$VENV_DIR"
  ls -lh "$VENV_DIR/bin/python" 2>/dev/null || echo "❌ Python解释器不存在"
else
  echo "❌ 虚拟环境目录不存在：$VENV_DIR"
fi
echo ""

# 测试3：检查关键依赖
echo "【测试3】关键依赖包检查："
if [[ -x "$VENV_DIR/bin/python" ]]; then
  for pkg in aiogram aiohttp aiosqlite; do
    if "$VENV_DIR/bin/python" -c "import $pkg" 2>/dev/null; then
      echo "✅ $pkg 已安装"
    else
      echo "❌ $pkg 未安装"
    fi
  done
else
  echo "⚠️  无法检查（Python解释器不可用）"
fi
echo ""

echo "=== 测试完成 ==="
