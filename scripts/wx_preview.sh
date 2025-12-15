#!/usr/bin/env bash
# 基于微信开发者工具 CLI 生成预览二维码，默认输出到 ~/Downloads/wx-preview.jpg。
set -eo pipefail

CLI_BIN="/Applications/wechatwebdevtools.app/Contents/MacOS/cli"
PROJECT_PATH="${PROJECT_PATH:-$(pwd)}"
OUTPUT_QR="${OUTPUT_QR:-$HOME/Downloads/wx-preview.jpg}"
PORT="${PORT:-12605}"

if [[ ! -x "$CLI_BIN" ]]; then
  echo "[错误] 未找到微信开发者工具 CLI：$CLI_BIN" >&2
  exit 1
fi

if [[ ! -d "$PROJECT_PATH" ]]; then
  echo "[错误] 项目目录不存在：$PROJECT_PATH" >&2
  exit 1
fi

mkdir -p "$(dirname "$OUTPUT_QR")"

# 清理代理，避免请求走代理
export http_proxy= https_proxy= all_proxy=
export no_proxy="servicewechat.com,.weixin.qq.com"

VERSION="$(date +%Y%m%d%H%M%S)"
echo "[信息] 生成预览，项目：$PROJECT_PATH，版本：$VERSION，输出：$OUTPUT_QR"

"$CLI_BIN" preview \
  --project "$PROJECT_PATH" \
  --upload-version "$VERSION" \
  --qr-format image \
  --qr-output "$OUTPUT_QR" \
  --compile-condition '{}' \
  --robot 1 \
  --port "$PORT"

echo "[完成] 预览二维码已生成：$OUTPUT_QR"
