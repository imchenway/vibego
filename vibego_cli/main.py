"""vibego 命令入口与子命令实现。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
from datetime import datetime, timezone
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from . import config
from .deps import check_cli_dependencies, install_requirements, python_version_ok
from command_center import (
    CommandAliasConflictError,
    CommandAlreadyExistsError,
    CommandService,
    DEFAULT_GLOBAL_COMMANDS,
    REMOVED_GLOBAL_COMMAND_NAMES,
    GLOBAL_COMMAND_PROJECT_SLUG,
    GLOBAL_COMMAND_SCOPE,
    resolve_global_command_db,
)
from project_repository import ProjectRepository


TOKEN_PATTERN = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{20,}$")
BOTFATHER_URL = "https://core.telegram.org/bots#botfather"
START_SIGNAL_PATH = config.STATE_DIR / "start_signal.json"
TELEGRAM_BOT_LIMITATION_DOC = "https://core.telegram.org/bots/faq#how-can-i-message-a-user"

def _find_repo_root() -> Path:
    """推导当前仓库根目录。"""

    return config.PACKAGE_ROOT


def _prompt_token(default: Optional[str] = None) -> str:
    """交互式获取 Telegram Bot Token。"""

    prompt = "请输入 Master Bot 的 Token"
    if default:
        prompt += f"（直接回车沿用当前值）"
    prompt += "："
    while True:
        value = input(prompt).strip()
        if not value and default:
            value = default
        if not value:
            print("Token 不能为空。若尚未创建 Bot，请参考官方指引：", BOTFATHER_URL)
            continue
        if TOKEN_PATTERN.match(value):
            return value
        print("Token 格式看起来不正确，请确认后重试。官方获取方式：", BOTFATHER_URL)


def _ensure_projects_assets() -> None:
    """保证 projects.json 与 master.db 均已初始化。"""

    config.ensure_directories()
    if not config.PROJECTS_JSON.exists():
        config.PROJECTS_JSON.write_text("[]\n", encoding="utf-8")
    ProjectRepository(config.MASTER_DB, config.PROJECTS_JSON)


def _virtualenv_paths(venv_dir: Path) -> Tuple[Path, Path]:
    """返回虚拟环境的 python 与 pip 路径。"""

    if os.name == "nt":
        bin_dir = venv_dir / "Scripts"
    else:
        bin_dir = venv_dir / "bin"
    return bin_dir / "python", bin_dir / "pip"


def _ensure_virtualenv(repo_root: Path) -> Tuple[Path, Path]:
    """在 runtime 目录中创建/升级虚拟环境并安装依赖。"""

    venv_dir = config.RUNTIME_DIR / "venv"
    python_exec, pip_exec = _virtualenv_paths(venv_dir)

    def _create_runtime_venv() -> None:
        """创建运行时虚拟环境。"""

        subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)

    def _recreate_runtime_venv(reason: str) -> None:
        """删除并重建运行时虚拟环境。

        运行时 venv 仅用于运行 master/worker，若发现解释器断链或 pip 缺失，
        通常是 Homebrew 升级 Python 后旧路径失效导致。此时直接重建最稳妥。
        """

        print("检测到运行时虚拟环境异常，将自动重建：", reason)
        print("目标目录：", venv_dir)
        shutil.rmtree(venv_dir, ignore_errors=True)
        print("正在重新创建虚拟环境:", venv_dir)
        _create_runtime_venv()

    if not venv_dir.exists():
        print("正在创建虚拟环境:", venv_dir)
        _create_runtime_venv()

    if not python_exec.exists() or not pip_exec.exists():
        details: list[str] = []
        if not python_exec.exists():
            details.append(f"python 缺失或断链：{python_exec}")
        if not pip_exec.exists():
            details.append(f"pip 缺失或断链：{pip_exec}")
        _recreate_runtime_venv("；".join(details))
        python_exec, pip_exec = _virtualenv_paths(venv_dir)
    if not python_exec.exists():
        raise RuntimeError(f"未找到虚拟环境 python: {python_exec}")
    if not pip_exec.exists():
        raise RuntimeError(f"未找到虚拟环境 pip: {pip_exec}")

    marker = venv_dir / ".requirements.timestamp"
    req_file = config.ensure_worker_requirements_copy()
    if not marker.exists() or req_file.stat().st_mtime > marker.stat().st_mtime:
        print("正在安装 Python 依赖（该步骤可能耗时）...")
        install_requirements(req_file, pip_executable=pip_exec)
        marker.touch()
    return python_exec, pip_exec


def _collect_auto_start_targets(env_values: Dict[str, str]) -> list[int]:
    """根据 .env 内容挑选需要推送启动提醒的 chat_id。"""

    targets: list[int] = []

    def _append(value: str) -> None:
        cleaned = (value or "").strip()
        if cleaned.isdigit():
            targets.append(int(cleaned))

    primary = env_values.get("MASTER_CHAT_ID", "")
    _append(primary)

    candidates = []
    for key in ("MASTER_ADMIN_IDS", "MASTER_ADMINS", "MASTER_WHITELIST", "ALLOWED_CHAT_ID"):
        raw = env_values.get(key)
        if raw:
            candidates.extend(item.strip() for item in raw.split(","))
    for item in candidates:
        _append(item)

    deduped: list[int] = []
    seen: set[int] = set()
    for chat_id in targets:
        if chat_id in seen:
            continue
        seen.add(chat_id)
        deduped.append(chat_id)
    return deduped


def _load_known_master_chats() -> set[int]:
    """读取 master_state.json 中记录的 chat_id，用于判断是否已与 Bot 建立会话。"""

    state_path = config.MASTER_STATE
    if not state_path.exists():
        return set()
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()
    if not isinstance(raw, dict):
        return set()
    chats: set[int] = set()
    for item in raw.values():
        if not isinstance(item, dict):
            continue
        chat_id = item.get("chat_id")
        if isinstance(chat_id, int):
            chats.add(chat_id)
        elif isinstance(chat_id, str) and chat_id.isdigit():
            chats.add(int(chat_id))
    return chats


def _write_start_signal(chat_ids: Sequence[int]) -> None:
    """将启动通知请求写入 state 目录，供 master 启动后读取。"""

    payload = {
        "chat_ids": list(chat_ids),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    START_SIGNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = START_SIGNAL_PATH.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temp_path, START_SIGNAL_PATH)


def _schedule_start_notification(env_values: Dict[str, str]) -> bool:
    """在成功启动 master 后安排一次自动 /start 推送。"""

    targets = _collect_auto_start_targets(env_values)
    if not targets:
        print("提示：未检测到 MASTER_CHAT_ID 或管理员列表，无法自动推送 /start。")
        return False

    known_chats = _load_known_master_chats()
    if not known_chats:
        print(
            "提示：尚未检测到任何已登记的 chat_id，请先在 Telegram 中对 Bot 发送 /start，"
            f"否则平台将拒绝主动消息（参考官方限制：{TELEGRAM_BOT_LIMITATION_DOC}）。"
        )
        return False

    missing = [str(chat_id) for chat_id in targets if chat_id not in known_chats]
    if len(missing) == len(targets):
        print(
            "提示：自动推送目标尚未与 Bot 建立对话，将不会发送。请先手动 /start 后再执行 `vibego start`。"
        )
        print("涉及 chat_id：", ", ".join(missing))
        print(f"Telegram 限制说明：{TELEGRAM_BOT_LIMITATION_DOC}")
        return False

    if missing:
        print(
            "警告：以下 chat_id 仍未与 Bot 建立对话，自动 /start 推送将跳过："
            + ", ".join(missing)
        )
        print(f"请参考 Telegram 限制：{TELEGRAM_BOT_LIMITATION_DOC}")

    eligible = [chat_id for chat_id in targets if chat_id in known_chats]
    try:
        _write_start_signal(eligible)
    except OSError as exc:
        print("自动推送信号写入失败：", exc)
        return False
    print("已安排 master 启动通知，将推送到 chat_id:", ", ".join(str(t) for t in eligible))
    return True


async def _seed_default_global_commands() -> None:
    """写入默认通用命令，避免新环境手动维护。"""

    db_path = resolve_global_command_db(config.CONFIG_ROOT)
    service = CommandService(
        db_path,
        GLOBAL_COMMAND_PROJECT_SLUG,
        scope=GLOBAL_COMMAND_SCOPE,
        history_project_slug=GLOBAL_COMMAND_PROJECT_SLUG,
    )
    await service.initialize()
    for legacy_name in REMOVED_GLOBAL_COMMAND_NAMES:
        try:
            existing = await service.resolve_by_trigger(legacy_name)
        except Exception as exc:  # noqa: BLE001
            print(f"查询废弃通用命令 {legacy_name} 失败：{exc}")
            continue
        if existing is None:
            continue
        try:
            await service.delete_command(existing.id)
            print(f"已删除废弃通用命令：{legacy_name}")
        except Exception as exc:  # noqa: BLE001
            print(f"删除废弃通用命令 {legacy_name} 失败：{exc}")

    for payload in DEFAULT_GLOBAL_COMMANDS:
        name = str(payload["name"])
        existing = await service.resolve_by_trigger(name)
        if existing:
            continue
        try:
            await service.create_command(**payload)
            print(f"已注入通用命令：{name}")
        except (CommandAlreadyExistsError, CommandAliasConflictError) as exc:
            print(f"跳过通用命令 {name}：{exc}")


def command_init(args: argparse.Namespace) -> None:
    """实现 `vibego init`。"""

    config.ensure_directories()
    env_values = config.parse_env_file(config.ENV_FILE)

    if not python_version_ok():
        print("警告：当前 Python 版本低于 3.11，建议升级以获得完整功能。")

    missing = check_cli_dependencies()
    if missing:
        print("依赖检查发现缺失项：")
        for item in missing:
            print("-", item)
        print("请先安装上述依赖后再执行初始化。")
        return

    if config.ENV_FILE.exists() and not args.force:
        print("检测到已存在的 .env。若需覆盖请使用 --force。")
    default_token = env_values.get("MASTER_BOT_TOKEN")
    token = args.token or _prompt_token(default_token)
    env_values["MASTER_BOT_TOKEN"] = token
    env_values.setdefault("MASTER_WHITELIST", "")
    env_values["MASTER_CONFIG_ROOT"] = str(config.CONFIG_ROOT)
    env_values.setdefault("MASTER_ADMINS", "")
    env_values.setdefault("TELEGRAM_PROXY", "")

    config.dump_env_file(config.ENV_FILE, env_values)
    _ensure_projects_assets()
    try:
        asyncio.run(_seed_default_global_commands())
    except Exception as exc:  # noqa: BLE001
        print("默认通用命令初始化失败：", exc)

    print("初始化完成，配置目录：", config.CONFIG_ROOT)
    print("可执行步骤：")
    print(f"  1. 如需代理，请编辑 {config.ENV_FILE} 调整相关变量。")
    print("  2. 运行 `vibego start` 启动 master 服务。")


def _load_env_or_fail() -> Dict[str, str]:
    """读取 .env，若不存在则提示用户执行 init。"""

    if not config.ENV_FILE.exists():
        raise RuntimeError(f"未检测到 {config.ENV_FILE}，请先执行 `vibego init`。")
    values = config.parse_env_file(config.ENV_FILE)
    if "MASTER_BOT_TOKEN" not in values or not values["MASTER_BOT_TOKEN"].strip():
        raise RuntimeError("MASTER_BOT_TOKEN 未定义，请重新执行 `vibego init`。")
    return values


def _build_master_env(base_env: Dict[str, str]) -> Dict[str, str]:
    """组装 master.py 所需的环境变量。"""

    env = os.environ.copy()
    env.update(base_env)
    env["MASTER_BOT_TOKEN"] = base_env["MASTER_BOT_TOKEN"].strip()
    env["MASTER_PROJECTS_PATH"] = str(config.PROJECTS_JSON)
    env["MASTER_PROJECTS_DB_PATH"] = str(config.MASTER_DB)
    env["MASTER_STATE_PATH"] = str(config.MASTER_STATE)
    env["MASTER_RESTART_SIGNAL_PATH"] = str(config.RESTART_SIGNAL_PATH)
    env["LOG_ROOT"] = str(config.LOG_DIR)
    env["TASKS_DATA_ROOT"] = str(config.DATA_DIR)
    env["LOG_FILE"] = str(config.LOG_FILE)
    env["MASTER_ENV_FILE"] = str(config.ENV_FILE)
    env["VIBEGO_PACKAGE_ROOT"] = str(config.PACKAGE_ROOT)
    env["VIBEGO_RUNTIME_ROOT"] = str(config.RUNTIME_DIR)
    requirements_path = config.ensure_worker_requirements_copy()
    env["VIBEGO_REQUIREMENTS_PATH"] = str(requirements_path)
    env.setdefault("MASTER_WHITELIST", base_env.get("MASTER_WHITELIST", ""))
    if base_env.get("TELEGRAM_PROXY"):
        env["TELEGRAM_PROXY"] = base_env["TELEGRAM_PROXY"]
    return env


def _write_pid(pid: int) -> None:
    """记录 master 进程 PID。"""

    config.MASTER_PID_FILE.write_text(str(pid), encoding="utf-8")


def _read_pid() -> Optional[int]:
    """读取 master 进程 PID。"""

    if not config.MASTER_PID_FILE.exists():
        return None
    raw = config.MASTER_PID_FILE.read_text(encoding="utf-8").strip()
    return int(raw) if raw.isdigit() else None


def _pid_alive(pid: int) -> bool:
    """检查进程是否仍在运行。"""

    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _collect_active_workers() -> List[Tuple[int, Path]]:
    """扫描日志目录下的 bot.pid，返回仍在运行的 worker。"""

    active: List[Tuple[int, Path]] = []
    if not config.LOG_DIR.exists():
        return active
    for pid_file in config.LOG_DIR.rglob("bot.pid"):
        try:
            raw = pid_file.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not raw.isdigit():
            continue
        pid = int(raw)
        if pid <= 0:
            continue
        if _pid_alive(pid):
            active.append((pid, pid_file))
        else:
            pid_file.unlink(missing_ok=True)
    return active


def _format_worker_label(pid: int, pid_path: Path) -> str:
    """格式化输出 worker 所属项目。"""

    try:
        relative = pid_path.parent.relative_to(config.LOG_DIR)
    except ValueError:
        relative = pid_path.parent
    return f"{relative} (pid={pid})"


def _stop_all_workers_via_script() -> None:
    """调用 scripts/stop_bot.sh 清理残余 worker。"""

    script_path = _find_repo_root() / "scripts/stop_bot.sh"
    if not script_path.exists():
        return
    env = os.environ.copy()
    env.setdefault("MASTER_CONFIG_ROOT", str(config.CONFIG_ROOT))
    env.setdefault("VIBEGO_CONFIG_DIR", str(config.CONFIG_ROOT))
    try:
        subprocess.run(
            [str(script_path)],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        pass


def _list_vibego_processes() -> List[Tuple[int, str]]:
    """列出系统中所有与 vibego 相关的 Python 进程。"""

    try:
        proc = subprocess.run(
            ["ps", "-eo", "pid=,args="],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return []

    result: List[Tuple[int, str]] = []
    for raw in proc.stdout.decode().splitlines():
        line = raw.strip()
        if not line or "vibego" not in line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue
        pid_str, cmd = parts
        if not pid_str.isdigit():
            continue
        pid = int(pid_str)
        if (
            "bot.py" in cmd
            or "master.py" in cmd
            or "session_binder.py" in cmd
            or "log_writer.py" in cmd
        ):
            result.append((pid, cmd))
    return result


def _find_foreign_processes(allowed_root: Path) -> List[Tuple[int, str]]:
    """查找命令行不包含 allowed_root 的 vibego 进程。"""

    allowed = str(allowed_root)
    candidates = []
    for pid, cmd in _list_vibego_processes():
        if allowed and allowed in cmd:
            continue
        candidates.append((pid, cmd))
    return candidates


def _terminate_processes(pids: Sequence[int]) -> None:
    """对给定 pid 发送 SIGTERM/SIGKILL。"""

    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            continue
    for pid in pids:
        try:
            os.kill(pid, 0)
        except OSError:
            continue
        time.sleep(0.5)
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def _kill_all_vibego_processes() -> None:
    """终止系统中所有 vibego 相关进程。"""

    processes = _list_vibego_processes()
    if not processes:
        return
    _terminate_processes([pid for pid, _ in processes])


def command_start(args: argparse.Namespace) -> None:
    """实现 `vibego start`。"""

    env_values = _load_env_or_fail()
    _ensure_projects_assets()

    foreign_workers = _find_foreign_processes(config.PACKAGE_ROOT)
    if foreign_workers:
        print("检测到系统中存在其他 vibego 进程，无法重复启动：")
        for pid, cmd in foreign_workers:
            print(f" - pid={pid} cmd={cmd}")
        print("请先执行 `vibego stop` 或手动终止上述进程后再重试。")
        return

    active_workers = _collect_active_workers()
    if active_workers:
        print("检测到以下 worker 仍在运行，无法重复启动：")
        for pid, pid_path in active_workers:
            print(" -", _format_worker_label(pid, pid_path))
        print("请执行 `vibego stop` 或运行 scripts/stop_bot.sh 清理后再尝试。")
        return

    if not python_version_ok():
        raise RuntimeError(
            "当前 Python 版本为 3.11 以下，无法运行 master。"
            "请通过 `brew install python@3.11` 或其他方式升级后重试。"
        )

    missing = check_cli_dependencies()
    if missing:
        print("依赖检查发现缺失项：")
        for item in missing:
            print("-", item)
        print("请补充依赖后重新执行。")
        return

    repo_root = _find_repo_root()
    try:
        python_exec, _ = _ensure_virtualenv(repo_root)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"虚拟环境初始化失败：{exc}") from exc

    master_env = _build_master_env(env_values)
    log_file = config.LOG_FILE
    log_file.parent.mkdir(parents=True, exist_ok=True)

    if _read_pid():
        print("检测到 master 已启动，如需重启请先执行 `vibego stop`。")
        return

    print("正在启动 master 服务...")
    with open(log_file, "a", encoding="utf-8") as log_fp:
        process = subprocess.Popen(
            [str(python_exec), "master.py"],
            cwd=str(repo_root),
            env=master_env,
            stdout=log_fp,
            stderr=log_fp,
            start_new_session=True,
        )
    _write_pid(process.pid)

    time.sleep(2)
    if process.poll() is not None:
        raise RuntimeError("master 进程启动失败，请检查日志。")

    print("master 已启动，PID:", process.pid)
    print("日志文件：", log_file)
    print("请在 Telegram 中向 Bot 发送 /start 以完成授权流程。")
    _schedule_start_notification(env_values)


def command_stop(args: argparse.Namespace) -> None:
    """实现 `vibego stop`。"""

    pid = _read_pid()
    if not pid:
        print("未检测到正在运行的 master。仍将尝试清理遗留 worker。")
    else:
        print("正在停止 master（PID =", pid, ")...")
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            print("进程不存在，视为已停止。")
        else:
            for _ in range(20):
                time.sleep(0.5)
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
            else:
                print("master 未在预期时间内退出，如仍存在请手动检查。")
        config.MASTER_PID_FILE.unlink(missing_ok=True)

    print("正在停止所有 worker...")
    _stop_all_workers_via_script()
    _kill_all_vibego_processes()
    print("停止完成。")


def command_status(args: argparse.Namespace) -> None:
    """实现 `vibego status`。"""

    env_values = config.parse_env_file(config.ENV_FILE)
    pid = _read_pid()
    running = False
    if pid:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            running = False
        else:
            running = True

    status = {
        "config_root": str(config.CONFIG_ROOT),
        "env_exists": config.ENV_FILE.exists(),
        "projects_json": config.PROJECTS_JSON.exists(),
        "master_db": config.MASTER_DB.exists(),
        "master_pid": pid,
        "master_running": running,
        "log_file": str(config.LOG_FILE),
        "token_configured": bool(env_values.get("MASTER_BOT_TOKEN")),
        "master_chat_id": env_values.get("MASTER_CHAT_ID"),
        "master_user_id": env_values.get("MASTER_USER_ID"),
    }
    print(json.dumps(status, indent=2, ensure_ascii=False))


def command_doctor(args: argparse.Namespace) -> None:
    """实现 `vibego doctor`，输出自检结果。"""

    report = {
        "python_version": sys.version,
        "python_ok": python_version_ok(),
        "dependencies": check_cli_dependencies(),
        "config_root": str(config.CONFIG_ROOT),
        "env_exists": config.ENV_FILE.exists(),
        "projects_json_exists": config.PROJECTS_JSON.exists(),
        "master_db_exists": config.MASTER_DB.exists(),
        "master_chat_id": config.parse_env_file(config.ENV_FILE).get("MASTER_CHAT_ID"),
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))


def command_seed_commands(args: argparse.Namespace) -> None:
    """实现 `vibego commands-seed`，手动注入默认通用命令。"""

    config.ensure_directories()
    try:
        asyncio.run(_seed_default_global_commands())
    except Exception as exc:  # noqa: BLE001
        print("默认通用命令注入失败：", exc)
        return
    print("默认通用命令注入完成。")


def build_parser() -> argparse.ArgumentParser:
    """构建最外层 argparse 解析器。"""

    parser = argparse.ArgumentParser(prog="vibego", description="vibego CLI 工具")
    parser.add_argument(
        "--config-dir",
        dest="config_dir",
        help="自定义配置目录（默认 ~/.config/vibego）",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="初始化配置目录与 master token")
    init_parser.add_argument("--token", help="直接传入 master bot token，避免交互输入")
    init_parser.add_argument("--force", action="store_true", help="覆盖已有 .env 配置")
    init_parser.set_defaults(func=command_init)

    start_parser = subparsers.add_parser("start", help="启动 master 服务")
    start_parser.set_defaults(func=command_start)

    stop_parser = subparsers.add_parser("stop", help="停止 master 服务")
    stop_parser.set_defaults(func=command_stop)

    status_parser = subparsers.add_parser("status", help="查看当前运行状态")
    status_parser.set_defaults(func=command_status)

    doctor_parser = subparsers.add_parser("doctor", help="运行依赖与配置自检")
    doctor_parser.set_defaults(func=command_doctor)

    seed_parser = subparsers.add_parser("commands-seed", help="注入默认通用命令（可重复执行）")
    seed_parser.set_defaults(func=command_seed_commands)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> None:
    """CLI 主入口。"""

    parser = build_parser()
    args = parser.parse_args(argv)
    if args.config_dir:
        os.environ["VIBEGO_CONFIG_DIR"] = args.config_dir
        # 重新加载路径设置
        from importlib import reload

        reload(config)

    try:
        args.func(args)
    except Exception as exc:  # pylint: disable=broad-except
        print("执行失败：", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
