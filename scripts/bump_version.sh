#!/usr/bin/env bash
# Convenient script for version management
# How to use:
#   ./scripts/bump_version.sh patch
#   ./scripts/bump_version.sh minor
#   ./scripts/bump_version.sh major
#   ./scripts/bump_version.sh show
#   ./scripts/bump_version.sh --help

set -e

# Project root directory
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

# bump-my-version path
MASTER_CONFIG_ROOT="${MASTER_CONFIG_ROOT:-$HOME/.config/vibego}"
RUNTIME_DIR="${VIBEGO_RUNTIME_ROOT:-$MASTER_CONFIG_ROOT/runtime}"
BUMP_CMD="$RUNTIME_DIR/.venv/bin/bump-my-version"

# Check if bump-my-version exists
if [ ! -f "$BUMP_CMD" ]; then
    echo "Error: bump-my-version not found"
    echo "Please install first: pip install bump-my-version"
    exit 1
fi

# If there are no parameters, display help
if [ $# -eq 0 ]; then
    echo "usage:"
    echo "  $0 patch         Increment patch version (0.2.11 → 0.2.12)"
    echo "                   Autocommit: fix: bugfixes"
    echo "  $0 minor         Increment minor version (0.2.11 → 0.3.0)"
    echo "                   Automatic submission: feat: Add new features"
    echo "  $0 major         Increment major version (0.2.11 → 1.0.0)"
    echo "                   Automatic submission: feat!: Major changes"
    echo "  $0 show          Show current version"
    echo "  $0 --dry-run     Preview changes (added in patch/minor/major back)"
    echo ""
    echo "illustrate:"
    echo "  The script automatically commits the currently uncommitted changes and then increments the version number."
    echo "  If you don't want to commit automatically, add --no-auto-commit to the parameters"
    echo ""
    echo "Example:"
    echo "  $0 patch                    # Automatically commit changes andIncrement patch version"
    echo "  $0 patch --dry-run         # The preview patch version is incremented (will not be submitted)"
    echo "  $0 minor --no-auto-commit  # Only increments version, does not automatically commit current modifications"
    exit 0
fi

# Handling show commands
if [ "$1" = "show" ]; then
    "$BUMP_CMD" show current_version
    exit 0
fi

# Processing --help
if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
    "$BUMP_CMD" --help
    exit 0
fi

# Check if autocommit is disabled
AUTO_COMMIT=true
if [[ "$*" =~ "--no-auto-commit" ]]; then
    AUTO_COMMIT=false
fi

# Check if it is dry-run
DRY_RUN=false
if [[ "$*" =~ "--dry-run" ]]; then
    DRY_RUN=true
fi

# Get version type
VERSION_TYPE="$1"

# Get the commit message of the corresponding version type
get_commit_message() {
    case "$1" in
        patch)
            echo "fix: bugfixes"
            ;;
        minor)
            echo "feat: Add new features"
            ;;
        major)
            echo "feat!: Major changes"
            ;;
        *)
            echo ""
            ;;
    esac
}

# Check if the version type is valid
COMMIT_MSG=$(get_commit_message "$VERSION_TYPE")
if [ -z "$COMMIT_MSG" ]; then
    # If not a valid version type, pass it directly to bump-my-version
    "$BUMP_CMD" bump "$@"
    exit 0
fi

# Show current version
echo "📦 Current version:$("$BUMP_CMD" show current_version)"
echo ""

# Check for uncommitted changes
if [ "$AUTO_COMMIT" = true ] && [ "$DRY_RUN" = false ]; then
    if ! git diff-index --quiet HEAD -- 2>/dev/null; then
        echo "📝 Uncommitted changes detected, prepare to create commit..."
        echo ""

        echo "Commit information:$COMMIT_MSG"
        echo ""

        # Show files to be submitted
        echo "Documents to be submitted:"
        git status --short
        echo ""

        # Commit all changes
        git add .
        git commit -m "$COMMIT_MSG"

        echo "OK: Code modification has been submitted"
        echo ""
    else
        echo "ℹ️  No uncommitted changes, skip automatic commit"
        echo ""
    fi
fi

# Execution version increment
echo "🚀 Start incremental version..."
echo ""

"$BUMP_CMD" bump "$@"

echo ""
echo "OK: Version management completed!"
echo ""
echo "📋 Operation summary:"
if [ "$AUTO_COMMIT" = true ] && [ "$DRY_RUN" = false ]; then
    echo "   1. Submitted code modifications (if any)"
    echo "   2. Version number incremented"
    echo "   3. Version commit and tag created"
else
    echo "   1. Version number incremented"
    echo "   2. Version commit and tag created"
fi
echo ""
echo "💡 Tip: To push to remote, please execute:"
echo "   git push && git push --tags"
