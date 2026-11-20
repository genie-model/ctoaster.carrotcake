# 2D spatial field plots for all variables — locked design

Live 2D spatial plots (horizontal maps + vertical sections) for **every** enabled
model variable, updating live as a run progresses — generalizing the existing
single SST heatmap. Derived from Andy's meeting notes + a design interview
(2026-07-24). Status: **design locked, implementation not started.**

## Goal / UX

A third tab in the Plots page alongside "Time series" and the SST "Heatmap":

- **Horizontal slice** — lon x lat map at a chosen **depth level** (generalizes
  the SST heatmap to any variable, any depth).
- **Vertical slice** — lon x depth cross-section at a chosen **latitude**
  (surface at top, land / below-seafloor masked).
- Dropdowns: **variable**, **slice type**, and **depth (horizontal) / latitude
  (vertical)**. Populated automatically from run metadata, no hard-coded list.

## Core architectural decision (Q1 = A, reuse)

Reuse the proven **Fortran-writes-small-netCDF -> Python backend reads it ->
serves tiny JSON -> frontend polls on a change-token** pattern that already backs
the live SST heatmap. Do **not** have Fortran write many ASCII slice files
(Andy's literal note #6) — that path breaks on the default sparse timeslice
schedule and causes a file explosion.

### Why not just read the existing `fields_biogem_3d.nc`

That archival file is written on the sparse, log-spaced `save_timeslice.dat`
schedule (default: `0.5, 1.5, 4.5, 9.5, 19.5, 49.5, ...`). A short teaching run
writes only ~3-5 records, at non-annual times. Reading it would produce almost
**no live frames** on a normal run. The cadence is also a per-run user-config
choice we do not control.

### The mechanism: generalize the SST snapshot

Add one new writer modeled exactly on `sub_data_netCDF_temp_snapshot`
(`src/biogem/biogem_data_netCDF.f90:149`), which already reads **live in-memory
arrays** (independent of `save_timeslice.dat`) and writes atomically:

- **`biogem_fields_snapshot.nc`** — a single-record file, **overwritten** each
  seasonal quarter (not accumulated -> stays ~1 MB), atomic `.tmp` + rename,
  carrying the `quarter_index` global attr as the frontend change-token.
- Written **at run init, zero-filled**, defining **every enabled variable + all
  axes** (lon, lat, zt/depth). This init file **doubles as the dropdown
  metadata** — it folds Andy's separate "dummy netCDF" (note #5) into the same
  mechanism, so there is **one** writer instead of two.
- Reads the live in-memory field arrays (like the SST writer reads `ocn(io_T)`),
  so it is **immune to the sparse-timeslice trap** and works on short runs.

## Locked decisions

| # | Decision | Choice |
|---|----------|--------|
| Q1 | Data path | **A** — reuse netCDF -> backend-read -> JSON (not Fortran ASCII files) |
| Q2 | Cadence | **annual mean, one frame per model year** (revised per Andy — see below; was seasonal in the first cut) |
| Q3 | Variables | **(a)** all enabled fields, **auto-discovered** (same loops as fields netCDF), readable `long_name` labels |
| Q4 | Color scale | **(b2)** whole-run **expanding** range — frontend tracks running min/max per variable across all frames seen; never clips, no per-variable curation |
| Q5 | Vertical slice | lon x depth section at a chosen **latitude** (reversed depth axis, surface top). Zonal-mean sections deferred |
| Q6 | 2D-only variables | **auto-detect** dimensionality from the snapshot; for a 2D variable hide the depth dropdown and disable the vertical tab |

## Implementation details (locked)

**Fortran** (`src/biogem/`)
- New snapshot writer generalizing `sub_data_netCDF_temp_snapshot` to loop over
  all enabled variables (2D + 3D), reusing the enable flags / tracer loops used
  by the fields netCDF savers (`ctrl_data_save_slice_*`, `n_l_ocn`, etc.).
- Init call writes the file zero-filled with full variable + axis definitions.
- Per-quarter call overwrites with current in-memory values; atomic write; bump
  `quarter_index`. Wire the trigger like the SST snapshot
  (`src/carrotcake.f90:409` -> `genie_loop_wrappers.f90:344`).
- Do **not** hardcode grid dims — read `n_i/n_j/n_k` as the existing code does
  (`biogem.f90:60-62`); depth levels vary by config (default 8, some 16).

**Backend** (`tools/REST.py`)
- Metadata endpoint: list variables (`long_name`, units, 2D-vs-3D flag) + axis
  arrays (lon, lat, depth) read from `biogem_fields_snapshot.nc`.
- Data endpoint: return **one variable's full array per request** (~<1 MB, not
  all ~30 at once) + axes + `token`.
