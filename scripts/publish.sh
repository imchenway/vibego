#!/usr/bin/env bash
# VibeGo 完整发布脚本
# 使用 keyring 进行 PyPI 认证，无需手动输入 token
#
# 前置条件：
#   1. 已安装 keyring: pip install keyring
#   2. 已存储 PyPI token 到 keyring:
#      python3.11 -c "import keyring; keyring.set_password('https://upload.pypi.org/legacy/', '__token__', 'your-token')"
#
# 使用方式：
#   ./scripts/publish.sh           # 发布 patch 版本（默认）
#   ./scripts/publish.sh minor     # 发布 minor 版本
#   ./scripts/publish.sh major     # 发布 major 版本

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 打印带颜色的消息
print_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

# 项目根目录
PROJECT_ROOT="/Users/david/hypha/tools/vibeBot"
cd "$PROJECT_ROOT"

print_info "开始 VibeGo 发布流程..."
echo ""

# 步骤 1: 检查 keyring 中是否存储了 PyPI token
print_info "检查 keyring 配置..."
if ! python3.11 -c "import keyring; token = keyring.get_password('https://upload.pypi.org/legacy/', '__token__'); exit(0 if token else 1)" 2>/dev/null; then
    print_error "未在 keyring 中找到 PyPI token"
    echo ""
    echo "请先执行以下命令存储 token："
    echo "  python3.11 -c \"import keyring; keyring.set_password('https://upload.pypi.org/legacy/', '__token__', 'your-pypi-token')\""
    echo ""
    exit 1
fi
print_success "Keyring 配置正确"
echo ""

# 步骤 2: 创建/激活虚拟环境
print_info "创建构建虚拟环境..."
python3.11 -m venv ~/.venvs/vibego-build
source ~/.venvs/vibego-build/bin/activate
print_success "虚拟环境已激活"
echo ""

# 步骤 3: 升级 pip 和安装构建工具
print_info "安装构建依赖..."
pip install --upgrade pip build twine keyring > /dev/null 2>&1
print_success "构建依赖已安装"
echo ""

# 步骤 4: 清理旧的构建产物
print_info "清理旧的构建产物..."
rm -rf "$PROJECT_ROOT/dist"
print_success "构建产物已清理"
echo ""

# 步骤 5: 递增版本号
VERSION_TYPE="${1:-patch}"  # 默认为 patch
print_info "递增版本号（类型：$VERSION_TYPE）..."
./scripts/bump_version.sh "$VERSION_TYPE"
echo ""

# 步骤 6: 构建分发包
print_info "构建 Python 分发包..."
python3.11 -m build
print_success "分发包构建完成"
echo ""

# 步骤 7: 上传到 PyPI（使用 keyring 自动认证）
print_info "上传到 PyPI（使用 keyring 认证）..."
twine upload dist/*
print_success "已成功上传到 PyPI"
echo ""

# 步骤 8: 清理并重装 pipx 中的 vibego
print_info "更新本地 pipx 安装..."
rm -rf ~/.cache/pipx
rm -rf ~/.local/pipx/venvs/vibego
pipx install --python python3.11 vibego
pipx upgrade vibego
print_success "本地 vibego 已更新"
echo ""

# 步骤 9: 重启 vibego 服务
print_info "重启 vibego 服务..."
vibego stop || true  # 忽略停止失败的错误
sleep 2
vibego start
print_success "vibego 服务已重启"
echo ""

# 完成
print_success "========================================="
print_success "🎉 发布流程完成！"
print_success "========================================="
echo ""
print_info "后续步骤："
echo "  1. 推送 git 提交和标签："
echo "     git push && git push --tags"
echo ""
echo "  2. 验证 PyPI 页面："
echo "     https://pypi.org/project/vibego/"
echo ""
