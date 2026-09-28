#!/usr/bin/env python3
"""
Broadcast tests: the app under GX_BROADCAST=1, proving it can change nothing.

    python3 tests/broadcast_tests.py

GX_BROADCAST is read at import time, and the socket guard refuses events by
never registering the real handler -- so this file sets the environment BEFORE
importing app, exactly as the server does. Setting app.BROADCAST afterwards
would flip the flag without unregistering anything, and every control check
here would pass while the handlers were still live. That is the one mistake
this file exists to not make.

No guitarix and no JACK are needed: nothing here reaches the engine, and the
point is what the server refuses before it would.
"""

import io
import os
import socket
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ---------------------------------------------------------------- environment
# Before the import, all of it: app.py reads these at module level.
os.environ["GX_BROADCAST"] = "1"
os.environ["GX_PORT"] = str(free_port())          # nothing is listening there
os.environ["FAKE_JACK_DIR"] = tempfile.mkdtemp(prefix="gxweb-jack-")
os.environ["PATH"] = os.path.join(HERE, "fakes", "bin") + os.pathsep + os.environ["PATH"]
sys.path[:0] = [os.path.join(HERE, "stubs"), ROOT]

import logging                       # noqa: E402
logging.disable(logging.WARNING)     # the scenarios provoke refusals on purpose

import recorder                      # noqa: E402
import backing as backing_mod        # noqa: E402
recorder.RECORDINGS_DIR = tempfile.mkdtemp(prefix="gxweb-rec-")
backing_mod.BACKING_DIR = tempfile.mkdtemp(prefix="gxweb-backing-")

import app as A                      # noqa: E402

A.rec.dir, A.backing.dir = recorder.RECORDINGS_DIR, backing_mod.BACKING_DIR
H = A.socketio.handlers

# ---------------------------------------------------------------- helpers
results = []


def step(name):
    def run(fn):
        try:
            fn()
            results.append((name, None))
            print("  ok    " + name)
        except Exception as exc:
            results.append((name, exc))
            print("  FAIL  %s\n        %s: %s" % (name, type(exc).__name__, exc))
        return fn
    return run


def check(cond, message):
    if not cond:
        raise AssertionError(message)


print("broadcast tests\n")


@step("the flag is on, and it was on before the handlers were registered")
def _():
    check(A.BROADCAST is True, "GX_BROADCAST did not take")


@step("serves its own page")
def _():
    with A.app.test_client() as c:
        r = c.get("/")
        check(r.status_code == 200, "page returned %s" % r.status_code)


@step("serves the page's own files")
def _():
    with A.app.test_client() as c:
        for path in ("/static/broadcast.js", "/static/broadcast.css"):
            check(c.get(path).status_code == 200, "%s was blocked" % path)


@step("allows the stream -- listening is the point")
def _():
    with A.app.test_client() as c:
        r = c.get("/monitor.mp3", buffered=False)
        check(r.status_code != 403, "the stream was refused")
        r.close()


@step("refuses everything that reads the rig or the disk")
def _():
    with A.app.test_client() as c:
        for url in ("/api/state", "/api/parameters.json",
                    "/recordings/anything.wav", "/backing/anything.mp3"):
            check(c.get(url).status_code == 403, "%s was allowed" % url)


@step("refuses every method but GET")
def _():
    with A.app.test_client() as c:
        up = c.post("/backing", data={"file": (io.BytesIO(b"x"), "a.wav")},
                    content_type="multipart/form-data")
        check(up.status_code == 403, "an upload was allowed")
        check(c.post("/").status_code == 403, "a POST to the page was allowed")


@step("control events are registered as refusals, not as themselves")
def _():
    for event in ("set_param", "set_preset", "preset_save", "preset_delete",
                  "bank_delete", "record_start", "reamp_start", "import_save",
                  "backing_play", "export_start"):
        check(event in H, "%s is not registered at all" % event)
        check(H[event]({}) is False, "%s did not refuse" % event)


@step("a refused control event changes nothing")
def _():
    before = dict(A.state.values)
    H["set_param"]({"id": "amp.out_master", "value": -9})
    check(A.state.values == before, "set_param mutated the cached state")
    check(A.rec.listing() == [], "a take appeared from nowhere")


@step("the snapshot publishes only its named fields")
def _():
    snap = A.broadcast_snapshot()
    check(set(snap) == {"broadcast", "connected", "bank", "preset",
                        "recording", "takes"},
          "snapshot fields are %s" % sorted(snap))
    for leak in ("values", "eq", "fx", "banks", "can", "audition"):
        check(leak not in snap, "%s leaked into the snapshot" % leak)


@step("emits outside the published set are dropped")
def _():
    sent = []
    real = A._socketio_emit
    try:
        A._socketio_emit = lambda ev, data=None, *a, **k: sent.append((ev, data))
        A.socketio.emit("toast", {"text": "should not reach a stranger"})
        A.socketio.emit("recordings", {"items": ["secret.wav"]})
        A.socketio.emit("params", {"amp.fuzz": 0.5})
        check(sent == [], "leaked: %s" % [e for e, _ in sent])
        A.socketio.emit("preset", {"bank": "Warm", "preset": "Crunch"})
        check([e for e, _ in sent] == ["preset"], "preset did not get through")
    finally:
        A._socketio_emit = real


@step("a full snapshot emit is reduced on the way out")
def _():
    sent = []
    real = A._socketio_emit
    try:
        A._socketio_emit = lambda ev, data=None, *a, **k: sent.append((ev, data))
        A.socketio.emit("snapshot", A.state.snapshot())
        check(len(sent) == 1, "snapshot did not go out")
        payload = sent[0][1]
        check("values" not in payload and "banks" not in payload,
              "the full snapshot went out: %s" % sorted(payload))
    finally:
        A._socketio_emit = real


# ---------------------------------------------------------------- done
failed = [n for n, e in results if e]
print("\n%s  (%d scenarios)" % ("FAILED: " + ", ".join(failed) if failed else "all good",
                                len(results)))
sys.stdout.flush()                 # os._exit skips it, and over a pipe that loses everything
os._exit(1 if failed else 0)
