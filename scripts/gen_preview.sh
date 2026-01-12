#!/usr/bin/env bash
# 通用微信小程序预览二维码生成脚本，输出 JPEG 到本地文件，并通过 TG_PHOTO_FILE 标记便于机器人回传
set -eo pipefail

CLI_BIN="${CLI_BIN:-/Applications/wechatwebdevtools.app/Contents/MacOS/cli}"  # 可通过环境变量覆盖 CLI 路径
PROJECT_PATH="${PROJECT_PATH:-}"                                              # 允许外部显式指定，未指定时后续自动探测
VERSION="${VERSION:-$(date +%Y%m%d%H%M%S)}"
PORT="${PORT:-}"                                                             # 可临时用环境变量覆盖；未设置则读取项目端口配置
WX_DEVTOOLS_PORTS_FILE="${WX_DEVTOOLS_PORTS_FILE:-}"                          # 可显式指定端口映射文件路径（默认读取 vibego 配置目录）
PROJECT_SEARCH_DEPTH="${PROJECT_SEARCH_DEPTH:-6}"                             # 自动探测目录的最大深度（默认提升为 6，覆盖深层项目）
PROJECT_BASE="${PROJECT_BASE:-${MODEL_WORKDIR:-$PWD}}"                        # 探测起始目录，可显式设置

# 选择 Python 解释器：
# - 优先外部显式指定：PYTHON_BIN / VIBEGO_PYTHON_BIN
# - 其次使用当前虚拟环境：$VIRTUAL_ENV/bin/python（run_bot.sh 会设置 VIRTUAL_ENV，但未必会改 PATH）
# - 最后回退到系统 python3.* / python3 / python（仅当 python 为 Python3 时）
_pick_python_bin() {
  if [[ -n "${PYTHON_BIN:-}" ]]; then
    printf '%s' "$PYTHON_BIN"
    return 0
  fi
  if [[ -n "${VIBEGO_PYTHON_BIN:-}" ]]; then
    printf '%s' "$VIBEGO_PYTHON_BIN"
    return 0
  fi
  if [[ -n "${VIRTUAL_ENV:-}" && -x "$VIRTUAL_ENV/bin/python" ]]; then
    printf '%s' "$VIRTUAL_ENV/bin/python"
    return 0
  fi
  local candidate
  for candidate in python3.14 python3.13 python3.12 python3.11 python3.10 python3.9 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  if command -v python >/dev/null 2>&1; then
    if python - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info.major == 3 else 1)
PY
    then
      printf '%s' "python"
      return 0
    fi
  fi
  return 1
}

PYTHON_BIN="${PYTHON_BIN:-${VIBEGO_PYTHON_BIN:-}}"
if [[ -z "${PYTHON_BIN:-}" ]]; then
  PYTHON_BIN="$(_pick_python_bin 2>/dev/null || true)"
fi

