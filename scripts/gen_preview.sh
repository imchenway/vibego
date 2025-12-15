#!/usr/bin/env bash
# 通用微信小程序预览二维码生成脚本，输出 JPEG 到本地文件，并通过 TG_PHOTO_FILE 标记便于机器人回传
set -eo pipefail

# 可通过环境变量覆盖 CLI 路径、项目目录、输出文件和端口
CLI_BIN="${CLI_BIN:-/Applications/wechatwebdevtools.app/Contents/MacOS/cli}"
# 优先使用模型工作目录，退回当前目录，避免机器人执行时项目路径为空
PROJECT_PATH="${PROJECT_PATH:-${MODEL_WORKDIR:-$PWD}}"
VERSION="${VERSION:-$(date +%Y%m%d%H%M%S)}"
# 默认输出到用户下载目录，避免 /tmp 目录权限或清理导致生成失败
OUTPUT_QR="${OUTPUT_QR:-${HOME:-/tmp}/Downloads/wx-preview-${VERSION}.jpg}"
PORT="${PORT:-12605}"

# 基础校验
if [[ ! -x "$CLI_BIN" ]]; then
  echo "[错误] 未找到微信开发者工具 CLI：$CLI_BIN" >&2
  exit 1
fi

if [[ ! -d "$PROJECT_PATH" ]]; then
  echo "[错误] 项目目录不存在：$PROJECT_PATH" >&2
  exit 1
fi

# 确保输出目录存在
mkdir -p "$(dirname "$OUTPUT_QR")"

# 清理代理，避免请求走代理失败
export http_proxy= https_proxy= all_proxy=
export no_proxy="servicewechat.com,.weixin.qq.com"

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
echo "TG_PHOTO_FILE: $OUTPUT_QR"
