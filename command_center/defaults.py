"""默认的通用命令定义集合。"""
from __future__ import annotations

from typing import Tuple, Dict

# 为了简化引用，统一使用 Tuple[dict, ...] 描述默认命令
DEFAULT_GLOBAL_COMMANDS: Tuple[Dict[str, object], ...] = (
    {
        "name": "git-fetch",
        "title": "git-fetch",
        "command": "git -c core.quotepath=false -c log.showSignature=false fetch origin --recurse-submodules=no --progress --prune",
        "description": "",
        "aliases": (),
    },
    {
        "name": "git-fetch-add-commit-push",
        "title": "git-fetch-add-commit-push",
        "command": 'git -c core.quotepath=false -c log.showSignature=false fetch origin --recurse-submodules=no --progress --prune && git add -A && git commit -m "commit via telegram" && git -c core.quotepath=false -c log.showSignature=false push --progress --porcelain origin refs/heads/master:master',
        "description": "",
        "aliases": (),
    },
    {
        "name": "git-pull-all",
        "title": "git pull 所有仓库",
        "command": 'bash "$ROOT_DIR/scripts/git_pull_all.sh" --dir "$MODEL_WORKDIR" --max-depth ${GIT_TREE_DEPTH:-4} --parallel ${GIT_PULL_PARALLEL:-6}',
        "description": "遍历当前项目配置的工作目录，自动并行执行 git pull，并处理 stash/pop。",
        "aliases": ("pull-all",),
        "timeout": 900,
    },
    {
        "name": "git-push-all",
        "title": "git push 所有仓库",
        "command": 'bash "$ROOT_DIR/scripts/git_push_all.sh" --dir "$MODEL_WORKDIR" --max-depth ${GIT_TREE_DEPTH:-4}',
        "description": "遍历当前项目配置的工作目录，自动执行 git add/commit/push。",
        "aliases": ("push-all",),
        "timeout": 900,
    },
    {
        "name": "git-sync-all",
        "title": "git pull+push 所有仓库",
        "command": 'bash "$ROOT_DIR/scripts/git_sync_all.sh" --dir "$MODEL_WORKDIR" --max-depth ${GIT_TREE_DEPTH:-4} --parallel ${GIT_PULL_PARALLEL:-6}',
        "description": "依次运行 pull-all 与 push-all，输出汇总清单，可通过并行参数控制性能。",
        "aliases": ("sync-all",),
        "timeout": 1500,
    },
)


__all__ = ["DEFAULT_GLOBAL_COMMANDS"]
