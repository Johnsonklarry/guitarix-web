#!/usr/bin/env python3
"""
Server tests: the whole app against fake guitarix, JACK, ffmpeg and mpv.

    python3 tests/run_server_tests.py

Safe to run on the Pi next to a real guitarix. It starts its own fake engine
on a free port, puts fake JACK tools first on PATH, keeps fake JACK state in
a temp directory, and records into temp folders -- it never touches your real
rig, your recordings or your banks.

What the fakes guarantee: they refuse what the real tools refuse. The fake
ffmpeg won't play INTO JACK (the real one can't), the fake jack_connect fails
for ports that don't exist, and a fake player's ports vanish when it exits.
A mock that's more forgiving than the real thing proves nothing.
"""

import io
import os
import socket
import subprocess
import sys
import shutil
import atexit
import tempfile
import time
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FAKES = os.path.join(HERE, "fakes")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ---------------------------------------------------------------- environment
# Downloads and copies don't keep the executable bit, and the fakes are run
# as programs -- so make sure they can be, rather than fail mysteriously.
for _tool in os.listdir(os.path.join(FAKES, "bin")):
    os.chmod(os.path.join(FAKES, "bin", _tool), 0o755)

# before the app is imported: it reads GX_PORT at import time
os.environ["FAKE_JACK_DIR"] = tempfile.mkdtemp(prefix="gxweb-jack-")
os.environ["PATH"] = os.path.join(FAKES, "bin") + os.pathsep + os.environ["PATH"]
os.environ["GX_PORT"] = str(free_port())
sys.path[:0] = [os.path.join(HERE, "stubs"), ROOT]

