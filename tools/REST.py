"""
tools/REST.py
-------------
Stateless FastAPI backend for ctoaster (v2 architecture).

Responsibilities:
  - Authenticate users (JWT)
  - CRUD for jobs (metadata in DB + folders on Filestore)
  - Submit run requests  → create DB run row + Kubernetes Job
  - Pause / cancel runs  → update DB desired_state + write command file
  - Serve logs / status / plot data  → read from Filestore directly
  - Never own live process handles or in-memory run state
"""

from __future__ import annotations

import asyncio
import base64
import datetime
import hashlib
import hmac
import json
import logging
import os
import shutil
import subprocess as sp
import sys
import tempfile
import time
import uuid
from typing import Dict, Generator, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from tools.utils import read_ctoaster_config

# ── ctoaster configuration ────────────────────────────────────────────────────
read_ctoaster_config()
if not read_ctoaster_config():
    raise RuntimeError("Failed to read ctoaster configuration")

from tools.utils import ctoaster_data, ctoaster_jobs, ctoaster_root, ctoaster_version

# ── DB / storage / k8s imports ───────────────────────────────────────────────
from tools.db import (
    count_jobs_by_user,
    create_run,
    create_user,
    delete_job_record,
    delete_user_cascade,
    force_delete_job_record,
    get_active_run_for_job,
    get_job_record,
    get_run_by_id,
    get_user_by_email,
    get_user_by_id,
    hash_password,
    init_db,
    list_all_active_runs,
    list_all_users,
    list_user_jobs,
    update_run,
    upsert_job_record,
    verify_password,
)
from tools.k8s_jobs import create_runner_job, delete_runner_job, get_runner_job_status
from tools.storage import (
    find_plot_data_path,
    get_job_path,
    get_user_root,
    read_owner,
    safe_join,
    sync_to_shared,
    validate_job_name,
    write_owner,
)

# ── app setup ─────────────────────────────────────────────────────────────────
app = FastAPI()

