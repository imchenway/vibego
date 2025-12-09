#!/usr/bin/env bash
# 微信小程序预览二维码生成脚本：读取项目级配置，调用 miniprogram-ci 输出图片并提示给 Telegram。

set -euo pipefail

# 解析配置根目录。
config_root() {
  local base
  if [[ -n "${MASTER_CONFIG_ROOT:-}" ]]; then
    base="$MASTER_CONFIG_ROOT"
  elif [[ -n "${VIBEGO_CONFIG_DIR:-}" ]]; then
    base="$VIBEGO_CONFIG_DIR"
  else
    local xdg_base
    xdg_base="${XDG_CONFIG_HOME:-$HOME/.config}"
    base="$xdg_base/vibego"
  fi
  printf '%s' "${base/#\~/$HOME}"
}

# 项目名 -> slug，避免路径穿越。
project_slug() {
  local raw
  raw="${PROJECT_NAME:-${PROJECT_SLUG:-default}}"
  raw="${raw:-default}"
  printf '%s' "${raw//\//-}"
}

# 从 env 文件加载默认值，再用调用时的环境变量覆盖。
load_project_env() {
  local env_file="$1"
  local caller_appid caller_pkp caller_path
  caller_appid="${WX_APPID:-}"
  caller_pkp="${WX_PKP:-}"
  caller_path="${PROJECT_PATH:-}"

  if [[ -f "$env_file" ]]; then
    # shellcheck disable=SC1090
    source "$env_file"
  fi

  WX_APPID="${caller_appid:-${WX_APPID:-}}"
  WX_PKP="${caller_pkp:-${WX_PKP:-}}"
  PROJECT_PATH="${caller_path:-${PROJECT_PATH:-}}"
}

# 将 base64 写为 PNG 文件。
write_png_from_base64() {
  local base64_path="$1"
  local png_path="$2"
  python - "$base64_path" "$png_path" <<'PY'
import base64, pathlib, sys

src = pathlib.Path(sys.argv[1])
dst = pathlib.Path(sys.argv[2])
raw = src.read_text(encoding="utf-8").strip()
if raw.startswith("data:image"):
    raw = raw.split(",", 1)[1]
dst.write_bytes(base64.b64decode(raw))
print(dst)
PY
}

main() {
  local slug root env_file appid pkp project_path desc version tmp_dir base64_file png_file

  if ! command -v node >/dev/null 2>&1; then
    echo "[错误] 未检测到 node，请先安装 Node.js 16+。" >&2
    exit 1
  fi
  if ! command -v npx >/dev/null 2>&1; then
    echo "[错误] 未检测到 npx，请检查 npm/Node.js 安装。" >&2
    exit 1
  fi

  slug="$(project_slug)"
  root="$(config_root)"
  env_file="$root/wx_ci/${slug}.env"

  load_project_env "$env_file"

  appid="${WX_APPID:-}"
  pkp="${WX_PKP:-}"
  project_path="${PROJECT_PATH:-}" 
  desc="${DESC:-${WX_DESC:-tg-preview}}"
  # 版本号必填：默认使用时间戳，可通过 WX_VERSION/UPLOAD_VERSION 覆盖
  version="${WX_VERSION:-${UPLOAD_VERSION:-$(date +%Y%m%d%H%M%S)}}"

  if [[ -z "$appid" || -z "$pkp" ]]; then
    echo "[错误] 未找到 WX_APPID 或 WX_PKP，请先执行 wx-setup 配置（或在命令前传入环境变量）。" >&2
    exit 1
  fi
  if [[ ! -f "$pkp" ]]; then
    echo "[错误] WX_PKP 指向的文件不存在：$pkp" >&2
    exit 1
  fi

  if [[ -z "$project_path" ]]; then
    project_path="$(pwd)"
  elif [[ "$project_path" != /* ]]; then
    project_path="$(cd "$project_path" && pwd)"
  fi

  if [[ ! -d "$project_path" ]]; then
    echo "[错误] 项目目录不存在：$project_path" >&2
    exit 1
  fi

  tmp_dir="${TMPDIR:-/tmp}/vibego-wx/${slug}"
  mkdir -p "$tmp_dir"
  base64_file="$tmp_dir/preview.b64"
  png_file="$tmp_dir/preview.png"

  echo "[信息] 使用项目目录：$project_path"
  echo "[信息] 生成预览二维码..."

  npx miniprogram-ci preview \
    --project-path "$project_path" \
    --pkp "$pkp" \
    --appid "$appid" \
    --desc "$desc" \
    --upload-version "$version" \
    --qrcode-format base64 \
    --qrcode-output-dest "$base64_file" \
    >/dev/null

  write_png_from_base64 "$base64_file" "$png_file"

  echo "[完成] 预览二维码已生成：$png_file"
  echo "TG_PHOTO_FILE: $png_file"
}

main "$@"
