"""
tools/storage.py
----------------
Filestore path helpers, job-name validation, path-traversal guard,
and workspace ↔ Filestore sync logic.

This module is intentionally free of FastAPI imports so it can be used
by both the API (tools/REST.py) and the runner (tools/runner.py).
Callers that need HTTP error responses should catch ValueError and
convert it to HTTPException themselves.

Filestore layout (all paths rooted at FILESTORE_ROOT):
  MODELS/<version>/<platform>/ship/carrotcake.exe
  jobs/<user_id>/<job_name>/
    config/
    data_genie/
    owner.json
    status
    run.log
    command
    output/biogem/
"""

from __future__ import annotations

import os
import platform
import shutil
from typing import FrozenSet, Optional

# ---------------------------------------------------------------------------
# Root resolution
# ---------------------------------------------------------------------------

def get_filestore_root() -> str:
    """
    Return the Filestore root directory.

    Priority:
      1. FILESTORE_ROOT environment variable  (set in every pod's env)
      2. ctoaster_jobs from .ctoasterrc       (fallback for local dev)
    """
    env_root = os.environ.get("FILESTORE_ROOT", "").strip()
    if env_root:
        return env_root

    # Fallback: read .ctoasterrc the same way utils.py does
    try:
        from tools.utils import ctoaster_jobs, read_ctoaster_config
        read_ctoaster_config()
        if ctoaster_jobs:
            return ctoaster_jobs
    except Exception:
        pass

    raise RuntimeError(
        "Filestore root not found: set the FILESTORE_ROOT environment variable "
        "or configure .ctoasterrc with ctoaster_jobs."
    )


# ---------------------------------------------------------------------------
# Job-name validation and path-traversal guard
# (raise ValueError — no FastAPI dependency here)
# ---------------------------------------------------------------------------

_ALLOWED_JOB_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789._-"
)


def validate_job_name(job_name: str) -> str:
    """Validate and return job_name. Raises ValueError on invalid input."""
    if not job_name:
        raise ValueError("Job name is required")
    if len(job_name) > 128:
        raise ValueError("Job name too long (max 128 characters)")
    bad = [ch for ch in job_name if ch not in _ALLOWED_JOB_CHARS]
    if bad:
        raise ValueError(
            f"Job name contains invalid character(s): {bad!r}. "
            "Only letters, digits, dots, underscores, and hyphens are allowed."
        )
    return job_name


def safe_join(base: str, *paths: str) -> str:
    """
    Join base with one or more path components and verify the result stays
    inside base.  Raises ValueError on path-traversal attempts.
    """
    final = os.path.abspath(os.path.join(base, *paths))
    base_abs = os.path.abspath(base)
    if not final.startswith(base_abs + os.sep) and final != base_abs:
        raise ValueError(f"Path traversal detected: {final!r} is outside {base_abs!r}")
    return final


# ---------------------------------------------------------------------------
# Filestore path helpers
# ---------------------------------------------------------------------------

def get_models_root() -> str:
    return os.path.join(get_filestore_root(), "MODELS")


def get_exe_path(
    version: Optional[str] = None,
    platform_name: Optional[str] = None,
    build_type: str = "ship",
) -> str:
    """
    Return the absolute path to carrotcake.exe on the Filestore.

    version       defaults to CTOASTER_VERSION env var, then ctoaster_version
                  from .ctoasterrc, then "DEVELOPMENT"
    platform_name defaults to CTOASTER_PLATFORM env var, then the host OS name
                  (e.g. "LINUX" inside the Docker container)
    build_type    always "ship" for production runs
    """
    if version is None:
        version = os.environ.get("CTOASTER_VERSION", "").strip()
        if not version:
            try:
                from tools.utils import ctoaster_version, read_ctoaster_config
                read_ctoaster_config()
                version = ctoaster_version or "DEVELOPMENT"
            except Exception:
                version = "DEVELOPMENT"

    if platform_name is None:
        platform_name = os.environ.get("CTOASTER_PLATFORM", "").strip()
        if not platform_name:
            platform_name = platform.system().upper()   # "LINUX" in Docker

    return os.path.join(
        get_models_root(), version, platform_name, build_type, "carrotcake.exe"
    )


def get_user_root(user_id: int) -> str:
    return os.path.join(get_filestore_root(), "jobs", str(user_id))


