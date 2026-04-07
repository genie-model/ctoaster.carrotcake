"""
tools/db.py
-----------
Central database access layer for ctoaster.

Connection is driven by the DB_URL environment variable:
  - Postgres (production):  DB_URL=postgresql://user:pass@host/dbname
  - Cloud SQL via proxy:    DB_URL=postgresql://user:pass@/dbname?host=/cloudsql/proj:region:inst
  - SQLite (local dev):     DB_URL=sqlite:///./ctoaster_dev.db  (default)

All public functions accept and return plain dicts.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Generator, List, Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DB_URL: str = os.environ.get("DB_URL", "sqlite:///./ctoaster_dev.db")


def _is_sqlite() -> bool:
    return DB_URL.startswith("sqlite://")


def _sqlite_path() -> str:
    # sqlite:///./relative  →  strip "sqlite:///"  →  "./relative"
    # sqlite:////abs/path   →  strip "sqlite:///"  →  "/abs/path"
    return DB_URL[len("sqlite:///"):]


# ---------------------------------------------------------------------------
# Connection context manager
# ---------------------------------------------------------------------------

@contextmanager
def _conn() -> Generator:
    if _is_sqlite():
        import sqlite3
        con = sqlite3.connect(_sqlite_path())
        con.row_factory = sqlite3.Row
        # WAL mode allows concurrent reads while a writer holds a lock
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()
    else:
        import psycopg2
        import psycopg2.extras
        con = psycopg2.connect(DB_URL)
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()


def _cursor(con):
    if _is_sqlite():
        return con.cursor()
    import psycopg2.extras
    return con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def _ph() -> str:
    """Return the parameter placeholder for the active backend."""
    return "?" if _is_sqlite() else "%s"


def _row(row) -> Optional[Dict]:
    if row is None:
        return None
    return dict(row)


def _rows(rows) -> List[Dict]:
    return [dict(r) for r in rows]


def _now() -> str:
    """ISO-8601 UTC timestamp compatible with both SQLite TEXT and Postgres TIMESTAMPTZ."""
    return datetime.now(timezone.utc).isoformat()


def _fetch_by_id(cur, table: str, row_id: int) -> Optional[Dict]:
    ph = _ph()
    cur.execute(f"SELECT * FROM {table} WHERE id = {ph}", (row_id,))
    return _row(cur.fetchone())


# ---------------------------------------------------------------------------
# Schema initialisation
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Create all tables if they do not exist. Safe to call on every startup."""
    ph = _ph()
    with _conn() as con:
        cur = _cursor(con)
        if _is_sqlite():
            # SQLite: execute statements one at a time (executescript auto-commits,
            # so we use regular execute() calls within the managed transaction)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    email       TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    salt        TEXT NOT NULL,
                    created_at  TEXT NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     INTEGER NOT NULL REFERENCES users(id),
                    job_name    TEXT NOT NULL,
                    shared_path TEXT NOT NULL,
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL,
                    UNIQUE(user_id, job_name)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id           TEXT UNIQUE NOT NULL,
                    job_id           INTEGER NOT NULL REFERENCES jobs(id),
                    user_id          INTEGER NOT NULL REFERENCES users(id),
                    k8s_job_name     TEXT,
                    k8s_pod_name     TEXT,
                    desired_state    TEXT NOT NULL DEFAULT 'QUEUED',
                    actual_state     TEXT NOT NULL DEFAULT 'QUEUED',
                    heartbeat_at     TEXT,
                    started_at       TEXT,
                    finished_at      TEXT,
                    shared_run_path  TEXT,
                    workspace_hint   TEXT,
                    exit_code        INTEGER,
                    error_message    TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS artifacts (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id        TEXT NOT NULL REFERENCES runs(run_id),
                    artifact_type TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    size          INTEGER,
                    created_at    TEXT NOT NULL
                )
            """)
        else:
            # Postgres
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id            SERIAL PRIMARY KEY,
                    email         TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    salt          TEXT NOT NULL,
                    created_at    TIMESTAMPTZ NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id          SERIAL PRIMARY KEY,
                    user_id     INTEGER NOT NULL REFERENCES users(id),
                    job_name    TEXT NOT NULL,
                    shared_path TEXT NOT NULL,
                    created_at  TIMESTAMPTZ NOT NULL,
                    updated_at  TIMESTAMPTZ NOT NULL,
                    UNIQUE (user_id, job_name)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    id               SERIAL PRIMARY KEY,
                    run_id           TEXT UNIQUE NOT NULL,
                    job_id           INTEGER NOT NULL REFERENCES jobs(id),
                    user_id          INTEGER NOT NULL REFERENCES users(id),
                    k8s_job_name     TEXT,
                    k8s_pod_name     TEXT,
                    desired_state    TEXT NOT NULL DEFAULT 'QUEUED',
                    actual_state     TEXT NOT NULL DEFAULT 'QUEUED',
                    heartbeat_at     TIMESTAMPTZ,
                    started_at       TIMESTAMPTZ,
                    finished_at      TIMESTAMPTZ,
                    shared_run_path  TEXT,
                    workspace_hint   TEXT,
                    exit_code        INTEGER,
                    error_message    TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS artifacts (
                    id            SERIAL PRIMARY KEY,
                    run_id        TEXT NOT NULL REFERENCES runs(run_id),
                    artifact_type TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    size          BIGINT,
                    created_at    TIMESTAMPTZ NOT NULL
                )
            """)


