#!/usr/bin/env bash
# VibeGo Full release script
# Use keyring for PyPI authentication without manually entering tokens
#
# Prerequisites:
#   1. keyring installed: pip install keyring
#   2. PyPI token stored in keyring:
#      python3.11 -c "import keyring; keyring.set_password('https://upload.pypi.org/legacy/', '__token__', 'your-token')"
#
# How to use:
#   ./scripts/publish.sh           # Publish patch version (default)
#   ./scripts/publish.sh minor     # Release minor version
#   ./scripts/publish.sh major     # Release major version

set -e

# color definition
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Print colored messages
print_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

print_success() {
    echo -e "${GREEN}OK: $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

print_error() {
    echo -e "${RED}ERROR: $1${NC}"
}

# Project root directory
PROJECT_ROOT="/Users/david/hypha/tools/vibeBot"
cd "$PROJECT_ROOT"

print_info "Start the VibeGo publishing process..."
echo ""

# Step 1: Check if PyPI token is stored in keyring
print_info "Check keyring configuration..."
if ! python3.11 -c "import keyring; token = keyring.get_password('https://upload.pypi.org/legacy/', '__token__'); exit(0 if token else 1)" 2>/dev/null; then
    print_error "PyPI token not found in keyring"
    echo ""
    echo "Please execute the following command to store the token first:"
    echo "  python3.11 -c \"import keyring; keyring.set_password('https://upload.pypi.org/legacy/', '__token__', 'your-pypi-token')\""
    echo ""
    exit 1
fi
print_success "Keyring Configured correctly"
echo ""

# Step 2: create/Activate virtual environment
print_info "createBuild a virtual environment..."
python3.11 -m venv ~/.venvs/vibego-build
source ~/.venvs/vibego-build/bin/activate
print_success "Virtual environment is activated"
echo ""

# Step 3: Upgrade pip and install build tools
print_info "Install build dependencies..."
pip install --upgrade pip build twine keyring > /dev/null 2>&1
print_success "Build dependencies installed"
echo ""

# Step 4: Clean up old build artifacts
print_info "Clean up old build artifacts..."
rm -rf "$PROJECT_ROOT/dist"
print_success "Build artifacts cleaned"
echo ""

# Step 5: Increment version number
VERSION_TYPE="${1:-patch}"  # Default is patch
print_info "Increment version number (type:$VERSION_TYPE)..."
./scripts/bump_version.sh "$VERSION_TYPE"
echo ""

# Step 6: Build distribution package
print_info "Build the Python distribution..."
python3.11 -m build
print_success "The distribution package is built"
echo ""

# Step 7: Upload to PyPI (automatic authentication using keyring)
print_info "Upload to PyPI (using keyring authentication)..."
twine upload dist/*
print_success "Successfully uploaded to PyPI"
echo ""

# Step 8: Clean and reinstall vibego in pipx
print_info "Update local pipx installation..."
rm -rf ~/.cache/pipx
rm -rf ~/.local/pipx/venvs/vibego
pipx install --python python3.11 vibego
pipx upgrade vibego
print_success "local vibego updated"
echo ""

# Step 9: Restart vibego service
print_info "Restart vibego service..."
vibego stop || true  # Ignore stop failure errors
sleep 2
vibego start
print_success "vibego Service has been restarted"
echo ""

# Complete
print_success "========================================="
print_success "🎉 The publishing process is complete!"
print_success "========================================="
echo ""
print_info "Next steps:"
echo "  1. Push git commits and tags:"
echo "     git push && git push --tags"
echo ""
echo "  2. Verify PyPI page:"
echo "     https://pypi.org/project/vibego/"
echo ""
