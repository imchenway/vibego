"""默认的通用命令定义集合。"""
from __future__ import annotations

from typing import Tuple, Dict

# 需要清理的废弃通用命令名称，启动/初始化时会主动删除，避免旧数据残留
REMOVED_GLOBAL_COMMAND_NAMES: Tuple[str, ...] = (
    "git-fetch",
    "git-fetch-add-commit-push",
)

# 为了简化引用，统一使用 Tuple[dict, ...] 描述默认命令
DEFAULT_GLOBAL_COMMANDS: Tuple[Dict[str, object], ...] = (
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
    {
        "name": "wx-setup",
        "title": "配置微信小程序 CI",
        "command": 'WX_APPID=${WX_APPID:-} WX_PKP=${WX_PKP:-} PROJECT_PATH=${PROJECT_PATH:-} bash "$ROOT_DIR/scripts/wx_setup.sh"',
        "description": "写入当前项目的 WX_APPID/WX_PKP 等配置，保存于 ~/.config/vibego/wx_ci/<project>.env。",
        "aliases": ("wx-init",),
        "timeout": 120,
    },
    {
        "name": "wx-preview",
        "title": "生成小程序预览二维码",
        "command": 'PROJECT_PATH=${PROJECT_PATH:-} DESC=${DESC:-} bash "$ROOT_DIR/scripts/wx_preview.sh"',
        "description": "使用 miniprogram-ci 生成预览二维码并回传，首次需先执行 wx-setup 写入 APPID/私钥路径。",
        "aliases": ("wxqr",),
        "timeout": 180,
    },
)


__all__ = ["DEFAULT_GLOBAL_COMMANDS", "REMOVED_GLOBAL_COMMAND_NAMES"]