# ---------------------------------------------------------------------------
# Password hashing  (moved here from REST.py)
# ---------------------------------------------------------------------------

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def hash_password(password: str, salt_b64: Optional[str] = None) -> tuple[str, str]:
    """Return (salt_b64, hash_b64). Generate a fresh salt when not supplied."""
    salt = _b64url_decode(salt_b64) if salt_b64 else secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
    return _b64url_encode(salt), _b64url_encode(dk)


def verify_password(password: str, salt_b64: str, hash_b64: str) -> bool:
    _, computed = hash_password(password, salt_b64)
    return hmac.compare_digest(computed, hash_b64)


# ---------------------------------------------------------------------------
# User helpers
# ---------------------------------------------------------------------------

def create_user(email: str, password: str) -> Dict:
    """
    Create a new user. Raises ValueError if the email already exists.
    Returns the created user dict (without password_hash / salt).
    """
    ph = _ph()
    email = email.strip().lower()
    salt, pw_hash = hash_password(password)
    now = _now()

    with _conn() as con:
        cur = _cursor(con)
        # Check uniqueness first so we can raise a meaningful error
        cur.execute(f"SELECT id FROM users WHERE email = {ph}", (email,))
        if cur.fetchone():
            raise ValueError(f"Email already registered: {email}")

        cur.execute(
            f"""
            INSERT INTO users (email, password_hash, salt, created_at)
            VALUES ({ph}, {ph}, {ph}, {ph})
            """,
            (email, pw_hash, salt, now),
        )
        row_id = cur.lastrowid if _is_sqlite() else _postgres_lastval(cur)
        row = _fetch_by_id(cur, "users", row_id)

    return {"id": row["id"], "email": row["email"], "created_at": row["created_at"]}


def get_user_by_email(email: str) -> Optional[Dict]:
    """Return full user row (including password_hash and salt) or None."""
    ph = _ph()
    with _conn() as con:
        cur = _cursor(con)
        cur.execute(
            f"SELECT id, email, password_hash, salt, created_at FROM users WHERE email = {ph}",
            (email.strip().lower(),),
        )
        return _row(cur.fetchone())


def get_user_by_id(user_id: int) -> Optional[Dict]:
    """Return public user row or None."""
    ph = _ph()
    with _conn() as con:
        cur = _cursor(con)
        cur.execute(
            f"SELECT id, email, created_at FROM users WHERE id = {ph}",
            (user_id,),
        )
        return _row(cur.fetchone())


