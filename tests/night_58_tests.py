#!/usr/bin/env python3
"""
Night 58: guitarix-connect.service ordering (After=guitarix-jack.service) only
says the JACK unit's start job finished, not that JACK accepts clients. The
wiring therefore has to come from tools/connect-input.sh waiting for the ports
itself. These tests pin that, using the JACK fakes in tests/fakes/bin:

    python3 tests/night_58_tests.py

  * JACK / guitarix ports not there yet: the script keeps polling (does not
    exit early, does not connect), and gives up non-zero when they never come.
  * Ports appear late: the script connects once they do and exits 0.
  * Already wired: exit 0, no duplicate edge.
  * The unit's ExecStart is not prefixed with '-', so a failure is visible.

Needs a POSIX sh and the executable fake tools; skipped otherwise.
"""

import json
import os
import subprocess
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPT = os.path.join(ROOT, "tools", "connect-input.sh")
UNIT = os.path.join(ROOT, "guitarix-connect.service")
FAKE_BIN = os.path.join(HERE, "fakes", "bin")

SRC = "system:capture_1"
DST = "gx_head_amp:in_0"

JACK_ONLY = {
    "system:capture_1": "output",
    "system:playback_1": "input",
}
FULL = dict(JACK_ONLY, **{
    "gx_head_amp:in_0": "input",
    "gx_head_amp:out_0": "output",
})


def write_graph(directory, ports, connections):
    tmp = os.path.join(directory, "graph.tmp")
    with open(tmp, "w") as f:
        json.dump({"ports": ports, "connections": connections}, f)
    os.replace(tmp, os.path.join(directory, "graph.json"))


def read_graph(directory):
    with open(os.path.join(directory, "graph.json")) as f:
        return json.load(f)


def can_run():
    sh = any(os.access(os.path.join(p, "sh"), os.X_OK)
             for p in os.environ.get("PATH", "").split(os.pathsep) if p)
    return (os.name == "posix" and sh
            and os.access(os.path.join(FAKE_BIN, "jack_lsp"), os.X_OK)
            and os.access(os.path.join(FAKE_BIN, "jack_connect"), os.X_OK))


@unittest.skipUnless(can_run(), "needs POSIX sh and executable fake JACK tools")
class ConnectWaitsForJack(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def env(self, attempts, delay="0.05"):
        env = dict(os.environ)
        env["PATH"] = FAKE_BIN + os.pathsep + env.get("PATH", "")
        env["FAKE_JACK_DIR"] = self.dir
        env["GX_CONNECT_ATTEMPTS"] = str(attempts)
        env["GX_CONNECT_DELAY"] = delay
        return env

    def start(self, attempts):
        return subprocess.Popen(["sh", SCRIPT], env=self.env(attempts),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True)

    def test_gives_up_nonzero_when_ports_never_appear(self):
        write_graph(self.dir, {}, [])
        p = self.start(3)
        out, err = p.communicate(timeout=30)
        self.assertEqual(p.returncode, 1)
        self.assertIn("gave up", err)
        self.assertEqual(read_graph(self.dir)["connections"], [])

    def test_waits_while_guitarix_ports_missing_then_connects(self):
        # JACK is up (system ports exist) but guitarix has not registered yet:
        # the script must neither fail fast nor connect to anything.
        write_graph(self.dir, JACK_ONLY, [])
        p = self.start(400)
        time.sleep(0.4)
        self.assertIsNone(p.poll(), "script exited before the ports appeared")
        self.assertEqual(read_graph(self.dir)["connections"], [])

        write_graph(self.dir, FULL, [])
        out, err = p.communicate(timeout=30)
        self.assertEqual(p.returncode, 0, err)
        self.assertIn("connected", out)
        self.assertEqual(read_graph(self.dir)["connections"], [[SRC, DST]])

    def test_already_connected_is_success_without_duplicate(self):
        write_graph(self.dir, FULL, [[SRC, DST]])
        p = self.start(5)
        out, err = p.communicate(timeout=30)
        self.assertEqual(p.returncode, 0, err)
        self.assertIn("already connected", out)
        self.assertEqual(read_graph(self.dir)["connections"], [[SRC, DST]])


class UnitFile(unittest.TestCase):
    def test_execstart_runs_the_waiting_script_and_failure_is_not_masked(self):
        with open(UNIT) as f:
            lines = [l.strip() for l in f if l.strip().startswith("ExecStart=")]
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].endswith("tools/connect-input.sh"), lines[0])
        self.assertFalse(lines[0].startswith("ExecStart=-"), lines[0])


if __name__ == "__main__":
    unittest.main()