- Reuse `_resolve_read_path` / `find_plot_data_path` and the
  `nc.Dataset` + `.tolist()` masked-array -> `null` shaping from
  `/get-temp-snapshot` (`REST.py:1209`). Masked land / below-bathymetry -> null.

**Frontend** (`cupcake-frontend/src/components/Plots.js`)
- Add a third `activeTab` value + button; conditional render a new
  `FieldsPlot` component.
- Chained dropdowns (variable -> slice type -> depth/latitude), following the
  existing timeseries chained-`<select>` pattern.
- Fetch the selected variable's full 3D array **once per token**; do depth-level
  and latitude slicing **client-side in JS** -> instant dropdown response, one
  endpoint, less backend load. Maintain per-variable running min/max (Q4 b2).
- Reuse the generalized `TempHeatmap` Plotly block; **drop `scaleanchor` /
  `aspectRatio`** for vertical sections and use a reversed depth y-axis; keep the
  token + 2.5 s polling while the job is in an active state.

## Deliverables (mapped to Andy's action items)

1. **Fortran** — the unified `biogem_fields_snapshot.nc` writer (folds Andy #5
   dummy netCDF + #6 slice mechanism into one).
2. **Backend** — metadata + single-variable data endpoints.
3. **Frontend** — 2D Fields tab: horizontal/vertical slice types, metadata-driven
   dropdowns, Plotly render.

## To verify during implementation

- Actual per-config depth levels (`n_k`: default 8, some configs 16) — read from
  the snapshot axes, never hardcode.
- Snapshot file size with all enabled 3D vars at the run's real config
  (~1 MB expected; confirm the 4x/year overwrite + 2 s sync stays cheap).
- Whether the auto-discovered dropdown is too noisy for students (obscure
  diagnostics) — filtering is a later frontend-only change if so.

## Andy's feedback (2026-07-24) — incorporated

- **Annual averages, not seasonal.** Andy ran a 10-yr seasonal test
  (`modern.OCEAN16` / `.SPIN`) and counted 17 frames, not 40 — seasonal isn't
  reliably 4x/yr, and an under-sampled seasonal signal on a trend aliases. He
  asked for **annual averages**: guarantees >=1 frame/year, never skips a year.
  → Reworked the writer to accumulate `ocn()` every timestep and publish one
  annual-mean frame per year (`sub_fields_snapshot_update` at each year rollover;
  `sub_fields_snapshot_finalize` in `end_biogem` flushes the final partial year).
  Token = the model **year**; the init/zero file uses year -1 so the frontend
  waits for the first real frame.
- **"Year: xxx" label** above the plot so users see run progress. → Added
  (backend returns `year`; frontend shows it).
- **Do lon-lat (horizontal) first.** Both slice types are built; horizontal is
  the focus for the first live test, vertical is a bonus.
- Confirmed: as netCDF; all variables auto-discovered / no hard-coded list; three
  tabs; read variables once the first slice is written (we do it at init).

## Still to tell Andy in the next email

- **Isotope tracers are skipped in v1** (delta-conversion sentinel would corrupt
  the auto colour scale) — easy follow-up.
- Possible future: option to show a student-friendly subset vs. all variables.