origins = [
    "https://ctoaster.org",
    "http://ctoaster.org",
    "http://localhost:3000",
    "http://localhost:5000",
    "http://localhost:5001",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5000",
    "http://127.0.0.1:8000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── JWT constants ─────────────────────────────────────────────────────────────
JWT_SECRET: str = os.environ.get("CTOASTER_JWT_SECRET", "changeme-in-prod")
TOKEN_TTL_SECONDS: int = 60 * 60 * 24 * 7  # 7 days

# ── Admin emails ──────────────────────────────────────────────────────────────
ADMIN_EMAILS = set(
    e.strip().lower()
    for e in os.environ.get(
        "ADMIN_EMAILS", "pthak006@ucr.edu,andy@seao2.org"
    ).split(",")
    if e.strip()
)

# ── initialise DB on startup ──────────────────────────────────────────────────
init_db()


# =============================================================================
# JWT helpers
# =============================================================================

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def generate_token(user_id: int, email: str) -> str:
    payload = {
        "uid": user_id,
        "email": email,
        "exp": int(time.time()) + TOKEN_TTL_SECONDS,
    }
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    sig = hmac.new(JWT_SECRET.encode("utf-8"), payload_bytes, hashlib.sha256).digest()
    return f"{_b64url_encode(payload_bytes)}.{_b64url_encode(sig)}"


def decode_token(token: str) -> dict:
    try:
        payload_b64, sig_b64 = token.split(".")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid token format")
    payload_bytes = _b64url_decode(payload_b64)
    expected_sig = hmac.new(
        JWT_SECRET.encode("utf-8"), payload_bytes, hashlib.sha256
    ).digest()
    if not hmac.compare_digest(expected_sig, _b64url_decode(sig_b64)):
        raise HTTPException(status_code=401, detail="Invalid token signature")
    payload = json.loads(payload_bytes.decode("utf-8"))
    if payload.get("exp", 0) < int(time.time()):
        raise HTTPException(status_code=401, detail="Token expired")
    return payload


def get_current_user(request: Request) -> dict:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = auth_header.split(" ", 1)[1].strip()
    payload = decode_token(token)
    user = get_user_by_id(payload["uid"])
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def require_admin(current_user=Depends(get_current_user)):
    if current_user["email"] not in ADMIN_EMAILS:
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


# =============================================================================
# Job path / ownership helpers
# =============================================================================

def _job_path(user: dict, job_name: str) -> str:
    """Resolve the canonical Filestore path for a job; converts ValueError → HTTPException."""
    try:
        return get_job_path(int(user["id"]), job_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _ensure_job_exists(job_path: str, job_name: str) -> None:
    if not os.path.isdir(job_path):
        raise HTTPException(status_code=404, detail=f"Job not found: {job_name}")


def _bg_remove_dir(path: str) -> None:
    """Delete a directory in a background thread with retries.

    The frontend polls job endpoints which hold NFS file handles open.
    By deleting the DB record first (caller's job) and deferring the
    Filestore cleanup, we give those handles time to close.
    """
    import threading

    def _cleanup():
        for attempt in range(6):
            if not os.path.isdir(path):
                return
            if attempt:
                time.sleep(2)
            shutil.rmtree(path, ignore_errors=True)
        if os.path.isdir(path):
            sp.run(["rm", "-rf", path], check=False)
        if os.path.isdir(path):
            logger.warning(f"Could not fully remove {path} after retries")

    threading.Thread(target=_cleanup, daemon=True).start()


def _ensure_owner(job_path: str, user: dict) -> None:
    """
    Verify the job belongs to the requesting user via owner.json.
    In the per-user-subdir layout the path itself already enforces ownership,
    so if owner.json is missing we fall back to checking the path contains the user ID.
    """
    owner = read_owner(job_path)
    if owner is None:
        expected_prefix = os.path.join(get_user_root(int(user["id"])), "")
        if not job_path.startswith(expected_prefix):
            raise HTTPException(status_code=403, detail="Job ownership could not be verified")
        return
    if str(owner.get("user_id")) != str(user["id"]):
        raise HTTPException(status_code=403, detail="Job not owned by this user")


# =============================================================================
# Status file helper
# =============================================================================

def read_status_file(job_dir: str) -> Optional[list]:
    """
    Read the first line of the 'status' file and return it split as a list.
    Retries up to 1000 times to handle transient NFS locks.
    Returns None if the file cannot be read after all retries.
    """
    status = None
    for attempt in range(1000):
        try:
            if attempt:
                time.sleep(0.001)
            with open(os.path.join(job_dir, "status")) as fp:
                status = fp.readline().strip().split()
            if status:
                break
        except IOError:
            pass
    return status or None


# =============================================================================
# Health / root
# =============================================================================

@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/")
def root():
    return {"ok": True}


# =============================================================================
# Auth endpoints
# =============================================================================

@app.post("/auth/register")
async def register(request: Request):
    data = await request.json()
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")
    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password are required")
    try:
        user = create_user(email, password)
    except ValueError:
        raise HTTPException(status_code=400, detail="User already exists")
    token = generate_token(user["id"], user["email"])
    return {"user": user, "token": token}


@app.post("/auth/login")
async def login(request: Request):
    data = await request.json()
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")
    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password are required")
    user = get_user_by_email(email)
    if not user or not verify_password(password, user["salt"], user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = generate_token(user["id"], user["email"])
    return {"user": {"id": user["id"], "email": user["email"]}, "token": token}


@app.get("/auth/me")
def me(current_user=Depends(get_current_user)):
    return {"user": {"id": current_user["id"], "email": current_user["email"]}}


# =============================================================================
# Job list / details
# =============================================================================

@app.get("/jobs")
def list_jobs(current_user=Depends(get_current_user)):
    try:
        user_id = int(current_user["id"])
        user_root = get_user_root(user_id)
        os.makedirs(user_root, exist_ok=True)

        jobs = []
        for name in os.listdir(user_root):
            job_dir = os.path.join(user_root, name)
            if os.path.isdir(job_dir):
                jobs.append({"name": name, "path": job_dir})
        return {"jobs": jobs}
    except Exception as exc:
        return {"error": str(exc)}


@app.get("/job/{job_name}")
def get_job_details(job_name: str, current_user=Depends(get_current_user)):
    try:
        job_path = _job_path(current_user, job_name)
        _ensure_job_exists(job_path, job_name)

        # Determine job status from the file on Filestore
        status = "UNCONFIGURED"
        if os.path.exists(os.path.join(job_path, "data_genie")):
            status = "RUNNABLE"
            if os.path.exists(os.path.join(job_path, "status")):
                parts = read_status_file(job_path)
                status = parts[0] if parts else "ERROR"

        run_length = "n/a"
        t100 = False
        config_path = os.path.join(job_path, "config", "config")
        if os.path.exists(config_path):
            with open(config_path) as f:
                for line in f:
                    if line.startswith("run_length:"):
                        run_length = line.split(":", 1)[1].strip()
                    if line.startswith("t100:"):
                        t100 = line.split(":", 1)[1].strip().lower() == "true"

        job_details = {
            "name": job_name,
            "path": job_path,
            "status": status,
            "run_length": run_length,
            "t100": "true" if t100 else "false",
        }
        logger.info(f"Job details retrieved: {job_details}")
        return {"job": job_details}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error retrieving job details: {exc}")
        return {"error": str(exc)}


# =============================================================================
# Job management (add / delete)
# =============================================================================

@app.post("/add-job")
async def add_job(request: Request, current_user=Depends(get_current_user)):
    data = await request.json()
    job_name = data.get("job_name", "")

    try:
        validate_job_name(job_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    job_dir = _job_path(current_user, job_name)
    if os.path.exists(job_dir):
        raise HTTPException(status_code=400, detail="Job already exists")

    try:
        os.makedirs(os.path.join(job_dir, "config"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not create job directory: {exc}")

    config_path = os.path.join(job_dir, "config", "config")
    try:
        with open(config_path, "w") as f:
            f.write("base_config: ?\nuser_config: ?\nrun_length: ?\nt100: ?\n")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not write config file: {exc}")

    write_owner(job_dir, int(current_user["id"]), current_user["email"])

    # Register in DB (best-effort — Filestore is authoritative for job existence)
    try:
        upsert_job_record(int(current_user["id"]), job_name, job_dir)
    except Exception as exc:
        logger.warning(f"DB upsert failed for new job '{job_name}': {exc}")

    return {"status": "success", "message": f"Job '{job_name}' created successfully"}


@app.delete("/delete-job")
def delete_job(job_name: str = Query(...), current_user=Depends(get_current_user)):
    job_path = _job_path(current_user, job_name)
    _ensure_job_exists(job_path, job_name)
    _ensure_owner(job_path, current_user)

    k8s_namespace = os.environ.get("K8S_NAMESPACE", "default")
    job_record = get_job_record(int(current_user["id"]), job_name)
    if job_record:
        active = get_active_run_for_job(job_record["id"])
        if active:
            k8s_name = active.get("k8s_job_name")
            k8s_status = get_runner_job_status(k8s_name, k8s_namespace) if k8s_name else None
            if k8s_status in (None, "succeeded", "failed"):
                update_run(active["run_id"], actual_state="CANCELLED",
                           finished_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
                logger.info(f"Auto-cancelled stale run {active['run_id']} (k8s status: {k8s_status})")
            else:
                raise HTTPException(
                    status_code=409,
                    detail=f"Job '{job_name}' has an active run. Pause or cancel it first.",
                )

    try:
        delete_job_record(int(current_user["id"]), job_name)
    except Exception as exc:
        logger.warning(f"DB delete failed for job '{job_name}': {exc}")

    _bg_remove_dir(job_path)

    logger.info(f"Job deleted: {job_path}")
    return {"message": f"Job '{job_name}' deleted successfully"}


# =============================================================================
# Run segments (informational)
# =============================================================================

@app.get("/run-segments/{job_name}")
def get_run_segments(job_name: str, current_user=Depends(get_current_user)):
    try:
        job_path = _job_path(current_user, job_name)
        _ensure_job_exists(job_path, job_name)

        segments_dir = os.path.join(job_path, "config", "segments")

        segments = []
        if os.path.exists(segments_dir):
            for segment_id in os.listdir(segments_dir):
                segment_path = os.path.join(segments_dir, segment_id)
                if os.path.isdir(segment_path):
                    cfg = os.path.join(segment_path, "config")
                    if os.path.exists(cfg):
                        with open(cfg) as f:
                            for line in f:
                                if line.startswith("run_length:"):
                                    segments.append((segment_id, int(line.split(":")[1].strip())))

        if not segments:
            return {"run_segments": ["1: 1-END"]}

        res = [f"{i + 1}: {start}-{end}" for i, (start, end) in enumerate(segments)]
        final_step = segments[-1][1] + 1
        res.append(f"{len(segments) + 1}: {final_step}-END")
        return {"run_segments": tuple(reversed(res))}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error fetching run segments: {exc}")


# =============================================================================
# Config dropdowns (no auth required — read-only static data)
# =============================================================================

@app.get("/base-configs")
def get_base_configs():
    try:
        base_configs_dir = os.path.join(ctoaster_data, "base-configs")
        base_configs = sorted(
            f.rpartition(".")[0]
            for f in os.listdir(base_configs_dir)
            if f.endswith(".config")
        )
        return {"base_configs": base_configs}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error fetching base configs: {exc}")


@app.get("/user-configs")
def get_user_configs():
    try:
        user_configs_dir = os.path.join(ctoaster_data, "user-configs")
        user_configs = sorted(
            os.path.relpath(os.path.join(root, f), user_configs_dir)
            for root, _, files in os.walk(user_configs_dir)
            for f in files
        )
        return {"user_configs": user_configs}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error fetching user configs: {exc}")


# =============================================================================
# Completed jobs (used by Status panel)
# =============================================================================

@app.get("/completed-jobs")
async def get_completed_jobs(current_user=Depends(get_current_user)):
    try:
        user_root = get_user_root(int(current_user["id"]))
        os.makedirs(user_root, exist_ok=True)

        completed = []
        for job_name in os.listdir(user_root):
            job_dir = os.path.join(user_root, job_name)
            if not os.path.isdir(job_dir):
                continue
            status_file = os.path.join(job_dir, "status")
            if os.path.exists(status_file):
                parts = read_status_file(job_dir)
                if parts and parts[0] == "COMPLETE":
                    completed.append(job_name)
        return {"completed_jobs": completed}
    except Exception as exc:
        logger.error(f"Error fetching completed jobs: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


# =============================================================================
# Setup (read + save)
# =============================================================================

@app.get("/setup/{job_name}")
def get_setup(job_name: str, current_user=Depends(get_current_user)):
    try:
        job_path = _job_path(current_user, job_name)
        _ensure_job_exists(job_path, job_name)

        config_path = os.path.join(job_path, "config", "config")
        if not os.path.exists(config_path):
            raise ValueError("Config file not found")

        setup = {
            "base_config": "",
            "user_config": "",
            "modifications": "",
            "run_length": "n/a",
            "restart_from": "",
        }
        with open(config_path) as f:
            for line in f:
                if line.startswith("base_config:"):
                    setup["base_config"] = line.split(":", 1)[1].strip()
                elif line.startswith("user_config:"):
                    setup["user_config"] = line.split(":", 1)[1].strip()
                elif line.startswith("run_length:"):
                    setup["run_length"] = line.split(":", 1)[1].strip()
                elif line.startswith("restart:"):
                    setup["restart_from"] = line.split(":", 1)[1].strip()

        mods_path = os.path.join(job_path, "config", "config_mods")
        if os.path.exists(mods_path):
            with open(mods_path) as f:
                setup["modifications"] = f.read().strip()

        return {"setup": setup}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error retrieving setup: {exc}")
        return {"error": str(exc)}


@app.post("/setup/{job_name}")
async def update_setup(job_name: str, request: Request, current_user=Depends(get_current_user)):
    try:
        data = await request.json()
        job_path = _job_path(current_user, job_name)
        _ensure_job_exists(job_path, job_name)

        config_path = os.path.join(job_path, "config", "config")
        if not os.path.exists(config_path):
            raise ValueError("Config file not found")

        base_config = data.get("base_config", "")
        user_config = data.get("user_config", "")
        modifications = data.get("modifications", "")
        run_length = data.get("run_length", "n/a")
        restart = data.get("restart_from") or None

        with open(config_path, "w") as f:
            if base_config:
                f.write(f"base_config_dir: {os.path.join(ctoaster_data, 'base-configs')}\n")
                f.write(f"base_config: {base_config}\n")
            if user_config:
                f.write(f"user_config_dir: {os.path.join(ctoaster_data, 'user-configs')}\n")
                f.write(f"user_config: {user_config}\n")
            f.write(f"restart: {restart or ''}\n")
            f.write(f"config_date: {datetime.datetime.today().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"run_length: {run_length}\n")

        mods_path = os.path.join(job_path, "config", "config_mods")
        if modifications:
            with open(mods_path, "w") as f:
                f.write(modifications)
        elif os.path.exists(mods_path):
            os.remove(mods_path)

        # Regenerate namelists using the per-user jobs root on Filestore
        user_jobs_root = get_user_root(int(current_user["id"]))
        os.makedirs(user_jobs_root, exist_ok=True)

        new_job_script = os.path.join(ctoaster_root, "tools", "new-job.py")
        cmd = [
            sys.executable, new_job_script,
            "--gui",
            "-b", base_config,
            "-u", user_config,
            "-j", user_jobs_root,
            job_name,
            str(run_length),
        ]
        if modifications:
            cmd.extend(["-m", mods_path])
        if restart:
            cmd.extend(["--restart", restart])

        try:
            res = sp.check_output(cmd, stderr=sp.STDOUT, text=True).strip()
        except sp.CalledProcessError as exc:
            raise ValueError(f"new-job script failed: {exc.output}")

        if not res.startswith("OK"):
            raise ValueError(res[4:] if len(res) > 4 else res)

        return {"message": "Setup updated successfully"}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error updating setup: {exc}")
        return {"error": str(exc)}


# =============================================================================
# Run job  (API creates DB row + Kubernetes Job; runner pod does the work)
# =============================================================================

@app.post("/run-job")
async def run_job(request: Request, current_user=Depends(get_current_user)):
    data = await request.json()
    job_name = data.get("job_name", "")

    try:
        validate_job_name(job_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    job_path = _job_path(current_user, job_name)
    _ensure_job_exists(job_path, job_name)

    # Determine current status from Filestore
    status = "UNCONFIGURED"
    if os.path.exists(os.path.join(job_path, "data_genie")):
        status = "RUNNABLE"
        if os.path.exists(os.path.join(job_path, "status")):
            parts = read_status_file(job_path)
            status = parts[0] if parts else "ERROR"

    if status not in ("RUNNABLE", "PAUSED", "RUNNING"):
        raise HTTPException(
            status_code=400,
            detail=f"Job '{job_name}' is not in a runnable state (current: {status}).",
        )

    # Ensure there is no active run already in the DB
    job_record = upsert_job_record(int(current_user["id"]), job_name, job_path)
    active = get_active_run_for_job(job_record["id"])
    if active:
        if active.get("actual_state") == "PAUSED":
            update_run(active["run_id"], actual_state="CANCELLED",
                       finished_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
            logger.info(f"Cancelled previous PAUSED run {active['run_id']} to allow resume")
        else:
            raise HTTPException(
                status_code=409,
                detail=f"Job '{job_name}' already has an active run (run_id={active['run_id']}).",
            )

    run_id = uuid.uuid4().hex
    k8s_namespace = os.environ.get("K8S_NAMESPACE", "default")

    try:
        k8s_job_name = create_runner_job(
            run_id=run_id,
            job_id=job_record["id"],
            user_id=int(current_user["id"]),
            job_name=job_name,
            namespace=k8s_namespace,
        )
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc))
    except Exception as exc:
        logger.error(f"Failed to create Kubernetes Job for run {run_id}: {exc}")
        raise HTTPException(status_code=500, detail=f"Could not create runner job: {exc}")

    create_run(
        job_id=job_record["id"],
        user_id=int(current_user["id"]),
        run_id=run_id,
        k8s_job_name=k8s_job_name,
        shared_run_path=job_path,
    )

    logger.info(f"Run {run_id} submitted for job '{job_name}' (k8s job: {k8s_job_name})")
    return {
        "message": f"Job '{job_name}' submitted for execution",
        "run_id": run_id,
        "k8s_job_name": k8s_job_name,
    }


# =============================================================================
# Pause job  (writes PAUSE command to Filestore; runner reads it)
# =============================================================================

@app.post("/pause-job")
async def pause_job(request: Request, current_user=Depends(get_current_user)):
    data = await request.json()
    job_name = data.get("job_name", "")

    try:
        validate_job_name(job_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    job_path = _job_path(current_user, job_name)
    _ensure_job_exists(job_path, job_name)

    status_file = os.path.join(job_path, "status")
    if not os.path.exists(status_file):
        raise HTTPException(status_code=400, detail="Job has no status file (not running)")

    with open(status_file) as f:
        status_line = f.readline().strip()
    if "PAUSED" in status_line:
        raise HTTPException(status_code=400, detail="Job is already paused")

    # Update desired_state in DB (best-effort)
    job_record = get_job_record(int(current_user["id"]), job_name)
    if job_record:
        active = get_active_run_for_job(job_record["id"])
        if active:
            try:
                update_run(active["run_id"], desired_state="PAUSE_REQUESTED")
            except Exception as exc:
                logger.warning(f"DB update for pause failed: {exc}")

    # Write PAUSE command to Filestore — runner will read it
    command_path = os.path.join(job_path, "command")
    with open(command_path, "w") as f:
        f.write("PAUSE\n")

    return {"message": f"Job '{job_name}' pause requested"}


# =============================================================================
# Log / output streaming  (reads directly from Filestore)
# =============================================================================

@app.get("/get-log/{job_name}")
async def get_log(job_name: str, current_user=Depends(get_current_user)):
    job_path = _job_path(current_user, job_name)
    log_file_path = os.path.join(job_path, "run.log")

    if not os.path.exists(log_file_path):
        return {"content": ""}

    with open(log_file_path) as f:
        return {"content": f.read()}


@app.get("/stream-output/{job_name}")
async def stream_output(
    job_name: str,
    background_tasks: BackgroundTasks,
    current_user=Depends(get_current_user),
):
    job_path = _job_path(current_user, job_name)
    log_file_path = os.path.join(job_path, "run.log")

    # Wait up to 30 seconds for the runner to create the log file
    for _ in range(30):
        if os.path.exists(log_file_path):
            break
        logger.info(f"Waiting for log file: {log_file_path}")
        await asyncio.sleep(1)

    if not os.path.exists(log_file_path):
        raise HTTPException(status_code=404, detail=f"Log file not found: {log_file_path}")

    async def log_reader():
        with open(log_file_path) as f:
            f.seek(0, os.SEEK_END)
            while True:
                line = f.readline()
                if line:
                    yield f"data: {line.strip()}\n\n"
                else:
                    await asyncio.sleep(1)

    return StreamingResponse(log_reader(), media_type="text/event-stream")


# =============================================================================
# Namelists
# =============================================================================

@app.get("/jobs/{job_id}/namelists")
def get_namelists(job_id: str, current_user=Depends(get_current_user)):
    job_dir = _job_path(current_user, job_id)
    _ensure_job_exists(job_dir, job_id)
    _ensure_owner(job_dir, current_user)

    namelists = [
        fname[len("data_"):]
        for fname in os.listdir(job_dir)
        if fname.startswith("data_") and os.path.isfile(os.path.join(job_dir, fname))
    ]
    return {"namelists": namelists}


@app.get("/jobs/{job_id}/namelists/{namelist_name}")
def get_namelist_content(job_id: str, namelist_name: str, current_user=Depends(get_current_user)):
    job_dir = _job_path(current_user, job_id)
    _ensure_job_exists(job_dir, job_id)
    _ensure_owner(job_dir, current_user)

    safe_name = os.path.basename(namelist_name)
    namelist_path = os.path.join(job_dir, f"data_{safe_name}")
    if not os.path.isfile(namelist_path):
        raise HTTPException(status_code=404, detail="Namelist not found")

    try:
        with open(namelist_path) as f:
            content = f.read()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error reading namelist: {exc}")

    return {"namelist_name": safe_name, "content": content}


# =============================================================================
# Plot data (data files list, variables, batch fetch, SSE stream)
# =============================================================================

@app.get("/get_data_files_list/{job_name}")
async def get_data_files_list(job_name: str, current_user=Depends(get_current_user)):
    job_path = _job_path(current_user, job_name)
    _ensure_job_exists(job_path, job_name)
    _ensure_owner(job_path, current_user)

    try:
        plot_data_path = find_plot_data_path(job_path)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    all_files = os.listdir(plot_data_path)
    data_files = [f for f in all_files if f.startswith("biogem_series")]

    if not data_files:
        raise HTTPException(
            status_code=404,
            detail=f"No biogem_series files found in {plot_data_path}",
        )
    return data_files


@app.get("/get-variables/{job_name}/{data_file_name}")
async def get_variables(
    job_name: str, data_file_name: str, current_user=Depends(get_current_user)
):
    job_path = _job_path(current_user, job_name)
    _ensure_job_exists(job_path, job_name)
    _ensure_owner(job_path, current_user)

    try:
        plot_data_path = find_plot_data_path(job_path)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    data_file_path = os.path.join(plot_data_path, data_file_name)
    if not os.path.isfile(data_file_path):
        raise HTTPException(status_code=404, detail="Data file not found")

    try:
        with open(data_file_path) as f:
            header = f.readline().strip()
        variables = [v.strip() for v in header.split("/")[1:]]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error reading data file: {exc}")

    if not variables:
        raise HTTPException(status_code=404, detail="No variables found in data file")
    return variables


class PlotDataRequest(BaseModel):
    job_name: str
    data_file_name: str
    variable: str


@app.post("/get-plot-data")
async def get_plot_data(request: PlotDataRequest, current_user=Depends(get_current_user)):
    job_path = _job_path(current_user, request.job_name)
    _ensure_job_exists(job_path, request.job_name)
    _ensure_owner(job_path, current_user)

    try:
        plot_data_path = find_plot_data_path(job_path)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    data_file_path = os.path.join(plot_data_path, request.data_file_name)
    if not os.path.isfile(data_file_path):
        raise HTTPException(status_code=404, detail="Data file not found")

    try:
        with open(data_file_path) as f:
            header = f.readline().strip()
            columns = [c.strip() for c in header.split("/")]
            first_col = columns[0]
            if request.variable not in columns:
                raise HTTPException(status_code=404, detail="Variable not found in data file")
            var_idx = columns.index(request.variable)
            data = []
            for line in f:
                parts = line.strip().split()
                if len(parts) > var_idx:
                    try:
                        data.append([float(parts[0]), float(parts[var_idx])])
                    except ValueError:
                        continue
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error reading data file: {exc}")

    if not data:
        raise HTTPException(status_code=404, detail="No data found for selected variable")

    return {"columns": [first_col, request.variable], "data": data}


def _trim(s: str) -> str:
    return s.strip()


async def _read_data_file_sse(
    file_path: str, variable: str
) -> Generator[str, None, None]:
    """Async generator: stream existing rows then tail for new ones (SSE format)."""
    try:
        with open(file_path) as f:
            header = f.readline().strip()
            cols = [_trim(c) for c in header.split("/")]
            var = _trim(variable)
            if var not in cols:
                raise HTTPException(status_code=404, detail=f"Variable '{variable}' not found")
            idx = cols.index(var)

            # Stream existing data
            while True:
                line = f.readline()
                if not line:
                    break
                parts = line.strip().split()
                if len(parts) > idx:
                    try:
                        yield f"data: {float(parts[0])},{float(parts[idx])}\n\n"
                    except ValueError:
                        continue

            # Tail for new data
            f.seek(0, os.SEEK_END)
            while True:
                line = f.readline()
                if not line:
                    await asyncio.sleep(0.5)
                    continue
                parts = line.strip().split()
                if len(parts) > idx:
                    try:
                        yield f"data: {float(parts[0])},{float(parts[idx])}\n\n"
                    except ValueError:
                        continue
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error reading data file: {exc}")


@app.get("/get-plot-data-stream")
async def get_plot_data_stream(
    job_name: str = Query(...),
    data_file_name: str = Query(...),
    variable: str = Query(...),
    current_user=Depends(get_current_user),
):
    job_path = _job_path(current_user, job_name)
    _ensure_job_exists(job_path, job_name)
    _ensure_owner(job_path, current_user)

    try:
        plot_data_path = find_plot_data_path(job_path)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    data_file_path = os.path.join(plot_data_path, data_file_name)
    if not os.path.isfile(data_file_path):
        raise HTTPException(status_code=404, detail="Data file not found")

    return StreamingResponse(
        _read_data_file_sse(data_file_path, variable),
        media_type="text/event-stream",
    )


# =============================================================================
# Download job zip
# =============================================================================

@app.get("/jobs/{job_name}/download")
async def download_job_zip(
    job_name: str,
    background_tasks: BackgroundTasks,
    current_user=Depends(get_current_user),
):
    job_path = _job_path(current_user, job_name)
    _ensure_job_exists(job_path, job_name)
    _ensure_owner(job_path, current_user)

    tmpdir = tempfile.mkdtemp(prefix=f"{job_name}_zip_")
    zip_base = os.path.join(tmpdir, job_name)
    archive_path = shutil.make_archive(zip_base, "zip", job_path)

    background_tasks.add_task(shutil.rmtree, tmpdir, True)

    return FileResponse(
        path=archive_path,
        media_type="application/zip",
        filename=f"{job_name}.zip",
        background=background_tasks,
    )


# =============================================================================
# Admin endpoints
# =============================================================================

@app.get("/admin/users")
def admin_list_users(admin=Depends(require_admin)):
    users = list_all_users()
    job_counts = count_jobs_by_user()
    for u in users:
        u["job_count"] = job_counts.get(u["id"], 0)
    return {"users": users}


@app.get("/admin/users/{user_id}/jobs")
def admin_user_jobs(user_id: int, admin=Depends(require_admin)):
    jobs = list_user_jobs(user_id)
    result = []
    for j in jobs:
        active = get_active_run_for_job(j["id"])
        result.append({
            **j,
            "active_run": {
                "run_id": active["run_id"],
                "actual_state": active["actual_state"],
                "k8s_job_name": active.get("k8s_job_name"),
            } if active else None,
        })
    return {"jobs": result}


@app.get("/admin/runs")
def admin_active_runs(admin=Depends(require_admin)):
    return {"runs": list_all_active_runs()}


@app.delete("/admin/users/{user_id}")
def admin_delete_user(user_id: int, admin=Depends(require_admin)):
    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user["email"] in ADMIN_EMAILS:
        raise HTTPException(status_code=400, detail="Cannot delete an admin user")

    k8s_namespace = os.environ.get("K8S_NAMESPACE", "default")

    for job in list_user_jobs(user_id):
        active = get_active_run_for_job(job["id"])
        if active and active.get("k8s_job_name"):
            try:
                delete_runner_job(active["k8s_job_name"], k8s_namespace)
            except Exception as exc:
                logger.warning(f"Failed to delete K8s job {active['k8s_job_name']}: {exc}")
            update_run(active["run_id"], actual_state="CANCELLED",
                       finished_at=datetime.datetime.now(datetime.timezone.utc).isoformat())

    user_root = get_user_root(user_id)
    if os.path.isdir(user_root):
        _bg_remove_dir(user_root)

    delete_user_cascade(user_id)
    logger.info(f"Admin deleted user {user_id} ({user['email']})")
    return {"message": f"User '{user['email']}' and all their data deleted"}


@app.delete("/admin/jobs/{user_id}/{job_name}")
def admin_delete_job(user_id: int, job_name: str, admin=Depends(require_admin)):
    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        validate_job_name(job_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    job_record = get_job_record(user_id, job_name)
    if not job_record:
        raise HTTPException(status_code=404, detail=f"Job '{job_name}' not found in DB")

    k8s_namespace = os.environ.get("K8S_NAMESPACE", "default")
    active = get_active_run_for_job(job_record["id"])
    if active and active.get("k8s_job_name"):
        try:
            delete_runner_job(active["k8s_job_name"], k8s_namespace)
        except Exception as exc:
            logger.warning(f"Failed to delete K8s job {active['k8s_job_name']}: {exc}")
        update_run(active["run_id"], actual_state="CANCELLED",
                   finished_at=datetime.datetime.now(datetime.timezone.utc).isoformat())

    job_path = get_job_path(user_id, job_name)
    if os.path.isdir(job_path):
        _bg_remove_dir(job_path)

    force_delete_job_record(job_record["id"])
    logger.info(f"Admin deleted job '{job_name}' for user {user_id}")
    return {"message": f"Job '{job_name}' force-deleted"}