# 解析 project.config.json 中的 miniprogramRoot，返回绝对路径（若存在且有效）
_extract_miniprogram_root() {
  local cfg="$1"
  # 仅在存在 Python 解释器时解析 JSON；否则直接返回空字符串，由后续逻辑走 app.json 探测兜底
  if [[ -z "${PYTHON_BIN:-}" ]]; then
    return 0
  fi
  "$PYTHON_BIN" - <<'PY' "$cfg" 2>/dev/null
import json, sys, os
cfg_path = sys.argv[1]
try:
    with open(cfg_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    root = data.get("miniprogramRoot") or ""
    if isinstance(root, str) and root.strip():
        print(root.strip())
except Exception:
    pass
PY
}

# 取得默认的下载目录，HOME 不存在时回退到 /tmp/Downloads
_default_download_dir() {
  if [[ -n "${HOME:-}" && -d "$HOME" ]]; then
    echo "$HOME/Downloads"
  else
    echo "/tmp/Downloads"
  fi
}

# 解析 vibego 配置根目录，需与 scripts/run_bot.sh 的逻辑保持一致（用于定位端口映射文件）。
_resolve_vibego_config_root() {
  local raw=""
  if [[ -n "${MASTER_CONFIG_ROOT:-}" ]]; then
    raw="$MASTER_CONFIG_ROOT"
  elif [[ -n "${VIBEGO_CONFIG_DIR:-}" ]]; then
    raw="$VIBEGO_CONFIG_DIR"
  elif [[ -n "${XDG_CONFIG_HOME:-}" ]]; then
    raw="${XDG_CONFIG_HOME%/}/vibego"
  else
    raw="$HOME/.config/vibego"
  fi
  if [[ "$raw" == ~* ]]; then
    printf '%s' "${raw/#\~/$HOME}"
  else
    printf '%s' "$raw"
  fi
}

# 端口映射文件默认位置：<vibego_config_root>/config/wx_devtools_ports.json
_default_wx_devtools_ports_file() {
  local root
  root="$(_resolve_vibego_config_root)"
  printf '%s\n' "$root/config/wx_devtools_ports.json"
}

# 从端口映射文件中解析当前项目对应的 IDE 服务端口。
# 规则：
# 1) 若已通过环境变量 PORT 显式设置，则直接使用；
# 2) 否则读取 wx_devtools_ports.json，优先按小程序目录（paths）匹配，其次按 vibego 项目名（projects/或顶层映射）匹配；
# 3) 若仍未找到端口，则返回空字符串，由调用方给出“要求用户配置”的错误提示。
_resolve_wx_devtools_port() {
  local project_root="$1"
  local project_slug="${PROJECT_NAME:-${PROJECT_SLUG:-}}"
  local ports_file="${WX_DEVTOOLS_PORTS_FILE:-$(_default_wx_devtools_ports_file)}"

  # 端口映射解析依赖 Python；若缺失则返回空字符串，让上层走“缺失端口”的可恢复提示。
  if [[ -z "${PYTHON_BIN:-}" ]]; then
    return 0
  fi

  "$PYTHON_BIN" - "$ports_file" "$project_slug" "$project_root" <<'PY' 2>/dev/null || true
import json
import os
import sys

ports_file = (sys.argv[1] or "").strip()
project_slug = (sys.argv[2] or "").strip()
project_root = (sys.argv[3] or "").strip()

def norm_path(value: str) -> str:
    if not value:
        return ""
    try:
        return os.path.realpath(os.path.expanduser(value))
    except Exception:
        return value

def normalize_port(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None

def get_casefold_key(mapping, key: str):
    if not isinstance(mapping, dict) or not key:
        return None
    if key in mapping:
        return mapping[key]
    lower_key = key.casefold()
    for k, v in mapping.items():
        if isinstance(k, str) and k.casefold() == lower_key:
            return v
    return None

if not ports_file or not os.path.exists(ports_file):
    sys.exit(2)

with open(ports_file, "r", encoding="utf-8") as f:
    raw = json.load(f)

if not isinstance(raw, dict):
    sys.exit(2)

if "projects" in raw or "paths" in raw:
    projects = raw.get("projects") or {}
    paths = raw.get("paths") or {}
else:
    # 兼容最简写法：{"my-project": 12605}
    projects = raw
    paths = {}

port = None

if project_root and isinstance(paths, dict):
    root_norm = norm_path(project_root)
    direct = get_casefold_key(paths, project_root)
    if direct is None and root_norm:
        direct = get_casefold_key(paths, root_norm)
    if direct is None and root_norm:
        for k, v in paths.items():
            if isinstance(k, str) and norm_path(k) == root_norm:
                direct = v
                break
    port = normalize_port(direct)

if port is None and project_slug and isinstance(projects, dict):
    port = normalize_port(get_casefold_key(projects, project_slug))

if port is None:
    sys.exit(2)

print(str(port))
PY
}

# 根据当前/模型工作目录自动探测小程序根目录（含 app.json 或 project.config.json）
_resolve_project_path() {
  local base="$PROJECT_BASE"
  local hint="${PROJECT_HINT:-}"
  local depth="$PROJECT_SEARCH_DEPTH"
  local candidates=()
  local config_candidates=()

  # 起始目录必须存在
  if [[ -z "$base" || ! -d "$base" ]]; then
    echo "[错误] 搜索基准目录不存在或不可读：$base" >&2
    return 1
  fi

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
      config_candidates+=( "$line" )
    done < <(rg --files -g 'project.config.json' --max-depth "$depth" "$base" 2>/dev/null)
  else
    while IFS= read -r line; do
      candidates+=( "$(dirname "$line")" )
    done < <(find "$base" -maxdepth "$depth" -type f -name app.json 2>/dev/null)
    while IFS= read -r line; do
      config_candidates+=( "$line" )
    done < <(find "$base" -maxdepth "$depth" -type f -name project.config.json 2>/dev/null)
  fi

  # 补充 project.config.json 对应的 miniprogramRoot 目录
  for cfg in "${config_candidates[@]}"; do
    [[ -z "$cfg" || ! -f "$cfg" ]] && continue
    local cfg_dir
    cfg_dir="$(dirname "$cfg")"
    candidates+=( "$cfg_dir" )
    local mini_root
    mini_root="$(_extract_miniprogram_root "$cfg")"
    if [[ -n "$mini_root" ]]; then
      local resolved_root
      resolved_root="$(cd "$cfg_dir" && cd "$mini_root" 2>/dev/null && pwd || true)"
      [[ -n "$resolved_root" ]] && candidates+=( "$resolved_root" )
    fi
  done

  # 去重并挑选最佳匹配：优先包含 hint，其次路径最短
  if [[ ${#candidates[@]} -gt 0 ]]; then
    declare -A seen=()
    local best="" best_len=0
    local listed=()
    for p in "${candidates[@]}"; do
      [[ -z "$p" || ! -d "$p" ]] && continue
      if [[ -n "${seen[$p]:-}" ]]; then
        continue
      fi
      seen["$p"]=1
      listed+=( "$p" )
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
    # 输出候选列表，便于排查
    if [[ ${#listed[@]} -gt 1 ]]; then
      echo "[提示] 检测到多个小程序候选目录（优先命中 PROJECT_HINT 其余按路径最短）：" >&2
      for c in "${listed[@]}"; do
        echo "  - $c" >&2
      done
    fi
    if [[ -n "$best" ]]; then
      echo "$best"
      return 0
    fi
  fi

  return 1
}

# 校验小程序目录是否可用：要求存在 app.json，或 project.config.json 指向的 miniprogramRoot 下存在 app.json
_validate_project_root() {
  local root="$1"
  [[ -d "$root" ]] || { echo "[错误] 小程序目录不存在：$root" >&2; return 1; }

  if [[ -f "$root/app.json" ]]; then
    return 0
  fi

  local cfg="$root/project.config.json"
  if [[ -f "$cfg" ]]; then
    local mini_root resolved
    mini_root="$(_extract_miniprogram_root "$cfg")"
    if [[ -n "$mini_root" ]]; then
      resolved="$(cd "$root" && cd "$mini_root" 2>/dev/null && pwd || true)"
      if [[ -n "$resolved" && -f "$resolved/app.json" ]]; then
        return 0
      fi
    fi
  fi

  echo "[错误] 目录缺少 app.json，且 project.config.json 未指向有效 miniprogramRoot：$root" >&2
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
  echo "[错误] 未找到小程序项目目录，请在当前目录下提供 app.json 或 project.config.json，或显式设置 PROJECT_BASE/PROJECT_PATH/PROJECT_HINT。搜索基准：$PROJECT_BASE，深度：$PROJECT_SEARCH_DEPTH" >&2
  exit 1
fi
_validate_project_root "$RESOLVED_PROJECT_PATH"

# 端口解析：不再使用全局默认端口，必须为每个项目配置（或临时通过 PORT 显式指定）。
if [[ -z "${PORT:-}" ]]; then
  PORT="$(_resolve_wx_devtools_port "$RESOLVED_PROJECT_PATH")"
fi

if [[ -z "${PORT:-}" ]]; then
  PORTS_FILE="${WX_DEVTOOLS_PORTS_FILE:-$(_default_wx_devtools_ports_file)}"
  echo "[错误] 未配置微信开发者工具 IDE 服务端口，无法生成预览二维码。" >&2
  echo "  - vibego 项目：${PROJECT_NAME:-<unknown>}" >&2
  echo "  - 小程序目录：$RESOLVED_PROJECT_PATH" >&2
  echo "  - 端口配置文件：$PORTS_FILE" >&2
  echo "" >&2
  echo "请在微信开发者工具：设置 -> 安全设置 -> 服务端口，查看端口号并写入端口配置文件后重试。" >&2
  echo "官方文档（命令行 V2 / --port 说明）：https://developers.weixin.qq.com/miniprogram/dev/devtools/cli.html" >&2
  echo "" >&2
  echo "配置示例（按 vibego 项目名 project_slug 配置）：" >&2
  echo "  {\"projects\": {\"${PROJECT_NAME:-my-project}\": 12605}}" >&2
  echo "" >&2
  echo "也可临时指定端口（单次生效）：" >&2
  echo "  PORT=12605 PROJECT_BASE=\"$PROJECT_BASE\" bash \"$0\"" >&2
  exit 2
fi

if [[ ! "$PORT" =~ ^[0-9]+$ ]]; then
  echo "[错误] 端口号无效：PORT=$PORT（必须为纯数字）" >&2
  exit 2
fi

# 设置输出路径，确保目录存在
DEFAULT_DOWNLOAD_DIR="$(_default_download_dir)"
OUTPUT_QR="${OUTPUT_QR:-${DEFAULT_DOWNLOAD_DIR}/wx-preview-${VERSION}.jpg}"

# 确保输出目录存在
mkdir -p "$(dirname "$OUTPUT_QR")"

# 清理代理，避免请求走代理失败
export http_proxy= https_proxy= all_proxy=
export no_proxy="servicewechat.com,.weixin.qq.com"

echo "[信息] 生成预览，项目：${RESOLVED_PROJECT_PATH}，版本：${VERSION}，端口：${PORT}，输出：${OUTPUT_QR}"

# 捕获 CLI 输出以便失败时回显
CLI_LOG="$(mktemp -t wx-preview-cli)"
set +e
pushd "$RESOLVED_PROJECT_PATH" >/dev/null
"$CLI_BIN" preview \
  --project "$RESOLVED_PROJECT_PATH" \
  --upload-version "$VERSION" \
  --qr-format image \
  --qr-output "$OUTPUT_QR" \
  --compile-condition '{}' \
  --robot 1 \
  --port "$PORT" >"$CLI_LOG" 2>&1
CLI_STATUS=$?
popd >/dev/null
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
