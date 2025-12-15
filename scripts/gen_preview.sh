#!/usr/bin/env bash
# 通用微信小程序预览二维码生成脚本，输出 JPEG 到本地文件，并通过 TG_PHOTO_FILE 标记便于机器人回传
set -eo pipefail

CLI_BIN="${CLI_BIN:-/Applications/wechatwebdevtools.app/Contents/MacOS/cli}"  # 可通过环境变量覆盖 CLI 路径
PROJECT_PATH="${PROJECT_PATH:-}"                                              # 允许外部显式指定，未指定时后续自动探测
VERSION="${VERSION:-$(date +%Y%m%d%H%M%S)}"
PORT="${PORT:-12605}"
PROJECT_SEARCH_DEPTH="${PROJECT_SEARCH_DEPTH:-4}"                             # 自动探测目录的最大深度

# 取得默认的下载目录，HOME 不存在时回退到 /tmp/Downloads
_default_download_dir() {
  if [[ -n "${HOME:-}" && -d "$HOME" ]]; then
    echo "$HOME/Downloads"
  else
    echo "/tmp/Downloads"
  fi
}

# 根据当前/模型工作目录自动探测小程序根目录（含 app.json 或 project.config.json）
_resolve_project_path() {
  local base="${MODEL_WORKDIR:-$PWD}"
  local hint="${PROJECT_HINT:-}"
  local depth="$PROJECT_SEARCH_DEPTH"
  local candidates=()

  # 已显式传入且目录存在，直接使用
  if [[ -n "$PROJECT_PATH" && -d "$PROJECT_PATH" ]]; then
    echo "$PROJECT_PATH"
    return 0
  fi

  # 优先使用 rg --files 搜索，退回 find 兼容
  if command -v rg >/dev/null 2>&1; then
    while IFS= read -r line; do
      candidates+=( "$(dirname "$line")" )
    done < <(rg --files -g 'app.json' --max-depth "$depth" "$base" 2>/dev/null)
    while IFS= read -r line; do
      candidates+=( "$(dirname "$line")" )
    done < <(rg --files -g 'project.config.json' --max-depth "$depth" "$base" 2>/dev/null)
  else
    while IFS= read -r line; do
      candidates+=( "$(dirname "$line")" )
    done < <(find "$base" -maxdepth "$depth" -type f \( -name app.json -o -name project.config.json \) 2>/dev/null)
  fi

  # 去重并挑选最佳匹配：优先包含 hint，其次路径最短
  if [[ ${#candidates[@]} -gt 0 ]]; then
    declare -A seen=()
    local best="" best_len=0
    for p in "${candidates[@]}"; do
      [[ -z "$p" || ! -d "$p" ]] && continue
      if [[ -n "${seen[$p]:-}" ]]; then
        continue
      fi
      seen["$p"]=1
      local preferred=0
      if [[ -n "$hint" && "$p" == *"$hint"* ]]; then
        preferred=1
      fi
      local len=${#p}
      if [[ -z "$best" || $preferred -gt 0 || ( $preferred -eq 0 && -n "$hint" && "$best" != *"$hint"* ) || ( $preferred -eq 0 && $len -lt $best_len ) ]]; then
        best="$p"
        best_len=$len
        # 如果命中 hint，直接使用
        if [[ $preferred -gt 0 ]]; then
          echo "$best"
          return 0
        fi
      fi
    done
    if [[ -n "$best" ]]; then
      echo "$best"
      return 0
    fi
  fi

  return 1
}

# 基础校验
if [[ ! -x "$CLI_BIN" ]]; then
  echo "[错误] 未找到微信开发者工具 CLI：$CLI_BIN" >&2
  exit 1
fi

# 解析项目目录：显式指定优先，未指定则自动探测
RESOLVED_PROJECT_PATH="$(_resolve_project_path)" || true
if [[ -z "$RESOLVED_PROJECT_PATH" ]]; then
  echo "[错误] 未找到小程序项目目录，请在当前目录下提供 app.json 或 project.config.json，或显式设置 PROJECT_PATH/PROJECT_HINT。" >&2
  exit 1
fi

# 设置输出路径，确保目录存在
DEFAULT_DOWNLOAD_DIR="$(_default_download_dir)"
OUTPUT_QR="${OUTPUT_QR:-${DEFAULT_DOWNLOAD_DIR}/wx-preview-${VERSION}.jpg}"

# 确保输出目录存在
mkdir -p "$(dirname "$OUTPUT_QR")"

# 清理代理，避免请求走代理失败
export http_proxy= https_proxy= all_proxy=
export no_proxy="servicewechat.com,.weixin.qq.com"

echo "[信息] 生成预览，项目：$RESOLVED_PROJECT_PATH，版本：$VERSION，输出：$OUTPUT_QR"

# 捕获 CLI 输出以便失败时回显
CLI_LOG="$(mktemp /tmp/wx-preview-cli-XXXX.log)"
set +e
"$CLI_BIN" preview \
  --project "$RESOLVED_PROJECT_PATH" \
  --upload-version "$VERSION" \
  --qr-format image \
  --qr-output "$OUTPUT_QR" \
  --compile-condition '{}' \
  --robot 1 \
  --port "$PORT" >"$CLI_LOG" 2>&1
CLI_STATUS=$?
set -e

if [[ $CLI_STATUS -ne 0 ]]; then
  echo "[错误] 微信开发者工具 CLI 退出码：$CLI_STATUS" >&2
  tail -n 40 "$CLI_LOG" >&2 || true
  exit "$CLI_STATUS"
fi

if [[ ! -f "$OUTPUT_QR" ]]; then
  echo "[错误] CLI 未生成二维码文件：$OUTPUT_QR" >&2
  tail -n 40 "$CLI_LOG" >&2 || true
  exit 3
fi

echo "[完成] 预览二维码已生成：$OUTPUT_QR"
echo "TG_PHOTO_FILE: $OUTPUT_QR"
