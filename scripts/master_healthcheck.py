#!/usr/bin/env python3
"""Master 启动后的健康检查脚本。

流程：
1. 等待 master 日志出现启动标记。
2. 调用 MasterManager 启动指定 worker（默认 hyphavibebotbackend）。
3. 自动发现该 worker 的 chat_id（优先 state 文件，其次读取最新日志）。
4. 通过 Telegram Bot API 向该 chat 发送探针消息，确认发送成功。
5. 任何步骤失败则抛出异常，并尝试通知管理员。

注意：本脚本不会自动重试重启，仅返回非零退出码供外层脚本处理。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen

# 导入 master 中的配置与工具，复用项目解析逻辑
ROOT_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR_STR = str(ROOT_DIR)
if ROOT_DIR_STR not in sys.path:
    # 确保可以从仓库根目录导入 master 模块
    sys.path.insert(0, ROOT_DIR_STR)

import master  # type: ignore
from project_repository import ProjectRepository
DEFAULT_MASTER_LOG = master.LOG_ROOT_PATH / "vibe.log"
DEFAULT_TIMEOUT_MASTER = 60.0
DEFAULT_TIMEOUT_PROBE = 15.0
PROBE_TEXT = "hello"
REPOSITORY = ProjectRepository(master.CONFIG_DB_PATH, master.CONFIG_PATH)


def _load_project(project_id: str) -> master.ProjectConfig:
    """根据 slug 或 bot 名获取项目配置，失败时列出可选项。"""

    record = REPOSITORY.get_by_slug(project_id)
    if record is None:
        record = REPOSITORY.get_by_bot_name(project_id)
    if record is None:
        available = [r.project_slug for r in REPOSITORY.list_projects()]
        raise RuntimeError(f"未找到项目 {project_id}，可选项目: {available}")
    return master.ProjectConfig.from_dict(record.to_dict())


def _wait_for_log_flag(path: Path, pattern: str, timeout: float) -> None:
    """在超时时间内等待日志出现特定标记。"""

    deadline = time.monotonic() + timeout
    position = 0
    while time.monotonic() < deadline:
        if path.exists():
            if position == 0:
                position = path.stat().st_size
            with path.open("r", encoding="utf-8", errors="ignore") as fh:
                fh.seek(position)
                while time.monotonic() < deadline:
                    line = fh.readline()
                    if not line:
                        time.sleep(0.5)
                        continue
                    position = fh.tell()
                    if pattern in line:
                        return
        time.sleep(0.5)
    raise TimeoutError(f"在 {timeout:.0f} 秒内未检测到日志标记: {pattern}")


def _extract_chat_id_from_logs(log_path: Path) -> Optional[int]:
    """从日志文件倒序查找最近的 chat_id。"""

    if not log_path.exists():
        return None
    pattern = re.compile(r"chat=(-?\d+)")
    try:
        lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return None
    for line in reversed(lines[-200:]):  # 反向查找最近的记录
        match = pattern.search(line)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                continue
    return None


def _ensure_chat_id(cfg: master.ProjectConfig, manager: master.MasterManager) -> int:
    """确保任务分配有 chat_id，必要时从日志回填并写回 state。"""

    state = manager.state_store.data.get(cfg.project_slug)
    if state and state.chat_id:
        return int(state.chat_id)
    # 回落到日志查找
    log_dir = master.LOG_ROOT_PATH / (cfg.default_model.lower()) / cfg.project_slug
    chat_id = _extract_chat_id_from_logs(log_dir / "run_bot.log")
    if chat_id is None:
        raise RuntimeError(
            "无法自动获取 chat_id，请先手动与该 bot 发生一次对话以写入 state/log"
        )
    # 将发现的 chat_id 写回 state，便于下次复用
    manager.state_store.update(cfg.project_slug, chat_id=chat_id)
    return chat_id


def _send_probe(bot_token: str, chat_id: int, text: str, timeout: float) -> None:
    """向指定 chat 发送探针消息，验证 Telegram API 可用。"""

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": text, "disable_notification": True}).encode("utf-8")
    request = Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:  # pragma: no cover - 网络异常时抛出
        raise RuntimeError(f"发送探针消息失败，HTTP {exc.code}: {exc.reason}") from exc
    except URLError as exc:  # pragma: no cover - 网络异常时抛出
        raise RuntimeError(f"发送探针消息失败: {exc}") from exc
    if not data.get("ok"):
        raise RuntimeError(f"发送探针消息失败: {data}")


def _format_admin_notice(reason: str) -> str:
    """生成通知管理员的告警文本。"""

    return (
        "Master 重启健康检查失败\n"
        f"原因：{reason}\n"
        "请尽快登录服务器排查（start.log / vibe.log）。"
    )


def _notify_admins(reason: str) -> None:
    """如果 master token 可用，则向管理员列表广播失败原因。"""

    master_token = os.environ.get("MASTER_BOT_TOKEN")
    if not master_token:
        return
    admins = master._collect_admin_targets()
    if not admins:
        return
    message = _format_admin_notice(reason)
    url = f"https://api.telegram.org/bot{master_token}/sendMessage"
    for chat_id in admins:
        payload = json.dumps(
            {"chat_id": chat_id, "text": message, "disable_notification": False}
        ).encode("utf-8")
        request = Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=10):
                pass
        except Exception:
            continue


def _ensure_worker(cfg: master.ProjectConfig) -> master.MasterManager:
    """启动指定项目 worker，并返回临时构造的 MasterManager。"""

    records = REPOSITORY.list_projects()
    configs = [master.ProjectConfig.from_dict(record.to_dict()) for record in records]
    state_store = master.StateStore(
        master.STATE_PATH, {item.project_slug: item for item in configs}
    )
    manager = master.MasterManager(configs, state_store=state_store)

    async def _run() -> None:
        """协程执行实际的 stop/start 流程。"""
        # 确保先停止旧实例（若存在）
        try:
            await manager.stop_worker(cfg)
        except Exception:
            pass
        await manager.run_worker(cfg)

    asyncio.run(_run())
    return manager


def main() -> int:
    """命令行入口，执行 master 健康检查并返回退出码。"""

    parser = argparse.ArgumentParser(description="Master 启动后的健康检查")
    parser.add_argument("--project", default="hyphavibebotbackend", help="项目 slug 或 bot 名称")
    parser.add_argument("--master-log", default=str(DEFAULT_MASTER_LOG), help="master 日志路径")
    parser.add_argument("--master-timeout", type=float, default=DEFAULT_TIMEOUT_MASTER, help="master 日志等待超时时间 (秒)")
    parser.add_argument("--probe-timeout", type=float, default=DEFAULT_TIMEOUT_PROBE, help="Telegram 探针超时时间 (秒)")
    args = parser.parse_args()

    project_id = master._sanitize_slug(args.project)
    master_log = Path(args.master_log)

    try:
        _wait_for_log_flag(master_log, "Master 已启动，监听管理员指令。", args.master_timeout)
        cfg = _load_project(project_id)
        manager = _ensure_worker(cfg)
        chat_id = _ensure_chat_id(cfg, manager)
        _send_probe(cfg.bot_token, chat_id, PROBE_TEXT, args.probe_timeout)
    except Exception as exc:
        reason = str(exc)
        _notify_admins(reason)
        print(f"[healthcheck] 失败: {reason}", file=sys.stderr)
        return 1
    else:
        print(
            "[healthcheck] 成功: master 已就绪，"
            f"worker={cfg.display_name} 启动完成，chat_id={chat_id}，已发送探针消息"
        )
        return 0


if __name__ == "__main__":
    sys.exit(main())
