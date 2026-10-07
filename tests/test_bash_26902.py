"""Regression tests for issue #26902: port-polling loop termination.

``tools/connect-input.sh`` polls jackd for its ports and must give up in
bounded time.  Two termination paths matter:

* the requested connection already exists -- a service restart must report
  success and exit 0 immediately instead of retrying or failing, and
* the ports never appear -- the loop must stop once its retry budget is
  exhausted and exit non-zero instead of polling forever.

The shell script itself is exercised end to end with stub ``jack_lsp`` and
``jack_connect`` binaries placed on a throwaway PATH inside a temp dir.
The poll-until-deadline contract is additionally covered directly through
``wait_for_state()``.
"""

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "tools" / "connect-input.sh"

SRC = "system:capture_1"
DST = "gx_head_amp:in_0"

# Hard kill for a script invocation that hangs.
SCRIPT_TIMEOUT = 15.0
# Termination is expected to be near-instant; well below the kill above.
PROMPT_LIMIT = 5.0


def wait_for_state(condition, timeout=1.0, interval=0.01, message="state not reached"):
    """Poll ``condition()`` until it is truthy or ``timeout`` elapses.

    Mirrors the bounded poll loop in tools/connect-input.sh: retry until a
    deadline instead of assuming the state already exists, and raise once
    the deadline passes instead of spinning forever.
    """
    deadline = time.monotonic() + timeout
    while True:
        state = condition()
        if state:
            return state
        if time.monotonic() >= deadline:
            raise TimeoutError(message)
        time.sleep(interval)


class WaitForStateTests(unittest.TestCase):
    """The polling contract the script's retry loop is built on."""

    def test_returns_promptly_when_condition_becomes_true(self):
        calls = []

        def condition():
            calls.append(1)
            return len(calls) >= 3

        started = time.monotonic()
        result = wait_for_state(condition, timeout=5.0, interval=0.01)
        elapsed = time.monotonic() - started

        self.assertTrue(result)
        self.assertEqual(len(calls), 3)
        # Returned as soon as the condition held, not at the deadline.
        self.assertLess(elapsed, 1.0)

    def test_returns_immediately_when_state_already_present(self):
        started = time.monotonic()
        self.assertEqual(wait_for_state(lambda: "connected"), "connected")
        self.assertLess(time.monotonic() - started, 1.0)

    def test_raises_timeout_error_when_condition_stays_false(self):
        calls = []

        def condition():
            calls.append(1)
            return False

        started = time.monotonic()
        with self.assertRaises(TimeoutError) as ctx:
            wait_for_state(
                condition, timeout=0.2, interval=0.01, message="ports never appeared"
            )
        elapsed = time.monotonic() - started

        self.assertIn("ports never appeared", str(ctx.exception))
        self.assertGreaterEqual(len(calls), 1)
        self.assertLessEqual(len(calls), 200)
        # Ran until the deadline and then stopped: neither instant nor hung.
        self.assertGreaterEqual(elapsed, 0.2)
        self.assertLess(elapsed, SCRIPT_TIMEOUT)

    def test_zero_timeout_gives_up_immediately(self):
        calls = []

        def condition():
            calls.append(1)
            return False

        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            wait_for_state(condition, timeout=0.0, interval=0.01)

        self.assertEqual(len(calls), 1)
        self.assertLess(time.monotonic() - started, 1.0)


class ConnectInputScriptTests(unittest.TestCase):
    """End-to-end checks that the script's loop terminates in bounded time."""

    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), f"missing script: {SCRIPT}")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmpdir = Path(tmp.name)
        self.bindir = self.tmpdir / "bin"
        self.bindir.mkdir()
        self.connect_marker = self.tmpdir / "jack_connect.called"

    def write_stub(self, name, body):
        stub = self.bindir / name
        stub.write_text("#!/bin/sh\n" + body, encoding="utf-8")
        stub.chmod(0o755)
        return stub

    def add_jack_lsp_stub(self, lines):
        # The stub ignores its arguments and always prints the same listing.
        listing = "\n".join(lines)
        self.write_stub("jack_lsp", f"cat <<'EOF'\n{listing}\nEOF\n")

    def add_jack_connect_stub(self, exit_status):
        self.write_stub(
            "jack_connect",
            'if [ -n "${JACK_CONNECT_MARKER:-}" ]; then\n'
            '    echo "$*" >> "$JACK_CONNECT_MARKER"\n'
            "fi\n"
            f"exit {exit_status}\n",
        )

    def run_script(self, attempts="3", delay="0.05"):
        env = os.environ.copy()
        env["PATH"] = str(self.bindir) + os.pathsep + env.get("PATH", "")
        env["GX_CONNECT_SRC"] = SRC
        env["GX_CONNECT_DST"] = DST
        env["GX_CONNECT_ATTEMPTS"] = attempts
        env["GX_CONNECT_DELAY"] = delay
        env["JACK_CONNECT_MARKER"] = str(self.connect_marker)

        started = time.monotonic()
        try:
            proc = subprocess.run(
                ["/bin/sh", str(SCRIPT)],
                capture_output=True,
                text=True,
                env=env,
                timeout=SCRIPT_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            self.fail(f"connect-input.sh did not terminate within {SCRIPT_TIMEOUT}s")
        return proc, time.monotonic() - started

    def test_already_connected_terminates_successfully(self):
        # jack_lsp -c gx_head_amp:in_0 already shows the wiring in place.
        self.add_jack_lsp_stub([DST, "   " + SRC])
        self.add_jack_connect_stub(exit_status=1)

        proc, elapsed = self.run_script()

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("already connected", proc.stdout)
        self.assertFalse(
            self.connect_marker.exists(),
            "jack_connect must not be called when the link already exists",
        )
        self.assertLess(elapsed, PROMPT_LIMIT)

    def test_missing_ports_terminate_after_exhaustion(self):
        # Neither configured port ever shows up in jack_lsp.
        self.add_jack_lsp_stub(["system:playback_1", "gx_head_amp:out_0"])
        self.add_jack_connect_stub(exit_status=0)

        proc, elapsed = self.run_script(attempts="3", delay="0.05")

        self.assertEqual(proc.returncode, 1)
        self.assertIn("gave up after 3 attempts", proc.stderr)
        self.assertFalse(self.connect_marker.exists())
        # It actually polled (3 x 0.05s) and then stopped instead of hanging.
        self.assertGreaterEqual(elapsed, 0.1)
        self.assertLess(elapsed, PROMPT_LIMIT)

    def test_unconnectable_ports_exit_non_zero(self):
        # Ports are visible but never link up: the loop must stop with a
        # failure status rather than retrying forever.
        self.add_jack_lsp_stub([SRC, DST])
        self.add_jack_connect_stub(exit_status=1)

        proc, elapsed = self.run_script()

        self.assertEqual(proc.returncode, 1)
        self.assertIn("jack_connect refused", proc.stderr)
        self.assertTrue(self.connect_marker.exists())
        self.assertLess(elapsed, PROMPT_LIMIT)


if __name__ == "__main__":
    unittest.main()