def get_job_path(user_id: int, job_name: str) -> str:
    """Return the canonical Filestore path for a job. Validates job_name."""
    validate_job_name(job_name)
    return safe_join(get_user_root(user_id), job_name)


# ---------------------------------------------------------------------------
# Staging: Filestore → local workspace
# ---------------------------------------------------------------------------

def stage_job_to_workspace(user_id: int, job_name: str, workspace_dir: str) -> str:
    """
    Copy the canonical job folder from Filestore into workspace_dir.
    Returns the workspace job path.

    workspace_dir is an emptyDir volume mount in the runner pod
    (e.g. /workspace/<run_id>).
    """
    shared_path = get_job_path(user_id, job_name)
    if not os.path.isdir(shared_path):
        raise FileNotFoundError(
            f"Job not found on Filestore: {shared_path}"
        )
    workspace_job_path = os.path.join(workspace_dir, job_name)
    os.makedirs(os.path.dirname(workspace_job_path), exist_ok=True)
    shutil.copytree(shared_path, workspace_job_path, dirs_exist_ok=True)
    return workspace_job_path


# ---------------------------------------------------------------------------
# Sync: workspace → Filestore
# ---------------------------------------------------------------------------

# Files that live only in the workspace and must never be pushed to Filestore.
_DEFAULT_SKIP: FrozenSet[str] = frozenset({"carrotcake-ship.exe"})

# Transient files that should be removed from Filestore if they disappear
# from the workspace (e.g. "command" is ephemeral; the exe is never on shared).
_MIRROR_DELETE: FrozenSet[str] = frozenset({"command", "carrotcake-ship.exe"})


def sync_to_shared(
    workspace_job_path: str,
    shared_job_path: str,
    skip: FrozenSet[str] = _DEFAULT_SKIP,
) -> None:
    """
    Incrementally sync workspace_job_path → shared_job_path (Filestore).

    - Files in `skip` are never copied to Filestore.
    - Files in _MIRROR_DELETE that have disappeared from workspace are removed
      from Filestore so they don't linger after a run finishes.
    - All other files/dirs are copied with shutil.copy2 / copytree, so mtimes
      are preserved and unchanged files are overwritten (cheap on NFS).

    This is called:
      - Every SYNC_INTERVAL_SECONDS by the runner's background thread.
      - Once more on final completion / failure.
    """
    if not os.path.isdir(workspace_job_path):
        return

    os.makedirs(shared_job_path, exist_ok=True)

    workspace_names = set(os.listdir(workspace_job_path)) - skip
    shared_names = set(os.listdir(shared_job_path))

    # Remove stale transient files that are no longer in the workspace
    for name in shared_names - workspace_names:
        if name not in _MIRROR_DELETE:
            continue
        stale = os.path.join(shared_job_path, name)
        try:
            if os.path.isdir(stale):
                shutil.rmtree(stale)
            else:
                os.remove(stale)
        except FileNotFoundError:
            pass

    # Copy everything else from workspace to Filestore
    for name in workspace_names:
        src = os.path.join(workspace_job_path, name)
        dst = os.path.join(shared_job_path, name)
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)


# ---------------------------------------------------------------------------
# Plot data helper (moved from REST.py; raises ValueError instead of HTTPException)
# ---------------------------------------------------------------------------

def find_plot_data_path(job_path: str) -> str:
    """
    Walk job_path and return the first directory whose path contains
    'output/biogem'.  Raises ValueError if not found.
    """
    for root, _dirs, _files in os.walk(job_path):
        # Normalise separators for platform independence
        if os.path.join("output", "biogem") in root:
            return root
    raise ValueError(f"output/biogem directory not found under {job_path}")


# ---------------------------------------------------------------------------
# Owner file helpers (moved from REST.py)
# ---------------------------------------------------------------------------

def write_owner(job_path: str, user_id: int, email: str) -> None:
    """Write owner.json into job_path."""
    import json
    owner_file = os.path.join(job_path, "owner.json")
    tmp = owner_file + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"user_id": user_id, "email": email}, f)
    os.replace(tmp, owner_file)   # atomic on POSIX


def read_owner(job_path: str) -> Optional[dict]:
    """Return the owner dict from owner.json, or None if missing/unreadable."""
    import json
    owner_file = os.path.join(job_path, "owner.json")
    try:
        with open(owner_file) as f:
            return json.load(f)
    except Exception:
        return None
