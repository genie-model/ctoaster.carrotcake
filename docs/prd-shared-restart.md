# PRD — Cross-user Shared Restart / Publish

Status: approved design (grill-me, 2026-06-19). Local document — not filed as a
GitHub issue and not pushed to remote, per the requester's constraint.

## Problem Statement

A user runs a long cGENIE job (potentially millions of model years) with a
particular configuration. Once it reaches a useful state (finished, or paused
at a checkpoint), they want **other users of the platform to be able to use
that run** — to inspect its results, download it, and especially to **start
their own runs as a "restart from" continuation of it** — without having to
re-run the expensive spin-up themselves. Today every job is private to the
account that created it (`owner.json` gates each job to one user), so there is
no way to hand a checkpoint to colleagues or students.

## Solution

A **publish / browse / consume** workflow:

- A user **publishes** one of their own COMPLETE or PAUSED jobs. Publishing
  takes an **immutable, point-in-time snapshot copy** of the whole job folder
  into a shared "published" area and registers a catalog entry with a
  user-supplied **title + optional description**.
- Any logged-in user can **browse the global catalog**, filtered by the
  **publisher's email**, and **add** a published job to their own panel.
- An added entry behaves like a normal job in the panel (highlighted with a
  **blue background** so it is recognisably a published/shared job) but is a
  lightweight **reference** by default. The recipient can **view** it
  (status / output / plots), **download** it, **restart from** it (spawning
  their own new run), **clone** it (opt-in full editable copy), or **remove**
  it from their panel.
- **Restart from a published job inherits and locks the source `base_config`**
  so the continuation is grid/module-compatible; the recipient chooses only the
  forward parameters (run length, extra modifications).

This rides on machinery that already exists: the centralized Filestore RWX
volume (so the data is physically reachable cluster-wide — only *compute* is
decentralized), and the existing `restart` / `copy_restart_files` /
`restart_options` continuation mechanics. The pause path already writes a full
restart set for every active module, so **pausing is the mid-run checkpoint
mechanism**.

## User Stories

1. As a job owner, I want to publish a COMPLETE job, so that others can build on my finished run.
2. As a job owner, I want to publish a PAUSED job, so that I can share a mid-run checkpoint at a chosen model year.
3. As a job owner, I must NOT be able to publish a RUNNING job, so that nobody ever reads a half-written, moving snapshot.
4. As a job owner, I want to give a published job a title and optional description, so that strangers understand what the run is.
5. As a job owner, I want publishing to take an immutable copy, so that resuming or deleting my original job never changes or breaks what others see.
6. As a job owner, I want to unpublish a job, so that I can withdraw it from the catalog at will.
7. As a job owner, when I unpublish, I accept that recipients who only added (not yet restarted/cloned) it lose access, so that the model stays simple and I keep control of my content.
8. As any logged-in user, I want to browse a global catalog of published jobs, so that I can find runs to reuse.
9. As any logged-in user, I want to filter the catalog by a publisher's email, so that I can find a specific person's shared runs.
10. As any logged-in user, I want to see each published job's title, description, base_config, stage (COMPLETE / PAUSED@year), run length, date, and size, so that I can tell runs apart.
11. As a recipient, I want to add a published job to my panel, so that I can work with it like any other job.
12. As a recipient, I want added published jobs highlighted with a blue background when selected, so that I know it is a shared/published job, not my own.
13. As a recipient, I want to view a published job's Status, Output/log, and Plots (time-series + surface-temperature heatmap) read-only, so that I can evaluate it before using it.
14. As a recipient, I want to download a published job as a .zip, so that I can keep my own copy of the results.
15. As a recipient, I want to restart from a published job, so that I continue the science from that checkpoint without redoing spin-up.
16. As a recipient, when I restart, I want the base_config inherited and locked to the source, so that my continuation is compatible and does not silently fail.
17. As a recipient, when I restart, I want to set my own run length and modifications, so that I control the forward run.
18. As a recipient, I want a restart to produce a new job in my own panel that I fully own, so that I can run/pause/delete it freely.
19. As a recipient, I want to clone a published job into a full editable copy, so that I can own all of its outputs when I really need to.
20. As a recipient, I want to remove a published reference from my panel, so that I can declutter without affecting the publisher's snapshot.
21. As a recipient, I must NOT be able to run, pause, delete, or edit the publisher's snapshot itself, so that the shared artifact stays immutable.
22. As a recipient who already restarted or cloned, I want to be insulated from later unpublishing, so that my work is not lost.
23. As a recipient, if I open a reference whose source was unpublished, I want a clear "no longer available" state, so that I am not confused by errors.
24. As a user who cloned a published job, I want to be able to re-publish my clone, so that I can share my own derived run.
25. As an owner, I want publishing to validate ownership and job state server-side, so that the rules cannot be bypassed by the client.
26. As an operator, I want published snapshots stored under a dedicated Filestore area, so that they are isolated from per-user job folders and easy to account for.

## Implementation Decisions

