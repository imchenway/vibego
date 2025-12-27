#!/usr/bin/env bash
# 基于微信开发者工具 CLI 生成预览二维码，默认输出到 ~/Downloads/wx-preview.jpg。
set -eo pipefail

CLI_BIN="/Applications/wechatwebdevtools.app/Contents/MacOS/cli"
PROJECT_PATH="${PROJECT_PATH:-$(pwd)}"
OUTPUT_QR="${OUTPUT_QR:-$HOME/Downloads/wx-preview.jpg}"
PORT="${PORT:-}"

if [[ ! -x "$CLI_BIN" ]]; then
  echo "[错误] 未找到微信开发者工具 CLI：$CLI_BIN" >&2
  exit 1
fi

if [[ ! -d "$PROJECT_PATH" ]]; then
  echo "[错误] 项目目录不存在：$PROJECT_PATH" >&2
  exit 1
fi

if [[ -z "${PORT:-}" ]]; then
  echo "[错误] 未配置微信开发者工具 IDE 服务端口（PORT），无法生成预览二维码。" >&2
  echo "  - 项目目录：$PROJECT_PATH" >&2
  echo "" >&2
  echo "请在微信开发者工具：设置 -> 安全设置 -> 服务端口，查看端口号后重试。" >&2
  echo "官方文档（命令行 V2 / --port 说明）：https://developers.weixin.qq.com/miniprogram/dev/devtools/cli.html" >&2
  echo "" >&2
  echo "示例：" >&2
  echo "  PORT=12605 PROJECT_PATH=\"$PROJECT_PATH\" OUTPUT_QR=\"$OUTPUT_QR\" bash \"$0\"" >&2
  exit 2
fi
if [[ ! "$PORT" =~ ^[0-9]+$ ]]; then
  echo "[错误] 端口号无效：PORT=$PORT（必须为纯数字）" >&2
  exit 2
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
