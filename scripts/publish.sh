#!/usr/bin/env bash
# vibego 发布脚本（适配本地终端与机器人非交互环境）
#
# 用法：
#   ./scripts/publish.sh                    # 默认 patch 发布
#   ./scripts/publish.sh patch              # patch 发布
#   ./scripts/publish.sh minor              # minor 发布
#   ./scripts/publish.sh major              # major 发布
#   ./scripts/publish.sh patch --skip-pipx  # 跳过本机 pipx 重装
#   ./scripts/publish.sh patch --dry-run    # 仅演练版本与构建，不上传
#
# 认证方式（优先级）：
#   1) TWINE_USERNAME + TWINE_PASSWORD
#   2) PYPI_API_TOKEN（自动映射为 __token__）
#   3) 本地 keyring（仅当可读取到 token 时使用）

set -Eeuo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_info() { echo -e "${BLUE}ℹ️  $*${NC}"; }
print_ok() { echo -e "${GREEN}✅ $*${NC}"; }
print_warn() { echo -e "${YELLOW}⚠️  $*${NC}"; }
print_err() { echo -e "${RED}❌ $*${NC}" >&2; }

on_error() {
    local exit_code="$1"
    local line="$2"
    print_err "发布失败（退出码=${exit_code}，行号=${line}）"
}
trap 'on_error "$?" "$LINENO"' ERR

usage() {
    cat <<'EOF'
vibego 发布脚本

用法：
  ./scripts/publish.sh [patch|minor|major] [选项]

选项：
  --skip-upload     跳过 PyPI 上传
  --skip-pipx       跳过 pipx reinstall/upgrade
  --skip-restart    跳过 vibego stop/start
  --dry-run         等价于 --skip-upload（保留版本 bump 与构建）
  -h, --help        显示帮助
EOF
}

resolve_project_root() {
    cd "$(dirname "${BASH_SOURCE[0]}")/.."
    pwd
}

require_command() {
    local cmd="$1"
    if ! command -v "$cmd" >/dev/null 2>&1; then
        print_err "缺少依赖命令：$cmd"
        exit 1
    fi
}

resolve_python() {
    if command -v python3 >/dev/null 2>&1; then
        echo "python3"
        return 0
    fi
    if command -v python >/dev/null 2>&1; then
        echo "python"
        return 0
    fi
    print_err "未找到 python3/python"
    exit 1
}

configure_twine_auth() {
    # 优先使用显式环境变量，避免机器人环境交互卡住。
    if [[ -n "${TWINE_USERNAME:-}" && -n "${TWINE_PASSWORD:-}" ]]; then
        print_ok "检测到 TWINE_USERNAME/TWINE_PASSWORD，使用非交互上传"
        return 0
    fi

    if [[ -n "${PYPI_API_TOKEN:-}" ]]; then
        export TWINE_USERNAME="__token__"
        export TWINE_PASSWORD="${PYPI_API_TOKEN}"
        print_ok "检测到 PYPI_API_TOKEN，已映射到 Twine 认证变量"
        return 0
    fi

    # 最后尝试 keyring，兼容已有本机环境。
    local py_bin="$1"
    if "$py_bin" - <<'PY' >/dev/null 2>&1
import sys
try:
    import keyring
except Exception:
    sys.exit(1)
token = keyring.get_password("https://upload.pypi.org/legacy/", "__token__")
sys.exit(0 if token else 1)
PY
    then
        print_ok "检测到 keyring 中的 PyPI token，上传将由 Twine/keyring 处理"
        return 0
    fi

    print_err "未检测到 PyPI 认证信息。请设置 PYPI_API_TOKEN 或 TWINE_USERNAME/TWINE_PASSWORD"
    exit 1
}

VERSION_TYPE="patch"
SKIP_UPLOAD="false"
SKIP_PIPX="false"
SKIP_RESTART="false"

if [[ $# -gt 0 ]]; then
    case "$1" in
        patch|minor|major)
            VERSION_TYPE="$1"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
    esac
fi

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-upload|--dry-run)
            SKIP_UPLOAD="true"
            ;;
        --skip-pipx)
            SKIP_PIPX="true"
            ;;
        --skip-restart)
            SKIP_RESTART="true"
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            print_err "未知参数：$1"
            usage
            exit 1
            ;;
    esac
    shift
done

PROJECT_ROOT="$(resolve_project_root)"
cd "$PROJECT_ROOT"

require_command "git"
require_command "pipx"
PY_BIN="$(resolve_python)"

# 统一构建虚拟环境，避免污染系统环境
BUILD_VENV="${BUILD_VENV:-$HOME/.venvs/vibego-build}"

print_info "项目目录：$PROJECT_ROOT"
print_info "发布类型：$VERSION_TYPE"
print_info "开始执行发布流程..."

print_info "步骤 1/7：准备构建环境"
"$PY_BIN" -m venv "$BUILD_VENV"
# shellcheck disable=SC1090
source "$BUILD_VENV/bin/activate"
python -m pip install --upgrade pip build twine >/dev/null
print_ok "构建环境就绪"

print_info "步骤 2/7：检查工作区与清理产物"
if ! git diff-index --quiet HEAD --; then
    print_warn "检测到未提交修改，bump_version.sh 可能会自动提交（保持与现有流程一致）"
fi
rm -rf "$PROJECT_ROOT/dist"
print_ok "dist 清理完成"

print_info "步骤 3/7：递增版本号"
"$PROJECT_ROOT/scripts/bump_version.sh" "$VERSION_TYPE"
print_ok "版本号已递增"

print_info "步骤 4/7：构建分发包"
python -m build
print_ok "构建完成"

print_info "步骤 5/7：上传 PyPI"
if [[ "$SKIP_UPLOAD" == "true" ]]; then
    print_warn "已跳过上传（--skip-upload/--dry-run）"
else
    configure_twine_auth "$PY_BIN"
    twine upload --non-interactive dist/*
    print_ok "PyPI 上传成功"
fi

print_info "步骤 6/7：更新本地 pipx 安装"
if [[ "$SKIP_PIPX" == "true" ]]; then
    print_warn "已跳过 pipx 重装（--skip-pipx）"
else
    rm -rf "$HOME/.cache/pipx" "$HOME/.local/pipx/venvs/vibego"
    pipx install --python "$PY_BIN" vibego
    pipx upgrade vibego
    print_ok "pipx 安装更新完成"
fi

print_info "步骤 7/7：重启 vibego 服务"
if [[ "$SKIP_RESTART" == "true" ]]; then
    print_warn "已跳过服务重启（--skip-restart）"
else
    vibego stop || true
    sleep 2
    vibego start
    print_ok "服务重启完成"
fi

print_ok "========================================="
print_ok "发布流程完成"
print_ok "========================================="
print_info "建议后续检查："
echo "  1) git push && git push --tags"
echo "  2) https://pypi.org/project/vibego/"
