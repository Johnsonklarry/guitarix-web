#!/usr/bin/env python3
"""
Night 72: rebuilding the control groups must drop the old controls from the
sliders / readouts / switches / selects maps and cancel pending glide frames.

    python3 tests/night_72_tests.py

The behavioural test pulls releaseControls() out of static/app.js and runs it
under node with fake elements (skipped if node is not installed). The source
test checks renderGroups() calls it before clearing the container.
"""

import json
import os
import re
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP_JS = os.path.join(ROOT, "static", "app.js")


def source():
    with open(APP_JS, encoding="utf-8") as fh:
        return fh.read()


def release_fn():
    m = re.search(r"^function releaseControls\(into\) \{.*?^\}\n", source(),
                  re.S | re.M)
    return m.group(0) if m else None


class TestReleaseControls(unittest.TestCase):
    def test_render_groups_releases_before_clearing(self):
        src = source()
        self.assertIsNotNone(release_fn(), "releaseControls() is missing")
        m = re.search(r"^function renderGroups\(into, groups\) \{\n(.*?)\n", src,
                      re.S | re.M)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1).strip(), "releaseControls(into);")

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_maps_emptied_and_glides_cancelled(self):
        fn = release_fn()
        self.assertIsNotNone(fn, "releaseControls() is missing")
        script = fn + r"""
const sliders = {}, readouts = {}, switches = {}, selects = {};
const dragging = new Set();
const cancelled = [];
function cancelAnimationFrame(h) { cancelled.push(h); }
const inside = new Set();
const into = { contains: function (el) { return inside.has(el); } };
const outsider = {};
const a = { _glide: 7 }, ra = {}, sw = {}, sel = {};
[a, ra, sw, sel].forEach(function (e) { inside.add(e); });
sliders.a = a; readouts.a = ra; switches.t = sw; selects.s = sel;
sliders.keep = outsider; dragging.add('a'); dragging.add('keep');
releaseControls(into);
console.log(JSON.stringify({
  sliders: Object.keys(sliders), readouts: Object.keys(readouts),
  switches: Object.keys(switches), selects: Object.keys(selects),
  cancelled: cancelled, glide: a._glide, dragging: Array.from(dragging),
}));
"""
        out = subprocess.run(["node", "-e", script], capture_output=True,
                             text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        r = json.loads(out.stdout)
        self.assertEqual(r["sliders"], ["keep"])
        self.assertEqual(r["readouts"], [])
        self.assertEqual(r["switches"], [])
        self.assertEqual(r["selects"], [])
        self.assertEqual(r["cancelled"], [7])
        self.assertIsNone(r["glide"])
        self.assertEqual(r["dragging"], ["keep"])


if __name__ == "__main__":
    unittest.main()
