#!/usr/bin/env bash
# 版本管理便捷脚本
# 使用方式：
#   ./scripts/bump_version.sh patch
#   ./scripts/bump_version.sh minor
#   ./scripts/bump_version.sh major
#   ./scripts/bump_version.sh show
#   ./scripts/bump_version.sh --help

set -e

# 项目根目录
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

# bump-my-version 路径
MASTER_CONFIG_ROOT="${MASTER_CONFIG_ROOT:-$HOME/.config/vibego}"
RUNTIME_DIR="${VIBEGO_RUNTIME_ROOT:-$MASTER_CONFIG_ROOT/runtime}"
BUMP_CMD="$RUNTIME_DIR/.venv/bin/bump-my-version"

# 检查 bump-my-version 是否存在
if [ ! -f "$BUMP_CMD" ]; then
    echo "错误：找不到 bump-my-version"
    echo "请先安装：pip install bump-my-version"
    exit 1
fi

# 如果没有参数，显示帮助
if [ $# -eq 0 ]; then
    echo "用法："
    echo "  $0 patch         递增补丁版本 (0.2.11 → 0.2.12)"
    echo "                   自动提交：fix: bugfixes"
    echo "  $0 minor         递增次版本 (0.2.11 → 0.3.0)"
    echo "                   自动提交：feat: 添加新功能"
    echo "  $0 major         递增主版本 (0.2.11 → 1.0.0)"
    echo "                   自动提交：feat!: 重大变更"
    echo "  $0 show          显示当前版本"
    echo "  $0 --dry-run     预览变更（添加在 patch/minor/major 后）"
    echo ""
    echo "说明："
    echo "  脚本会自动提交当前未提交的修改，然后递增版本号。"
    echo "  如果不想自动提交，请在参数中添加 --no-auto-commit"
    echo ""
    echo "示例："
    echo "  $0 patch                    # 自动提交修改并递增补丁版本"
    echo "  $0 patch --dry-run         # 预览补丁版本递增（不会提交）"
    echo "  $0 minor --no-auto-commit  # 仅递增版本，不自动提交当前修改"
    exit 0
fi

# 处理 show 命令
if [ "$1" = "show" ]; then
    "$BUMP_CMD" show current_version
    exit 0
fi

# 处理 --help
if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
    "$BUMP_CMD" --help
    exit 0
fi

# 检查是否禁用自动提交
AUTO_COMMIT=true
if [[ "$*" =~ "--no-auto-commit" ]]; then
    AUTO_COMMIT=false
fi

# 检查是否是 dry-run
DRY_RUN=false
if [[ "$*" =~ "--dry-run" ]]; then
    DRY_RUN=true
fi

# 获取版本类型
VERSION_TYPE="$1"

# 获取对应版本类型的 commit 消息
get_commit_message() {
    case "$1" in
        patch)
            echo "fix: bugfixes"
            ;;
        minor)
            echo "feat: 添加新功能"
            ;;
        major)
            echo "feat!: 重大变更"
            ;;
        *)
            echo ""
            ;;
    esac
}

# 检查版本类型是否有效
COMMIT_MSG=$(get_commit_message "$VERSION_TYPE")
if [ -z "$COMMIT_MSG" ]; then
    # 如果不是有效的版本类型，直接传递给 bump-my-version
    "$BUMP_CMD" bump "$@"
    exit 0
fi

# 显示当前版本
echo "📦 当前版本：$("$BUMP_CMD" show current_version)"
echo ""

# 检查是否有未提交的修改
if [ "$AUTO_COMMIT" = true ] && [ "$DRY_RUN" = false ]; then
    if ! git diff-index --quiet HEAD -- 2>/dev/null; then
        echo "📝 检测到未提交的修改，准备创建 commit..."
        echo ""

        echo "Commit 消息：$COMMIT_MSG"
        echo ""

        # 显示将要提交的文件
        echo "将要提交的文件："
        git status --short
        echo ""

        # 提交所有修改
        git add .
        git commit -m "$COMMIT_MSG"

        echo "✅ 代码修改已提交"
        echo ""
    else
        echo "ℹ️  没有未提交的修改，跳过自动 commit"
        echo ""
    fi
fi

# 执行版本递增
echo "🚀 开始递增版本..."
echo ""

"$BUMP_CMD" bump "$@"

echo ""
echo "✅ 版本管理完成！"
echo ""
echo "📋 操作摘要："
if [ "$AUTO_COMMIT" = true ] && [ "$DRY_RUN" = false ]; then
    echo "   1. 已提交代码修改（如有）"
    echo "   2. 已递增版本号"
    echo "   3. 已创建版本 commit 和 tag"
else
    echo "   1. 已递增版本号"
    echo "   2. 已创建版本 commit 和 tag"
fi
echo ""
echo "💡 提示：如需推送到远程，请执行："
echo "   git push && git push --tags"
