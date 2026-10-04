import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SPIKE = os.path.join(ROOT, "spike_latency.py")


def read_spike():
    with open(SPIKE, "r", encoding="utf-8") as f:
        return f.read()


class TestPing2ErrorHandling(unittest.TestCase):
    """Issue #200: ping2 socket.emit needs a timeout plus an error callback."""

    def setUp(self):
        self.src = read_spike()

    def test_ping2_emit_has_ack_callback(self):
        self.assertIn("socket.emit('ping2'", self.src)

    def test_timeout_cancelled_in_ack(self):
        # the ack callback must clear the timeout it was racing
        ack = re.search(
            r"socket\.emit\('ping2'.*?\{(.*?)\}\s*\)\s*;",
            self.src,
            re.DOTALL,
        )
        self.assertIsNotNone(ack, "could not locate the ping2 emit call")
        self.assertIn("clearTimeout(timer)", ack.group(1))

    def test_error_callback_defined(self):
        # the change introduces an error callback the timeout can invoke
        self.assertIn("onError", self.src)
        self.assertRegex(
            self.src,
            r"const\s+onError\s*=\s*function",
            "expected an onError callback to be defined in sync()",
        )

    def test_timeout_invokes_error_callback(self):
        # the timeout path must call onError rather than silently advancing
        timer = re.search(
            r"const\s+timer\s*=\s*setTimeout\(function\s*\(\)\s*\{(.*?)\}\s*,\s*1000\s*\)",
            self.src,
            re.DOTALL,
        )
        self.assertIsNotNone(timer, "could not locate the ping2 timeout")
        self.assertIn("onError(", timer.group(1))

    def test_catch_invokes_error_callback(self):
        # a synchronous throw from emit must also reach the error callback
        catch = re.search(
            r"catch\s*\(err\)\s*\{(.*?)\n    \}",
            self.src,
            re.DOTALL,
        )
        self.assertIsNotNone(catch, "could not locate the try/catch around emit")
        self.assertIn("onError(err)", catch.group(1))

    def test_sync_still_advances_after_error(self):
        # error handling must not break the loop: next() is still called
        timer = re.search(
            r"const\s+timer\s*=\s*setTimeout\(function\s*\(\)\s*\{(.*?)\}\s*,\s*1000\s*\)",
            self.src,
            re.DOTALL,
        )
        self.assertIsNotNone(timer)
        self.assertIn("next(i + 1)", timer.group(1))


if __name__ == "__main__":
    unittest.main()
