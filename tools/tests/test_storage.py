"""Behavioural tests for tools.storage file movement.

Run from the repo root:  python3 -m unittest discover -s tools/tests -t .
"""
import os
import shutil
import tempfile
import time
import unittest
import zipfile
from unittest import mock

from tools import storage


def _write(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def _read(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


class _TmpDirs(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp()
        self.work = os.path.join(self.root, "work")
        self.shared = os.path.join(self.root, "shared")
        os.makedirs(self.work)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


class SyncToSharedTest(_TmpDirs):
    def _copies_during_sync(self) -> list:
        """Run one sync; return the source paths that were actually copied."""
        copied = []
        real = storage._copy_bytes

        def spy(src, dst):
            copied.append(src)
            real(src, dst)

        with mock.patch.object(storage, "_copy_bytes", side_effect=spy):
            storage.sync_to_shared(self.work, self.shared)
        return copied

    def test_new_files_are_copied_including_subdirectories(self):
        _write(os.path.join(self.work, "run.log"), b"hello")
        _write(os.path.join(self.work, "output", "biogem", "a.nc"), b"nc-data")
        storage.sync_to_shared(self.work, self.shared)
        self.assertEqual(_read(os.path.join(self.shared, "run.log")), b"hello")
        self.assertEqual(
            _read(os.path.join(self.shared, "output", "biogem", "a.nc")), b"nc-data"
        )

    def test_unchanged_files_are_not_recopied(self):
        _write(os.path.join(self.work, "run.log"), b"hello")
        _write(os.path.join(self.work, "output", "biogem", "a.nc"), b"nc-data")
        storage.sync_to_shared(self.work, self.shared)
        self.assertEqual(self._copies_during_sync(), [])

    def test_changed_file_is_recopied(self):
        log = os.path.join(self.work, "run.log")
        _write(log, b"hello")
        storage.sync_to_shared(self.work, self.shared)
        _write(log, b"hello, more output")
        self.assertEqual(self._copies_during_sync(), [log])
        self.assertEqual(_read(os.path.join(self.shared, "run.log")), b"hello, more output")

    def test_same_size_rewrite_is_recopied(self):
        # The live fields snapshot is overwritten every model year at the same
        # size, sometimes many times a second; it must not be mistaken for
        # unchanged.
        snap = os.path.join(self.work, "snap.nc")
        _write(snap, b"year-0001")
        storage.sync_to_shared(self.work, self.shared)
        time.sleep(0.002)
        _write(snap, b"year-0002")
        self.assertEqual(self._copies_during_sync(), [snap])
        self.assertEqual(_read(os.path.join(self.shared, "snap.nc")), b"year-0002")

    def test_netcdf_leaves_no_temp_file(self):
        _write(os.path.join(self.work, "a.nc"), b"nc-data")
        storage.sync_to_shared(self.work, self.shared)
        self.assertEqual(sorted(os.listdir(self.shared)), ["a.nc"])


class PublishSnapshotTest(_TmpDirs):
    def test_published_snapshot_skips_history_frames(self):
        job = os.path.join(self.work, "job")
        _write(os.path.join(job, "output", "biogem", "fields_biogem_3d.nc"), b"x")
        _write(os.path.join(job, "output", "biogem", "fields_frames", "fields_1.nc"), b"f")
        with mock.patch.object(storage, "get_published_root", return_value=self.shared), \
             mock.patch.object(storage, "get_published_path",
                               return_value=os.path.join(self.shared, "7")):
            dest, _ = storage.snapshot_job_to_published(job, 7)
        self.assertTrue(os.path.isfile(os.path.join(dest, "output", "biogem", "fields_biogem_3d.nc")))
        self.assertFalse(os.path.exists(os.path.join(dest, "output", "biogem", "fields_frames")))


class ZipJobTest(_TmpDirs):
    def test_zip_contains_outputs_but_not_history_frames(self):
        job = os.path.join(self.work, "job")
        _write(os.path.join(job, "config", "job.config"), b"cfg")
        _write(os.path.join(job, "output", "biogem", "fields_biogem_3d.nc"), b"x")
        _write(os.path.join(job, "output", "biogem", "fields_frames", "fields_1.nc"), b"f")
        archive = storage.zip_job(job, os.path.join(self.root, "job"))
        with zipfile.ZipFile(archive) as zf:
            names = sorted(zf.namelist())
        self.assertIn("config/job.config", names)
        self.assertIn("output/biogem/fields_biogem_3d.nc", names)
        self.assertFalse(any("fields_frames" in n for n in names))


if __name__ == "__main__":
    unittest.main()
