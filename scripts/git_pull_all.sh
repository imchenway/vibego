#!/usr/bin/env bash
# 自动遍历脚本所在目录（或指定目录）内的 Git 仓库并执行 git pull。
# 默认会在发现工作区脏数据时执行 git stash，成功拉取后自动恢复。

set -uo pipefail

SCRIPT_NAME=$(basename "$0")
DEFAULT_BASE_DIR="/Users/david/hypha" # 默认扫描根目录

print_usage() {
  cat <<'USAGE'
用法: pull-all.sh [--dir 目录] [--max-depth 层级] [--dry-run] [--help]

参数说明:
  --dir         指定遍历的起始目录，默认为脚本所在目录。
  --max-depth   限制遍历深度，默认为 4。
  --dry-run     仅输出将执行的操作，不实际修改任何仓库。
  -h, --help    显示本帮助信息。
USAGE
}

info() {
  printf '[INFO] %s\n' "$*"
}

warn() {
  printf '[WARN] %s\n' "$*" >&2
}

error() {
  printf '[ERROR] %s\n' "$*" >&2
}

BASE_DIR=""
MAX_DEPTH=4
DRY_RUN=0
PARALLEL=""
# 使用 BatchMode 的 SSH 命令确保无凭证时快速失败，可通过 GIT_PULL_SSH_FAILFAST_COMMAND 自定义
SSH_FAILFAST_COMMAND=${GIT_PULL_SSH_FAILFAST_COMMAND:-"ssh -oBatchMode=yes -oConnectTimeout=15 -oConnectionAttempts=1"}
export SSH_FAILFAST_COMMAND

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir)
      shift || { error "--dir 需要一个参数"; exit 1; }
      BASE_DIR="$1"
      ;;
    --max-depth)
      shift || { error "--max-depth 需要一个数字"; exit 1; }
      MAX_DEPTH="$1"
      ;;
    --dry-run)
      DRY_RUN=1
      ;;
    --parallel)
      shift || { error "--parallel 需要一个数字"; exit 1; }
      PARALLEL="$1"
      ;;
    -h|--help)
      print_usage
      exit 0
      ;;
    *)
      error "未知参数: $1"
      print_usage
      exit 1
      ;;
  esac
  shift
done

if [[ -z "$BASE_DIR" ]]; then
  BASE_DIR="$DEFAULT_BASE_DIR"
fi

if ! BASE_DIR=$(cd "$BASE_DIR" 2>/dev/null && pwd); then
  error "无法进入目录: $BASE_DIR"
  exit 1
fi

detect_default_parallel() {
  local cpu_count
  if [[ -n "$PARALLEL" ]]; then
    echo "$PARALLEL"
    return
  fi

  if command -v nproc >/dev/null 2>&1; then
    cpu_count=$(nproc)
  elif command -v getconf >/dev/null 2>&1 && cpu_count=$(getconf _NPROCESSORS_ONLN 2>/dev/null); then
    :
  elif [[ $(uname -s) == "Darwin" ]]; then
    cpu_count=$(sysctl -n hw.logicalcpu 2>/dev/null)
  fi

  if [[ -z "$cpu_count" || "$cpu_count" -lt 1 ]]; then
    cpu_count=1
  fi

  if [[ "$cpu_count" -gt 20 ]]; then
    cpu_count=20
  fi

  echo "$cpu_count"
}

PARALLEL=$(detect_default_parallel)

if ! [[ "$PARALLEL" =~ ^[0-9]+$ ]] || [[ "$PARALLEL" -lt 1 || "$PARALLEL" -gt 20 ]]; then
  error "--parallel 仅支持 1-20 的整数"
  exit 1
fi

info "遍历目录: $BASE_DIR (max-depth=$MAX_DEPTH, dry-run=$DRY_RUN, parallel=$PARALLEL)"

STATUS_FILE=$(mktemp)
REPO_LIST_FILE=$(mktemp)
REPORT_DONE=0
FINAL_FAILED_COUNT=0

cleanup_temp_files() {
  rm -f "$STATUS_FILE" "$REPO_LIST_FILE"
}

