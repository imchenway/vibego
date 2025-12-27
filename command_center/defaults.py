"""默认的通用命令定义集合。"""
from __future__ import annotations

from typing import Tuple, Dict

# 需要清理的废弃通用命令名称，启动/初始化时会主动删除，避免旧数据残留
REMOVED_GLOBAL_COMMAND_NAMES: Tuple[str, ...] = (
    "git-fetch",
    "git-fetch-add-commit-push",
    "wx-preview",
    "wx-setup",
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
        "name": "wx-dev-preview",
        "title": "生成微信开发预览二维码",
        "command": 'PROJECT_BASE="${PROJECT_BASE:-$MODEL_WORKDIR}" OUTPUT_QR="${OUTPUT_QR:-$HOME/Downloads/wx-preview-$(date +%s).jpg}" bash "$ROOT_DIR/scripts/gen_preview.sh"',
        "description": "调用微信开发者工具 CLI，在当前工作目录生成预览二维码并回传 Telegram（需本机已登录 IDE 并开启服务端口；需在 vibego 配置目录的 config/wx_devtools_ports.json 为当前项目配置 IDE 服务端口，或临时设置 PORT）。默认将二维码输出到 ~/Downloads。",
        "aliases": (),
        "timeout": 600,
    },
)


__all__ = ["DEFAULT_GLOBAL_COMMANDS", "REMOVED_GLOBAL_COMMAND_NAMES"]
