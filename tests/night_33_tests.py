#!/usr/bin/env python3
"""
Night 33: the _review_tmp/ scratch directory from PR #4 must not ship.

    python3 tests/night_33_tests.py

_review_tmp/ held 8 review-output copies of files that live properly elsewhere
(static/broadcast.css, templates/broadcast.html, tests/broadcast_tests.py, ...).
It must be gone from the tree, untracked by git, ignored by .gitignore so it
cannot creep back, and referenced by nothing.
"""

import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRATCH = "_review_tmp"

SKIP_DIRS = {".git", ".venv", "__pycache__", ".delegate", "delegate", "node_modules", SCRATCH}
TEXT_EXT = {".py", ".js", ".html", ".css", ".sh", ".service", ".md", ".txt", ".json", ".yml", ".yaml", ".toml"}


class TestReviewTmpRemoved(unittest.TestCase):
    def test_directory_is_gone(self):
        path = os.path.join(ROOT, SCRATCH)
        leftovers = sorted(os.listdir(path)) if os.path.isdir(path) else []
        self.assertFalse(os.path.exists(path),
                         "%s/ still exists with: %s" % (SCRATCH, leftovers))

    def test_nothing_tracked_by_git(self):
        try:
            out = subprocess.run(["git", "ls-files", SCRATCH], cwd=ROOT,
                                 capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            self.skipTest("git not available")
        if out.returncode != 0:
            self.skipTest("not a git checkout")
        self.assertEqual(out.stdout.strip(), "",
                         "files under %s/ are still tracked:\n%s" % (SCRATCH, out.stdout))

    def test_gitignore_blocks_it(self):
        with open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as fh:
            lines = [ln.strip() for ln in fh]
        self.assertTrue(SCRATCH + "/" in lines or SCRATCH in lines,
                        ".gitignore must list %s/" % SCRATCH)

    def test_nothing_references_it(self):
        me = os.path.abspath(__file__)
        hits = []
        for base, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in files:
                path = os.path.join(base, name)
                if os.path.abspath(path) == me or name == ".gitignore":
                    continue
                if os.path.splitext(name)[1].lower() not in TEXT_EXT:
                    continue
                try:
                    with open(path, encoding="utf-8", errors="ignore") as fh:
                        if SCRATCH in fh.read():
                            hits.append(os.path.relpath(path, ROOT))
                except OSError:
                    pass
        self.assertEqual(hits, [], "files reference %s: %s" % (SCRATCH, hits))


if __name__ == "__main__":
    unittest.main()
