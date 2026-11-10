"""
2D-field snapshots and their per-year history frames.

The model (BIOGEM) writes, under a job's output/biogem/ directory:
  - biogem_fields_snapshot.nc   the live frame: the latest annual mean, rewritten
                                every model year (zero-filled at init for
                                metadata only);
  - fields_frames/fields_<year>.nc
                                history frames: one annual mean per sampled year
                                (<= 200 per job), never rewritten.

Every file holds tracer variables shaped (time=1, zt, lat, lon) (or
(time=1, lat, lon) for 2D fields) with land / sub-seafloor cells masked via
missing_value, and a global 'year' attribute.

This module answers three questions for the API: which years exist, what a
variable looks like in a given year, and the colour range of one slice across
all years. It has no web-framework dependency.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Tuple

import netCDF4 as nc
import numpy as np

from tools.storage import HISTORY_FRAMES_DIR

SNAPSHOT_FILE = "biogem_fields_snapshot.nc"
FRAMES_DIR = HISTORY_FRAMES_DIR
SLICES = ("horizontal", "vertical", "zonal")

_FRAME_RE = re.compile(r"^fields_(-?\d+)\.nc$")

# (out_dir, var, slice, index) -> (signature, (lo, hi) | None)
_RANGE_CACHE: Dict[tuple, tuple] = {}


# ── which years exist ─────────────────────────────────────────────────────────

def _frame_paths(out_dir: str) -> Dict[int, str]:
    """year -> path of every history frame (no file opens)."""
    frames_dir = os.path.join(out_dir, FRAMES_DIR)
    found: Dict[int, str] = {}
    try:
        entries = os.scandir(frames_dir)
    except FileNotFoundError:
        return found
    with entries:
        for e in entries:
            m = _FRAME_RE.match(e.name)
            if m and e.is_file():
                found[int(m.group(1))] = e.path
    return found


def _live_year(out_dir: str) -> Optional[int]:
    """Year of the live snapshot, or None if absent or only the init file."""
    path = os.path.join(out_dir, SNAPSHOT_FILE)
    if not os.path.isfile(path):
        return None
    with nc.Dataset(path, "r") as ds:
        attrs = ds.ncattrs()
        if "year" not in attrs:
            return None
        year = int(ds.getncattr("year"))
        if "init" in attrs:
            is_init = str(ds.getncattr("init")).strip() == "1"
        else:
            is_init = year == -1  # files written before the 'init' attribute
    return None if is_init else year


def list_years(out_dir: str) -> dict:
    """
    {"years": sorted frame years plus the live year, "live_year": int | None}.
    The live year is included even when it is not on the sampling stride, so
    the time slider always reaches the newest year.
    """
    years = set(_frame_paths(out_dir))
    live = _live_year(out_dir)
    if live is not None:
        years.add(live)
    return {"years": sorted(years), "live_year": live}


def _path_for(out_dir: str, year: Optional[int]) -> Tuple[str, int]:
    """(file path, year it represents) for a request; KeyError if unavailable."""
    live = _live_year(out_dir)
    if year is None:
        if live is None:
            raise KeyError("no live frame yet")
        return os.path.join(out_dir, SNAPSHOT_FILE), live
    frames = _frame_paths(out_dir)
    if year in frames:
        return frames[year], year
    if year == live:
        return os.path.join(out_dir, SNAPSHOT_FILE), year
    raise KeyError(f"no frame for year {year}")


# ── one variable, one year ────────────────────────────────────────────────────

def _field_var(ds: "nc.Dataset", var: str):
    if var not in ds.variables:
        raise KeyError(f"unknown variable {var}")
    v = ds.variables[var]
    if "lon" not in v.dimensions or "lat" not in v.dimensions:
        raise KeyError(f"{var} is not a plottable field")
    return v


def read_field(out_dir: str, var: str, year: Optional[int] = None) -> dict:
    """
    {"year", "is3d", "values"} for one variable. values is depth x lat x lon
    (surface first) for 3D variables, lat x lon for 2D; masked cells are None.
    year=None means the live frame. KeyError if the year or variable is unknown.
    """
    path, actual_year = _path_for(out_dir, year)
    with nc.Dataset(path, "r") as ds:
        v = _field_var(ds, var)
        return {
            "year": actual_year,
            "is3d": "zt" in v.dimensions,
            # drop the unit time axis; .tolist() turns masked cells into None
            "values": v[0].tolist(),
        }


# ── colour range of one slice across all years ────────────────────────────────

def _slice(field: np.ma.MaskedArray, kind: str, index: Optional[int]) -> np.ma.MaskedArray:
    """The 2D grid a plot shows for this slice of one year's field."""
    if field.ndim == 2:  # surface-only variable: only the map exists
        if kind != "horizontal":
            raise ValueError(f"{kind} slice needs a 3D variable")
        return field
    if kind == "horizontal":
        return field[index, :, :]   # depth level -> lat x lon
    if kind == "vertical":
        return field[:, index, :]   # latitude    -> depth x lon
    return field.mean(axis=2)       # zonal mean  -> depth x lat (ocean cells only)


def _signature(out_dir: str) -> tuple:
    """Changes whenever a frame is added or the live frame is rewritten."""
    frames = _frame_paths(out_dir)
    newest = max((os.stat(p).st_mtime_ns for p in frames.values()), default=0)
    snap = os.path.join(out_dir, SNAPSHOT_FILE)
    snap_mtime = os.stat(snap).st_mtime_ns if os.path.isfile(snap) else 0
    return (len(frames), newest, snap_mtime)


def slice_range(out_dir: str, var: str, kind: str,
                index: Optional[int]) -> Optional[Tuple[float, float]]:
    """
    (min, max) of one slice of `var` over every available year, ignoring
    masked cells; None if there is no data yet. Used for the Fixed colour
    scale, so a colour means the same value in every year. Cached until the
    set of frames changes.
    """
    if kind not in SLICES:
        raise ValueError(f"unknown slice {kind!r}")
    if kind != "zonal" and index is None:
        raise ValueError(f"{kind} slice needs an index")

    key = (out_dir, var, kind, index)
    sig = _signature(out_dir)
    cached = _RANGE_CACHE.get(key)
    if cached and cached[0] == sig:
        return cached[1]

    paths: List[str] = list(_frame_paths(out_dir).values())
    if _live_year(out_dir) is not None:
        paths.append(os.path.join(out_dir, SNAPSHOT_FILE))

    lo, hi = np.inf, -np.inf
    for path in paths:
        with nc.Dataset(path, "r") as ds:
            grid = _slice(np.ma.asarray(_field_var(ds, var)[0]), kind, index)
        if grid.count():
            lo, hi = min(lo, float(grid.min())), max(hi, float(grid.max()))

    result = (lo, hi) if np.isfinite(lo) else None
    _RANGE_CACHE[key] = (sig, result)
    return result
