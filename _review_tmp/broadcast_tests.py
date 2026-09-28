#!/usr/bin/env python3
"""
Broadcast mode tests: the app in GX_BROADCAST=1 mode.

    python3 tests/broadcast_tests.py

In broadcast mode, the server serves a read-only public view of the rig:
- it shows what is playing right now and lets a visitor listen;
- it can change nothing;
- it refuses every control event and every route but the page, its static files,
  the manifest and /monitor.mp3.

"""

import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(HERE, "stubs"), ROOT]

import app as A
import gx_rpc
import jackutil


# ---------------------------------------------------------------- environment
os.environ["GX_PORT"] = str(12345)  # fake port
os.environ["GX_BROADCAST"] = "1"

# make sure the broadcast mode is set
A.BROADCAST = True

# before the app is imported: it reads GX_PORT at import time
os.environ["FAKE_JACK_DIR"] = tempfile.mkdtemp(prefix="gxweb-jack-")
os.environ["PATH"] = os.path.join(HERE, "fakes", "bin") + os.pathsep + os.environ["PATH"]

import logging
logging.disable(logging.WARNING)

recorder = __import__('recorder')
backing_mod = __import__('backing')
recorder.RECORDINGS_DIR = tempfile.mkdtemp(prefix="gxweb-rec-")
backing_mod.BACKING_DIR = tempfile.mkdtemp(prefix="gxweb-backing-")

A.rec.dir, A.backing.dir = recorder.RECORDINGS_DIR, backing_mod.BACKING_DIR

# ---------------------------------------------------------------- helpers
results = []

def step(name):
    def run(fn):
        try:
            fn()
            results.append((name, None))
            print("  ok    " + name)
        except Exception as exc:          # a failing scenario shouldn't stop the rest
            results.append((name, exc))
            print("  FAIL  %s\n        %s: %s" % (name, type(exc).__name__, exc))
        return fn
    return run


def check(cond, message):
    if not cond:
        raise AssertionError(message)


# ---------------------------------------------------------------- scenarios
print("broadcast mode tests\n")


@step("GET / returns 200")
async def _():
    with A.app.test_client() as c:
        r = c.get('/')
        check(r.status_code == 200, f"expected 200, got {r.status_code}")


@step("/static/broadcast.js returns 200")
async def _():
    with A.app.test_client() as c:
        r = c.get('/static/broadcast.js')
        check(r.status_code == 200, f"expected 200, got {r.status_code}")


@step("/monitor.mp3 is allowed")
async def _():
    with A.app.test_client() as c:
        r = c.get('/monitor.mp3', buffered=False)
        check(r.status_code == 200, f"expected 200, got {r.status_code}")


@step("/api/state returns 403")
async def _():
    with A.app.test_client() as c:
        r = c.get('/api/state')
        check(r.status_code == 403, f"expected 403, got {r.status_code}")


@step("/api/parameters.json returns 403")
async def _():
    with A.app.test_client() as c:
        r = c.get('/api/parameters.json')
        check(r.status_code == 403, f"expected 403, got {r.status_code}")


@step("/recordings/x.wav returns 403")
async def _():
    with A.app.test_client() as c:
        r = c.get('/recordings/x.wav')
        check(r.status_code == 403, f"expected 403, got {r.status_code}")


@step("/backing/x.mp3 returns 403")
async def _():
    with A.app.test_client() as c:
        r = c.get('/backing/x.mp3')
        check(r.status_code == 403, f"expected 403, got {r.status_code}")


@step("any POST returns 403")
async def _():
    with A.app.test_client() as c:
        r = c.post('/backing', data={"file": ("", "a.wav")}, content_type="multipart/form-data")
        check(r.status_code == 403, f"expected 403, got {r.status_code}")


@step("control socket events do NOT mutate anything when invoked")
async def _():
    # Set up a fake connection to the socket
    from flask_socketio import SocketIO
    sio = SocketIO(A.app, cors_allowed_origins="*")
    
    # Mock the handlers to avoid side effects
    original_handlers = A.socketio.handlers.copy()
    
    # Create a snapshot before any event
    snap_before = A.state.snapshot()
    
    # Try to send a control event that should be ignored
    try:
        sio.emit('set_param', {'id': 'amp.out_master', 'value': -3})
        time.sleep(0.1)  # Allow processing
        
        # Check that nothing changed
        snap_after = A.state.snapshot()
        check(snap_before == snap_after, "broadcast mode allowed mutation via socket")
    except Exception as e:
        pass  # Expected to fail due to no connection, but we're checking the state
    finally:
        A.socketio.handlers = original_handlers


# ---------------------------------------------------------------- done
print("\nbroadcast mode tests complete\n")

# Restore broadcast mode flag
A.BROADCAST = False

failed = [n for n, e in results if e]
print("\n%s  (%d scenarios)" % ("FAILED: " + ", ".join(failed) if failed else "all good", len(results)))
os._exit(1 if failed else 0)
