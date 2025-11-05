#!/usr/bin/env python3
"""Master post-startup health check.

This lightweight variant only verifies that the master process has emitted the
readiness marker in its log file. Worker bootstrap and Telegram probes are no
longer executed because master restart confirmation is now limited to the
controller process itself.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

# Make sure the master module can be imported from the repository root
ROOT_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR_STR = str(ROOT_DIR)
if ROOT_DIR_STR not in sys.path:
    sys.path.insert(0, ROOT_DIR_STR)

import master  # type: ignore

DEFAULT_MASTER_LOG = master.LOG_ROOT_PATH / "vibe.log"
DEFAULT_TIMEOUT_MASTER = 60.0
MASTER_READY_MARKER = "Master Started, listening for administrator commands."


def _wait_for_log_flag(path: Path, pattern: str, timeout: float) -> None:
    """Poll the master log until the readiness marker is detected or timeout."""

    deadline = time.monotonic() + timeout
    if path.exists():
        position = path.stat().st_size
        initialized = True
    else:
        position = 0
        initialized = False
    while time.monotonic() < deadline:
        if path.exists():
            if not initialized:
                position = 0
                initialized = True
            with path.open("r", encoding="utf-8", errors="ignore") as fh:
                fh.seek(position)
                while time.monotonic() < deadline:
                    line = fh.readline()
                    if not line:
                        time.sleep(0.5)
                        break
                    position = fh.tell()
                    if pattern in line:
                        return
        time.sleep(0.5)
    raise TimeoutError(f"No log markers detected within {timeout:.0f}s: {pattern}")


def _format_admin_notice(reason: str) -> str:
    """Compose the notification text for administrator alerts."""

    return (
        "Master Restart health check failed\n"
        f"reason:{reason}\n"
        "Please log in to the server as soon as possible to troubleshoot (start.log / vibe.log)."
    )


def _notify_admins(reason: str) -> None:
    """Broadcast the failure reason to administrators if the master token exists."""

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


def main() -> int:
    """Command line entry point, only validates master readiness."""

    parser = argparse.ArgumentParser(description="Master post-launch health check (master only)")
    parser.add_argument("--master-log", default=str(DEFAULT_MASTER_LOG), help="Master log path")
    parser.add_argument(
        "--master-timeout",
        type=float,
        default=DEFAULT_TIMEOUT_MASTER,
        help="Master log wait timeout (seconds)",
    )
    args = parser.parse_args()

    master_log = Path(args.master_log)

    try:
        _wait_for_log_flag(master_log, MASTER_READY_MARKER, args.master_timeout)
    except Exception as exc:
        reason = str(exc)
        _notify_admins(reason)
        print(f"[healthcheck] fail: {reason}", file=sys.stderr)
        return 1
    else:
        print("[healthcheck] success: master ready, worker checks skipped by configuration")
        return 0


if __name__ == "__main__":
    sys.exit(main())
