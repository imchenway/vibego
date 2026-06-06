#!/usr/bin/env bash
# 通用微信小程序“上传到微信服务”脚本（体验版），仅上传不回传二维码。
set -eo pipefail

CLI_BIN="${CLI_BIN:-/Applications/wechatwebdevtools.app/Contents/MacOS/cli}"  # 可通过环境变量覆盖 CLI 路径
PROJECT_PATH="${PROJECT_PATH:-}"                                              # 允许外部显式指定，未指定时后续自动探测
VERSION="${VERSION:-$(date +%Y%m%d%H%M%S)}"
UPLOAD_DESC="${UPLOAD_DESC:-vibego upload ${VERSION}}"
PORT="${PORT:-}"                                                             # 可临时用环境变量覆盖；未设置则读取项目端口配置
WX_DEVTOOLS_PORTS_FILE="${WX_DEVTOOLS_PORTS_FILE:-}"                          # 可显式指定端口映射文件路径（默认读取 vibego 配置目录）
PROJECT_SEARCH_DEPTH="${PROJECT_SEARCH_DEPTH:-6}"                             # 自动探测目录的最大深度
PROJECT_BASE="${PROJECT_BASE:-${MODEL_WORKDIR:-$PWD}}"                        # 探测起始目录
UPLOAD_RETRY_ON_FAIL="${UPLOAD_RETRY_ON_FAIL:-1}"                             # 失败自动重试次数（不含首轮）
UPLOAD_RETRY_DELAY_SECONDS="${UPLOAD_RETRY_DELAY_SECONDS:-1}"                 # 重试间隔秒数
UPLOAD_VERSION_FLAG=""                                                         # upload 版本参数（运行时探测）
UPLOAD_DESC_FLAG=""                                                            # upload 描述参数（运行时探测）
UPLOAD_INFO_OUTPUT_SUPPORTED=0                                                 # 是否支持 --info-output
LAST_UPLOAD_VERSION_FLAG=""                                                    # 最近一次成功使用的版本参数
LAST_UPLOAD_DESC_FLAG=""                                                       # 最近一次成功使用的描述参数
UPLOAD_RESULT_VERSION=""                                                       # 校验后的上传版本
UPLOAD_RESULT_APPID=""                                                         # 校验后的上传 AppID
UPLOAD_RESULT_VERSION_SOURCE=""                                                # 上传版本来源
UPLOAD_RESULT_APPID_SOURCE=""                                                  # 上传 AppID 来源

# 选择 Python 解释器：
# - 优先外部显式指定：PYTHON_BIN / VIBEGO_PYTHON_BIN
# - 其次使用当前虚拟环境：$VIRTUAL_ENV/bin/python
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

