#!/usr/bin/env python3
"""Master Post-startup health check script.

Process:
1. Wait for a startup flag to appear in the master log.
2. Call MasterManager to start the specified worker (default hyphavibebotbackend).
3. Automatically discover the worker's chat_id(Prioritize the state file, then read the latest log).
4. Send a probe message to the chat through the Telegram Bot API to confirm that the sending is successful.
5. If any step fails, an exception is thrown and an attempt is made to notify the administrator.

Note: This script will not automatically retry the restart and will only return a non-zero exit code for processing by the outer script.
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

# Import the configuration and tools in master and reuse the project parsing logic
ROOT_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR_STR = str(ROOT_DIR)
if ROOT_DIR_STR not in sys.path:
    # Make sure the master module can be imported from the repository root
    sys.path.insert(0, ROOT_DIR_STR)

import master  # type: ignore
from project_repository import ProjectRepository
DEFAULT_MASTER_LOG = master.LOG_ROOT_PATH / "vibe.log"
DEFAULT_TIMEOUT_MASTER = 60.0
DEFAULT_TIMEOUT_PROBE = 15.0
PROBE_TEXT = "hello"
REPOSITORY = ProjectRepository(master.CONFIG_DB_PATH, master.CONFIG_PATH)


def _load_project(project_id: str) -> master.ProjectConfig:
    """Get the project configuration based on the slug or bot name, and list the options on failure."""

    record = REPOSITORY.get_by_slug(project_id)
    if record is None:
        record = REPOSITORY.get_by_bot_name(project_id)
    if record is None:
        available = [r.project_slug for r in REPOSITORY.list_projects()]
        raise RuntimeError(f"No items found {project_id}, Optional items: {available}")
    return master.ProjectConfig.from_dict(record.to_dict())


def _wait_for_log_flag(path: Path, pattern: str, timeout: float) -> None:
    """Waits for a specific mark in the log within the timeout period."""

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
    raise TimeoutError(f"exist {timeout:.0f} No log markers detected in seconds: {pattern}")


def _extract_chat_id_from_logs(log_path: Path) -> Optional[int]:
    """Find the most recent chat from the log file in reverse order_id."""

    if not log_path.exists():
        return None
    pattern = re.compile(r"chat=(-?\d+)")
    try:
        lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return None
    for line in reversed(lines[-200:]):  # Reverse search for recent records
        match = pattern.search(line)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                continue
    return None


def _ensure_chat_id(cfg: master.ProjectConfig, manager: master.MasterManager) -> int:
    """Make sure the task is assigned chat_id, Backfill from log and write back to state if necessary."""

    state = manager.state_store.data.get(cfg.project_slug)
    if state and state.chat_id:
        return int(state.chat_id)
    # Fall back to log search
    log_dir = master.LOG_ROOT_PATH / (cfg.default_model.lower()) / cfg.project_slug
    chat_id = _extract_chat_id_from_logs(log_dir / "run_bot.log")
    if chat_id is None:
        raise RuntimeError(
            "Unable to get chat automatically_id, Please manually have a conversation with the bot to write the state/log"
        )
    # will discover chat_id Write back the state for easy reuse next time
    manager.state_store.update(cfg.project_slug, chat_id=chat_id)
    return chat_id


def _send_probe(bot_token: str, chat_id: int, text: str, timeout: float) -> None:
    """Send a probe message to the specified chat to verify that the Telegram API is available."""

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": text, "disable_notification": True}).encode("utf-8")
    request = Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:  # pragma: no cover - Thrown when network exception occurs
        raise RuntimeError(f"Failed to send probe message, HTTP {exc.code}: {exc.reason}") from exc
    except URLError as exc:  # pragma: no cover - Thrown when network exception occurs
        raise RuntimeError(f"Failed to send probe message: {exc}") from exc
    if not data.get("ok"):
        raise RuntimeError(f"Failed to send probe message: {data}")


def _format_admin_notice(reason: str) -> str:
    """Generate alarm text to notify the administrator."""

    return (
        "Master Restart health check failed\n"
        f"reason:{reason}\n"
        "Please log in to the server as soon as possible to troubleshoot (start.log / vibe.log)."
    )


def _notify_admins(reason: str) -> None:
    """If the master token is available, the failure reason is broadcast to the administrator list."""

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
    """Starts the specified project worker and returns the temporarily constructed MasterManager."""

    records = REPOSITORY.list_projects()
    configs = [master.ProjectConfig.from_dict(record.to_dict()) for record in records]
    state_store = master.StateStore(
        master.STATE_PATH, {item.project_slug: item for item in configs}
    )
    manager = master.MasterManager(configs, state_store=state_store)

    async def _run() -> None:
        """The coroutine performs the actual stop/start process."""
        # Make sure to stop the old instance first(If it existsexist)
        try:
            await manager.stop_worker(cfg)
        except Exception:
            pass
        await manager.run_worker(cfg)

    asyncio.run(_run())
    return manager


def main() -> int:
    """Command line entry, performs master health check and returns exit code."""

    parser = argparse.ArgumentParser(description="Master Post-launch health check")
    parser.add_argument("--project", default="hyphavibebotbackend", help="Project slug or bot name")
    parser.add_argument("--master-log", default=str(DEFAULT_MASTER_LOG), help="master Log path")
    parser.add_argument("--master-timeout", type=float, default=DEFAULT_TIMEOUT_MASTER, help="master Log wait timeout (Second)")
    parser.add_argument("--probe-timeout", type=float, default=DEFAULT_TIMEOUT_PROBE, help="Telegram Probe timeout (Second)")
    args = parser.parse_args()

    project_id = master._sanitize_slug(args.project)
    master_log = Path(args.master_log)

    try:
        _wait_for_log_flag(master_log, "Master Started, listening for administrator commands.", args.master_timeout)
        cfg = _load_project(project_id)
        manager = _ensure_worker(cfg)
        chat_id = _ensure_chat_id(cfg, manager)
        _send_probe(cfg.bot_token, chat_id, PROBE_TEXT, args.probe_timeout)
    except Exception as exc:
        reason = str(exc)
        _notify_admins(reason)
        print(f"[healthcheck] fail: {reason}", file=sys.stderr)
        return 1
    else:
        print(
            "[healthcheck] success: master ready,"
            f"worker={cfg.display_name} Startup completed, chat_id={chat_id}, Probe message sent"
        )
        return 0


if __name__ == "__main__":
    sys.exit(main())