# ---------------------------------------------------------------------------
# Job helpers
# ---------------------------------------------------------------------------

def create_job_record(user_id: int, job_name: str, shared_path: str) -> Dict:
    """
    Insert a jobs row. Raises ValueError if (user_id, job_name) already exists.
    Returns the created job dict.
    """
    ph = _ph()
    now = _now()
    with _conn() as con:
        cur = _cursor(con)
        cur.execute(
            f"SELECT id FROM jobs WHERE user_id = {ph} AND job_name = {ph}",
            (user_id, job_name),
        )
        if cur.fetchone():
            raise ValueError(f"Job already exists: {job_name}")
        cur.execute(
            f"""
            INSERT INTO jobs (user_id, job_name, shared_path, created_at, updated_at)
            VALUES ({ph}, {ph}, {ph}, {ph}, {ph})
            """,
            (user_id, job_name, shared_path, now, now),
        )
        row_id = cur.lastrowid if _is_sqlite() else _postgres_lastval(cur)
        return _row(_fetch_by_id(cur, "jobs", row_id))


def get_job_record(user_id: int, job_name: str) -> Optional[Dict]:
    ph = _ph()
    with _conn() as con:
        cur = _cursor(con)
        cur.execute(
            f"SELECT * FROM jobs WHERE user_id = {ph} AND job_name = {ph}",
            (user_id, job_name),
        )
        return _row(cur.fetchone())


def upsert_job_record(user_id: int, job_name: str, shared_path: str) -> Dict:
    """Return existing record or create a new one. Useful when the job folder
    was created directly on Filestore before a DB record existed."""
    existing = get_job_record(user_id, job_name)
    if existing:
        return existing
    return create_job_record(user_id, job_name, shared_path)


def list_user_jobs(user_id: int) -> List[Dict]:
    ph = _ph()
    with _conn() as con:
        cur = _cursor(con)
        cur.execute(
            f"SELECT * FROM jobs WHERE user_id = {ph} ORDER BY created_at DESC",
            (user_id,),
        )
        return _rows(cur.fetchall())


def delete_job_record(user_id: int, job_name: str) -> None:
    """Delete the jobs row (and cascade to runs/artifacts if FK cascade is set).
    If no cascade, caller should delete runs first."""
    ph = _ph()
    with _conn() as con:
        cur = _cursor(con)
        # Delete dependent runs and their artifacts first (SQLite has no cascade by default)
        cur.execute(
            f"SELECT run_id FROM runs WHERE user_id = {ph} AND job_id IN "
            f"(SELECT id FROM jobs WHERE user_id = {ph} AND job_name = {ph})",
            (user_id, user_id, job_name),
        )
        run_ids = [r["run_id"] if not _is_sqlite() else r[0] for r in cur.fetchall()]
        for rid in run_ids:
            cur.execute(f"DELETE FROM artifacts WHERE run_id = {ph}", (rid,))
        cur.execute(
            f"DELETE FROM runs WHERE user_id = {ph} AND job_id IN "
            f"(SELECT id FROM jobs WHERE user_id = {ph} AND job_name = {ph})",
            (user_id, user_id, job_name),
        )
        cur.execute(
            f"DELETE FROM jobs WHERE user_id = {ph} AND job_name = {ph}",
            (user_id, job_name),
        )


# ---------------------------------------------------------------------------
# Run helpers
# ---------------------------------------------------------------------------

# Valid state values
TERMINAL_STATES = {"COMPLETE", "FAILED", "CANCELLED"}
ACTIVE_STATES = {"QUEUED", "RUNNING", "PAUSE_REQUESTED", "PAUSED", "CANCEL_REQUESTED"}


