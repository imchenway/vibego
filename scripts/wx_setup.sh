#!/usr/bin/env bash
# 微信小程序 CI 首次配置脚本：写入按项目隔离的 env 文件。
# 依赖：调用方传入 WX_APPID、WX_PKP（private.key 路径），可选 PROJECT_PATH。

set -euo pipefail

# 确定配置根目录，优先使用显式环境变量。
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

# 项目标识使用项目名，去掉斜杠避免路径穿越。
project_slug() {
  local raw
  raw="${PROJECT_NAME:-${PROJECT_SLUG:-default}}"
  raw="${raw:-default}"
  printf '%s' "${raw//\//-}"
}

main() {
  local appid pkp project_path slug root env_dir env_file

  appid="${WX_APPID:-}"
  pkp="${WX_PKP:-}"
  project_path="${PROJECT_PATH:-}"

  if [[ -z "$appid" || -z "$pkp" ]]; then
    echo "[错误] WX_APPID 或 WX_PKP 未提供，请在命令前添加环境变量，例如：" >&2
    echo "  WX_APPID=xxx WX_PKP=/abs/path/private.key PROJECT_PATH=./miniapp bash \"$0\"" >&2
    exit 1
  fi

  if [[ ! -f "$pkp" ]]; then
    echo "[错误] WX_PKP 指向的文件不存在：$pkp" >&2
    exit 1
  fi

  slug="$(project_slug)"
  root="$(config_root)"
  env_dir="$root/wx_ci"
  env_file="$env_dir/${slug}.env"

  mkdir -p "$env_dir"

  # 确保文件权限为 600，避免敏感数据泄露。
  umask 077
  {
    echo "WX_APPID=$appid"
    echo "WX_PKP=$pkp"
    if [[ -n "$project_path" ]]; then
      echo "PROJECT_PATH=$project_path"
    fi
  } >"$env_file"

  chmod 600 "$env_file" 2>/dev/null || true

  echo "[完成] 已写入小程序 CI 配置：$env_file"
  echo "APPID: $appid"
  echo "PKP : $pkp"
  if [[ -n "$project_path" ]]; then
    echo "PATH: $project_path"
  else
    echo "PATH: 未指定（默认使用当前工作目录或调用参数）"
  fi
}

main "$@"