### New deep module: publish/catalog domain (`tools/publish.py`)
Encapsulates all publish/catalog logic behind a small interface; hides the
Filestore layout of the published area, the catalog table, and the
ownership/state checks. Proposed interface:
- `publish_job(user, job_name, title, description) -> entry` — assert owner + state ∈ {COMPLETE, PAUSED}; snapshot-copy the folder; insert catalog row; return entry (incl. publish_id, stage, stage_year, size).
- `unpublish(user, publish_id)` — owner-only; delete catalog row + snapshot dir.
- `list_published(filter_email: str | None) -> list[entry]`.
- `get_published(publish_id) -> entry | None`.
- `resolve_snapshot_path(publish_id) -> str` (safe, path-traversal guarded).

### Schema changes (`tools/db.py`)
- New table `published_jobs(id, owner_user_id, owner_email, title, description, base_config, stage, stage_year, run_length, snapshot_path, size_bytes, created_at)`.
- Add nullable `ref_publish_id` to `jobs` so a recipient's added reference is an ordinary job row whose `ref_publish_id` points at the catalog entry and whose `shared_path` is the published snapshot (read-only). This keeps the panel listing unified (one `list_user_jobs` path) and lets the UI flag references by the presence of `ref_publish_id`.
- New functions: `create_published`, `get_published_by_id`, `list_published`, `delete_published`, plus reference creation/removal via existing job-record helpers.

### Storage helpers (`tools/storage.py`)
- `get_published_root()` → `<FILESTORE_ROOT>/PUBLISHED`.
- `get_published_path(publish_id)`.
- `snapshot_job_to_published(user_id, job_name, publish_id) -> (path, size_bytes)` — full immutable folder copy (reusing `_copy_file` atomic-`.nc` behaviour and `safe_join`).
- `dir_size(path)`.

### API contracts (`tools/REST.py`)
- `POST /publish/{job_name}` `{title, description}` → publish.
- `DELETE /publish/{publish_id}` → unpublish (owner-only).
- `GET /published?email=` → catalog list.
- `GET /published/{publish_id}` → entry detail.
- `POST /published/{publish_id}/add` → create reference job in caller's panel.
- `POST /published/{publish_id}/clone` → full copy into caller's space (new owned job).
- `POST /published/{publish_id}/restart` `{job_name, run_length, modifications}` → new owned job with base_config inherited+locked, restart files copied from snapshot, configured as a continuing run.
- Existing read endpoints (`/get-log`, `/get_data_files_list`, `/get-variables`, `/get-plot-data`, `/get-temp-snapshot`, `/jobs/{name}/download`, `/setup`) resolve a reference job (`ref_publish_id` set) to the published snapshot path and bypass the owner check (public-read), while keeping the path-traversal guard.
- `/run-job`, `/pause-job`, `/delete-job` reject jobs with `ref_publish_id` set (cannot mutate the publisher's snapshot); "remove from panel" deletes only the reference job row.
- `/jobs` listing returns `ref_publish_id` (and publisher email/title) so the UI can render the blue highlight.

### Restart wiring (`tools/config_utils.py` / restart endpoint)
The restart endpoint resolves the source restart files from the published
snapshot and configures the new job as a continuing run (`restart_options`),
copying `*restart*` / `*rst*` files (and `sedcore.nc`) via the existing
`copy_restart_files` logic, with `base_config` taken from the published entry
and not editable by the recipient.

### Frontend
- New `BrowsePublished.js` — email filter, results list with metadata, Add/Clone/Restart buttons.
- `HomePage.js` — "Browse Published" entry point; blue-background styling for reference jobs (distinct from the existing `.selected-job` left-border); wire add/clone/restart/remove and publish/unpublish actions.
- Publish/Unpublish controls surfaced where job actions live, enabled only for own COMPLETE/PAUSED jobs.
- `api.js` — new calls.

## Testing Decisions

A good test asserts **external behaviour**, not internals. The natural deep
module to test is `tools/publish.py` against a temp Filestore + SQLite:
- publishing a COMPLETE (and a PAUSED) job creates a catalog entry and an
  independent snapshot directory;
- publishing a RUNNING job is rejected;
- the snapshot survives deletion/mutation of the original job (immutability);
- `list_published(email)` filters correctly;
- `unpublish` removes the catalog row and snapshot and is owner-only;
- restart resolution copies a non-empty restart set and locks the source
  base_config.
Prior art: `tools/coverage.py` already reasons about restart dependency graphs
and `copy_restart_files`; mirror that file-fixture style. Per the requester's
"no additional things / report when done" instruction, the build prioritises a
working deployed feature with smoke validation; the above is the test surface
if/when a formal suite is added.

## Out of Scope

- Per-year / arbitrary-checkpoint selection (only one restart set exists per
  job; pause is the checkpoint mechanism).
- Live mirroring of a still-running source (snapshot only).
- Provenance chains for re-published clones ("derived from X").
- Targeted/private shares to specific users (catalog is public to all logged-in
  users).
- Storage quotas / size caps on the published area (**v2 follow-up risk**: many
  large published runs could pressure the 1Ti Filestore).

## Further Notes

- Publishing pays a one-time full-folder copy (publisher's deliberate cost);
  recipients pay nothing unless they Clone.
- Published snapshots are self-contained (they include the job's own config),
  so restart works even if the platform's shared config library later changes.
- Decentralization is not a blocker: storage is centralized (Filestore RWX);
  only run execution is per-pod.
