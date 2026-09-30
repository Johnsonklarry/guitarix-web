#!/usr/bin/env python3
"""
Night 44: the broadcast process must show the Studio process's recording state
and a real take count, not its own (always idle) Recorder, and never a null.

    python3 tests/night_44_tests.py

Two independent processes: a child Studio (GX_BROADCAST unset, GX_REC_FEED set)
pretends to start/stop a take and publishes the feed; this process is the
broadcast (GX_BROADCAST=1, same GX_REC_FEED) and only reads it. No guitarix or
JACK needed.
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


WORK = tempfile.mkdtemp(prefix="gxweb-night44-")
FEED = os.path.join(WORK, "rec-feed.json")
REC_DIR = os.path.join(WORK, "recordings")
os.makedirs(REC_DIR)
for name in ("Attempt 1.wav", "Attempt 2.wav", "Attempt 2 (dry).wav", "Attempt 1.json"):
    with open(os.path.join(REC_DIR, name), "wb") as f:
        f.write(b"x")                    # two takes; a dry twin and a sidecar

# app.py reads these at import time
os.environ["GX_BROADCAST"] = "1"
os.environ["GX_REC_FEED"] = FEED
os.environ["GX_PORT"] = str(free_port())
os.environ["FAKE_JACK_DIR"] = tempfile.mkdtemp(prefix="gxweb-jack-")
os.environ["PATH"] = os.path.join(HERE, "fakes", "bin") + os.pathsep + os.environ["PATH"]
sys.path[:0] = [os.path.join(HERE, "stubs"), ROOT]

import logging                       # noqa: E402
logging.disable(logging.WARNING)

import app as A                      # noqa: E402

A.rec.dir = REC_DIR

STUDIO = r'''
import os, sys
here, root, rec_dir, action = sys.argv[1:5]
sys.path[:0] = [os.path.join(here, "stubs"), root]
import logging
logging.disable(logging.WARNING)
import app as A
assert not A.BROADCAST and A.REC_FEED
A.rec.dir = rec_dir
class Rolling:
    def poll(self):
        return None
if action == "start":
    A.rec._proc = Rolling()
A.publish_rec_feed()
os._exit(0)
'''


def studio(action):
    env = dict(os.environ)
    env.pop("GX_BROADCAST", None)
    env["GX_PORT"] = str(free_port())
    subprocess.run([sys.executable, "-c", STUDIO, HERE, ROOT, REC_DIR, action],
                   env=env, check=True, timeout=60)


class BroadcastFollowsStudio(unittest.TestCase):
    def setUp(self):
        for p in (FEED, FEED + ".tmp"):
            if os.path.exists(p):
                os.remove(p)

    def test_count_is_never_null(self):
        self.assertEqual(A.rec.status()["count"], 2)       # takes, not dry/sidecar
        self.assertEqual(A.broadcast_snapshot()["takes"], 2)

    def test_studio_start_and_stop_reach_broadcast(self):
        self.assertIs(A.broadcast_snapshot()["recording"], False)
        studio("start")
        snap = A.broadcast_snapshot()
        self.assertIs(snap["recording"], True)
        self.assertEqual(snap["takes"], 2)
        self.assertFalse(A.rec.recording)                   # not our own recorder
        studio("stop")
        snap = A.broadcast_snapshot()
        self.assertIs(snap["recording"], False)
        self.assertEqual(snap["takes"], 2)

    def test_reconnect_reads_the_current_feed(self):
        studio("start")
        first = A.broadcast_snapshot()
        studio("stop")
        again = A.broadcast_snapshot()          # what a reconnecting viewer gets
        self.assertIs(first["recording"], True)
        self.assertIs(again["recording"], False)

    def test_takes_files_alone_do_not_mean_recording(self):
        # Take files sit in the folder, and there is no feed: not recording.
        self.assertFalse(os.path.exists(FEED))
        self.assertIs(A.broadcast_snapshot()["recording"], False)

    def test_stale_feed_is_not_recording(self):
        studio("start")
        later = time.time() + A.REC_FEED_STALE + 30
        self.assertIs(A.read_rec_feed(now=later)["recording"], False)

    def test_bad_feed_is_not_recording(self):
        for text in ("not json", "[]", "7", '{"recording": true}',
                     '{"recording": true, "at": "x"}'):
            with open(FEED, "w") as f:
                f.write(text)
            self.assertIs(A.broadcast_snapshot()["recording"], False, text)
            self.assertEqual(A.broadcast_snapshot()["takes"], 2, text)
        with open(FEED, "w") as f:
            json.dump({"recording": "yes", "count": 3, "at": time.time()}, f)
        self.assertIs(A.broadcast_snapshot()["recording"], False)

    def test_only_approved_fields_are_published(self):
        with open(FEED, "w") as f:
            json.dump({"recording": True, "count": 5, "at": time.time(),
                       "file": "secret.wav", "stop": True}, f)
        self.assertEqual(A.read_rec_feed(), {"recording": True, "count": 5})
        sent = []
        real = A._socketio_emit
        try:
            A._socketio_emit = lambda ev, data=None, *a, **k: sent.append((ev, data))
            A.socketio.emit("rec", A.rec_payload())
        finally:
            A._socketio_emit = real
        self.assertEqual(sent, [("rec", {"recording": True, "count": 5})])
        self.assertNotIn("secret", json.dumps(A.broadcast_snapshot()))


if __name__ == "__main__":
    unittest.main()
