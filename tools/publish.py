"""
tools/publish.py
----------------
Cross-user shared-restart / publish domain logic (v2 architecture).

A user publishes one of their own COMPLETE or PAUSED jobs. Publishing takes an
immutable, point-in-time snapshot copy of the whole job folder into a shared
"published" area on the Filestore and registers a catalog entry. Any logged-in
user can browse the catalog (filtered by publisher email) and add a published
job to their panel as a read-only reference, restart from it, or clone it.

This module hides the Filestore layout of the published area and the catalog
table behind a small interface; eligibility/state resolution is done by the
caller (REST layer), which already has the job folder + run state.
"""

from __future__ import annotations

import logging
import shutil
from typing import Dict, List, Optional

from tools import db
from tools import storage

logger = logging.getLogger("ctoaster.publish")

# Job states from which a snapshot is well-defined (stable restart files exist).
PUBLISHABLE_STATES = {"COMPLETE", "PAUSED"}


def publish_job(
    owner_user_id: int,
    owner_email: str,
    job_path: str,
    title: str,
    description: str,
    base_config: str,
    stage: str,
    stage_year: Optional[str],
    run_length: Optional[str],
) -> Dict:
    """
    Snapshot a job folder into the published area and register a catalog entry.

    The caller is responsible for verifying ownership and that `stage` is one of
    PUBLISHABLE_STATES (the job is COMPLETE or PAUSED, not RUNNING).

    Returns the catalog entry dict (including id, snapshot_path, size_bytes).
    """
    if stage not in PUBLISHABLE_STATES:
        raise ValueError(f"Only COMPLETE or PAUSED jobs can be published (got {stage!r})")
    if not title or not title.strip():
        raise ValueError("A title is required to publish a job")

    # Insert the catalog row first to obtain a stable id, which determines the
    # snapshot directory name; then copy the folder and record path + size.
    entry = db.create_published(
        owner_user_id=owner_user_id,
        owner_email=owner_email,
        title=title.strip(),
        description=(description or "").strip(),
        base_config=base_config or "",
        stage=stage,
        stage_year=stage_year,
        run_length=run_length,
        snapshot_path="",
        size_bytes=0,
    )
    publish_id = entry["id"]
    try:
        dest, size = storage.snapshot_job_to_published(job_path, publish_id)
    except Exception:
        # Roll back the catalog row if the snapshot copy fails.
        db.delete_published(publish_id, owner_user_id)
        raise
    db.set_published_snapshot(publish_id, dest, size)
    entry["snapshot_path"] = dest
    entry["size_bytes"] = size
    return entry


def unpublish(publish_id: int, owner_user_id: int) -> bool:
    """
    Remove a catalog entry (owner-only) and delete its snapshot directory.
    Returns True if something was removed, False if not found / not owned.
    """
    entry = db.get_published_by_id(publish_id)
    if not entry or entry["owner_user_id"] != owner_user_id:
        return False
    deleted = db.delete_published(publish_id, owner_user_id)
    if deleted:
        snap = entry.get("snapshot_path")
        if snap:
            shutil.rmtree(snap, ignore_errors=True)
    return deleted


def list_published(filter_email: Optional[str] = None) -> List[Dict]:
    """Return catalog entries, optionally filtered by publisher email."""
    return db.list_published(filter_email)


def get_published(publish_id: int) -> Optional[Dict]:
    """Return a single catalog entry, or None."""
    return db.get_published_by_id(publish_id)


def resolve_snapshot_path(publish_id: int) -> str:
    """
    Return the absolute, path-traversal-guarded snapshot directory for a
    published job. Raises ValueError if the entry is unknown.
    """
    entry = db.get_published_by_id(publish_id)
    if not entry:
        raise ValueError(f"Published job {publish_id} not found")
    # Re-derive (and validate) the path from the id rather than trusting the
    # stored string.
    return storage.get_published_path(publish_id)
