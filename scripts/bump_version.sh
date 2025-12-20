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

# pipx fallback 标记
USE_PIPEX=false

# 检测脚本型可执行文件的 shebang 是否指向不存在的解释器。
# 典型场景：运行时 venv 被重建/迁移后，bump-my-version 的首行仍指向旧的 python 路径，
# 执行时会报：bad interpreter: No such file or directory (exit=126)。
is_broken_shebang() {
    local candidate="$1"
    [[ -f "$candidate" && -x "$candidate" ]] || return 1
    local first_line=""
    first_line="$(head -n 1 "$candidate" 2>/dev/null || true)"
    [[ "$first_line" == "#!"* ]] || return 1
    local shebang="${first_line#\#!}"
    # 兼容 CRLF
    shebang="${shebang%%$'\r'}"
    local interpreter="${shebang%% *}"
    # /usr/bin/env 由系统提供，后续由 PATH 解析真正解释器，无法在此处强校验
    if [[ "$interpreter" == "/usr/bin/env" || "$interpreter" == "env" ]]; then
        return 1
    fi
    # 仅对绝对路径做存在性校验
    if [[ "$interpreter" == /* && ! -x "$interpreter" ]]; then
        return 0
    fi
    return 1
}

is_usable_bump_cmd() {
    local candidate="$1"
    [[ -n "$candidate" && -x "$candidate" ]] || return 1
    if is_broken_shebang "$candidate"; then
        return 1
    fi
    return 0
}

# 依次尝试运行时虚拟环境、pipx 安装目录以及 PATH 中的可执行文件
if ! is_usable_bump_cmd "$BUMP_CMD"; then
    if [[ -x "$BUMP_CMD" ]] && is_broken_shebang "$BUMP_CMD"; then
        echo "⚠️  检测到 bump-my-version 解释器缺失（bad interpreter），将自动降级到 pipx/PATH 版本。" >&2
    fi
    # 优先尝试 pipx 默认的 ~/.local/bin
    if is_usable_bump_cmd "$HOME/.local/bin/bump-my-version"; then
        BUMP_CMD="$HOME/.local/bin/bump-my-version"
    elif command -v bump-my-version >/dev/null 2>&1; then
        CANDIDATE="$(command -v bump-my-version)"
        if is_usable_bump_cmd "$CANDIDATE"; then
            BUMP_CMD="$CANDIDATE"
        else
            # PATH 中的 bump-my-version 也可能因解释器缺失而不可用
            if command -v pipx >/dev/null 2>&1; then
                USE_PIPEX=true
            else
                echo "错误：检测到 bump-my-version 但不可用（可能是 bad interpreter），且未安装 pipx" >&2
                echo "请先执行：pipx install bump-my-version" >&2
                exit 1
            fi
        fi
    elif command -v pipx >/dev/null 2>&1; then
        USE_PIPEX=true
    else
        echo "错误：找不到 bump-my-version"
        echo "请先执行：pipx install bump-my-version"
        exit 1
    fi
fi

# 封装执行逻辑，pipx 模式下使用 pipx run 避免重复安装
run_bump_my_version() {
    if [ "$USE_PIPEX" = true ]; then
        pipx run bump-my-version "$@"
    else
        "$BUMP_CMD" "$@"
    fi
}

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
    run_bump_my_version show current_version
    exit 0
fi

# 处理 --help
if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
    run_bump_my_version --help
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
    run_bump_my_version bump "$@"
    exit 0
fi

# 显示当前版本
echo "📦 当前版本：$(run_bump_my_version show current_version)"
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

run_bump_my_version bump "$@"

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