finalize_and_report() {
  if ((REPORT_DONE)); then
    return
  fi
  REPORT_DONE=1

  if [[ ! -f "$STATUS_FILE" ]]; then
    warn "缺少汇总状态文件，无法输出结果"
    return
  fi

  local success_repos=()
  local failed_repos=()
  local skipped_repos=()

  while IFS=$'\t' read -r status repo note; do
    case "$status" in
      SUCCESS)
        success_repos+=("$repo")
        ;;
      FAILED)
        if [[ -n "$note" ]]; then
          failed_repos+=("$repo ($note)")
        else
          failed_repos+=("$repo")
        fi
        ;;
      SKIPPED)
        if [[ -n "$note" ]]; then
          skipped_repos+=("$repo ($note)")
        else
          skipped_repos+=("$repo")
        fi
        ;;
    esac
  done <"$STATUS_FILE"

  FINAL_FAILED_COUNT=${#failed_repos[@]}

  info "汇总: 成功 ${#success_repos[@]} 个，失败 ${FINAL_FAILED_COUNT} 个，跳过 ${#skipped_repos[@]} 个"

  if [[ ${#failed_repos[@]} -gt 0 ]]; then
    warn "失败列表:"
    for item in "${failed_repos[@]}"; do
      warn "  - $item"
    done
  fi

  if [[ ${#skipped_repos[@]} -gt 0 ]]; then
    info "跳过列表:"
    for item in "${skipped_repos[@]}"; do
      info "  - $item"
    done
  fi
}

terminate_with() {
  local exit_code="$1"
  finalize_and_report
  if [[ $FINAL_FAILED_COUNT -gt 0 && "$exit_code" -eq 0 ]]; then
    exit_code=1
  fi
  cleanup_temp_files
  exit "$exit_code"
}

signal_handler() {
  local signal_name="$1"
  warn "捕获到信号: $signal_name，提前结束"
  terminate_with 1
}

trap 'signal_handler SIGINT' SIGINT
trap 'signal_handler SIGTERM' SIGTERM
trap 'signal_handler SIGPIPE' SIGPIPE

record_result() {
  local status="$1"
  local repo="$2"
  local note="${3:-}"
  printf '%s\t%s\t%s\n' "$status" "$repo" "$note" >>"$STATUS_FILE"
}

collect_repos() {
  local tmp_repo_list
  tmp_repo_list=$(mktemp)

  # 捕获 .git 目录形式的仓库
  while IFS= read -r -d '' gitdir; do
    printf '%s\n' "${gitdir%/.git}" >>"$tmp_repo_list"
  done < <(find "$BASE_DIR" -maxdepth "$MAX_DEPTH" -type d -name .git \
    -not -path "*/.git/modules/*" -print0 2>/dev/null)

  # 捕获 worktree 结构中以 .git 文件存在的仓库
  while IFS= read -r -d '' gitfile; do
    printf '%s\n' "$(dirname "$gitfile")" >>"$tmp_repo_list"
  done < <(find "$BASE_DIR" -maxdepth "$MAX_DEPTH" -type f -name .git \
    -not -path "*/.git/modules/*" -print0 2>/dev/null)

  if [[ ! -s "$tmp_repo_list" ]]; then
    rm -f "$tmp_repo_list"
    REPOS=()
    return
  fi

  local tmp_sorted
  tmp_sorted=$(mktemp)
  sort -u "$tmp_repo_list" >"$tmp_sorted"
  rm -f "$tmp_repo_list"

  REPOS=()
  while IFS= read -r repo; do
    if git -C "$repo" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
      REPOS+=("$repo")
    fi
  done <"$tmp_sorted"
  rm -f "$tmp_sorted"
}

collect_repos

if [[ ${#REPOS[@]} -eq 0 ]]; then
  warn "未在指定目录内发现 Git 仓库"
  cleanup_temp_files
  exit 0
fi

pull_repo() {
  local repo="$1"
  info "---- 处理仓库: $repo ----"

  local branch
  if ! branch=$(git -C "$repo" rev-parse --abbrev-ref HEAD 2>/dev/null); then
    warn "无法识别当前分支，跳过"
    record_result "SKIPPED" "$repo" "未知分支"
    return 0
  fi

  if [[ "$branch" == "HEAD" ]]; then
    warn "仓库处于游离 HEAD 状态，跳过"
    record_result "SKIPPED" "$repo" "游离HEAD"
    return 0
  fi

  local upstream
  if upstream=$(git -C "$repo" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null); then
    :
  else
    if git -C "$repo" remote get-url origin >/dev/null 2>&1; then
      upstream="origin/$branch"
      info "未绑定上游，改用 origin/$branch"
    else
      warn "仓库缺少远端，跳过"
      record_result "SKIPPED" "$repo" "无远端"
      return 0
    fi
  fi

  local status_output
  status_output=$(git -C "$repo" status --short)
  local stash_created=0
  local stash_label="auto-stash::pull-all::$(date -u +%Y%m%dT%H%M%SZ)"

  if [[ -n "$status_output" ]]; then
    if ((DRY_RUN)); then
      info "[dry-run] 将 stash 工作区改动"
      stash_created=1
    else
      info "检测到工作区改动，执行 git stash"
      local stash_output
      if ! stash_output=$(git -C "$repo" stash push --include-untracked --message "$stash_label" 2>&1); then
        warn "git stash 失败: $stash_output"
        record_result "FAILED" "$repo" "stash 失败"
        return 1
      fi
      if [[ "$stash_output" == *"No local changes to save"* ]]; then
        stash_created=0
      else
        stash_created=1
        info "已保存 stash: $stash_label"
      fi
    fi
  fi

  local remote
  local remote_branch
  if [[ "$upstream" == */* ]]; then
    remote=${upstream%%/*}
    remote_branch=${upstream#*/}
  else
    remote=""
    remote_branch=""
  fi

  if ((DRY_RUN)); then
    if [[ -n "$remote" ]]; then
      info "[dry-run] 将执行: git -C '$repo' pull $remote $remote_branch"
    else
      info "[dry-run] 将执行: git -C '$repo' pull"
    fi
  else
    info "开始拉取最新代码"
    # 为 git pull 注入 BatchMode SSH，避免无密钥时阻塞等待密码
    if [[ -n "$remote" ]]; then
      if ! GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND="$SSH_FAILFAST_COMMAND" git -C "$repo" pull "$remote" "$remote_branch"; then
        warn "git pull 失败，请手动处理"
        if ((stash_created)); then
          warn "stash 已保留: $stash_label"
        fi
        record_result "FAILED" "$repo" "pull 失败"
        return 1
      fi
    else
      if ! GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND="$SSH_FAILFAST_COMMAND" git -C "$repo" pull; then
        warn "git pull 失败，请手动处理"
        if ((stash_created)); then
          warn "stash 已保留: $stash_label"
        fi
        record_result "FAILED" "$repo" "pull 失败"
        return 1
      fi
    fi
  fi

  if ((stash_created)); then
    if ((DRY_RUN)); then
      info "[dry-run] 将执行: git -C '$repo' stash pop"
    else
      info "恢复工作区改动"
      if ! git -C "$repo" stash pop; then
        warn "stash pop 发生冲突或失败，请手动处理 (标记: $stash_label)"
        record_result "FAILED" "$repo" "stash pop 失败"
        return 1
      fi
    fi
  fi

  record_result "SUCCESS" "$repo"
  return 0
}

export DRY_RUN STATUS_FILE
export -f info warn error record_result pull_repo

if [[ ${#REPOS[@]} -gt 0 ]]; then
  if ! printf '%s\0' "${REPOS[@]}" >"$REPO_LIST_FILE"; then
    error "无法写入仓库列表到临时文件"
    terminate_with 1
  fi

  if ! xargs -0 -n1 -P "$PARALLEL" bash -c 'pull_repo "$@"' _ <"$REPO_LIST_FILE"; then
    warn "部分仓库处理失败，详见汇总"
  fi
fi

terminate_with 0
