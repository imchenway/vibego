#!/usr/bin/env bash
# patch 发布快捷入口，便于命令中心直接调用
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/publish.sh" patch "$@"
