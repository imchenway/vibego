from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "start_tmux_codex.sh"


def _run_start_tmux_dry_run(tmp_path: Path, *, model_name: str, model_cmd: str) -> subprocess.CompletedProcess[str]:
    workdir = tmp_path / "workdir"
    sessions = tmp_path / "sessions"
    logs = tmp_path / "logs"
    workdir.mkdir()
    sessions.mkdir()
    logs.mkdir()

    env = os.environ.copy()
    env.update(
        {
            "MODEL_NAME": model_name,
            "MODEL_CMD": model_cmd,
            "MODEL_WORKDIR": str(workdir),
            "MODEL_SESSION_ROOT": str(sessions),
            "MODEL_SESSION_GLOB": "events.jsonl",
            "LOG_ROOT": str(logs),
            "TMUX_LOG": str(logs / "model.log"),
            "SESSION_POINTER_FILE": str(logs / "current_session.txt"),
            "SESSION_ACTIVE_ID_FILE": str(logs / "active_session_id.txt"),
            "SESSION_BINDER_LOG": str(logs / "session_binder.log"),
            "SESSION_BINDER_PID_FILE": str(logs / "session_binder.pid"),
            "PROJECT_NAME": "demo",
            "TMUX_SESSION": "vibe-demo",
            "VIBEGO_AGENTS_SYNCED": "1",
            "PYTHON_EXEC": sys.executable,
            "DISABLE_UPDATE_PROMPT": "true",
        }
    )
    return subprocess.run(
        ["bash", str(SCRIPT), "--dry-run"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
        env=env,
    )


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux 未安装")
def test_start_tmux_dry_run_keeps_codex_config_flags(tmp_path: Path) -> None:
    """Codex dry-run 应继续附带 model_instructions/project_doc 配置。"""

    result = _run_start_tmux_dry_run(
        tmp_path,
        model_name="codex",
        model_cmd="codex --dangerously-bypass-approvals-and-sandbox -c trusted_workspace=true",
    )

    combined = result.stdout + result.stderr
    assert "-c model_instructions_file=" in combined
    assert "-c project_doc_max_bytes=131072" in combined


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux 未安装")
def test_start_tmux_dry_run_does_not_append_codex_flags_for_copilot(tmp_path: Path) -> None:
    """Copilot dry-run 不应被错误追加 Codex 专属 -c 参数。"""

    result = _run_start_tmux_dry_run(
        tmp_path,
        model_name="copilot",
        model_cmd="copilot --yolo",
    )

    combined = result.stdout + result.stderr
    assert "copilot --yolo" in combined
    assert "model_instructions_file=" not in combined
    assert "project_doc_max_bytes=" not in combined


def test_copilot_model_script_defaults_to_yolo() -> None:
    """Copilot 模型脚本默认应使用 --yolo 启动。"""

    result = subprocess.run(
        [
            "bash",
            "-lc",
            'set -euo pipefail; ROOT_DIR="$PWD"; unset MODEL_CMD COPILOT_CMD; '
            'source scripts/models/common.sh; source scripts/models/copilot.sh; '
            'model_configure; printf "%s\\n" "$MODEL_CMD"',
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    assert result.stdout.strip() == "copilot --yolo"
