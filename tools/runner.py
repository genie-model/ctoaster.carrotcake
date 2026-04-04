"""
tools/runner.py
---------------
Runner pod entrypoint for ctoaster science runs.

This module runs INSIDE a Kubernetes Job pod — never in the API pod.
Invoked as:   python -m tools.runner

Environment variables (injected by k8s_jobs.create_runner_job):
  RUN_ID          UUID hex identifying this run (e.g. "abc123def456...")
  JOB_ID          Integer PK of the jobs row in the DB
  USER_ID         Integer PK of the users row in the DB
  JOB_NAME        Human-readable job name (validated, filesystem-safe)
  DB_URL          Database connection string (Postgres or SQLite)
  FILESTORE_ROOT  Mount path of the shared Filestore NFS volume
  WORKSPACE_ROOT  Local emptyDir mount path (default: /workspace)
  CTOASTER_SYNC_INTERVAL_SECONDS  How often to sync back (default: 2.0)

Execution flow:
  1. Mark run RUNNING in DB
  2. Stage job folder from Filestore → local workspace (emptyDir)
  3. Copy carrotcake.exe from MODELS on Filestore → workspace
  4. If resuming from PAUSED, write GUI_RESTART command to workspace
  5. Launch carrotcake-ship.exe (blocks until process exits)
  6. Background thread: every SYNC_INTERVAL seconds
       - sync workspace → Filestore
       - update DB heartbeat + actual_state from status file
       - honour PAUSE_REQUESTED / CANCEL_REQUESTED desired_state
  7. On process exit: final sync, final DB update
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess as sp
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Optional

# ---------------------------------------------------------------------------
# Logging (plain stdout — captured by kubectl logs / Cloud Logging)
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [runner] %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Read required env vars before any module-level DB imports
# (DB_URL and FILESTORE_ROOT must already be set at import time by tools/db.py
#  and tools/storage.py at their own module level, but we force-check here.)
# ---------------------------------------------------------------------------

def _require_env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        sys.exit(f"[runner] FATAL: required environment variable {name!r} is not set.")
    return val


RUN_ID       = _require_env("RUN_ID")
JOB_ID       = int(_require_env("JOB_ID"))
USER_ID      = int(_require_env("USER_ID"))
JOB_NAME     = _require_env("JOB_NAME")

WORKSPACE_ROOT    = os.environ.get("WORKSPACE_ROOT", "/workspace").strip()
SYNC_INTERVAL     = float(os.environ.get("CTOASTER_SYNC_INTERVAL_SECONDS", "2.0"))


# ---------------------------------------------------------------------------
# DB / storage imports (after env vars so DB_URL / FILESTORE_ROOT are set)
# ---------------------------------------------------------------------------

from tools.db import get_run_by_id, init_db, update_run
from tools.storage import get_exe_path, get_job_path, sync_to_shared


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_status(job_dir: str) -> Optional[list]:
    """
    Return the first line of the status file split into tokens, or None.
    Retries briefly to handle transient NFS latency on the emptyDir copy.
    """
    status_path = os.path.join(job_dir, "status")
    for attempt in range(50):
        try:
            if attempt:
                time.sleep(0.02)
            with open(status_path) as f:
                parts = f.readline().strip().split()
                if parts:
                    return parts
        except IOError:
            pass
    return None


def _desired_state_from_db() -> Optional[str]:
    """Fetch the desired_state column for this run from the DB."""
    try:
        run = get_run_by_id(RUN_ID)
        return run.get("desired_state") if run else None
    except Exception as exc:
        logger.warning(f"Could not read desired_state from DB: {exc}")
        return None


# ---------------------------------------------------------------------------
# Background sync loop
# ---------------------------------------------------------------------------

def _sync_loop(
    workspace_path: str,
    shared_path: str,
    stop_event: threading.Event,
    process: sp.Popen,
) -> None:
    """
    Runs in a daemon thread while the science process is alive.

    Every SYNC_INTERVAL seconds:
      1. Sync workspace → Filestore so logs/status/output are visible to the API.
      2. Update DB: heartbeat_at and actual_state (read from workspace status file).
      3. If desired_state == PAUSE_REQUESTED  → write PAUSE to workspace command file.
      4. If desired_state == CANCEL_REQUESTED → SIGTERM the process.
    """
    while not stop_event.is_set():
        # ── 1. sync ──────────────────────────────────────────────────────────
        try:
            sync_to_shared(workspace_path, shared_path)
        except Exception as exc:
            logger.warning(f"Periodic sync failed: {exc}")

        # ── 2. heartbeat + actual_state ───────────────────────────────────────
        try:
            parts = _read_status(workspace_path)
            actual = parts[0] if parts else "RUNNING"
            update_run(RUN_ID, heartbeat_at=_now(), actual_state=actual)
        except Exception as exc:
            logger.warning(f"DB heartbeat update failed: {exc}")

        # ── 3 & 4. desired_state signals ─────────────────────────────────────
        desired = _desired_state_from_db()

        if desired == "PAUSE_REQUESTED":
            command_path = os.path.join(workspace_path, "command")
            try:
                with open(command_path, "w") as f:
                    f.write("PAUSE\n")
                logger.info("Wrote PAUSE command to workspace; Fortran will pause gracefully")
                # Transition to PAUSING so we don't write PAUSE repeatedly
                try:
                    update_run(RUN_ID, desired_state="PAUSING")
                except Exception:
                    pass
            except Exception as exc:
                logger.warning(f"Could not write PAUSE command: {exc}")

        elif desired == "CANCEL_REQUESTED":
            logger.info("CANCEL requested — sending SIGTERM to science process")
            try:
                process.terminate()
            except Exception as exc:
                logger.warning(f"Could not terminate process: {exc}")
            # Mark so we don't send SIGTERM on every loop iteration
            try:
                update_run(RUN_ID, desired_state="CANCELLING")
            except Exception:
                pass

        stop_event.wait(SYNC_INTERVAL)


# ---------------------------------------------------------------------------
# Main runner logic
# ---------------------------------------------------------------------------

def run() -> None:
    logger.info(
        f"Runner starting — run_id={RUN_ID}  job_name={JOB_NAME}  "
        f"user_id={USER_ID}  workspace_root={WORKSPACE_ROOT}"
    )

    # ── Step 1: initialise DB and mark run as RUNNING ─────────────────────────
    init_db()

    pod_name = os.environ.get("HOSTNAME", "unknown-pod")
    workspace_path = os.path.join(WORKSPACE_ROOT, RUN_ID, JOB_NAME)

    update_run(
        RUN_ID,
        actual_state="RUNNING",
        k8s_pod_name=pod_name,
        started_at=_now(),
        workspace_hint=workspace_path,
    )

    # ── Step 2: resolve shared Filestore path ────────────────────────────────
    try:
        shared_path = get_job_path(USER_ID, JOB_NAME)
    except Exception as exc:
        _fail(f"Could not resolve job path: {exc}")

    if not os.path.isdir(shared_path):
        _fail(f"Shared job path not found on Filestore: {shared_path}")

    # ── Step 3: stage job to local workspace (emptyDir) ──────────────────────
    logger.info(f"Staging {shared_path!r} → {workspace_path!r}")
    try:
        os.makedirs(os.path.dirname(workspace_path), exist_ok=True)
        shutil.copytree(shared_path, workspace_path, dirs_exist_ok=True)
    except Exception as exc:
        _fail(f"Failed to stage job to workspace: {exc}")

    # ── Step 4: copy executable from MODELS on Filestore ─────────────────────
    exe_src = get_exe_path()
    runexe = os.path.join(workspace_path, "carrotcake-ship.exe")

    if not os.path.isfile(exe_src):
        _fail(f"Executable not found on Filestore: {exe_src}")

    try:
        shutil.copy2(exe_src, runexe)
        os.chmod(runexe, 0o755)
    except Exception as exc:
        _fail(f"Failed to copy executable: {exc}")

    logger.info(f"Executable ready at {runexe!r}")

    # ── Step 5: prepare command file (resume from PAUSED if needed) ──────────
    command_path = os.path.join(workspace_path, "command")
    # Remove any stale command file brought in from the staged copy
    if os.path.exists(command_path):
        os.remove(command_path)

    status_parts = _read_status(workspace_path)
    prior_status = status_parts[0] if status_parts else None

    if prior_status == "PAUSED":
        # Status line format: PAUSED <koverall> <step> <genie_clock> [...]
        if status_parts and len(status_parts) >= 4:
            _, koverall, _step, genie_clock = status_parts[:4]
            with open(command_path, "w") as f:
                f.write(f"GUI_RESTART {koverall} {genie_clock}\n")
            logger.info(
                f"Resuming from PAUSED checkpoint: "
                f"GUI_RESTART {koverall} {genie_clock}"
            )
        else:
            _fail(
                "Cannot resume PAUSED job: status file is missing restart parameters "
                f"(got: {status_parts!r})"
            )

    # ── Step 6: launch the science executable ────────────────────────────────
    log_path = os.path.join(workspace_path, "run.log")
    logger.info(
        f"Launching science executable "
        f"(cwd={workspace_path!r}, log={log_path!r})"
    )
    try:
        with open(log_path, "a") as log_file:
            process = sp.Popen(
                [runexe],
                cwd=workspace_path,
                stdout=log_file,
                stderr=sp.STDOUT,
            )
    except Exception as exc:
        _fail(f"Failed to launch executable: {exc}")

    # ── Step 7: start background sync thread ─────────────────────────────────
    stop_event = threading.Event()
    sync_thread = threading.Thread(
        target=_sync_loop,
        args=(workspace_path, shared_path, stop_event, process),
        daemon=True,
        name="ctoaster-sync",
    )
    sync_thread.start()
    logger.info(f"Sync loop started (interval={SYNC_INTERVAL}s)")

    # ── Step 8: wait for the science process ─────────────────────────────────
    exit_code = process.wait()
    logger.info(f"Science process exited (exit_code={exit_code})")

    # ── Step 9: stop sync thread ─────────────────────────────────────────────
    stop_event.set()
    sync_thread.join(timeout=15)

    # ── Step 10: determine final state ───────────────────────────────────────
    final_parts = _read_status(workspace_path)
    file_status = final_parts[0] if final_parts else None

    if file_status == "PAUSED":
        actual_state = "PAUSED"
    elif file_status == "COMPLETE" or exit_code == 0:
        actual_state = "COMPLETE"
    else:
        actual_state = "FAILED"

    # Respect an explicit cancellation
    desired = _desired_state_from_db()
    if desired in ("CANCEL_REQUESTED", "CANCELLING"):
        actual_state = "CANCELLED"

    # ── Step 11: final sync to Filestore ─────────────────────────────────────
    logger.info(f"Final sync → {shared_path!r}  (state={actual_state})")
    try:
        sync_to_shared(workspace_path, shared_path)
    except Exception as exc:
        logger.error(f"Final sync failed: {exc}")

    # ── Step 12: update DB with final state ──────────────────────────────────
    try:
        update_run(
            RUN_ID,
            actual_state=actual_state,
            desired_state=actual_state,
            exit_code=exit_code,
            finished_at=_now(),
        )
    except Exception as exc:
        logger.error(f"Final DB update failed: {exc}")

    logger.info(
        f"Run {RUN_ID} completed — "
        f"state={actual_state}  exit_code={exit_code}"
    )

    # Exit with the science process's exit code so K8s sees success/failure
    sys.exit(0 if actual_state in ("COMPLETE", "PAUSED") else 1)


# ---------------------------------------------------------------------------
# Error helper — syncs what we have so far before dying
# ---------------------------------------------------------------------------

def _fail(message: str) -> None:
    logger.error(message)
    try:
        update_run(
            RUN_ID,
            actual_state="FAILED",
            error_message=message,
            finished_at=_now(),
        )
    except Exception as exc:
        logger.warning(f"Could not write FAILED state to DB: {exc}")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    run()