def create_run(
    job_id: int,
    user_id: int,
    run_id: str,
    k8s_job_name: str,
    shared_run_path: str,
) -> Dict:
    ph = _ph()
    now = _now()
    with _conn() as con:
        cur = _cursor(con)
        cur.execute(
            f"""
            INSERT INTO runs
                (run_id, job_id, user_id, k8s_job_name, desired_state, actual_state,
                 shared_run_path, started_at)
            VALUES ({ph},{ph},{ph},{ph},'QUEUED','QUEUED',{ph},{ph})
            """,
            (run_id, job_id, user_id, k8s_job_name, shared_run_path, now),
        )
        row_id = cur.lastrowid if _is_sqlite() else _postgres_lastval(cur)
        return _row(_fetch_by_id(cur, "runs", row_id))


def update_run(run_id: str, **fields: Any) -> None:
    """
    Update any columns on a runs row by keyword argument.

    Examples:
        update_run(run_id, actual_state="RUNNING", heartbeat_at=_now())
        update_run(run_id, actual_state="COMPLETE", exit_code=0, finished_at=_now())
    """
    if not fields:
        return
    ph = _ph()
    set_clause = ", ".join(f"{col} = {ph}" for col in fields)
    values = list(fields.values()) + [run_id]
    sql = f"UPDATE runs SET {set_clause} WHERE run_id = {ph}"
    with _conn() as con:
        cur = _cursor(con)
        cur.execute(sql, values)


def get_run_by_id(run_id: str) -> Optional[Dict]:
    ph = _ph()
    with _conn() as con:
        cur = _cursor(con)
        cur.execute(f"SELECT * FROM runs WHERE run_id = {ph}", (run_id,))
        return _row(cur.fetchone())


def get_active_run_for_job(job_id: int) -> Optional[Dict]:
    """Return the most recent run that is not in a terminal state, or None."""
    ph = _ph()
    terminal = ", ".join(f"{ph}" for _ in TERMINAL_STATES)
    params = list(TERMINAL_STATES) + [job_id]
    sql = (
        f"SELECT * FROM runs WHERE actual_state NOT IN ({terminal}) "
        f"AND job_id = {ph} ORDER BY started_at DESC LIMIT 1"
    )
    with _conn() as con:
        cur = _cursor(con)
        cur.execute(sql, params)
        return _row(cur.fetchone())


def get_latest_run_for_job(job_id: int) -> Optional[Dict]:
    """Return the most recent run (any state) for the job."""
    ph = _ph()
    with _conn() as con:
        cur = _cursor(con)
        cur.execute(
            f"SELECT * FROM runs WHERE job_id = {ph} ORDER BY started_at DESC LIMIT 1",
            (job_id,),
        )
        return _row(cur.fetchone())


# ---------------------------------------------------------------------------
# Artifact helpers
# ---------------------------------------------------------------------------

def create_artifact(
    run_id: str,
    artifact_type: str,
    relative_path: str,
    size: Optional[int] = None,
) -> Dict:
    ph = _ph()
    now = _now()
    with _conn() as con:
        cur = _cursor(con)
        cur.execute(
            f"""
            INSERT INTO artifacts (run_id, artifact_type, relative_path, size, created_at)
            VALUES ({ph},{ph},{ph},{ph},{ph})
            """,
            (run_id, artifact_type, relative_path, size, now),
        )
        row_id = cur.lastrowid if _is_sqlite() else _postgres_lastval(cur)
        return _row(_fetch_by_id(cur, "artifacts", row_id))


def list_artifacts(run_id: str) -> List[Dict]:
    ph = _ph()
    with _conn() as con:
        cur = _cursor(con)
        cur.execute(
            f"SELECT * FROM artifacts WHERE run_id = {ph} ORDER BY created_at",
            (run_id,),
        )
        return _rows(cur.fetchall())


# ---------------------------------------------------------------------------
# Postgres-specific helper
# ---------------------------------------------------------------------------

def _postgres_lastval(cur) -> int:
    """After an INSERT in Postgres, retrieve the generated id via lastval()."""
    cur.execute("SELECT lastval() AS id")
    return cur.fetchone()["id"]
