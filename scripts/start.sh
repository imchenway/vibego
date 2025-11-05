#!/usr/bin/env bash
set -eo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MASTER_CONFIG_ROOT="${MASTER_CONFIG_ROOT:-$HOME/.config/vibego}"
RUNTIME_DIR="${VIBEGO_RUNTIME_ROOT:-$MASTER_CONFIG_ROOT/runtime}"
VENV_DIR="$RUNTIME_DIR/.venv"
LEGACY_VENV_DIR="$ROOT_DIR/.venv"
STATE_DIR="$MASTER_CONFIG_ROOT/state"
LOG_DIR="$MASTER_CONFIG_ROOT/logs"
LOCK_FILE="$STATE_DIR/master_restart.lock"
START_LOG="$LOG_DIR/start.log"
CODEX_STAMP_FILE="$STATE_DIR/npm_codex_install.stamp"
CODEX_INSTALL_TTL="${CODEX_INSTALL_TTL:-86400}"

# Unified restart signal file path: use the configuration directory instead of the code directory
# In this way, the master installed by pipx and the master run from the source code can share the same signal file.
export MASTER_RESTART_SIGNAL_PATH="$STATE_DIR/restart_signal.json"
export LOG_ROOT="${LOG_ROOT:-$LOG_DIR}"
if [[ -z "${LOG_FILE:-}" ]]; then
  export LOG_FILE="$LOG_DIR/vibe.log"
fi

log_line() {
  local ts
  ts=$(date '+%Y-%m-%d %H:%M:%S%z')
  printf '[%s] %s\n' "$ts" "$*"
}

log_info() {
  log_line "$@"
}

log_error() {
  log_line "$@" >&2
}

cleanup() {
  rm -f "$LOCK_FILE"
}

trap cleanup EXIT

cd "$ROOT_DIR"

mkdir -p "$(dirname "$LOCK_FILE")"
mkdir -p "$(dirname "$START_LOG")"
mkdir -p "$RUNTIME_DIR"
touch "$START_LOG"
exec >>"$START_LOG"
exec 2>&1

log_info "start.sh start, pid=$$"

if [[ -f "$LOCK_FILE" ]]; then
  log_error "Already have start.sh During execution, skip this startup."
  exit 1
fi

printf '%d\n' $$ > "$LOCK_FILE"

log_info "Lock file created:$LOCK_FILE"

ensure_codex_installed() {
  local need_install=1
  local now
  local codex_bin
  if ! command -v npm >/dev/null 2>&1; then
    log_error "npm not detected, executable missing, skipped @openai/codex Global installation"
    return
  fi

  log_info "npm version detected:$(npm --version)"

  if [[ ! "$CODEX_INSTALL_TTL" =~ ^[0-9]+$ ]]; then
    log_error "CODEX_INSTALL_TTL Illegal value:$CODEX_INSTALL_TTL, Fallback to 86400 seconds"
    CODEX_INSTALL_TTL=86400
  fi

  if (( need_install )); then
    codex_bin=$(command -v codex 2>/dev/null || true)
    if [[ -n "$codex_bin" ]]; then
      log_info "Detected existing codex binary at ${codex_bin}; skipping install (upgrade manually if needed)"
      need_install=0
    elif [[ -x "/opt/homebrew/bin/codex" ]]; then
      log_info "Detected existing codex binary at /opt/homebrew/bin/codex; skipping install (upgrade manually if needed)"
      need_install=0
    fi
  fi

  if (( need_install )) && [[ -f "$CODEX_STAMP_FILE" ]]; then
    local last_ts
    last_ts=$(cat "$CODEX_STAMP_FILE" 2>/dev/null || printf '0')
    if [[ "$last_ts" =~ ^[0-9]+$ ]]; then
      now=$(date +%s)
      local elapsed=$(( now - last_ts ))
      if (( elapsed < CODEX_INSTALL_TTL )); then
        local remaining=$(( CODEX_INSTALL_TTL - elapsed ))
        log_info "Previous install happened ${elapsed}s ago (cooldown ${CODEX_INSTALL_TTL}s); skipping install with ${remaining}s remaining"
        need_install=0
      fi
    fi
  fi

  if (( need_install )); then
    log_info "Start executing npm install -g @openai/codex@latest"
    if npm install -g @openai/codex@latest; then
      now=$(date +%s)
      printf '%s\n' "$now" > "$CODEX_STAMP_FILE"
      log_info "npm install -g @openai/codex@latest success"
    else
      local status=$?
      log_error "npm install -g @openai/codex@latest failed (exit code ${status}); continuing startup"
    fi
  fi
}

ensure_codex_installed