# 解析 project.config.json 中的 miniprogramRoot，返回相对路径（若存在且有效）
_extract_miniprogram_root() {
  local cfg="$1"
  # 仅在存在 Python 解释器时解析 JSON；否则后续交由 app.json 探测兜底
  if [[ -z "${PYTHON_BIN:-}" ]]; then
    return 0
  fi
  "$PYTHON_BIN" - <<'PY' "$cfg" 2>/dev/null
import json, sys
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

# 解析 vibego 配置根目录，需与 scripts/run_bot.sh 逻辑一致（用于定位端口映射文件）。
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

_extract_project_appid() {
  local project_root="$1"
  local cfg="$project_root/project.config.json"
  local appid=""
  [[ -f "$cfg" ]] || return 0

  if [[ -n "${PYTHON_BIN:-}" ]]; then
    appid="$(
      "$PYTHON_BIN" - "$cfg" <<'PY' 2>/dev/null || true
import json
import sys

cfg_path = sys.argv[1]
try:
    with open(cfg_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    value = data.get("appid")
    if isinstance(value, str) and value.strip():
        print(value.strip())
except Exception:
    pass
PY
    )"
  fi

  if [[ -z "$appid" ]]; then
    appid="$(sed -nE 's/.*"appid"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p' "$cfg" | head -n 1)"
  fi
  [[ -n "$appid" ]] && printf '%s\n' "$appid"
}

_detect_upload_cli_capabilities() {
  local help_text=""
  local help_status=0

  # 默认优先使用新版参数，探测失败时再走兼容兜底。
  UPLOAD_VERSION_FLAG="--version"
  UPLOAD_DESC_FLAG="--desc"
  UPLOAD_INFO_OUTPUT_SUPPORTED=0

  set +e
  help_text="$("$CLI_BIN" upload --help 2>&1)"
  help_status=$?
  set -e

  if grep -Fq -- "--upload-version" <<<"$help_text"; then
    UPLOAD_VERSION_FLAG="--upload-version"
  elif grep -Fq -- "--version" <<<"$help_text"; then
    UPLOAD_VERSION_FLAG="--version"
  fi

  if grep -Fq -- "--upload-desc" <<<"$help_text"; then
    UPLOAD_DESC_FLAG="--upload-desc"
  elif grep -Fq -- "--desc" <<<"$help_text"; then
    UPLOAD_DESC_FLAG="--desc"
  fi

  if grep -Fq -- "--info-output" <<<"$help_text"; then
    UPLOAD_INFO_OUTPUT_SUPPORTED=1
  fi

  if [[ $help_status -ne 0 && -z "$help_text" ]]; then
    echo "[警告] 未能读取 upload --help 输出，将按兼容参数组合重试。" >&2
  fi
}

_extract_upload_info_fields() {
  local info_file="$1"
  if [[ -z "${PYTHON_BIN:-}" || -z "$info_file" || ! -s "$info_file" ]]; then
    return 0
  fi

  "$PYTHON_BIN" - "$info_file" <<'PY' 2>/dev/null || true
import json
import re
import sys

path = sys.argv[1]

try:
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
except Exception:
    raise SystemExit(0)

version = ""
appid = ""
wx_appid_pattern = re.compile(r"^wx[a-zA-Z0-9]{16}$")

version_keys = {"version", "uploadversion", "compileversion"}
appid_keys = {"appid", "miniappid", "miniprogramappid", "extappid"}

def normalize_key(raw):
    text = str(raw or "").strip().lower()
    for ch in (" ", "-", "_", "."):
        text = text.replace(ch, "")
    return text

def as_text(value):
    if isinstance(value, bool):
        return ""
    if isinstance(value, (str, int, float)):
        text = str(value).strip()
        return text
    return ""

def walk(node):
    global version, appid
    if isinstance(node, dict):
        for k, v in node.items():
            key = normalize_key(k)
            value_text = as_text(v)
            if not version and key in version_keys and value_text:
                version = value_text
            if not appid and key in appid_keys and value_text:
                appid = value_text
            if not appid and value_text and wx_appid_pattern.match(value_text):
                appid = value_text
            walk(v)
    elif isinstance(node, list):
        for item in node:
            walk(item)
    else:
        value_text = as_text(node)
        if not appid and value_text and wx_appid_pattern.match(value_text):
            appid = value_text

walk(payload)
print(f"{version}\t{appid}")
PY
}

_run_upload_cli_with_flags() {
  local project_root="$1"
  local version="$2"
  local upload_desc="$3"
  local port="$4"
  local cli_log="$5"
  local info_output="$6"
  local version_flag="$7"
  local desc_flag="$8"
  local cmd=()
  local status=1

  cmd=(
    "$CLI_BIN"
    upload
    --project "$project_root"
    "$version_flag" "$version"
    "$desc_flag" "$upload_desc"
    --compile-condition '{}'
    --robot 1
    --port "$port"
  )
  if [[ "$UPLOAD_INFO_OUTPUT_SUPPORTED" -eq 1 && -n "$info_output" ]]; then
    cmd+=(--info-output "$info_output")
  fi

  pushd "$project_root" >/dev/null
  set +e
  "${cmd[@]}" >"$cli_log" 2>&1
  status=$?
  set -e
  popd >/dev/null
  return "$status"
}

_run_upload_cli_once() {
  local project_root="$1"
  local version="$2"
  local upload_desc="$3"
  local port="$4"
  local cli_log="$5"
  local info_output="$6"
  local raw_candidates=()
  local candidates=()
  local candidate=""
  local existing=""
  local duplicated=0
  local status=1
  local version_flag=""
  local desc_flag=""

  raw_candidates=(
    "${UPLOAD_VERSION_FLAG}|${UPLOAD_DESC_FLAG}"
    "--version|--desc"
    "--upload-version|--upload-desc"
  )

  for candidate in "${raw_candidates[@]}"; do
    duplicated=0
    for existing in "${candidates[@]}"; do
      if [[ "$existing" == "$candidate" ]]; then
        duplicated=1
        break
      fi
    done
    if [[ $duplicated -eq 0 ]]; then
      candidates+=("$candidate")
    fi
  done

  for candidate in "${candidates[@]}"; do
    version_flag="${candidate%%|*}"
    desc_flag="${candidate##*|}"
    if _run_upload_cli_with_flags "$project_root" "$version" "$upload_desc" "$port" "$cli_log" "$info_output" "$version_flag" "$desc_flag"; then
      LAST_UPLOAD_VERSION_FLAG="$version_flag"
      LAST_UPLOAD_DESC_FLAG="$desc_flag"
      return 0
    fi
    status=$?
    # 参数不兼容常见表现：
    # 1) Unknown option / argument
    # 2) 将参数识别失败后提示缺少必填 version/desc
    if grep -Eiq "unknown (option|argument)|option '.*' is unknown|unknown arguments|missing required arguments:.*(version|upload-version).*(desc|upload-desc)" "$cli_log"; then
      echo "[提示] 当前 upload 参数组合不兼容：${version_flag} ${desc_flag}，尝试其他组合..." >&2
      continue
    fi
    return "$status"
  done

  return "$status"
}

_verify_upload_result() {
  local expected_version="$1"
  local expected_appid="$2"
  local info_output="$3"
  local cli_log="$4"
  local parsed_fields=""
  local parsed_version=""
  local parsed_appid=""
  local actual_version=""
  local actual_appid=""
  local version_source=""
  local appid_source=""

  parsed_fields="$(_extract_upload_info_fields "$info_output")"
  if [[ "$parsed_fields" == *$'\t'* ]]; then
    parsed_version="${parsed_fields%%$'\t'*}"
    parsed_appid="${parsed_fields#*$'\t'}"
  fi
  parsed_version="${parsed_version//$'\r'/}"
  parsed_appid="${parsed_appid//$'\r'/}"

  if [[ -n "$parsed_version" ]]; then
    actual_version="$parsed_version"
    version_source="info_output"
  fi
  if [[ -n "$parsed_appid" ]]; then
    actual_appid="$parsed_appid"
    appid_source="info_output"
  fi

  if [[ -z "$actual_version" && -n "$expected_version" && -f "$cli_log" ]] && grep -Fq -- "$expected_version" "$cli_log"; then
    actual_version="$expected_version"
    version_source="cli_log"
  fi
  if [[ -z "$actual_appid" && -f "$cli_log" ]]; then
    actual_appid="$(grep -Eo 'wx[a-zA-Z0-9]{16}' "$cli_log" | head -n 1 || true)"
    if [[ -n "$actual_appid" ]]; then
      appid_source="cli_log"
    fi
  fi

  if [[ -z "$actual_appid" && -n "$expected_appid" ]]; then
    actual_appid="$expected_appid"
    appid_source="project_config"
  fi

  if [[ -z "$actual_version" && -n "$expected_version" ]]; then
    # 部分 CLI 版本的 info-output 不回传版本号，此时回退到命令入参版本进行核验。
    actual_version="$expected_version"
    version_source="command_arg"
  fi

  if [[ -z "$actual_version" ]]; then
    echo "[错误] 上传结果校验失败：未解析到上传版本号，拒绝标记成功。" >&2
    return 1
  fi
  if [[ "$actual_version" != "$expected_version" ]]; then
    echo "[错误] 上传结果校验失败：版本号不一致（期望：$expected_version，实际：$actual_version）。" >&2
    return 1
  fi
  if [[ -z "$actual_appid" ]]; then
    echo "[错误] 上传结果校验失败：未解析到 AppID，拒绝标记成功。" >&2
    return 1
  fi
  if [[ -n "$expected_appid" && "$actual_appid" != "$expected_appid" ]]; then
    echo "[错误] 上传结果校验失败：AppID 不一致（期望：$expected_appid，实际：$actual_appid）。" >&2
    return 1
  fi

  UPLOAD_RESULT_VERSION="$actual_version"
  UPLOAD_RESULT_APPID="$actual_appid"
  UPLOAD_RESULT_VERSION_SOURCE="$version_source"
  UPLOAD_RESULT_APPID_SOURCE="$appid_source"
  return 0
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
EXPECTED_APPID="$(_extract_project_appid "$RESOLVED_PROJECT_PATH")"

# 端口解析：必须为每个项目配置（或临时通过 PORT 显式指定）。
if [[ -z "${PORT:-}" ]]; then
  PORT="$(_resolve_wx_devtools_port "$RESOLVED_PROJECT_PATH")"
fi

if [[ -z "${PORT:-}" ]]; then
  PORTS_FILE="${WX_DEVTOOLS_PORTS_FILE:-$(_default_wx_devtools_ports_file)}"
  echo "[错误] 未配置微信开发者工具 IDE 服务端口，无法执行上传。" >&2
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
  echo "  PORT=12605 PROJECT_BASE=\"$PROJECT_BASE\" VERSION=\"$VERSION\" bash \"$0\"" >&2
  exit 2
fi

if [[ ! "$PORT" =~ ^[0-9]+$ ]]; then
  echo "[错误] 端口号无效：PORT=$PORT（必须为纯数字）" >&2
  exit 2
fi

if ! [[ "$UPLOAD_RETRY_ON_FAIL" =~ ^[0-9]+$ ]]; then
  UPLOAD_RETRY_ON_FAIL=1
fi
if ! [[ "$UPLOAD_RETRY_DELAY_SECONDS" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
  UPLOAD_RETRY_DELAY_SECONDS=1
fi

# 清理代理，避免请求走代理失败
export http_proxy= https_proxy= all_proxy=
export no_proxy="servicewechat.com,.weixin.qq.com"

_detect_upload_cli_capabilities
echo "[信息] upload 参数探测：版本参数=${UPLOAD_VERSION_FLAG}，描述参数=${UPLOAD_DESC_FLAG}，支持 --info-output=${UPLOAD_INFO_OUTPUT_SUPPORTED}" >&2
echo "[信息] 执行上传，项目：${RESOLVED_PROJECT_PATH}，版本：${VERSION}，端口：${PORT}"

CLI_LOG="$(mktemp -t wx-upload-cli.XXXXXX)"
UPLOAD_INFO_FILE="$(mktemp -t wx-upload-info.XXXXXX.json)"
MAX_ATTEMPTS=$((UPLOAD_RETRY_ON_FAIL + 1))
if [[ $MAX_ATTEMPTS -lt 1 ]]; then
  MAX_ATTEMPTS=1
fi

ATTEMPT=1
CLI_STATUS=1
while [[ $ATTEMPT -le $MAX_ATTEMPTS ]]; do
  : >"$CLI_LOG"
  : >"$UPLOAD_INFO_FILE"
  if _run_upload_cli_once "$RESOLVED_PROJECT_PATH" "$VERSION" "$UPLOAD_DESC" "$PORT" "$CLI_LOG" "$UPLOAD_INFO_FILE"; then
    CLI_STATUS=0
    break
  fi
  CLI_STATUS=$?
  if [[ $ATTEMPT -lt $MAX_ATTEMPTS ]]; then
    echo "[警告] 上传失败（第 ${ATTEMPT}/${MAX_ATTEMPTS} 次），${UPLOAD_RETRY_DELAY_SECONDS}s 后自动重试..." >&2
    sleep "$UPLOAD_RETRY_DELAY_SECONDS"
  fi
  ATTEMPT=$((ATTEMPT + 1))
done

if [[ $CLI_STATUS -ne 0 ]]; then
  echo "[错误] 微信开发者工具 CLI 退出码：$CLI_STATUS" >&2
  tail -n 60 "$CLI_LOG" >&2 || true
  exit "$CLI_STATUS"
fi

if ! _verify_upload_result "$VERSION" "$EXPECTED_APPID" "$UPLOAD_INFO_FILE" "$CLI_LOG"; then
  echo "[错误] 上传命令退出成功，但结果校验失败，已拒绝标记成功。" >&2
  if [[ -s "$CLI_LOG" ]]; then
    echo "[调试] CLI 输出（末尾 80 行）：" >&2
    tail -n 80 "$CLI_LOG" >&2 || true
  fi
  if [[ -s "$UPLOAD_INFO_FILE" ]]; then
    echo "[调试] info-output 文件：$UPLOAD_INFO_FILE" >&2
    tail -n 80 "$UPLOAD_INFO_FILE" >&2 || true
  fi
  exit 4
fi

echo "[完成] 上传成功：项目：${RESOLVED_PROJECT_PATH}，版本：${UPLOAD_RESULT_VERSION}，AppID：${UPLOAD_RESULT_APPID}"
echo "[信息] 上传校验来源：version=${UPLOAD_RESULT_VERSION_SOURCE:-unknown}，appid=${UPLOAD_RESULT_APPID_SOURCE:-unknown}"
echo "[信息] 上传参数组合：${LAST_UPLOAD_VERSION_FLAG} ${LAST_UPLOAD_DESC_FLAG}"
echo "UPLOAD_VERSION: ${UPLOAD_RESULT_VERSION}"
echo "UPLOAD_APPID: ${UPLOAD_RESULT_APPID}"
