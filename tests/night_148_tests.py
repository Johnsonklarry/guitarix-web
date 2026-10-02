#!/usr/bin/env python3
"""Regression tests for the port-polling loop in tools/connect-input.sh.

Covers the two requirements of sub-task 1 of #39:
  1. the loop terminates correctly when the port is already connected
  2. the loop terminates correctly when the port never appears

The script is driven with fake jack_lsp / jack_connect / sleep binaries on
PATH, so no real JACK server is needed.  The "never appears" case uses a
large ATTEMPTS value with a fake sleep that records how many times it was
called; without the fix the loop sleeps once more after the final attempt,
so the recorded sleep count exceeds ATTEMPTS.
"""

import os
import stat
import subprocess
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, "tools", "connect-input.sh")


def write_exe(path, body):
    with open(path, "w") as fh:
        fh.write(body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


class ConnectInputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bindir = os.path.join(self.tmp.name, "bin")
        os.makedirs(self.bindir)
        self.sleep_log = os.path.join(self.tmp.name, "sleeps")
        with open(self.sleep_log, "w") as fh:
            fh.write("")

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, env_extra=None):
        env = dict(os.environ)
        env["PATH"] = self.bindir + os.pathsep + env.get("PATH", "")
        env["GX_CONNECT_SLEEP_LOG"] = self.sleep_log
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            ["/bin/sh", SCRIPT],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=60,
        )

    def install_fake_sleep(self):
        write_exe(
            os.path.join(self.bindir, "sleep"),
            "#!/bin/sh\n"
            "echo x >> \"$GX_CONNECT_SLEEP_LOG\"\n"
            "exit 0\n",
        )

    def sleep_count(self):
        with open(self.sleep_log) as fh:
            return len(fh.read().split())

    def test_already_connected_terminates(self):
        """Port present and already wired: exit 0, no jack_connect, no sleep."""
        write_exe(
            os.path.join(self.bindir, "jack_lsp"),
            "#!/bin/sh\n"
            "case \"$1\" in\n"
            "  -c) printf 'gx_head_amp:in_0\\n  system:capture_1\\n' ;;\n"
            "  *)  printf 'system:capture_1\\ngx_head_amp:in_0\\n' ;;\n"
            "esac\n"
            "exit 0\n",
        )
        write_exe(
            os.path.join(self.bindir, "jack_connect"),
            "#!/bin/sh\necho 'jack_connect should not be called' >&2\nexit 1\n",
        )
        self.install_fake_sleep()

        proc = self.run_script({"GX_CONNECT_ATTEMPTS": "5"})

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("already connected", proc.stdout)
        self.assertEqual(self.sleep_count(), 0, "loop slept despite being connected")

    def test_port_never_appears_terminates(self):
        """Ports never show up: exit 1 after exactly ATTEMPTS attempts."""
        write_exe(
            os.path.join(self.bindir, "jack_lsp"),
            "#!/bin/sh\nexit 0\n",
        )
        write_exe(
            os.path.join(self.bindir, "jack_connect"),
            "#!/bin/sh\nexit 1\n",
        )
        self.install_fake_sleep()

        attempts = 4
        proc = self.run_script({"GX_CONNECT_ATTEMPTS": str(attempts)})

        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("gave up after %d attempts" % attempts, proc.stderr)
        self.assertEqual(
            self.sleep_count(),
            attempts - 1,
            "expected %d sleeps between %d attempts, got %d"
            % (attempts - 1, attempts, self.sleep_count()),
        )


if __name__ == "__main__":
    unittest.main()