select_python_binary() {
  # Select a compatible CPython version; defaults accept 3.9-3.14 and can be overridden via env variables.
  local allow_py313="${VIBEGO_ALLOW_PY313:-}"
  local supported_max_minor="${VIBEGO_MAX_MINOR:-14}"
  local candidates=()
  local chosen=""
  local name
  if [[ -n "${VIBEGO_PYTHON:-}" ]]; then
    candidates+=("$VIBEGO_PYTHON")
  fi
  for name in python3.13 python3.12 python3.11 python3.10 python3.9 python3; do
    if [[ "${VIBEGO_PYTHON:-}" == "$name" ]]; then
      continue
    fi
    candidates+=("$name")
  done

  for name in "${candidates[@]}"; do
    if [[ -z "$name" ]]; then
      continue
    fi
    if ! command -v "$name" >/dev/null 2>&1; then
      continue
    fi
    local version_raw
    version_raw=$("$name" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null) || continue
    local major="${version_raw%%.*}"
    local minor="${version_raw#*.}"
    if [[ "$major" != "3" ]]; then
      log_line "jump over ${name} (Version ${version_raw}): Non-CPython 3.x" >&2
      continue
    fi
    local explicit_override=0
    if [[ -n "${VIBEGO_PYTHON:-}" && "$name" == "$VIBEGO_PYTHON" ]]; then
      explicit_override=1
    fi
    if [[ "$minor" =~ ^[0-9]+$ ]] && (( minor == 13 )) && (( explicit_override == 0 )); then
      if [[ "$allow_py313" == "0" ]]; then
        log_line "Skip ${name} (version ${version_raw}): disabled explicitly by VIBEGO_ALLOW_PY313=0" >&2
        continue
      fi
      log_line "Detected ${name} (version ${version_raw}): Python 3.13 accepted by default; use VIBEGO_ALLOW_PY313=1 to prefer or 0 to disable" >&2
    fi
    if [[ "$minor" =~ ^[0-9]+$ ]] && (( minor > supported_max_minor )) && (( explicit_override == 0 )); then
      log_line "Skip ${name} (version ${version_raw}): above supported ceiling 3.${supported_max_minor}; override with VIBEGO_MAX_MINOR if needed" >&2
      continue
    fi
    if [[ "$minor" =~ ^[0-9]+$ ]] && (( minor < 9 )); then
      log_line "jump over ${name} (Version ${version_raw}): less than 3.9, May be missing official wheels" >&2
      continue
    fi
    if [[ "$minor" =~ ^[0-9]+$ ]] && (( minor >= 14 )); then
      log_line "Detected ${name} (version ${version_raw}): ensure dependencies support this Python version" >&2
    fi
    chosen="$name"
    log_line "Using the Python interpreter:${chosen} (Version ${version_raw})" >&2
    break
  done

  if [[ -z "$chosen" ]]; then
    log_error "no satisfaction found <=3.13 The Python interpreter can be set by VIBEGO_PYTHON Specify path"
    exit 1
  fi

  printf '%s' "$chosen"
}

# Check whether Python dependencies are installed completely
check_deps_installed() {
  # Check if the virtual environment exists
  if [[ ! -d "$VENV_DIR" ]]; then
    log_info "The virtual environment does not exist and needs to be initialized."
    return 1
  fi

  # Check the virtual environment's Python interpreter
  if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    log_info "Virtual environment Python interpreter missing"
    return 1
  fi

  # Activate the virtual environment and check key dependency packages
  # aiogram: Telegram Botframe
  # aiohttp: Asynchronous HTTP client
  # aiosqlite: Asynchronous SQLite database
  if ! "$VENV_DIR/bin/python" -c "import aiogram, aiohttp, aiosqlite" 2>/dev/null; then
    log_info "Key dependency packages are missing or damaged"
    return 1
  fi

  log_info "The dependency check passed and the virtual environment is complete"
  return 0
}

