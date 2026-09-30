#!/usr/bin/env python3
"""
Regression test: dump_params.py stops the RPC when guitarix never becomes ready.

    python3 tests/night_57_tests.py

dump_params.py is a script (it runs at import), so it is executed with runpy
against a fake gx_rpc module whose on_ready is never called and whose
Event.wait reports a timeout immediately. No guitarix, no network.
"""

import os
import runpy
import sys
import threading
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


class FakeRPC:
    instances = []

    def __init__(self, *args, **kwargs):
        self.started = False
        self.stopped = 0
        FakeRPC.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped += 1


class DumpParamsTimeout(unittest.TestCase):
    def test_rpc_is_stopped_when_readiness_times_out(self):
        FakeRPC.instances = []
        fake = types.ModuleType("gx_rpc")
        fake.GuitarixRPC = FakeRPC
        with mock.patch.dict(sys.modules, {"gx_rpc": fake}), \
                mock.patch.object(sys, "argv", ["dump_params.py"]), \
                mock.patch.object(threading.Event, "wait", return_value=False):
            with self.assertRaises(SystemExit) as ctx:
                runpy.run_path(os.path.join(ROOT, "dump_params.py"), run_name="__main__")
        self.assertIn("could not reach guitarix", str(ctx.exception.code))
        self.assertEqual(len(FakeRPC.instances), 1)
        rpc = FakeRPC.instances[0]
        self.assertTrue(rpc.started)
        self.assertEqual(rpc.stopped, 1, "rpc.stop() must run before exiting on timeout")


if __name__ == "__main__":
    unittest.main()
