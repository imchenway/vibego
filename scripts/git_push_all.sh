#!/usr/bin/env bash
# 自动遍历脚本所在目录（或指定目录）内的 Git 仓库并执行自动提交与推送。

set -uo pipefail

SCRIPT_NAME=$(basename "$0")
DEFAULT_BASE_DIR="/Users/david/hypha" # 默认扫描根目录

print_usage() {
  cat <<'USAGE'
用法: push-all.sh [--dir 目录] [--max-depth 层级] [--dry-run] [--help]

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

info "遍历目录: $BASE_DIR (max-depth=$MAX_DEPTH, dry-run=$DRY_RUN)"

collect_repos() {
  local tmp_repo_list
  tmp_repo_list=$(mktemp)

  while IFS= read -r -d '' gitdir; do
    printf '%s\n' "${gitdir%/.git}" >>"$tmp_repo_list"
  done < <(find "$BASE_DIR" -maxdepth "$MAX_DEPTH" -type d -name .git \
    -not -path "*/.git/modules/*" -print0 2>/dev/null)

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
  exit 0
fi

SUCCESS_REPOS=()
FAILED_REPOS=()
SKIPPED_REPOS=()

push_repo() {
  local repo="$1"
  info "---- 处理仓库: $repo ----"

  local branch
  if ! branch=$(git -C "$repo" rev-parse --abbrev-ref HEAD 2>/dev/null); then
    warn "无法识别当前分支，跳过"
    SKIPPED_REPOS+=("$repo (未知分支)")
    return
  fi

  if [[ "$branch" == "HEAD" ]]; then
    warn "仓库处于游离 HEAD 状态，跳过"
    SKIPPED_REPOS+=("$repo (游离HEAD)")
    return
  fi

  local remotes
  remotes=$(git -C "$repo" remote 2>/dev/null)
  if [[ -z "$remotes" ]]; then
    warn "仓库缺少远端配置，跳过"
    SKIPPED_REPOS+=("$repo (无远端)")
    return
  fi

  local plan_commit=0
  local made_commit=0
  local status_output
  status_output=$(git -C "$repo" status --short)

  if [[ -n "$status_output" ]]; then
    if ((DRY_RUN)); then
      info "[dry-run] 将执行: git -C '$repo' add -A"
      info "[dry-run] 将执行: git -C '$repo' commit -m 'chore(sync): auto-sync <UTC>'"
      plan_commit=1
    else
      info "检测到工作区改动，执行 git add -A"
      if ! git -C "$repo" add -A; then
        warn "git add 失败"
        FAILED_REPOS+=("$repo (git add 失败)")
        return
      fi

      if git -C "$repo" diff --cached --quiet; then
        info "无暂存改动需要提交，跳过提交"
      else
        local commit_msg
        commit_msg="chore(sync): auto-sync $(date -u +%Y%m%dT%H%M%SZ)"
        info "提交信息: $commit_msg"
        if ! git -C "$repo" commit -m "$commit_msg"; then
          warn "git commit 失败"
          FAILED_REPOS+=("$repo (commit 失败)")
          return
        fi
        made_commit=1
      fi
    fi
  fi

  local branch_summary
  branch_summary=$(git -C "$repo" status --short --branch | head -n 1)
  local needs_push=0
  if [[ "$branch_summary" == *"ahead"* ]] || [[ "$branch_summary" == *"No commits yet"* ]] || [[ "$branch_summary" == *"gone"* ]]; then
    needs_push=1
  fi
  if ((plan_commit)); then
    needs_push=1
  fi
  if ((made_commit)); then
    needs_push=1
  fi

  if ((DRY_RUN)); then
    if ((needs_push)); then
      info "[dry-run] 将执行: git -C '$repo' push"
    else
      info "[dry-run] 分支无需推送"
    fi
    SUCCESS_REPOS+=("$repo")
    return
  fi

  local push_cmd
  local upstream
  local upstream_exists=0
  if upstream=$(git -C "$repo" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null); then
    upstream_exists=1
    push_cmd=(git -C "$repo" push)
  else
    if git -C "$repo" remote get-url origin >/dev/null 2>&1; then
      push_cmd=(git -C "$repo" push --set-upstream origin "$branch")
      info "未检测到上游分支，使用 origin/$branch"
    else
      warn "仓库缺少可用远端，跳过"
      SKIPPED_REPOS+=("$repo (缺少上游)")
      return
    fi
  fi

  if (( ! upstream_exists )); then
    needs_push=1
  fi

  if ((needs_push)); then
    info "执行: ${push_cmd[*]}"
    if ! GIT_TERMINAL_PROMPT=0 "${push_cmd[@]}"; then
      warn "git push 失败"
      FAILED_REPOS+=("$repo (push 失败)")
      return
    fi
    SUCCESS_REPOS+=("$repo")
  else
    info "无改动需要推送"
    SUCCESS_REPOS+=("$repo")
  fi
}

for repo in "${REPOS[@]}"; do
  push_repo "$repo"
  echo
done

info "汇总: 成功 ${#SUCCESS_REPOS[@]} 个，失败 ${#FAILED_REPOS[@]} 个，跳过 ${#SKIPPED_REPOS[@]} 个"

if [[ ${#FAILED_REPOS[@]} -gt 0 ]]; then
  warn "失败列表:"
  for item in "${FAILED_REPOS[@]}"; do
    warn "  - $item"
  done
fi

if [[ ${#SKIPPED_REPOS[@]} -gt 0 ]]; then
  info "跳过列表:"
  for item in "${SKIPPED_REPOS[@]}"; do
    info "  - $item"
  done
fi

if [[ ${#FAILED_REPOS[@]} -gt 0 ]]; then
  exit 1
fi

exit 0