# Robust function to clean up old master processes (improved version: supports PID files + pgrep Double insurance)
cleanup_old_master() {
  local max_wait=10  # Wait up to 10 seconds to exit gracefully
  local waited=0
  local old_pids=""
  local master_pid_file="$STATE_DIR/master.pid"

  # Option 1: Read from the PID file first
  if [[ -f "$master_pid_file" ]]; then
    local pid_from_file
    pid_from_file=$(cat "$master_pid_file" 2>/dev/null || true)
    if [[ "$pid_from_file" =~ ^[0-9]+$ ]]; then
      if kill -0 "$pid_from_file" 2>/dev/null; then
        old_pids="$pid_from_file"
        log_info "Old master instance detected from PID file (PID: $old_pids)"
      else
        log_info "PID The file exists but the process is no longer there, clean up expired PID files"
        rm -f "$master_pid_file"
      fi
    fi
  fi

  # Option 2: Use pgrep to find (supports multiple running modes)
  if [[ -z "$old_pids" ]]; then
    # Matching mode: supports source code running and pipx installation methods
    # - python.*master.py(Source code running)
    # - Python.*master.py(macOS Python on.app)
    # - bot.py(pipx installed master alias)
    local pgrep_pids
    pgrep_pids=$(pgrep -f "master\.py$" 2>/dev/null || true)
    if [[ -n "$pgrep_pids" ]]; then
      old_pids="$pgrep_pids"
      log_info "Old master instance detected via pgrep (PID: $old_pids)"
    fi
  fi

  # If neither method is found, it means there is no old process.
  if [[ -z "$old_pids" ]]; then
    log_info "Old master instance not detected"
    return 0
  fi

  # Start cleaning up old processes
  log_info "Gracefully terminating old master instance (PID: $old_pids)..."

  # Send SIGTERM signal to terminate gracefully
  for pid in $old_pids; do
    kill -15 "$pid" 2>/dev/null || true
  done

  # Loop waiting for process to exit
  while (( waited < max_wait )); do
    sleep 1
    ((waited++))

    # Check if all PIDs have exited
    local all_exited=1
    for pid in $old_pids; do
      if kill -0 "$pid" 2>/dev/null; then
        all_exited=0
        break
      fi
    done

    if (( all_exited )); then
      log_info "OK: The old master exited gracefully (elapsed ${waited}s)"
      rm -f "$master_pid_file"
      return 0
    fi
  done

  # Graceful termination timeout, execution forced end
  log_info "graceful termination timeout (${max_wait}Second), Execute forced end..."
  for pid in $old_pids; do
    kill -9 "$pid" 2>/dev/null || true
  done
  sleep 2

  # final check
  local remaining_pids=""
  for pid in $old_pids; do
    if kill -0 "$pid" 2>/dev/null; then
      remaining_pids="$remaining_pids $pid"
    fi
  done

  if [[ -n "$remaining_pids" ]]; then
    log_error "ERROR: Unable to clean up old master process (residual PID:$remaining_pids)"
    log_error "Please execute manually: kill -9$remaining_pids"
    exit 1
  fi

  log_info "OK: The old master instance has been forcefully cleaned"
  rm -f "$master_pid_file"
  return 0
}

# Call the cleanup function
cleanup_old_master

# Smart dependency management: install only when necessary
REQUIREMENTS_FILE="${VIBEGO_REQUIREMENTS_PATH:-$ROOT_DIR/scripts/requirements.txt}"
if [[ ! -f "$REQUIREMENTS_FILE" ]]; then
  log_error "Dependency files are missing: $REQUIREMENTS_FILE"
  exit 1
fi

PYTHON_BIN="$(select_python_binary)"

# Compatible with oldVersion: If it is detected in the warehouse .venv, then migrate to the runtime directory
migrate_legacy_venv() {
  if [[ -d "$LEGACY_VENV_DIR" && ! -e "$VENV_DIR" ]]; then
    log_info "Old virtual environment directory detected:$LEGACY_VENV_DIR, Prepare to migrate to $VENV_DIR"
    if mv "$LEGACY_VENV_DIR" "$VENV_DIR"; then
      log_info "The virtual environment has been migrated to:$VENV_DIR"
    else
      log_error "Migration of the old virtual environment failed. Please check manually and try again."
    fi
  fi
}

migrate_legacy_venv

# Check if dependencies need to be installed
if check_deps_installed; then
  log_info "Dependencies are installed and complete, jump overpip install(accelerated restart)"
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
else
  log_info "First startup or missing dependencies, installing dependencies..."

  # Create or rebuild a virtual environment
  "$PYTHON_BIN" -m venv "$VENV_DIR"
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"

  # Install dependencies
  # Redirect pip output to log file to avoid BrokenPipe errors
  log_info "Start executing pip install -r $REQUIREMENTS_FILE"
  PIP_LOG_FILE="$LOG_DIR/pip_install_$(date +%Y%m%d_%H%M%S).log"
  if pip install -r "$REQUIREMENTS_FILE" > "$PIP_LOG_FILE" 2>&1; then
    log_info "Dependency installation completed"
  else
    PIP_EXIT_CODE=$?
    log_error "pip install Failure, exit code=$PIP_EXIT_CODE, See details $PIP_LOG_FILE"
    # If it is BrokenPipe (Exit code 141), Verify that dependencies are actually installed
    if [[ $PIP_EXIT_CODE -eq 141 ]]; then
      log_info "BrokenPipe error detected, dependency integrity verified..."
      # Verify whether key dependency packages can be imported
      if "$VENV_DIR/bin/python" -c "import aiogram, aiohttp, aiosqlite" 2>/dev/null; then
        log_info "Dependency verification passed, BrokenPipe can be ignored and execution continues."
      else
        log_error "Dependency verification failed. Although it is BrokenPipe, the dependency is not completely installed."
        exit 1
      fi
    else
      # If there are other errors, exit directly.
      exit $PIP_EXIT_CODE
    fi
  fi
