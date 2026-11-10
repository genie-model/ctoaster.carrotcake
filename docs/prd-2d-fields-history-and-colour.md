# PRD — 2D Fields: time history, colour controls, sliders

Status: approved design (grill-me, 2026-11-10). Local document — not filed as a
GitHub issue. Builds on `docs/2d-fields-plot-design.md` (branch
`2d-fields-plots`, PRs genie-model/ctoaster.carrotcake#10 and
genie-model/cupcake-frontend#11, still open); this work is stacked on branch
`2d-fields-history` in both repos.

## Problem Statement

Andy's feedback on the live 2D Fields tab:

1. Users can only see the **latest** year. There is no way to go back and look
   at an earlier year of the run (no history to scrub).
2. Colour scaling hides detail. The range is the min/max over **all years and
   all depths** of a variable, so (a) deep-ocean slices, whose values span a
   much narrower range than the surface, render as a single colour, and (b)
   early spin-up extremes wash out later years. Andy asked for the colours to
   be **rescaled each timeslice**.
3. Users cannot set the colour range or colormap themselves.
4. Depth (and latitude) are long dropdowns; sliders are more natural.

Root cause of (1): the model writes one `biogem_fields_snapshot.nc` that is
**overwritten every year**, so no history exists.

## Solution

- **Per-year history frames.** Alongside the live snapshot, the model writes
  each sampled year's annual mean to its own small file. The backend lists the
  available years and serves any *variable × year*.
- **Time slider** in the 2D Fields tab, usable **during the run**: it follows
  the newest year until the user scrubs, then stays put; a **Live** button snaps
  back and resumes following; **▶ Play** animates through the years.
- **Colour scale selector** — **Per frame** (default; each year stretched to its
  own range), **Fixed** (one range across all years, for honest comparison) and
  **Manual** (user min/max + colormap). In every mode the range is computed from
  **the slice on screen** (this depth / this section), not the whole 3D field.
- **Sliders** for depth (vertical, surface at top) and latitude (horizontal),
  snapping to model grid levels.

## User Stories

1. As a student watching a run, I want the plot to keep showing the newest
   year automatically, so I can follow the run live.
2. As a student, I want to drag a time slider back to an earlier year, so I can
   compare how a field has changed.
3. As a user who scrubbed back, I want the slider to stay where I left it when
   new years arrive, and a Live button to return to the newest year.
4. As a lecturer, I want to press Play and watch the field evolve year by year,
   so I can demonstrate a run to a class.
5. As a user reopening a finished run, I want the full history still available.
6. As a user viewing a deep-ocean slice, I want colours stretched to that
   slice's range, so its structure is visible.
7. As a user comparing years, I want a Fixed scale so a colour means the same
   value in every year.
8. As a user preparing a figure, I want to set min/max and pick a colormap.
9. As a user switching variables, I want each variable to keep its own manual
   settings.
10. As a user, I want sliders for depth and latitude that snap to model levels
    and show the actual depth/latitude.

## Implementation Decisions

### Model (Fortran, `src/biogem/`)
- Keep `biogem_fields_snapshot.nc` exactly as today (zero-filled at init for
  metadata; overwritten each year with the latest annual mean = the live frame).
- Add history frames: `output/biogem/fields_frames/fields_<year>.nc` (integer
  year in the name so listing needs no file opens). Same structure/writer as the
  live snapshot.
- **Cap: ≤200 frames per job.** `stride = ceiling(par_misc_t_runtime / 200)`;
  a frame is written for every `stride`-th completed year and for the final
  (flushed) year. Each frame is that single year's annual mean, labelled with its
  year. Runs ≤200 years keep every year.
- Create the `fields_frames/` directory at init.

### Runner sync (`tools/storage.py`)
- `sync_to_shared` copies only **new or changed** files (size + mtime check);
  today it re-copies every output every 2 s. Atomic `.nc` handling unchanged.

### Backend API (`tools/REST.py`)
- `GET /get-field-frames/{job_name}` → sorted list of frame years + the live
  year (from the snapshot), so the slider can include the newest year even when
  it is not on the stride.
- `GET /get-field-snapshot/{job_name}/{var}?year=Y` → optional `year`; serves
  the frame for that year, else the live snapshot (current behaviour, unchanged).
- `GET /get-field-range/{job_name}/{var}?slice=horizontal|vertical|zonal&index=i`
  → min/max of that slice across all frames (for **Fixed** mode); cached per
  job/var/slice/index, invalidated when the frame set changes.
- Job **download zip** and **publish snapshot** both skip `fields_frames/`.
- Old jobs with no `fields_frames/`: frames endpoint returns only the live year;
  the UI hides the slider.

### Frontend (`cupcake-frontend/src/components/Plots.js`, FieldsPlot)
- Time slider (horizontal) + Live + Play/Pause; follow-live state.
- Depth slider (vertical, surface at top) and latitude slider (horizontal),
  snapping to grid levels, labelled with real depth/latitude.
- Colour scale selector: Per frame (default) / Fixed / Manual.
  Manual: min/max inputs (prefilled from current range) + Reset; colormap
  dropdown: Viridis (default), Plasma, RdBu, Greys. Manual settings stored per
  variable in browser storage (failure-tolerant).
- Range always computed from the slice on screen; Fixed uses the range endpoint.
- Frames fetched per *variable × year*, cached in memory for smooth playback.

## Testing Decisions

Test external behaviour, not internals.
- **Sync (TDD):** unchanged files are not re-copied; changed/new files are;
  `.nc` still published atomically.
- **Backend endpoints (TDD)** against a temp directory of synthetic netCDF
  frames: frame listing (incl. live year, old-job fallback), `year` selection,
  range per slice across frames, download/publish exclusion.
- **Fortran:** compile in the backend image + short local model run; check
  frame count/stride and contents (as done for the original feature).
- **Frontend:** production build + browser check of slider/Live/Play, colour
  modes, sliders.
- Deploy (backend image, model exe on Filestore, frontend) only after the above
  pass and with the requester's OK.

## Out of Scope

- History/slider for the separate **Surface Temperature** tab (seasonal
  mechanism). Retiring that tab is to be raised with Andy.
- Frames in the job download (excluded; a separate download can be added).
- Log colour scale (can be added later).
- Isotope tracers, surface/atmosphere fields (separate items).

## Further Notes

- Storage: measured ~0.5 MB/frame at 8 levels, 12 tracers (405-yr local run: 136 frames = 66 MB) → ~1 MB/frame at 16 levels → ≤~200 MB per job at the 200-frame cap. Scales with tracer count.
- The sync change also removes an existing cost: every output file (e.g.
  `fields_biogem_3d.nc`) is currently re-copied to Filestore every 2 s.
