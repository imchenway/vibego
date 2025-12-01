#!/usr/bin/env bash
# 一键执行 git_pull_all.sh 与 git_push_all.sh，支持常用参数透传，并汇总结果。

set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PULL_SCRIPT="$SCRIPT_DIR/git_pull_all.sh"
PUSH_SCRIPT="$SCRIPT_DIR/git_push_all.sh"
DEFAULT_BASE_DIR="/Users/david/hypha" # 默认扫描根目录
KEEP_LOG_ALWAYS=${SYNC_ALL_KEEP_LOG:-0}
declare -a ALL_REPOS=()

print_usage() {
  cat <<'USAGE'
用法: sync-all.sh [--dir 目录] [--max-depth 层级] [--dry-run] [--parallel 并行数] [--help]

脚本会先执行 git_pull_all.sh，再执行 git_push_all.sh，参数会分别传递给对应脚本，并在结束时输出仓库同步清单。
USAGE
}

if [[ ! -x "$PULL_SCRIPT" ]]; then
  echo "[ERROR] 找不到可执行的 git_pull_all.sh ($PULL_SCRIPT)" >&2
  exit 1
fi

if [[ ! -x "$PUSH_SCRIPT" ]]; then
  echo "[ERROR] 找不到可执行的 git_push_all.sh ($PUSH_SCRIPT)" >&2
  exit 1
fi

BASE_DIR=""
MAX_DEPTH=""
DRY_RUN=0
PARALLEL=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir)
      shift || { echo "[ERROR] --dir 需要一个参数" >&2; exit 1; }
      BASE_DIR="$1"
      ;;
    --max-depth)
      shift || { echo "[ERROR] --max-depth 需要一个数字" >&2; exit 1; }
      MAX_DEPTH="$1"
      ;;
    --dry-run)
      DRY_RUN=1
      ;;
    --parallel)
      shift || { echo "[ERROR] --parallel 需要一个数字" >&2; exit 1; }
      PARALLEL="$1"
      ;;
    -h|--help)
      print_usage
      exit 0
      ;;
    *)
      echo "[ERROR] 未知参数: $1" >&2
      print_usage
      exit 1
      ;;
  esac
  shift
done

pull_args=()
push_args=()

if [[ -n "$BASE_DIR" ]]; then
  pull_args+=("--dir" "$BASE_DIR")
  push_args+=("--dir" "$BASE_DIR")
fi

if [[ -n "$MAX_DEPTH" ]]; then
  pull_args+=("--max-depth" "$MAX_DEPTH")
  push_args+=("--max-depth" "$MAX_DEPTH")
fi

if (( DRY_RUN )); then
  pull_args+=("--dry-run")
  push_args+=("--dry-run")
fi

if [[ -n "$PARALLEL" ]]; then
  pull_args+=("--parallel" "$PARALLEL")
fi

pull_log=$(mktemp)
push_log=$(mktemp)
pull_log_retained=""
push_log_retained=""

cleanup_logs() {
  if [[ -n "$pull_log" && -f "$pull_log" ]]; then
    rm -f "$pull_log"
  fi
  if [[ -n "$push_log" && -f "$push_log" ]]; then
    rm -f "$push_log"
  fi
}
trap 'cleanup_logs' EXIT

run_with_log() {
  name="$1"
  log_file="$2"
  shift 2
  echo "[INFO] 开始执行 $name"
  "$@" 2>&1 | tee "$log_file"
  rc=${PIPESTATUS[0]}
  if [[ $rc -ne 0 ]]; then
    echo "[ERROR] $name 执行失败 (exit=$rc)" >&2
    return $rc
  fi
  echo "[INFO] $name 执行完成"
  return 0
}