fi

# Start the master in the background, and the log falls in vibe.log
# Explicitly pass the restart flag environment variable if present
if [[ -n "${MASTER_RESTART_EXPECTED:-}" ]]; then
  log_info "Restart flag environment variable MASTER detected_RESTART_EXPECTED=$MASTER_RESTART_EXPECTED"
  export MASTER_RESTART_EXPECTED
fi

log_info "Prepare to start the master process..."

# Clean old error logs (keep the last 10)
cleanup_old_error_logs() {
  local error_log_pattern="$LOG_DIR/master_error_*.log"
  local error_logs
  error_logs=$(ls -t $error_log_pattern 2>/dev/null || true)
  if [[ -n "$error_logs" ]]; then
    local count=0
    while IFS= read -r logfile; do
      ((count++))
      if (( count > 10 )); then
        rm -f "$logfile"
        log_info "Cleaned old error logs: $logfile"
      fi
    done <<< "$error_logs"
  fi
}

cleanup_old_error_logs

# Create a timestamped error log file
MASTER_ERROR_LOG="$LOG_DIR/master_error_$(date +%Y%m%d_%H%M%S).log"
MASTER_STDOUT_LOG="$LOG_DIR/master_stdout.log"

# Explicitly pass environment variables to the nohup process to ensure that the restart signal file path is correct
# Using virtual environment Python interpreter, avoidVersionMismatch causes dependency loading to fail
# Important: Save stderr to a log file to facilitate troubleshooting startup failures.
MASTER_RESTART_SIGNAL_PATH="$MASTER_RESTART_SIGNAL_PATH" nohup "$VENV_DIR/bin/python" master.py > "$MASTER_STDOUT_LOG" 2> "$MASTER_ERROR_LOG" &
MASTER_PID=$!

# Robustness check: ensure processsuccessstart up
if [[ -z "${MASTER_PID:-}" ]]; then
  log_error "ERROR: Unable to obtain master process PID; startup failed"
  log_error "Possible reasons: python command is not available or master.py There are grammatical errors"
  exit 1
fi

# Check if the process is still running after a short wait
sleep 0.5
if ! kill -0 "$MASTER_PID" 2>/dev/null; then
  log_error "ERROR: Master process exited immediately after starting (PID=$MASTER_PID)"
  log_error "Check, please:"
  log_error "  - master.py whetherThere are grammatical errors: python master.py"
  log_error "  - Are dependencies complete?: pip list | grep aiogram"
  log_error "  - error log: $MASTER_ERROR_LOG"

  # outputerror logthe end 20 OK, Help locate problems quickly
  if [[ -s "$MASTER_ERROR_LOG" ]]; then
    log_error ""
    log_error "=== error logat last 20 OK ==="
    tail -20 "$MASTER_ERROR_LOG" | while IFS= read -r line; do
      log_error "  $line"
    done
    log_error "=========================="
  else
    log_error "error logFile is empty, It may be an environment variable or path problem"
  fi

  exit 1
fi

log_info "master Started in background, PID=$MASTER_PID, Log writing ${LOG_FILE}"

# Health check: wait for master to come online and verify key workers
log_info "Start master readiness check..."
HEALTHCHECK_START=$(date +%s)

if python scripts/master_healthcheck.py --master-log "$LOG_FILE"; then
  HEALTHCHECK_END=$(date +%s)
  HEALTHCHECK_DURATION=$((HEALTHCHECK_END - HEALTHCHECK_START))
  log_info "OK: Master readiness confirmed (elapsed ${HEALTHCHECK_DURATION}s)"
else
  HEALTHCHECK_END=$(date +%s)
  HEALTHCHECK_DURATION=$((HEALTHCHECK_END - HEALTHCHECK_START))
  log_error "⚠️ master Health check failed, time consuming ${HEALTHCHECK_DURATION}s"
  log_error "It is recommended to check:"
  log_error "  - process status: ps aux | grep 'python.*master.py'"
  log_error "  - Startup log: tail -100 $LOG_DIR/start.log"
  log_error "  - Run log: tail -100 $LOG_FILE"
  log_error "  - Process PID: $MASTER_PID"

  # Check if the process is still running
  if kill -0 "$MASTER_PID" 2>/dev/null; then
    log_info "master The process is still running (PID=$MASTER_PID), Allow startup to continue"
    log_info "⚠️ Please manually verify that the service is working properly"
  else
    log_error "ERROR: Master process exited and failed to start"
    exit 1
  fi
fi