engine = subprocess.Popen([sys.executable, os.path.join(FAKES, "engine.py")],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(50):
    try:
        socket.create_connection(("127.0.0.1", int(os.environ["GX_PORT"])), timeout=0.2).close()
        break
    except OSError:
        time.sleep(0.1)

import logging                       # noqa: E402
logging.disable(logging.WARNING)     # the scenarios provoke failures on purpose

import recorder                      # noqa: E402
import backing as backing_mod        # noqa: E402
recorder.RECORDINGS_DIR = tempfile.mkdtemp(prefix="gxweb-rec-")
atexit.register(shutil.rmtree, recorder.RECORDINGS_DIR, True)   # recordings are GBs: never leave them in TMPDIR
backing_mod.BACKING_DIR = tempfile.mkdtemp(prefix="gxweb-backing-")

import gx_rpc                        # noqa: E402
gx_rpc.PRESET_METHODS.update({"save_current": None, "save_as": "save_preset",
                              "new_bank": "bank_insert_new", "move": None})

import jackutil                      # noqa: E402
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
        except Exception as exc:          # a failing scenario shouldn't stop the rest
            results.append((name, exc))
            print("  FAIL  %s\n        %s: %s" % (name, type(exc).__name__, exc))
        return fn
    return run


def check(cond, message):
    if not cond:
        raise AssertionError(message)


def wait_op(op, timeout=25):
    end = time.time() + timeout
    while time.time() < end:
        for event, data in list(A.socketio.emitted):
            if event == "op_done" and data.get("op") == op:
                return data["ok"]
        time.sleep(0.1)
    raise AssertionError("operation %s never finished" % op)


def toasts():
    return [d["text"] for e, d in A.socketio.emitted if e == "toast"]


def live(*ids):
    return A.rpc.get(list(ids))


def guitar():
    return sorted(jackutil.connections("gx_head_amp:in_0"))


def record(seconds=1.4, **kw):
    H["record_start"](dict({"dry": True}, **kw))
    time.sleep(seconds)
    H["record_stop"]({})
    time.sleep(0.4)
    return A.rec.listing()[0]


# ---------------------------------------------------------------- scenarios
print("server tests\n")

lint = subprocess.run([sys.executable, os.path.join(HERE, "lint.py")], capture_output=True, text=True)


@step("static check: no undefined names or stale cross-module references")
def _():
    check(lint.returncode == 0, lint.stdout.strip())


A.rpc.start()
A.socketio.start_background_task(A.flusher)
time.sleep(1.8)


@step("connects to guitarix and loads its banks")
def _():
    snap = A.state.snapshot()
    check(snap["connected"], "not connected")
    check(any(b["name"] == "Warm" for b in snap["banks"]), "banks: %s" % snap["banks"])


@step("preset: Save as stores the live settings under a new name")
def _():
    A.socketio.emitted.clear()
    H["preset_save_as"]({"bank": "Warm", "name": "Saved One", "op": "sa"})
    time.sleep(4)
    check(any(b["name"] == "Warm" and "Saved One" in b["presets"] for b in A.rpc.banks()),
          "banks now: %s | %s" % (A.rpc.banks(), toasts()))


FILE = ('{"bank": "Claude", "base": "Warm/Clean Warm", "presets": ['
        '{"name": "Sultans Clean", "params": {"amp.tonestack.select": "Twin", '
        '"freeverb.RoomSize": 0.3, "echo.on_off": 1, "echo.time": 320}}]}')


@step("import: audition from a base, then Discard restores everything, unsaved tweaks included")
def _():
    A.rpc.set_preset("Warm", "Crunch")
    time.sleep(0.5)
    H["set_param"]({"id": "amp.out_master", "value": -3})
    time.sleep(0.3)
    H["import_audition"]({"text": FILE, "index": 0, "op": "au"})
    check(wait_op("au"), "audition failed: %s" % toasts())
    v = live("amp.fuzz", "freeverb.RoomSize", "cab.on_off")
    check(v["amp.fuzz"] == 0.02, "base not loaded: %s" % v)
    check(v["freeverb.RoomSize"] == 0.3 and v["cab.on_off"] == 1, "changes wrong: %s" % v)
    H["audition_discard"]({"op": "di"})
    check(wait_op("di"), "discard failed")
    v = live("amp.fuzz", "amp.out_master", "system.current_preset")
    check(v["system.current_preset"] == "Crunch" and v["amp.out_master"] == -3,
          "not restored: %s" % v)


@step("import: a tweak made during an audition is what gets saved")
def _():
    H["import_audition"]({"text": FILE, "index": 0, "op": "a2"})
    wait_op("a2")
    H["set_param"]({"id": "echo.time", "value": 400})
    time.sleep(0.3)
    H["audition_save"]({"op": "s2"})
    check(wait_op("s2"), "save failed: %s" % toasts())
    A.rpc.set_preset("Warm", "Crunch")
    time.sleep(0.4)
    A.rpc.set_preset("Claude", "Sultans Clean")
    time.sleep(0.4)
    check(live("echo.time")["echo.time"] == 400, "tweak lost")


@step("record: + dry and settings travel with the take, named Attempt N")
def _():
    take = record()
    check(take["name"].startswith("Attempt "), take["name"])
    check(take["dry"] and take["settings"], "siblings missing: %s" % take)
    check(len([t for t in A.rec.listing() if "(dry)" in t["name"]]) == 0, "dry twin listed as its own take")
    check(A.rec_payload()["next_attempt"] == int(take["name"].split()[1].split(".")[0]) + 1,
          "next attempt number is off")


@step("record: rename and delete carry the dry twin and settings along")
def _():
    take = record()
    new = A.rec.rename(take["name"], "Renamed Take")
    files = os.listdir(A.rec.dir)
    check({"Renamed Take.wav", "Renamed Take (dry).wav", "Renamed Take.json"} <= set(files), files)
    A.rec.delete(new)
    check(not [f for f in os.listdir(A.rec.dir) if f.startswith("Renamed Take")], "leftovers")


@step("reamp: the guitar comes back after the take ends, after Stop, and after a failure")
def _():
    take = record()
    before = guitar()
    H["reamp_start"]({"take": take["name"], "dry": take["dry"], "op": "r1"})
    check(wait_op("r1"), "reamp failed: %s" % toasts())
    check(before[0] not in guitar(), "guitar still wired in during the reamp")
    for _ in range(40):
        if not A.reamp.active:
            break
        time.sleep(0.1)
    check(guitar() == before, "after the end: %s" % guitar())

    H["reamp_start"]({"take": take["name"], "dry": take["dry"], "loop": True, "op": "r2"})
    wait_op("r2")
    H["reamp_stop"]({"op": "r3"})
    wait_op("r3")
    time.sleep(0.3)
    check(guitar() == before, "after Stop: %s" % guitar())

    mpv = os.path.join(FAKES, "bin", "mpv")
    os.rename(mpv, mpv + ".off")
    try:
        A.socketio.emitted.clear()
        H["reamp_start"]({"take": take["name"], "dry": take["dry"], "op": "r4"})
        check(wait_op("r4") is False, "should have failed without mpv")
    finally:
        os.rename(mpv + ".off", mpv)
    check(guitar() == before, "after a failure: %s" % guitar())


@step("reamp: Amp off reroutes live, and is refused while a pass is being recorded")
def _():
    take = record()
    H["reamp_start"]({"take": take["name"], "dry": take["dry"], "loop": True, "op": "m1"})
    wait_op("m1")
    H["reamp_mode"]({"mode": "dry", "op": "m2"})
    check(wait_op("m2"), "switch failed")
    check(any(p.startswith("system:playback") for p in jackutil.connections("gxweb-reamp:out_0")),
          "amp off didn't reach the outputs")
    H["reamp_stop"]({"op": "m3"})
    wait_op("m3")
    time.sleep(0.3)
    H["reamp_start"]({"take": take["name"], "dry": take["dry"], "record": True, "op": "m4"})
    wait_op("m4")
    A.socketio.emitted.clear()
    H["reamp_mode"]({"mode": "dry", "op": "m5"})
    check(wait_op("m5") is False, "amp off allowed during a recorded pass")
    for _ in range(40):
        if not A.reamp.active:
            break
        time.sleep(0.1)


@step("backing: upload, play, and feed into a take when asked")
def _():
    buf = io.BytesIO()
    w = wave.open(buf, "wb")
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(48000)
    w.writeframes(b"\0\0\0\0" * 48000)
    w.close()
    buf.seek(0)
    with A.app.test_client() as c:
        r = c.post("/backing", data={"file": (buf, "Jam.wav")}, content_type="multipart/form-data")
        check(r.status_code == 200, r.get_json())
        bad = c.post("/backing", data={"file": (io.BytesIO(b"x"), "notes.txt")},
                     content_type="multipart/form-data")
        check(bad.status_code == 400, "non-audio accepted")
        check(c.get("/backing/..%2F..%2Fetc%2Fpasswd").status_code == 404, "path traversal")
    H["backing_include"]({"include": True})
    H["backing_play"]({"name": "Jam.wav", "op": "b1"})
    check(wait_op("b1"), "play failed: %s" % toasts())
    H["record_start"]({})
    time.sleep(1.2)
    check(any(p.startswith("gxweb:input") for p in jackutil.connections("gxweb-backing:out_0")),
          "backing not fed into the take")
    H["record_stop"]({})
    H["backing_stop"]({"op": "b2"})
    wait_op("b2")
    H["backing_include"]({"include": False})
    time.sleep(0.4)


@step("export: through another preset, as MP3, then everything is put back")
def _():
    take = record()
    A.rpc.set_preset("Warm", "Crunch")
    time.sleep(0.5)
    H["set_param"]({"id": "amp.out_master", "value": -3})
    time.sleep(0.3)
    A.socketio.emitted.clear()
    H["export_start"]({"take": take["name"], "dry": take["dry"], "source": "preset:Warm/Clean Warm",
                       "format": "mp3", "op": "e1"})
    check(wait_op("e1"), "export failed: %s" % toasts())
    check(any(f.endswith("(export - Clean Warm).mp3") for f in os.listdir(A.rec.dir)),
          "no mp3: %s" % os.listdir(A.rec.dir))
    v = live("system.current_preset", "amp.out_master")
    check(v["system.current_preset"] == "Crunch" and v["amp.out_master"] == -3, "not restored: %s" % v)


@step("export: cancelling keeps nothing and reconnects the guitar, at any moment")
def _():
    take = record(2.0)
    before = guitar()
    for i, delay in enumerate((0.4, 0.8, 1.2)):
        A.socketio.emitted.clear()
        H["export_start"]({"take": take["name"], "dry": take["dry"], "source": "recorded", "op": "c%d" % i})
        time.sleep(delay)
        H["export_cancel"]({})
        wait_op("c%d" % i)
        check(not [f for f in os.listdir(A.rec.dir) if "as recorded" in f],
              "kept a partial file (cancel at %.1fs): %s" % (delay, toasts()))
        check(guitar() == before, "guitar not back after cancel at %.1fs" % delay)


@step("export: concurrent starts claim the job once, and cancel with no job is a no-op")
def _():
    import threading
    from unittest import mock
    # A slow or failed earlier scenario can leave its export job (and rig state)
    # behind, which would make every start here bail out early. Let it finish,
    # then start from a clean slate.
    for _ in range(300):
        if not A._export_job():
            break
        time.sleep(0.1)
    A._export.update(job=None, cancel=False)
    started, n = [], 6
    barrier = threading.Barrier(n)
    # widen the window between the "already running?" check and the claim
    slow_active = property(lambda self: (time.sleep(0.05), False)[1])
    not_recording = property(lambda self: False)
    A.socketio.emitted.clear()
    with mock.patch.object(type(A.reamp), "active", slow_active), \
            mock.patch.object(type(A.rec), "recording", not_recording), \
            mock.patch.object(A.state, "audition", None), \
            mock.patch.object(A.socketio, "start_background_task",
                              side_effect=lambda fn, *a, **k: started.append(fn)):
        def go():
            barrier.wait()
            H["export_start"]({"take": "t.wav", "dry": "t.dry.wav", "source": "live"})
        threads = [threading.Thread(target=go) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        try:
            check(len(started) == 1,
                  "%d exports were started at once (toasts: %s)" % (len(started), toasts()))
            check(toasts().count("An export is already running.") == n - 1,
                  "the losers weren't told: %s" % toasts())
        finally:
            A._export.update(job=None, cancel=False)
    H["export_cancel"]({})
    check(A._export["cancel"] is False, "cancel with no job left a stale flag")


@step("monitor: streams MP3 to a listener, and stops encoding when they leave")
def _():
    with A.app.test_client() as c:
        r = c.get("/monitor.mp3", buffered=False)
        check(r.status_code == 200 and r.headers["Content-Type"] == "audio/mpeg", r.status_code)
        it = iter(r.response)
        got = sum(len(next(it)) for _ in range(4))
        check(got > 0 and A.monitor.status()["running"], "nothing streamed")
        for _ in range(10):
            if any(p.startswith("gxweb-mon") for p in jackutil.connections("gx_head_amp:out_0")):
                break
            time.sleep(0.1)
        else:
            raise AssertionError("the amp wasn't tapped within a second")
        r.close()
    time.sleep(0.8)
    check(not A.monitor.status()["running"], "still encoding with nobody listening")


@step("demo: ?demo swaps in the stand-in, and the normal page doesn't")
def _():
    with A.app.test_client() as c:
        normal = c.get("/").get_data(as_text=True)
        demo = c.get("/?demo").get_data(as_text=True)
    check("socket.io" in normal and "demo.js" not in normal, "the normal page loads the demo")
    check("demo.js" in demo and "socket.io" not in demo, "the demo page opens a real connection")


@step("demo-only server: serves the demo, refuses everything that touches the rig")
def _():
    A.DEMO_ONLY = True
    try:
        with A.app.test_client() as c:
            page = c.get("/").get_data(as_text=True)
            check("demo.js" in page and "socket.io" not in page, "demo-only served the real page")
            check(c.get("/static/demo.js").status_code == 200, "the demo script itself is blocked")
            for url in ("/api/state", "/api/parameters.json", "/monitor.mp3",
                        "/recordings/anything.wav", "/backing/anything.mp3"):
                check(c.get(url).status_code == 403, "%s was allowed" % url)
            up = c.post("/backing", data={"file": (io.BytesIO(b"x"), "a.wav")},
                        content_type="multipart/form-data")
            check(up.status_code == 403, "an upload was allowed")
        check(H["connect"]() is False, "the live connection was accepted")
    finally:
        A.DEMO_ONLY = False


# ---------------------------------------------------------------- done
engine.terminate()
failed = [n for n, e in results if e]
print("\n%s  (%d scenarios)" % ("FAILED: " + ", ".join(failed) if failed else "all good", len(results)))
sys.stdout.flush()                 # os._exit skips it, and over a pipe that loses everything
os._exit(1 if failed else 0)       # background threads (the rpc reader) needn't be waited for