if [[ ${#pull_args[@]} -gt 0 ]]; then
  run_with_log "pull-all" "$pull_log" "$PULL_SCRIPT" "${pull_args[@]}"
else
  run_with_log "pull-all" "$pull_log" "$PULL_SCRIPT"
fi
pull_rc=$?

if [[ $pull_rc -ne 0 ]]; then
  echo "[WARN] pull-all 阶段存在失败仓库，请查看最终汇总" >&2
fi

if [[ ${#push_args[@]} -gt 0 ]]; then
  run_with_log "push-all" "$push_log" "$PUSH_SCRIPT" "${push_args[@]}"
else
  run_with_log "push-all" "$push_log" "$PUSH_SCRIPT"
fi
push_rc=$?

BASE_DIR_USED="$BASE_DIR"
if [[ -z "$BASE_DIR_USED" ]]; then
  BASE_DIR_USED="$DEFAULT_BASE_DIR"
fi
BASE_DIR_USED=$(cd "$BASE_DIR_USED" && pwd)
MAX_DEPTH_VALUE=${MAX_DEPTH:-4}

collect_repos() {
  base="$1"
  depth="$2"
  tmp_repo=$(mktemp)

  while IFS= read -r -d '' gitdir; do
    printf '%s\n' "${gitdir%/.git}" >>"$tmp_repo"
  done < <(find "$base" -maxdepth "$depth" -type d -name .git \
    -not -path "*/.git/modules/*" -print0 2>/dev/null)

  while IFS= read -r -d '' gitfile; do
    printf '%s\n' "$(dirname "$gitfile")" >>"$tmp_repo"
  done < <(find "$base" -maxdepth "$depth" -type f -name .git \
    -not -path "*/.git/modules/*" -print0 2>/dev/null)

  if [[ ! -s "$tmp_repo" ]]; then
    rm -f "$tmp_repo"
    ALL_REPOS=()
    return
  fi

  tmp_sorted=$(mktemp)
  sort -u "$tmp_repo" >"$tmp_sorted"
  rm -f "$tmp_repo"

  ALL_REPOS=()
  while IFS= read -r repo; do
    ALL_REPOS+=("$repo")
  done <"$tmp_sorted"
  rm -f "$tmp_sorted"
}

ALL_REPOS=()
collect_repos "$BASE_DIR_USED" "$MAX_DEPTH_VALUE"

pull_fail_repos=()
pull_fail_notes=()
pull_skip_repos=()
pull_skip_notes=()
push_fail_repos=()
push_fail_notes=()
push_skip_repos=()
push_skip_notes=()
push_note_repos=()
push_note_values=()

trim_suffix_note() {
  entry="$1"
  repo=${entry%% (*}
  note=""
  if [[ $entry == *"("*"" ]]; then
    note=${entry#"$repo"}
    note=${note# }
    note=${note#(}
    note=${note%)}
  fi
  printf '%s|%s\n' "$repo" "$note"
}

parse_pull_summary() {
  log_file="$1"
  mode=""
  while IFS= read -r line; do
    case "$line" in
      "[WARN] 失败列表:")
        mode="fail"
        continue
        ;;
      "[INFO] 跳过列表:")
        mode="skip"
        continue
        ;;
    esac

    if [[ $line == \[*\]" 汇总:"* ]]; then
      mode=""
    fi

    if [[ $mode == "fail" && $line == "[WARN]   - "* ]]; then
      entry=${line#"[WARN]   - "}
      result=$(trim_suffix_note "$entry")
      pull_fail_repos+=("${result%%|*}")
      pull_fail_notes+=("${result#*|}")
    elif [[ $mode == "skip" && $line == "[INFO]   - "* ]]; then
      entry=${line#"[INFO]   - "}
      result=$(trim_suffix_note "$entry")
      pull_skip_repos+=("${result%%|*}")
      pull_skip_notes+=("${result#*|}")
    fi
  done <"$log_file"
}

parse_pull_details() {
  log_file="$1"
  current=""
  while IFS= read -r line; do
    if [[ $line == "[INFO] ---- 处理仓库: "* ]]; then
      current=${line#"[INFO] ---- 处理仓库: "}
      current=${current%" ----"}
      continue
    fi
    if [[ -z "$current" ]]; then
      continue
    fi
    case "$line" in
      "[WARN] git stash 失败:"*)
        pull_fail_repos+=("$current")
        pull_fail_notes+=("stash 失败")
        current=""
        ;;
      "[WARN] git pull 失败，请手动处理")
        pull_fail_repos+=("$current")
        pull_fail_notes+=("pull 失败")
        current=""
        ;;
      "[WARN] stash pop 发生冲突或失败，请手动处理 "*)
        pull_fail_repos+=("$current")
        pull_fail_notes+=("stash pop 失败")
        current=""
        ;;
    esac
  done <"$log_file"
}

parse_push_summary() {
  log_file="$1"
  mode=""
  while IFS= read -r line; do
    case "$line" in
      "[WARN] 失败列表:")
        mode="fail"
        continue
        ;;
      "[INFO] 跳过列表:")
        mode="skip"
        continue
        ;;
    esac

    if [[ $line == \[*\]" 汇总:"* ]]; then
      mode=""
    fi

    if [[ $mode == "fail" && $line == "[WARN]   - "* ]]; then
      entry=${line#"[WARN]   - "}
      result=$(trim_suffix_note "$entry")
      push_fail_repos+=("${result%%|*}")
      push_fail_notes+=("${result#*|}")
    elif [[ $mode == "skip" && $line == "[INFO]   - "* ]]; then
      entry=${line#"[INFO]   - "}
      result=$(trim_suffix_note "$entry")
      push_skip_repos+=("${result%%|*}")
      push_skip_notes+=("${result#*|}")
    fi
  done <"$log_file"
}

parse_push_details() {
  log_file="$1"
  current=""
  while IFS= read -r line; do
    if [[ $line == "[INFO] ---- 处理仓库: "*" ----" ]]; then
      current=${line#"[INFO] ---- 处理仓库: "}
      current=${current%" ----"}
      continue
    fi
    if [[ -z "$current" ]]; then
      continue
    fi
    case "$line" in
      "[INFO] 无改动需要推送")
        push_note_repos+=("$current")
        push_note_values+=("无改动")
        ;;
      "[INFO] 提交信息: "*)
        push_note_repos+=("$current")
        push_note_values+=("提交: ${line#"[INFO] 提交信息: "}")
        ;;
      "[WARN] git push 失败")
        push_fail_repos+=("$current")
        push_fail_notes+=("push 失败")
        ;;
    esac
  done <"$log_file"
}

parse_pull_summary "$pull_log"
parse_push_summary "$push_log"
parse_push_details "$push_log"

if [[ $pull_rc -ne 0 && ${#pull_fail_repos[@]} -eq 0 ]]; then
  parse_pull_details "$pull_log"
fi
if [[ ${#pull_fail_repos[@]} -gt 0 ]]; then
  pull_rc=1
fi

echo
echo "同步结果清单："
for repo in "${ALL_REPOS[@]-}"; do
  if [[ -z "$repo" ]]; then
    continue
  fi
  pull_status_text="成功"
  pull_note_text=""
  idx=0
  for ((idx=0; idx<${#pull_fail_repos[@]}; ++idx)); do
    if [[ ${pull_fail_repos[idx]} == "$repo" ]]; then
      pull_status_text="失败"
      pull_note_text=${pull_fail_notes[idx]}
      break
    fi
  done
  if [[ $pull_status_text == "成功" ]]; then
    for ((idx=0; idx<${#pull_skip_repos[@]}; ++idx)); do
      if [[ ${pull_skip_repos[idx]} == "$repo" ]]; then
        pull_status_text="跳过"
        pull_note_text=${pull_skip_notes[idx]}
        break
      fi
    done
  fi

  push_status_text="成功"
  push_note_text=""
  for ((idx=0; idx<${#push_fail_repos[@]}; ++idx)); do
    if [[ ${push_fail_repos[idx]} == "$repo" ]]; then
      push_status_text="失败"
      push_note_text=${push_fail_notes[idx]}
      break
    fi
  done
  if [[ $push_status_text == "成功" ]]; then
    for ((idx=0; idx<${#push_skip_repos[@]}; ++idx)); do
      if [[ ${push_skip_repos[idx]} == "$repo" ]]; then
        push_status_text="跳过"
        push_note_text=${push_skip_notes[idx]}
        break
      fi
    done
  fi
  if [[ $push_status_text == "成功" && -z "$push_note_text" ]]; then
    for ((idx=0; idx<${#push_note_repos[@]}; ++idx)); do
      if [[ ${push_note_repos[idx]} == "$repo" ]]; then
        push_note_text=${push_note_values[idx]}
        break
      fi
    done
  fi

  echo "- $repo"
  if [[ -n "$pull_note_text" ]]; then
    echo "  pull: ${pull_status_text}（${pull_note_text}）"
  else
    echo "  pull: ${pull_status_text}"
  fi
  if [[ -n "$push_note_text" ]]; then
    echo "  push: ${push_status_text}（${push_note_text}）"
  else
    echo "  push: ${push_status_text}"
  fi
done

echo
if [[ ( "$pull_rc" -ne 0 || "$KEEP_LOG_ALWAYS" != "0" ) && -n "$pull_log" && -f "$pull_log" ]]; then
  if pull_retained_path=$(mktemp -t sync-all-pull.XXXX.log); then
    if mv "$pull_log" "$pull_retained_path"; then
      pull_log=""
      pull_log_retained="$pull_retained_path"
      echo "[INFO] pull-all 日志保留在: $pull_log_retained"
    else
      echo "[WARN] 无法保留 pull-all 日志" >&2
    fi
  else
    echo "[WARN] 创建 pull-all 日志副本失败" >&2
  fi
fi
if [[ ( "$push_rc" -ne 0 || "$KEEP_LOG_ALWAYS" != "0" ) && -n "$push_log" && -f "$push_log" ]]; then
  if push_retained_path=$(mktemp -t sync-all-push.XXXX.log); then
    if mv "$push_log" "$push_retained_path"; then
      push_log=""
      push_log_retained="$push_retained_path"
      echo "[INFO] push-all 日志保留在: $push_log_retained"
    else
      echo "[WARN] 无法保留 push-all 日志" >&2
    fi
  else
    echo "[WARN] 创建 push-all 日志副本失败" >&2
  fi
fi

echo
final_rc=0
if [[ $pull_rc -ne 0 || $push_rc -ne 0 ]]; then
  final_rc=1
fi
exit $final_rc
