#!/usr/bin/env python3
"""
Night 20: bulk-select and delete takes from the Record tab.

    python3 tests/night_20_tests.py

Recorder.delete_many removes N takes at once, each with its dry twin and
settings, never reaches outside the recordings folder, refuses a take that is
still recording, and tells the clients once (so the list and the free-space
readout refresh together). The page, the socket handler and the demo are
checked for the wiring by reading their sources. No guitarix, JACK or network.
"""

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import recorder as R                # noqa: E402


def source(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


class TestBulkDelete(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gx-night20-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.calls = []
        self.rec = R.Recorder(directory=os.path.join(self.tmp, "rec"),
                              on_change=lambda: self.calls.append(1))

    def take(self, stem, dry=True, settings=True):
        names = [stem + ".wav"]
        if dry:
            names.append(stem + R.DRY_SUFFIX + ".wav")
        if settings:
            names.append(stem + ".json")
        for n in names:
            with open(os.path.join(self.rec.dir, n), "wb") as fh:
                fh.write(b"x" * 10)
        return stem + ".wav"

    def files(self):
        return sorted(os.listdir(self.rec.dir))

    def test_deletes_every_selected_take_with_its_siblings(self):
        a, b, c = self.take("A"), self.take("B"), self.take("C", dry=False)
        keep = self.take("Keep")
        deleted, failed = self.rec.delete_many([a, b, c])
        self.assertEqual(deleted, [a, b, c])
        self.assertEqual(failed, [])
        self.assertEqual(self.files(), sorted(["Keep.wav", "Keep" + R.DRY_SUFFIX + ".wav", "Keep.json"]))
        self.assertEqual([i["name"] for i in self.rec.listing()], [keep])

    def test_clients_are_told_once_for_the_whole_batch(self):
        names = [self.take("A"), self.take("B"), self.take("C")]
        self.rec.delete_many(names)
        self.assertEqual(len(self.calls), 1)

    def test_nothing_deleted_means_no_notification(self):
        self.rec.delete_many(["nope.wav"])
        self.assertEqual(self.calls, [])

    def test_paths_outside_the_folder_are_untouched(self):
        secret = os.path.join(self.tmp, "secret.wav")
        with open(secret, "wb") as fh:
            fh.write(b"keep me")
        inside = self.take("Inside")
        deleted, failed = self.rec.delete_many(
            ["../secret.wav", secret, "..\\secret.wav", "../../etc/passwd", "sub/../../secret.wav"])
        self.assertEqual(deleted, [])
        self.assertEqual(len(failed), 5)
        self.assertTrue(os.path.isfile(secret))
        self.assertTrue(os.path.isfile(os.path.join(self.rec.dir, inside)))

    def test_a_take_that_is_still_recording_is_refused(self):
        live, other = self.take("Live", dry=False), self.take("Other", dry=False)
        proc = mock.Mock()
        proc.poll.return_value = None
        self.rec._proc = proc
        self.rec._path = os.path.join(self.rec.dir, live)
        deleted, failed = self.rec.delete_many([live, other])
        self.assertEqual(deleted, [other])
        self.assertEqual([n for n, _ in failed], [live])
        self.assertTrue(os.path.isfile(os.path.join(self.rec.dir, live)))

    def test_bad_entries_and_duplicates_dont_stop_the_rest(self):
        a = self.take("A")
        deleted, failed = self.rec.delete_many([None, 5, a, a, "missing.wav"])
        self.assertEqual(deleted, [a])
        self.assertEqual([n for n, _ in failed], ["missing.wav"])

    def test_single_delete_still_works(self):
        a = self.take("A")
        self.rec.delete(a)
        self.assertEqual(self.files(), [])
        self.assertEqual(len(self.calls), 1)
        with self.assertRaises(FileNotFoundError):
            self.rec.delete(a)


class TestWiring(unittest.TestCase):
    def test_server_route_exists_and_uses_delete_many(self):
        src = source("app.py")
        self.assertIn('@socketio.on("rec_delete_many")', src)
        self.assertIn("rec.delete_many(", src)

    def test_page_selects_and_asks_before_deleting(self):
        js = source("static", "app.js")
        self.assertIn("'rec_delete_many'", js)
        self.assertIn("Delete selected", js)
        self.assertIn("takes__check", js)
        # the confirmation comes before the emit
        self.assertLess(js.index("Type DELETE to confirm", js.index("Delete selected")),
                        js.index("run('rec_delete_many'"))

    def test_demo_knows_the_event(self):
        self.assertIn("rec_delete_many", source("static", "demo.js"))


if __name__ == "__main__":
    unittest.main()
