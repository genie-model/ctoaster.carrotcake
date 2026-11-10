"""Behavioural tests for tools.fields_frames (2D-field history frames).

Synthetic files mimic the model output: dims (time, zt, lat, lon), tracer
variables shaped (time, zt, lat, lon) with a missing_value for land, and a
global 'year' attribute.

Run from the repo root:  python3 -m unittest discover -s tools/tests -t .
"""
import os
import shutil
import tempfile
import time
import unittest

import netCDF4 as nc
import numpy as np

from tools import fields_frames as ff

NZ, NY, NX = 3, 2, 4
FILL = 9.96920996838687e36


def _field(base: float) -> np.ndarray:
    """Depth-dependent field: surface varies widely, deep levels narrowly."""
    a = np.zeros((NZ, NY, NX))
    for k in range(NZ):
        a[k] = base + (10.0 / (k + 1)) * np.arange(NY * NX).reshape(NY, NX)
    a[:, 0, 0] = FILL  # one land column
    return a


def _write_nc(path: str, year: int, temp: np.ndarray) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with nc.Dataset(path, "w") as ds:
        ds.setncattr("year", str(year))
        ds.createDimension("time", None)
        ds.createDimension("zt", NZ)
        ds.createDimension("lat", NY)
        ds.createDimension("lon", NX)
        ds.createVariable("zt", "f8", ("zt",))[:] = [10.0, 100.0, 1000.0]
        ds.createVariable("lat", "f8", ("lat",))[:] = [-10.0, 10.0]
        ds.createVariable("lon", "f8", ("lon",))[:] = [-255.0, -245.0, -235.0, -225.0]
        v = ds.createVariable("ocn_temp", "f4", ("time", "zt", "lat", "lon"))
        v.missing_value = np.float32(FILL)
        v[0] = temp


class _JobDir(unittest.TestCase):
    def setUp(self) -> None:
        self.out = tempfile.mkdtemp()  # plays the job's output/biogem/
        ff._RANGE_CACHE.clear()

    def tearDown(self) -> None:
        shutil.rmtree(self.out, ignore_errors=True)

    def snapshot(self, year: int, base: float = 0.0) -> None:
        _write_nc(os.path.join(self.out, ff.SNAPSHOT_FILE), year, _field(base))

    def frame(self, year: int, base: float = 0.0) -> None:
        _write_nc(os.path.join(self.out, ff.FRAMES_DIR, f"fields_{year}.nc"), year, _field(base))


class ListYearsTest(_JobDir):
    def test_frames_plus_live_year_sorted(self):
        for y in (5, 1, 2):
            self.frame(y)
        self.snapshot(6)
        self.assertEqual(ff.list_years(self.out), {"years": [1, 2, 5, 6], "live_year": 6})

    def test_live_year_not_duplicated_when_it_is_a_frame(self):
        self.frame(1)
        self.frame(2)
        self.snapshot(2)
        self.assertEqual(ff.list_years(self.out)["years"], [1, 2])

    def test_old_job_without_frames_has_only_live_year(self):
        self.snapshot(9)
        self.assertEqual(ff.list_years(self.out), {"years": [9], "live_year": 9})

    def test_init_only_snapshot_has_no_years(self):
        self.snapshot(-1)  # zero-filled metadata file written at model init
        self.assertEqual(ff.list_years(self.out), {"years": [], "live_year": None})

    def test_negative_model_years_in_filenames(self):
        self.frame(-500)
        self.frame(-400)
        self.snapshot(-400)
        self.assertEqual(ff.list_years(self.out)["years"], [-500, -400])


class ReadFieldTest(_JobDir):
    def test_reads_requested_year_from_its_frame(self):
        self.frame(1, base=0.0)
        self.frame(2, base=100.0)
        self.snapshot(3, base=200.0)
        got = ff.read_field(self.out, "ocn_temp", year=2)
        self.assertEqual(got["year"], 2)
        self.assertTrue(got["is3d"])
        self.assertAlmostEqual(got["values"][0][0][1], 110.0, places=4)  # 100 + 10*1

    def test_no_year_reads_live_snapshot(self):
        self.frame(1)
        self.snapshot(3, base=200.0)
        got = ff.read_field(self.out, "ocn_temp")
        self.assertEqual(got["year"], 3)
        self.assertAlmostEqual(got["values"][0][0][1], 210.0, places=4)  # 200 + 10*1

    def test_live_year_without_frame_reads_snapshot(self):
        self.frame(1)
        self.snapshot(3, base=200.0)
        self.assertEqual(ff.read_field(self.out, "ocn_temp", year=3)["year"], 3)

    def test_land_cells_are_null(self):
        self.snapshot(1)
        self.assertIsNone(ff.read_field(self.out, "ocn_temp")["values"][0][0][0])

    def test_unknown_year_raises(self):
        self.snapshot(3)
        with self.assertRaises(KeyError):
            ff.read_field(self.out, "ocn_temp", year=99)

    def test_unknown_variable_raises(self):
        self.snapshot(3)
        with self.assertRaises(KeyError):
            ff.read_field(self.out, "ocn_nope")


class SliceRangeTest(_JobDir):
    def setUp(self) -> None:
        super().setUp()
        self.frame(1, base=0.0)
        self.frame(2, base=50.0)
        self.snapshot(3, base=20.0)

    def test_horizontal_range_spans_all_years_for_that_depth_only(self):
        # level 2 (deepest): values base + (10/3)*idx, idx 1..7 (idx 0 is land)
        lo, hi = ff.slice_range(self.out, "ocn_temp", "horizontal", 2)
        self.assertAlmostEqual(lo, 0.0 + 10.0 / 3, places=4)
        self.assertAlmostEqual(hi, 50.0 + 70.0 / 3, places=4)

    def test_surface_and_deep_ranges_differ(self):
        surf = ff.slice_range(self.out, "ocn_temp", "horizontal", 0)
        deep = ff.slice_range(self.out, "ocn_temp", "horizontal", 2)
        self.assertGreater(surf[1] - surf[0], deep[1] - deep[0])

    def test_vertical_range_at_latitude_ignores_land(self):
        lo, hi = ff.slice_range(self.out, "ocn_temp", "vertical", 0)
        # lat row 0 holds idx 0..3; idx 0 is land, so min uses idx 1 at the deepest level
        self.assertAlmostEqual(lo, 0.0 + 10.0 / 3, places=4)
        self.assertAlmostEqual(hi, 50.0 + 30.0, places=4)

    def test_zonal_range_uses_longitude_means(self):
        lo, hi = ff.slice_range(self.out, "ocn_temp", "zonal", None)
        # deepest level, lat row 0, year 1: mean of idx 1..3 = 2 -> 2*(10/3)
        self.assertAlmostEqual(lo, 20.0 / 3, places=4)
        # surface, lat row 1, year 2: mean of idx 4..7 = 5.5 -> 50 + 55
        self.assertAlmostEqual(hi, 105.0, places=4)

    def test_range_updates_when_a_new_frame_arrives(self):
        before = ff.slice_range(self.out, "ocn_temp", "horizontal", 0)
        time.sleep(0.01)
        self.frame(4, base=500.0)
        after = ff.slice_range(self.out, "ocn_temp", "horizontal", 0)
        self.assertGreater(after[1], before[1])

    def test_bad_slice_raises(self):
        with self.assertRaises(ValueError):
            ff.slice_range(self.out, "ocn_temp", "diagonal", 0)


if __name__ == "__main__":
    unittest.main()
