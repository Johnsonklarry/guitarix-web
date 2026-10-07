"""
Guitarix web control surface.

One long-lived JSON-RPC socket to the guitarix engine, shared by every
browser. Changes flow both ways:

    browser --socket.io--> flask --json-rpc--> guitarix
    guitarix --json-rpc--> flask --socket.io--> every browser

Run it:
    pip install flask flask-socketio simple-websocket
    python3 app.py
"""

import contextvars
import hmac
import json
import logging
import mimetypes
import os
import subprocess
import sys
import re
import threading
import time

# --check must run BEFORE the third-party imports below: a missing package is
# one of the things it exists to tell you about, and it can't do that if
# importing it is what crashes.
if __name__ == "__main__" and "--check" in sys.argv:
    import diagnose
    sys.exit(diagnose.main(sys.argv[1:]))

from flask import (Flask, Response, abort, jsonify, render_template, request,
                   send_from_directory, session, url_for)
from flask_socketio import SocketIO

import controls
import gx_rpc
import presets_io
from gx_rpc import GuitarixRPC, RpcError, RpcMethodMissing
from recorder import Recorder
from reamp import Reamp, ReampError
from backing import Backing, BackingError
from monitor import Monitor
import jackutil
from recorder import SOURCE_CLIENT

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
log = logging.getLogger("amp")

GX_HOST = os.environ.get("GX_HOST", "127.0.0.1")
GX_PORT = int(os.environ.get("GX_PORT", "7000"))      # guitarix -p <port>
WEB_PORT = int(os.environ.get("GX_WEB_PORT", "5000"))

# GX_DEMO_ONLY=1 serves nothing but the demo: every page is the demo, anything
# that could reach the rig is refused, and guitarix is never contacted. Run a
# second copy like this on its own port, and forward THAT one to show people
# -- see "Demo mode" in the README.
DEMO_ONLY = os.environ.get("GX_DEMO_ONLY", "").lower() in ("1", "true", "yes")

# GX_BROADCAST=1 is the read-only shop window: it shows what is playing right
# now -- the preset, whether a take is rolling -- and lets a visitor listen.
# It talks to guitarix, unlike the demo, so it must give away nothing that
# could change the rig. Nothing is registered that could.
BROADCAST = os.environ.get("GX_BROADCAST", "").lower() in ("1", "true", "yes")
FLUSH_INTERVAL = 0.05     # seconds; coalesces slider storms into ~20 fps

# GX_REC_FEED is the one-way state feed from the Studio process to the
# broadcast process (guitarix-broadcast.service is a second app.py with its own
# Recorder, which never records). Point both at the same path: Studio writes a
# small JSON file there, broadcast only reads it and publishes recording/count.
# Recording is true only while the feed SAYS so and is fresh; Studio refreshes
# it every second while a take rolls, so a crashed Studio stops reading as
# "recording" after REC_FEED_STALE seconds. File existence proves nothing.
REC_FEED = os.environ.get("GX_REC_FEED", "")
REC_FEED_STALE = 5.0      # seconds

# Python doesn't know the manifest's extension, and would serve it as a
# generic binary download.
mimetypes.add_type("application/manifest+json", ".webmanifest")

app = Flask(__name__)
# Signs Socket.IO's session cookie. Nothing needs to survive a restart, so a
# fresh random key each start is fine; set GX_SECRET_KEY to pin one.
app.config["SECRET_KEY"] = os.environ.get("GX_SECRET_KEY") or os.urandom(24).hex()
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024       # per upload request


@app.template_global()
def asset(filename):
    """
    A static URL stamped with the file's modification time. Browsers happily
    keep running an old app.js after a deploy; a new stamp is a new URL, so
    they can't.
    """
    path = os.path.join(app.static_folder, filename)
    try:
        stamp = int(os.path.getmtime(path))
    except OSError:
        stamp = 0
    return url_for("static", filename=filename, v=stamp)
socketio = SocketIO(app, async_mode="threading", cors_allowed_origins="*")

# Broadcast mode refuses control events by never registering their handlers.
# Wrapping the decorator rather than guarding inside each one means a handler
# added later is refused too, without anyone having to remember.
BROADCAST_EVENTS = frozenset(("connect", "disconnect"))
_socketio_on = socketio.on


def _broadcast_guarded_on(event, *a, **kw):
    register = _socketio_on(event, *a, **kw)
    if not BROADCAST or event in BROADCAST_EVENTS:
        return register

    def refuse(fn):
        def blocked(*_a, **_kw):
            log.warning("broadcast: refused %s", event)
            return False
        blocked.__name__ = getattr(fn, "__name__", "blocked")
        register(blocked)
        return fn
    return refuse


socketio.on = _broadcast_guarded_on

# The push side needs the same treatment. flusher(), on_ready() and the preset
# hooks all emit the full state, and a broadcast viewer is on the same wire as
# everyone else -- so filter at the emit, once, rather than at each call site.
BROADCAST_EMITS = frozenset(("snapshot", "preset", "status", "rec"))
_socketio_emit = socketio.emit


def _broadcast_guarded_emit(event, data=None, *a, **kw):
    if BROADCAST:
        if event not in BROADCAST_EMITS:
            return
        if event == "snapshot":
            data = broadcast_snapshot()
        elif event == "rec":
            data = {"recording": bool((data or {}).get("recording")),
                    "count": (data or {}).get("count")}
    return _socketio_emit(event, data, *a, **kw)


socketio.emit = _broadcast_guarded_emit

# op_done has to reach the browser that started the operation, not every
# connected client. A socket handler knows its client (request.sid), but the
# background tasks it spawns run outside the request, so the spawner's sid is
# captured here and restored inside the task.
_op_sid = contextvars.ContextVar("op_sid", default=None)


def _requester():
    """The socket id of the client whose request we are serving, or None."""
    sid = _op_sid.get()
    if sid:
        return sid
    try:
        return getattr(request, "sid", None)
    except RuntimeError:              # no request context (startup, tests)
        return None


_socketio_start_task = socketio.start_background_task


def _start_task_for_requester(target, *args, **kwargs):
    sid = _requester()

    def run(*a, **kw):
        token = _op_sid.set(sid)
        try:
            return target(*a, **kw)
        finally:
            _op_sid.reset(token)
    return _socketio_start_task(run, *args, **kwargs)


socketio.start_background_task = _start_task_for_requester


class StateCoordinator:
    """Thread-safe coordinator tracking subsystem states with monotonic generation numbering."""

    def __init__(self, state_obj=None):
        self.lock = threading.Lock()
        self._lock = self.lock
        self.state = state_obj
        self.generation = 0
        self._states = {}

    @property
    def _generation(self):
        return self.generation

    @_generation.setter
    def _generation(self, value):
        self.generation = value

    def update_subsystem(self, name, state):
        with self.lock:
            self._states[name] = state
            self.generation += 1
            return self.generation

    def next_generation(self):
        with self.lock:
            self.generation += 1
            return self.generation

    def get_generation(self):
        with self.lock:
            return self.generation

    def snapshot(self):
        with self.lock:
            if self.state is not None:
                snap = self.state.snapshot()
                snap["generation"] = self.generation
                return snap
            return {
                "generation": self.generation,
                "states": dict(self._states),
            }

    def publish(self, event, payload=None):
        gen = self.next_generation()
        if isinstance(payload, dict):
            payload = dict(payload)
            payload["generation"] = gen
        socketio.emit(event, payload)
        return gen


class AmpState:
    """Everything the browsers need, kept in one place behind a lock."""

    def __init__(self):
        self.lock = threading.Lock()
        self.banks = []
        self.bank = None
        self.preset = None
        self.eq_groups = []
        self.fx_groups = []
        self.values = {}
        self.connected = False
        self.parameters = {}      # full engine parameter list, for the importer
        self.dirty = False        # settings changed here since the preset loaded
        self.audition = None      # an imported preset being tried out, unsaved

    def public_audition(self):
        # Caller must hold self.lock (snapshot() and client_audition() both do).
        # It is a plain, non-reentrant Lock, so taking it here would deadlock.
        a = self.audition
        if not a or "name" not in a:
            return None
        return {"name": a["name"], "bank": a["bank"], "replaces": a.get("replaces", False),
                "base": a.get("base")}

    def snapshot(self):
        with self.lock:
            return {
                "connected": self.connected,
                "engine": "%s:%s" % (GX_HOST, GX_PORT),
                "banks": self.banks,
                "bank": self.bank,
                "preset": self.preset,
                "eq": self.eq_groups,
                "fx": self.fx_groups,
                "values": dict(self.values),
                "can": _capabilities(),
                "dirty": self.dirty,
                "audition": self.public_audition(),
                "rec": rec_payload(),
                "backing_items": backing.listing(),
                "recordings": rec.listing(),
            }


def _capabilities():
    """
    What the preset toolbar can offer. Two of these work even without a
    dedicated method: saving over the loaded preset is just save_as under
    its own name, and a move is a save into the destination followed by a
    delete from the source.
    """
    m = gx_rpc.PRESET_METHODS
    return {
        "save_current": bool(m.get("save_current") or m.get("save_as")),
        "save_as":      bool(m.get("save_as")),
        "rename":       bool(m.get("rename")),
        "delete":       bool(m.get("delete")),
        "new_bank":     bool(m.get("new_bank")),
        "delete_bank":  bool(m.get("delete_bank")),
        "move":         bool(m.get("move") or (m.get("save_as") and m.get("delete"))),
    }


def rec_payload():
    """Everything the Record tab shows about what's happening right now."""
    status = rec.status()
    status["reamp"] = reamp.status()
    status["reamp_error"] = reamp.restore_error
    status["backing"] = backing.status()
    status["export"] = _export_job()
    if BROADCAST and REC_FEED:
        # This process never records; Studio does. Take its word, not ours.
        feed = read_rec_feed() or {}
        status["recording"] = bool(feed.get("recording"))
        if feed.get("count") is not None:
            status["count"] = feed["count"]
    return status


_feed_last = None


def publish_rec_feed():
    """
    Studio side: write the approved fields (recording, count, a timestamp) for
    the broadcast process. Atomic replace, so a reader never sees half a file.
    No filenames, no controls. Unchanged idle state is not rewritten; a
    rolling take is, every call, as the heartbeat.
    """
    global _feed_last
    if not REC_FEED or BROADCAST:
        return
    recording, count = rec.recording, rec.take_count()
    if not recording and (recording, count) == _feed_last:
        return
    doc = {"recording": recording, "count": count, "at": time.time()}
    tmp = REC_FEED + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(doc, f)
        os.replace(tmp, REC_FEED)
    except OSError:
        log.warning("couldn't write the recording feed %s", REC_FEED)
        return
    _feed_last = (recording, count)


def read_rec_feed(path=None, now=None):
    """
    Broadcast side: {"recording": bool, "count": int|None} from the feed, or
    None when it is missing or unreadable. Only those two fields survive;
    anything else in the file is ignored. Recording needs a literal true AND a
    fresh timestamp.
    """
    path = path or REC_FEED
    now = time.time() if now is None else now
    try:
        with open(path) as f:
            doc = json.load(f)
        at = float(doc["at"])
        recording = doc.get("recording") is True and abs(now - at) <= REC_FEED_STALE
        count = doc.get("count")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        count = None
    return {"recording": recording, "count": count}


def push_recordings():
    publish_rec_feed()
    socketio.emit("recordings", {"rec": rec_payload(), "items": rec.listing(),
                                 "backing_items": backing.listing()})


rec = Recorder(on_change=push_recordings)
reamp = Reamp(rec, on_change=push_recordings)
backing = Backing(on_change=push_recordings)


def monitor_sources():
    """What the web page hears: the same things the outputs play."""
    sources = [jackutil.ports(SOURCE_CLIENT, "output")]          # the amp
    if backing.player.playing:
        sources.append(jackutil.ports(backing.player.client, "output"))
    r = reamp.status()
    if r and r["mode"] == "dry":
        # through the amp, a reamp is already in the amp's output; bypassed,
        # it goes straight to the outputs and has to be tapped itself
        sources.append(jackutil.ports(reamp.player.client, "output"))
    return sources


monitor = Monitor(monitor_sources)
_export = {"job": None, "cancel": False}
# One lock guards every read and write of _export. Never call out to the
# rig, recorder or socket while holding it: these helpers only touch the dict.
_export_lock = threading.Lock()
state = AmpState()


def _export_job():
    """The running export job (a dict that is only ever replaced, never edited), or None."""
    with _export_lock:
        return _export["job"]


def _export_claim(job):
    """Atomically reserve the export slot. False if a job already holds it."""
    with _export_lock:
        if _export["job"]:
            return False
        _export.update(job=job, cancel=False)
        return True


def _export_release():
    with _export_lock:
        _export["job"] = None


def _export_cancelled():
    with _export_lock:
        return _export["cancel"]


def _export_request_cancel():
    """Flag the running job for cancelling. False (and no flag) if none is running."""
    with _export_lock:
        if not _export["job"]:
            return False
        _export["cancel"] = True
        return True


# Finished operations land here so a duplicate request -- one carrying the same
# durable operation id -- replays the original result instead of doing the work
# twice. The journal is bounded in size and in age: a retry that arrives after
# the TTL is treated as a brand new request.
DONE_JOURNAL_MAX = 1024
DONE_JOURNAL_TTL = 24 * 60 * 60
_done_journal = {}
_done_journal_lock = threading.Lock()


def _journal_get(op_id):
    """Caller holds _done_journal_lock. The cached (op, ok), or None."""
    entry = _done_journal.get(op_id)
    if entry is None:
        return None
    cached_op, cached_ok, recorded = entry
    if DONE_JOURNAL_TTL and time.time() - recorded > DONE_JOURNAL_TTL:
        del _done_journal[op_id]
        return None
    return cached_op, cached_ok


def _journal_put(op_id, op, ok):
    """Caller holds _done_journal_lock. Store the result, then drop the oldest
    entries until the journal is back inside its bound."""
    _done_journal[op_id] = (op, bool(ok), time.time())
    while DONE_JOURNAL_MAX and len(_done_journal) > DONE_JOURNAL_MAX:
        _done_journal.pop(next(iter(_done_journal)))


def done_journal_lookup(op_id):
    """The cached (op, ok) recorded for `op_id`, or None if unknown or expired."""
    if not op_id:
        return None
    with _done_journal_lock:
        return _journal_get(op_id)


def done_journal_record(op_id, op, ok):
    """Remember `op_id`, dropping the oldest entries once the bound is exceeded."""
    with _done_journal_lock:
        _journal_put(op_id, op, ok)


def done_journal_claim(op_id, op, ok):
    """
    Look `op_id` up and, when it is not already known, record `op`/`ok` for it
    -- both under one lock acquisition, so two calls racing on the same id
    cannot both miss and both record.

    Returns the recorded (op, ok) when the id already belongs to that
    operation (a duplicate request: replay it), or None when this call was the
    first for the id and has now recorded it. An id journaled for a DIFFERENT
    operation is not a retry of this one, so it is reported -- and recorded --
    in its own right rather than answered with the other operation's result.
    """
    if not op_id:
        return None
    with _done_journal_lock:
        cached = _journal_get(op_id)
        if cached is not None and cached[0] == op:
            return cached
        _journal_put(op_id, op, ok)
        return None


def done(op, ok, op_id=None):
    """Tell the browser that started `op` it has finished, so its button settles.

    `op_id` is the client's durable operation id. When one is supplied the
    result is recorded in a bounded in-memory journal, and a repeat call with
    the same id and the same operation replays the recorded result instead of
    reporting the freshly supplied one -- so a retried request is idempotent.
    An id that turns up attached to a different operation is not a retry, and
    is reported -- and recorded -- in its own right.
    """
    if op:
        # One atomic lookup-or-record: a duplicate is replayed from the
        # journal, a first (or a reused id belonging to another operation) is
        # reported and recorded as itself.
        cached = done_journal_claim(op_id, op, ok)
        if cached is not None:        # duplicate request: replay what we sent
            op, ok = cached
        payload = {"op": op, "ok": bool(ok)}
        sid = _requester()
        if sid:
            socketio.emit("op_done", payload, to=sid)
        else:                         # no known requester: nobody to single out
            socketio.emit("op_done", payload)


def set_dirty(value):
    with state.lock:
        changed = state.dirty != value
        state.dirty = value
    if changed:
        socketio.emit("dirty", {"dirty": value})


def toast(text, kind="info"):
    socketio.emit("toast", {"text": text, "kind": kind})


def ticker():
    """While a take is running or rendering, keep the clock honest."""
    last_view = None
    while True:
        socketio.sleep(1)
        if BROADCAST and REC_FEED:
            # Studio owns the take: follow its feed, push only what changed.
            payload = rec_payload()
            view = (payload["recording"], payload["count"])
            if view != last_view:
                last_view = view
                socketio.emit("rec", payload)
        elif rec.recording or reamp.active or backing.player.playing:
            socketio.emit("rec", rec_payload())
        publish_rec_feed()

# changes waiting to be pushed to browsers
_pending = {}
_pending_lock = threading.Lock()


def queue_params(changes):
    with _pending_lock:
        _pending.update(changes)


def flusher():
    """Batch parameter updates so a dragged slider doesn't flood the wire."""
    while True:
        socketio.sleep(FLUSH_INTERVAL)
        with _pending_lock:
            if not _pending:
                continue
            batch = dict(_pending)
            _pending.clear()
        with state.lock:
            state.values.update(batch)
        socketio.emit("params", batch)


# ------------------------------------------------------------------ rpc hooks

def on_params(changes):
    """Guitarix told us parameters changed (from the GTK UI, MIDI, a preset...)."""
    preset_keys = {"system.current_bank", "system.current_preset"}
    if preset_keys & changes.keys():
        with state.lock:
            state.bank = changes.get("system.current_bank", state.bank)
            state.preset = changes.get("system.current_preset", state.preset)
            payload = {"bank": state.bank, "preset": state.preset}
        socketio.emit("preset", payload)
    queue_params(changes)


_refresh_timer = None
_refresh_lock = threading.Lock()


def on_event(method, params):
    """
    Anything that isn't a plain parameter set -- preset loaded, preset list
    changed, and whatever else a given build sends. Rather than depend on the
    exact method names, re-read the banks and the current preset whenever one
    arrives. They tend to come in bursts, so wait for a quiet moment first.
    """
    global _refresh_timer
    log.debug("rpc event %s %s", method, params)
    with _refresh_lock:
        if _refresh_timer is not None:
            _refresh_timer.cancel()
        _refresh_timer = threading.Timer(0.3, _refresh_after_event)
        _refresh_timer.daemon = True
        _refresh_timer.start()


def _refresh_after_event():
    refresh_banks()
    refresh_preset()


def on_status(connected):
    with state.lock:
        state.connected = connected
    socketio.emit("status", {"connected": connected})
    log.info("guitarix %s", "connected" if connected else "disconnected")


def on_ready():
    """Runs after every (re)connect. Rebuilds the whole picture."""
    parameters = rpc.parameter_list()
    eq = controls.build_groups(controls.EQ_GROUPS, parameters)
    fx = controls.build_groups(controls.FX_GROUPS, parameters)

    ids = controls.all_ids(eq) + controls.all_ids(fx)
    values = rpc.get(ids) if ids else {}

    banks = [{"name": b.get("name"), "presets": b.get("presets", [])}
             for b in rpc.banks()]
    bank, preset = rpc.current_preset()

    with state.lock:
        state.parameters = parameters
        state.eq_groups, state.fx_groups = eq, fx
        state.values = values
        state.banks, state.bank, state.preset = banks, bank, preset

    log.info("loaded %d parameters, %d eq groups, %d fx groups, %d banks",
             len(parameters), len(eq), len(fx), len(banks))
    socketio.emit("snapshot", state.snapshot())


def refresh_preset():
    try:
        bank, preset = rpc.current_preset()
    except (OSError, ValueError, TimeoutError) as exc:
        log.warning("preset refresh failed: %s", exc)
        return
    with state.lock:
        state.bank, state.preset = bank, preset
    socketio.emit("preset", {"bank": bank, "preset": preset})
    # a preset load moves every knob, so re-read what's on screen
    with state.lock:
        ids = controls.all_ids(state.eq_groups) + controls.all_ids(state.fx_groups)
    if ids:
        try:
            queue_params(rpc.get(ids))
        except (OSError, ValueError, TimeoutError):
            pass


rpc = GuitarixRPC(GX_HOST, GX_PORT,
                  on_params=on_params, on_event=on_event,
                  on_status=on_status, on_ready=on_ready)


# ------------------------------------------------------------------ http

# What a broadcast server will answer: the page, its assets, and the stream.
# Listening is the point of it -- everything else is refused, including the
# parameter list, the recordings and the uploads.
BROADCAST_ENDPOINTS = ("index", "static", "manifest", "monitor_stream")


# What a demo-only server will answer: the page, its assets, and the manifest.
# Everything else -- the parameter list, the recordings, the uploads, the
# monitor stream -- could reach the rig, so it is refused.
DEMO_ENDPOINTS = ("index", "static", "manifest")


@app.before_request
def demo_only_guard():
    """A demo-only server hands out the page and its files, and nothing else."""
    if DEMO_ONLY and request.endpoint not in DEMO_ENDPOINTS:
        abort(403)
    if BROADCAST and request.endpoint not in BROADCAST_ENDPOINTS:
        abort(403)
    if BROADCAST and request.method != "GET":
        abort(403)


# Routes that change state and must not be reachable without a session.
# GET is always allowed (it only reads), and so are the login/logout routes
# themselves -- otherwise there would be no way in.
AUTH_EXEMPT_ENDPOINTS = ("login", "logout")


@app.before_request
def auth_guard():
    """
    Gate state-changing requests behind an active session.

    Only when GX_PASSWORD is set: with no password configured the app is
    deliberately open, exactly as it was before this guard existed. GET
    requests, static assets and the demo/broadcast endpoints stay open --
    the demo never reaches the rig and the broadcast server refuses
    non-GET requests in demo_only_guard() above.
    """
    if not os.environ.get("GX_PASSWORD"):
        return
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    if request.endpoint in AUTH_EXEMPT_ENDPOINTS:
        return
    if request.endpoint in DEMO_ENDPOINTS or request.endpoint in BROADCAST_ENDPOINTS:
        return
    if not session.get("authenticated"):
        abort(401)


@app.route("/")
def index():
    # The shop window has its own page and client; the Studio template is wired
    # to the full snapshot shape and to control events a broadcast server refuses.
    # A demo-only server keeps the demo page (it never shares a process with a
    # broadcast one in practice, but the demo branch stays first to be safe).
    if BROADCAST and not DEMO_ONLY:
        return render_template("broadcast.html")
    demo = DEMO_ONLY or "demo" in request.args
    return render_template("index.html", demo=demo, demo_only=DEMO_ONLY)


@app.route("/manifest.webmanifest")
def manifest():
    """
    Served from the root rather than /static/ so the app's scope is the whole
    site. It's what lets a phone launch this from the home screen as an app,
    with no browser bars.
    """
    return send_from_directory(app.static_folder, "manifest.webmanifest",
                               mimetype="application/manifest+json")


def check_password(candidate):
    """True only when GX_PASSWORD is set and `candidate` matches it exactly."""
    expected = os.environ.get("GX_PASSWORD", "")
    if not expected or not isinstance(candidate, str):
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


@app.route("/login", methods=["POST"])
def login():
    """Valid password: start a session (signed cookie). Anything else: 401."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = request.form
    if not check_password(data.get("password")):
        session.clear()
        return jsonify({"ok": False, "error": "Wrong password."}), 401
    session.clear()
    session["authenticated"] = True
    return jsonify({"ok": True})


@app.route("/logout", methods=["POST"])
def logout():
    """Ends the session; the cookie is cleared."""
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/state")
def api_state():
    return jsonify(state.snapshot())


def _confined(root, name, must_exist=True):
    """
    The one place where a name from a URL or an upload becomes a path.

    Returns the absolute path of `root` joined with the name's final
    component, or None when the name can't be trusted with that. A name
    carrying a ".." path component is refused outright; any other directory
    part is dropped, so the result is always one plain file name inside
    `root` -- it is never the name that decides where in the filesystem it
    lands. Whole components are compared, never substrings, so "..take.wav"
    is an ordinary file name. The directory part is dropped rather than
    refused because some browsers send an upload's filename as a full client
    path, and only its last component is of any use.
    Uploads pass must_exist=False: they name the file they are about to
    write, which is not there yet.
    """
    root = os.path.abspath(root)
    name = (name or "").replace("\\", "/")
    # whole segments only, so "..take.wav" stays an ordinary file name
    if not name or ".." in name.split("/"):
        return None
    path = os.path.abspath(os.path.join(root, os.path.basename(name)))
    if os.path.dirname(path) != root:
        return None
    if must_exist and not os.path.isfile(path):
        return None
    return path


@app.route("/recordings/<path:name>")
def recording_file(name):
    """Serves a take for the <audio> element, or as a download with ?dl=1."""
    path = _confined(rec.dir, name)
    if path is None:
        abort(404)
    return send_from_directory(os.path.dirname(path), os.path.basename(path),
                               as_attachment=bool(request.args.get("dl")),
                               conditional=True)


@app.route("/api/parameters.json")
def api_parameters_json():
    """
    The engine's parameters, laid out for writing presets against -- the
    file to hand Claude along with what you want to hear. It carries its own
    instructions, so it works without any other context.
    """
    params = _engine_parameters()
    if not params:
        return jsonify({"error": NOT_CONNECTED}), 503
    response = jsonify(presets_io.export_parameters(params, _banks_now()))
    response.headers["Content-Disposition"] = 'attachment; filename="guitarix-parameters.json"'
    return response


@app.route("/monitor.mp3")
def monitor_stream():
    """
    The rig, live, as an MP3 stream for an <audio> element. Plain http is
    fine. X-Accel-Buffering stops nginx -- Nginx Proxy Manager, if this ever
    sits behind it -- from holding the stream back to fill its buffer.
    """
    if not monitor.available():
        return Response("Listening needs ffmpeg and the JACK tools on the Pi.", 503)
    return Response(monitor.listen(), mimetype="audio/mpeg",
                    headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@app.route("/backing", methods=["POST"])
def backing_upload():
    """Add backing tracks. Several at once is fine."""
    files = request.files.getlist("file")
    if not files:
        return jsonify({"ok": False, "error": "No file came through."}), 400
    saved = []
    try:
        for f in files:
            safe = _confined(backing.dir, f.filename, must_exist=False)
            if safe is None:
                return jsonify({"ok": False, "error": "That file name leaves the backing folder.",
                                "saved": saved}), 400
            # hand the library the name that was checked, not the raw one from
            # the client, so the guard and the write can't drift apart
            saved.append(backing.save_upload(os.path.basename(safe), f.stream))
    except (BackingError, OSError) as exc:
        return jsonify({"ok": False, "error": str(exc), "saved": saved}), 400
    return jsonify({"ok": True, "saved": saved})


@app.route("/backing/<path:name>")
def backing_file(name):
    path = _confined(backing.dir, name)
    if path is None:
        abort(404)
    return send_from_directory(os.path.dirname(path), os.path.basename(path),
                               conditional=True)


@app.route("/api/parameters")
def api_parameters():
    """Every parameter the engine exposes. Handy when editing controls.py."""
    try:
        return jsonify(rpc.parameter_list())
    except (OSError, ValueError, TimeoutError) as exc:
        return jsonify({"error": str(exc)}), 503


# ------------------------------------------------------------------ socket.io

def broadcast_snapshot():
    """
    What a stranger may see: what is playing, and whether a take is rolling.
    Built by naming fields rather than by deleting them from the full
    snapshot, so a field added there is not published here by accident.
    """
    full = state.snapshot()
    rec = full.get("rec") or {}
    return {
        "broadcast": True,
        "connected": full.get("connected"),
        "bank": full.get("bank"),
        "preset": full.get("preset"),
        "recording": bool(rec.get("recording")),
        "takes": rec.get("count"),
    }


@socketio.on("connect")
def client_connected():
    if DEMO_ONLY:
        return False            # refuse: the demo never needs the live connection
    if BROADCAST:
        socketio.emit("snapshot", broadcast_snapshot())
        return
    socketio.emit("snapshot", state.snapshot())


def _param_resync(pid):
    """
    Put an optimistic control back in step with the engine.

    A known id gets its fresh engine value; anything else gets the whole
    snapshot, since there is nothing better to show for a control we don't
    recognise. Reads from the engine, not the app's mirror, so a rejected
    write can't leave the browser showing the value it guessed.
    """
    with state.lock:
        known = bool(pid) and pid in state.parameters
    if known:
        try:
            value = rpc.get([pid]).get(pid)
        except (RpcError, OSError, TimeoutError) as exc:
            log.warning("resync read of %s failed: %s", pid, exc)
            known = False
    if known:
        event, data = "params", {pid: value}
    else:
        event, data = "snapshot", state.snapshot()
    sid = _requester()
    if sid:
        socketio.emit(event, data, to=sid)
    else:
        socketio.emit(event, data)


@socketio.on("set_param")
def client_set_param(msg):
    pid = msg.get("id") if isinstance(msg, dict) else None
    value = msg.get("value") if isinstance(msg, dict) else None
    if not isinstance(pid, str) or not pid:
        _param_resync(None)
        return
    with state.lock:
        parameters = state.parameters
        current = state.values.get(pid)
    p = parameters.get(pid)
    if p is None or p.get("non_preset") or presets_io.plumbing(pid):
        _param_resync(pid)
        return
    try:
        # The same pure normalization the importer uses, in live-write mode:
        # out-of-range is refused, not clamped.
        normalized, _ = presets_io.coerce(p, value, live=True)
    except presets_io.Rejected as exc:
        log.warning("set %s rejected: %s", pid, exc)
        _param_resync(pid)
        return
    if isinstance(normalized, dict):
        # choices can be reported as an index or a key; send back whichever
        # form the engine is currently using
        normalized = presets_io.option_wire(normalized, current)
    try:
        rpc.set({pid: normalized}, lane=gx_rpc.HIGH)
    except OSError as exc:
        log.warning("set %s failed: %s", pid, exc)
        toast("Couldn't set %s: %s" % (pid, exc), "error")
        _param_resync(pid)
        return
    # Guitarix broadcasts changes to every client except the one that made
    # them, so our own writes never come back. Read the value back rather than
    # queueing the one that was submitted: the engine may have rounded or
    # refused it, and the control must show what is actually set.
    try:
        confirmed = rpc.get([pid])
    except (RpcError, OSError, TimeoutError) as exc:
        log.warning("couldn't confirm %s: %s", pid, exc)
        toast("Couldn't confirm %s was set: %s" % (pid, exc), "error")
        _param_resync(pid)
        return
    queue_params({pid: confirmed.get(pid)})
    set_dirty(True)


@socketio.on("set_preset")
def client_set_preset(msg):
    bank, preset = msg.get("bank"), msg.get("preset")
    if not bank or not preset:
        return
    try:
        rpc.set_preset(bank, preset, lane=gx_rpc.HIGH)
    except OSError as exc:
        log.warning("preset load failed: %s", exc)
        return
    with state.lock:
        state.bank, state.preset = bank, preset
        # loading a preset is a deliberate move away from an audition
        ended = state.audition is not None
        state.audition = None
    socketio.emit("preset", {"bank": bank, "preset": preset})
    if ended:
        socketio.emit("audition", None)
    set_dirty(False)
    threading.Thread(target=refresh_preset, daemon=True).start()


# ------------------------------------------------------------------ presets

def _banks_map():
    return {b.get("name"): list(b.get("presets", [])) for b in rpc.banks()}


def _preset_transaction(fn, verify, settle=3.0):
    """
    Run one preset mutation and report what the engine actually did.

    The preset methods are notify-only: guitarix acts on them and sends
    nothing back, so there's no reply to check. Instead the bank list is
    read before and after, and the change is polled for until `settle`
    seconds elapse.

    Returns {"before", "after", "ok", "ambiguous"}:

      ok         the verify predicate saw the change within the timeout
      ambiguous  the poll failed AND the bank list is byte-for-byte what it
                 was before the notify, so the engine never acted on it.
                 That is the signature of a wrong method name, and it is
                 reported differently from a change that landed but did not
                 look the way the caller expected.
    """
    before = _banks_map()
    fn()

    ok = False
    deadline = time.time() + settle
    while time.time() < deadline:
        socketio.sleep(0.25)
        try:
            if verify(_banks_map()):
                ok = True
                break
        except (RpcError, OSError, TimeoutError):
            continue

    after = _banks_map()
    return {"before": before, "after": after, "ok": ok,
            "ambiguous": (not ok) and before == after}


def _preset_action(fn, ok_message, verify=None, settle=3.0, fail_message=None,
                   op=None, after=None):
    """
    Run a preset mutation and report what actually happened.

    The preset methods are notify-only: guitarix acts on them and sends
    nothing back, so there's no reply to check. Instead we re-read the bank
    list until it shows the change, or give up and say so. That also catches
    a wrong method name, which would otherwise fail completely silently.
    """
    def run():
        try:
            if verify is None:
                fn()
                result = {"ok": True, "ambiguous": False}
            else:
                result = _preset_transaction(fn, verify, settle)
        except RpcMethodMissing as exc:
            toast("No %r method is configured. Run probe_rpc.py." % str(exc), "error")
            done(op, False)
            return
        except (RpcError, OSError, TimeoutError) as exc:
            toast(str(exc), "error")
            done(op, False)
            return

        ok = result["ok"]
        if ok and after:
            try:
                after()
            except (RpcError, OSError, TimeoutError) as exc:
                log.warning("follow-up after %r failed: %s", ok_message, exc)
        refresh_banks()
        if ok:
            toast(ok_message, "ok")
        elif result["ambiguous"]:
            toast("The engine didn't act on that preset change, so the "
                  "outcome is ambiguous. Check the method name in "
                  "PRESET_METHODS.", "error")
        else:
            toast(fail_message or
                  "The engine didn't change anything. Check the method name "
                  "in PRESET_METHODS.", "error")
        done(op, ok)

    socketio.start_background_task(run)


def _load_quietly(bank, name):
    """Make a just-saved preset the loaded one, and say so everywhere."""
    rpc.set_preset(bank, name)
    with state.lock:
        state.bank, state.preset = bank, name
    socketio.emit("preset", {"bank": bank, "preset": name})
    set_dirty(False)


def refresh_banks():
    try:
        banks = [{"name": b.get("name"), "presets": b.get("presets", [])}
                 for b in rpc.banks()]
        bank, preset = rpc.current_preset()
    except (OSError, ValueError, TimeoutError) as exc:
        log.warning("bank refresh failed: %s", exc)
        return
    with state.lock:
        state.banks, state.bank, state.preset = banks, bank, preset
    socketio.emit("banks", {"banks": banks, "bank": bank, "preset": preset})


@socketio.on("preset_save")
def client_preset_save(msg=None):
    op = (msg or {}).get("op")
    with state.lock:
        bank, name = state.bank, state.preset
    if not name:
        toast("No preset is loaded, so there's nothing to save over. Use Save as.", "error")
        done(op, False)
        return

    # Not every build has save_current_preset. Saving over the loaded preset
    # by name is the same operation, so use save_as when it's the only one
    # available.
    if gx_rpc.PRESET_METHODS.get("save_current"):
        action = rpc.preset_save_current
    elif gx_rpc.PRESET_METHODS.get("save_as") and bank:
        action = lambda: rpc.preset_save_as(bank, name)
    else:
        toast("No save method is configured. Run probe_rpc.py.", "error")
        done(op, False)
        return

    # Overwriting leaves the bank list unchanged, so there's nothing to verify.
    _preset_action(action, "Saved %s" % name, op=op,
                   after=lambda: set_dirty(False))


@socketio.on("preset_save_as")
def client_preset_save_as(msg):
    msg = msg or {}
    op, bank, name = msg.get("op"), msg.get("bank"), msg.get("name")
    if not bank or not name:
        done(op, False)
        return
    # Afterwards you're on the new preset -- what you hear is what you just
    # saved, so the header should say so and nothing is unsaved any more.
    _preset_action(lambda: rpc.preset_save_as(bank, name), "Saved %s" % name,
                   verify=lambda m: name in m.get(bank, []), op=op,
                   after=lambda: _load_quietly(bank, name))


@socketio.on("preset_rename")
def client_preset_rename(msg):
    bank, old, new = (msg or {}).get("bank"), (msg or {}).get("old"), (msg or {}).get("new")
    if not (bank and old and new):
        done((msg or {}).get("op"), False)
        return
    _preset_action(lambda: rpc.preset_rename(bank, old, new), "Renamed to %s" % new,
                   verify=lambda m: new in m.get(bank, []) and old not in m.get(bank, []),
                   op=(msg or {}).get("op"))


@socketio.on("preset_delete")
def client_preset_delete(msg):
    bank, name = (msg or {}).get("bank"), (msg or {}).get("name")
    if not bank or not name:
        done((msg or {}).get("op"), False)
        return
    _preset_action(lambda: rpc.preset_delete(bank, name), "Deleted %s" % name,
                   verify=lambda m: name not in m.get(bank, []),
                   op=(msg or {}).get("op"))


@socketio.on("preset_move")
def client_preset_move(msg):
    msg = msg or {}
    src_bank, name = msg.get("bank"), msg.get("name")
    dst_bank, new_name = msg.get("to_bank"), msg.get("new") or msg.get("name")
    if not (src_bank and name and dst_bank):
        done(msg.get("op"), False)
        return
    # a move is only done when the copy exists AND the original is gone
    moved = (src_bank, name) != (dst_bank, new_name)
    _preset_action(lambda: _move_preset(src_bank, name, dst_bank, new_name),
                   "Moved %s to %s" % (new_name, dst_bank),
                   verify=lambda m: new_name in m.get(dst_bank, [])
                                    and (not moved or name not in m.get(src_bank, [])),
                   op=msg.get("op"))


def _wait_for(check, what, timeout=4.0):
    """
    Poll the bank list until `check` passes. A failed read mid-poll is retried
    rather than treated as the end -- one hiccup shouldn't abandon a move
    halfway through.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.25)
        try:
            if check(_banks_map()):
                return True
        except (RpcError, OSError, TimeoutError):
            continue
    raise RpcError(what)


def _move_preset(src_bank, name, dst_bank, new_name):
    """
    Move a preset between banks.

    If the engine has a real move method, use it. Otherwise compose one from
    what does exist:

      1. load the original, so the engine is holding its settings
      2. save those into the destination under the new name
      3. wait until the copy is really there
      4. load the COPY -- so the original is no longer the loaded preset
      5. delete the original, and wait until it's really gone

    Step 4 matters. Deleting the preset that's currently loaded is exactly
    what a previous version of this did, and an engine is within its rights
    to refuse that. It also means you end up playing the moved preset at
    its new home, which is what a move should leave you with.
    """
    if gx_rpc.PRESET_METHODS.get("move"):
        rpc.preset_move(src_bank, name, dst_bank, new_name)
        return

    same_slot = (src_bank, name) == (dst_bank, new_name)

    rpc.set_preset(src_bank, name)
    time.sleep(0.4)                       # let the engine finish loading it
    rpc.preset_save_as(dst_bank, new_name)

    _wait_for(lambda m: new_name in m.get(dst_bank, []),
              "the copy into %s never appeared, so %s was left alone"
              % (dst_bank, name))

    if same_slot:
        return

    rpc.set_preset(dst_bank, new_name)
    time.sleep(0.4)
    rpc.preset_delete(src_bank, name)

    _wait_for(lambda m: name not in m.get(src_bank, []),
              "%s is in %s now, but the original in %s wouldn't delete. "
              "Remove it from there by hand." % (new_name, dst_bank, src_bank))


@socketio.on("bank_create")
def client_bank_create(msg):
    name = (msg or {}).get("name")
    if not name:
        done((msg or {}).get("op"), False)
        return
    _preset_action(lambda: rpc.bank_create(name), "Created bank %s" % name,
                   verify=lambda m: name in m, op=(msg or {}).get("op"))


@socketio.on("bank_delete")
def client_bank_delete(msg):
    op = (msg or {}).get("op")
    name = (msg or {}).get("name")
    if not name:
        done(op, False)
        return

    with state.lock:
        banks = [b["name"] for b in state.banks]
        loaded = state.bank

    if name not in banks:
        toast("No bank called %s" % name, "error")
        done(op, False)
        return
    if len(banks) <= 1:
        toast("That's the only bank — make another one first", "error")
        done(op, False)
        return
    if name == loaded:
        toast("%s is the bank you're playing from. Switch to another preset "
              "first." % name, "error")
        done(op, False)
        return

    _preset_action(
        lambda: rpc.bank_delete(name),
        "Deleted bank %s" % name,
        verify=lambda m: name not in m,
        fail_message="%s is still there. Guitarix won't delete factory or "
                     "read-only banks, and the method name may be wrong "
                     "-- run probe_rpc.py." % name,
        op=op)


# ------------------------------------------------------------------ import / export
#
# Presets arrive as JSON (see presets_io.py). Every check runs against the
# live engine's own parameter list, so a file written for a different build
# says exactly what didn't fit instead of half-working.

NOT_CONNECTED = ("Guitarix isn't connected, so there's nothing to check the file "
                 "against yet.")


def _engine_parameters():
    with state.lock:
        return state.parameters if state.connected else {}


def _others_off(msg, parsed):
    """Switch off units a preset doesn't use? Only when asked — see presets_io."""
    if isinstance(msg.get("others_off"), bool):
        return msg["others_off"]
    return parsed.get("others") == "off"


def _import_bank(msg, parsed, preset):
    chosen = str(msg.get("bank") or "").strip()
    return chosen or preset.get("bank") or parsed.get("bank") or presets_io.DEFAULT_BANK


def _banks_now():
    with state.lock:
        return {b["name"]: list(b.get("presets", [])) for b in state.banks}


def _plan_live(preset, params, others_off, reset_defaults=True):
    current = rpc.get(presets_io.ids_to_read(preset, params))
    return presets_io.plan(preset, params, current, others_off, reset_defaults)


def _prepare_one(msg):
    """Parse, pick msg["index"], find its base. Planning waits until the base is loaded."""
    params = _engine_parameters()
    if not params:
        raise presets_io.ImportProblem(NOT_CONNECTED)
    parsed = presets_io.parse(msg.get("text", ""))
    try:
        preset = parsed["presets"][int(msg.get("index", 0))]
    except (IndexError, ValueError, TypeError):
        raise presets_io.ImportProblem("That preset isn't in the file any more. Check it again.")
    base = presets_io.resolve_base(preset.get("base"), _banks_now())
    return preset, base, params, _others_off(msg, parsed), _import_bank(msg, parsed, preset)


def _load_base_and_plan(preset, base, params, others_off):
    """
    Load the base preset, if there is one, then plan against what's live.
    With a base, settings the file leaves out come from the base; without
    one they go back to defaults.
    """
    if base:
        rpc.set_preset(*base)
        time.sleep(0.5)                  # let the engine finish loading it
    changes, report = _plan_live(preset, params, others_off, reset_defaults=base is None)
    if not report["set"]:
        raise presets_io.ImportProblem(
            "None of the settings in %s match this amp, so there's nothing to "
            "import. It was probably written for a different parameter list." % preset["name"])
    return changes, report


_bank_lock = threading.Lock()


def _ensure_bank(bank):
    # check-then-create must be one step: two imports racing for the same
    # new bank would otherwise both see it missing and both create it
    with _bank_lock:
        if bank in _banks_map():
            return
        if not gx_rpc.PRESET_METHODS.get("new_bank"):
            raise RpcError("There's no bank called %s, and this build can't create banks "
                           "from here. Choose an existing bank." % bank)
        rpc.bank_create(bank)
        _wait_for(lambda m: bank in m, "Couldn't create the bank %s." % bank)


def _apply(changes):
    rpc.set(changes)
    queue_params(changes)          # the engine won't echo our own writes


def _resync():
    """After a whole preset loads, re-read everything on screen."""
    try:
        refresh_preset()
    except Exception:              # a refresh failing mustn't fail the operation
        log.exception("resync failed")


# One import_audition at a time: the "is this the first audition?" check, the
# snapshot of what was playing, and installing state.audition must not
# interleave between two operations (the second would snapshot settings the
# first had already changed, and Discard would restore the wrong sound).
_audition_op_lock = threading.Lock()


def _end_audition():
    with state.lock:
        state.audition = None
    socketio.emit("audition", None)


@socketio.on("import_check")
def client_import_check(msg):
    """Validate a file and describe what each preset would do. Changes nothing."""
    msg = msg or {}
    params = _engine_parameters()
    if not params:
        return {"ok": False, "error": NOT_CONNECTED}
    try:
        parsed = presets_io.parse(msg.get("text", ""))
    except presets_io.ImportProblem as exc:
        return {"ok": False, "error": str(exc)}

    others_off = _others_off(msg, parsed)
    banks = _banks_now()

    reports = []
    for i, preset in enumerate(parsed["presets"]):
        bank = _import_bank(msg, parsed, preset)
        try:
            base = presets_io.resolve_base(preset.get("base"), banks)
            base_error = None
        except presets_io.ImportProblem as exc:
            base, base_error = None, str(exc)
        try:
            _, report = _plan_live(preset, params, others_off, reset_defaults=base is None)
        except (RpcError, OSError, TimeoutError) as exc:
            return {"ok": False, "error": "Couldn't read the current settings: %s" % exc}
        report.update(index=i, bank=bank, bank_exists=bank in banks,
                      replaces=preset["name"] in banks.get(bank, []),
                      base="%s/%s" % base if base else None, base_error=base_error)
        reports.append(report)

    return {"ok": True, "presets": reports, "others_off": others_off,
            "bank": _import_bank(msg, parsed, {}),
            "can_create_bank": bool(gx_rpc.PRESET_METHODS.get("new_bank"))}


@socketio.on("import_audition")
def client_import_audition(msg):
    """
    Apply a preset live without saving it.

    The first audition in a session records everything that was playing --
    the loaded preset and every setting, unsaved tweaks included -- BEFORE a
    base preset is loaded. Loading a base changes every setting, not just the
    ones the file mentions, so recording afterwards would make Discard put
    back the base instead of what you had.
    """
    msg = msg or {}
    op = msg.get("op")

    def attempt():
        try:
            preset, base, params, others_off, bank = _prepare_one(msg)
            with state.lock:
                fresh = state.audition is None
            prev = _loaded_now() if fresh else None
            if fresh:
                ids = set(presets_io.export_ids(params)) | {
                    pid for pid in preset["params"] if pid in params}
                before = rpc.get(sorted(ids))
            changes, report = _load_base_and_plan(preset, base, params, others_off)
            _apply(changes)
        except presets_io.ImportProblem as exc:
            toast(str(exc), "error")
            return done(op, False)
        except (RpcError, OSError, TimeoutError) as exc:
            toast("Couldn't audition it: %s" % exc, "error")
            return done(op, False)

        replaces = preset["name"] in _banks_now().get(bank, [])
        with state.lock:
            if state.audition is None:
                state.audition = {"before": dict(before), "prev": prev}
            state.audition.update(name=preset["name"], bank=bank, replaces=replaces,
                                  base="%s/%s" % base if base else None)
            public = state.public_audition()
        _resync()
        socketio.emit("audition", public)
        done(op, True)

    def run():
        with _audition_op_lock:
            attempt()

    socketio.start_background_task(run)


@socketio.on("audition_save")
def client_audition_save(msg=None):
    """Keep what's playing — tweaks included -- as the auditioned preset."""
    op = (msg or {}).get("op")
    with state.lock:
        audition = dict(state.audition) if state.audition else None
    if not audition:
        toast("Nothing is being auditioned.", "error")
        return done(op, False)
    bank, name = audition["bank"], audition["name"]

    def save():
        _ensure_bank(bank)
        rpc.preset_save_as(bank, name)

    def after():
        _end_audition()
        _load_quietly(bank, name)

    _preset_action(save, "Saved %s to %s" % (name, bank),
                   verify=lambda m: name in m.get(bank, []), op=op, after=after)


@socketio.on("audition_discard")
def client_audition_discard(msg=None):
    """Put back the preset that was loaded, then every setting as it was."""
    op = (msg or {}).get("op")
    with state.lock:
        audition = state.audition
        state.audition = None
    if not audition:
        return done(op, False)

    def run():
        prev = audition.get("prev") or (None, None)
        try:
            if prev[0] and prev[1]:
                rpc.set_preset(*prev)
                time.sleep(0.5)
            _apply(audition["before"])       # unsaved tweaks included
        except OSError as exc:
            with state.lock:
                state.audition = audition    # still auditioning; nothing was restored
            toast("Couldn't put things back: %s" % exc, "error")
            return done(op, False)
        if prev[0] and prev[1]:
            with state.lock:
                state.bank, state.preset = prev
            socketio.emit("preset", {"bank": prev[0], "preset": prev[1]})
        socketio.emit("audition", None)
        toast("Discarded %s. Everything is back how it was." % audition["name"], "ok")
        done(op, True)

    socketio.start_background_task(run)


@socketio.on("import_save")
def client_import_save(msg):
    """Save one preset from the file straight into a bank, no audition."""
    msg = msg or {}
    op = msg.get("op")
    try:
        preset, base, params, others_off, bank = _prepare_one(msg)
    except presets_io.ImportProblem as exc:
        toast(str(exc), "error")
        return done(op, False)
    except (RpcError, OSError, TimeoutError) as exc:
        toast("Couldn't import it: %s" % exc, "error")
        return done(op, False)
    name = preset["name"]

    def save():
        changes, _ = _load_base_and_plan(preset, base, params, others_off)
        _apply(changes)
        _ensure_bank(bank)
        rpc.preset_save_as(bank, name)

    def after():
        _end_audition()
        _load_quietly(bank, name)
        _resync()

    _preset_action(save, "Saved %s to %s" % (name, bank),
                   verify=lambda m: name in m.get(bank, []), op=op, after=after)


@socketio.on("import_save_all")
def client_import_save_all(msg):
    """Save every preset in the file, one after another, then load the last."""
    msg = msg or {}
    op = msg.get("op")
    params = _engine_parameters()
    try:
        if not params:
            raise presets_io.ImportProblem(NOT_CONNECTED)
        parsed = presets_io.parse(msg.get("text", ""))
    except presets_io.ImportProblem as exc:
        toast(str(exc), "error")
        return done(op, False)
    others_off = _others_off(msg, parsed)
    banks = _banks_now()

    # a preset with nothing usable in it, or a base that doesn't exist, is left out
    targets, skipped = [], []
    for p in parsed["presets"]:
        try:
            base = presets_io.resolve_base(p.get("base"), banks)
        except presets_io.ImportProblem:
            skipped.append(p["name"])
            continue
        if presets_io.plan(p, params, {}, others_off)[1]["set"]:
            targets.append((_import_bank(msg, parsed, p), p, base))
        else:
            skipped.append(p["name"])
    if not targets:
        toast("None of the presets in this file could be imported, so nothing was saved.", "error")
        return done(op, False)

    def save_all():
        # each preset starts from its base, and is confirmed saved before the next
        for bank, preset, base in targets:
            changes, _ = _load_base_and_plan(preset, base, params, others_off)
            _apply(changes)
            _ensure_bank(bank)
            rpc.preset_save_as(bank, preset["name"], lane=gx_rpc.LOW)
            _wait_for(lambda m, b=bank, n=preset["name"]: n in m.get(b, []),
                      "%s didn't save, so the rest weren't tried." % preset["name"])

    def after():
        _end_audition()
        bank, preset, _ = targets[-1]
        _load_quietly(bank, preset["name"])
        _resync()

    count = len(targets)
    message = "Saved %d preset%s" % (count, "" if count == 1 else "s")
    if skipped:
        message += ". Left out %s: nothing matched this amp, or its base is missing." % (
            ", ".join(skipped))
    _preset_action(save_all, message,
                   verify=lambda m: all(p["name"] in m.get(b, []) for b, p, _ in targets),
                   op=op, after=after)


@socketio.on("export_preset")
def client_export_preset(msg=None):
    """The sound as it is right now, as an importable file."""
    params = _engine_parameters()
    if not params:
        return {"ok": False, "error": NOT_CONNECTED}
    with state.lock:
        bank, name = state.bank, state.preset
        if state.audition:
            bank, name = state.audition["bank"], state.audition["name"]
    try:
        current = rpc.get(presets_io.export_ids(params))
    except (RpcError, OSError, TimeoutError) as exc:
        return {"ok": False, "error": "Couldn't read the current settings: %s" % exc}
    data = presets_io.export_live(params, current, bank, name)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", (name or "preset")).strip("-") or "preset"
    return {"ok": True, "data": data, "filename": stem + ".json"}


# ------------------------------------------------------------------ recording

@socketio.on("record_start")
def client_record_start(msg=None):
    msg = msg or {}
    params = _engine_parameters()
    sidecar = None
    if params:
        # best-effort: a take with no sidecar is still a perfectly good
        # recording, so a read failure here shouldn't block starting it
        try:
            current = rpc.get(presets_io.export_ids(params))
            sidecar = presets_io.export_live(params, current, state.bank, state.preset)
        except (RpcError, OSError, TimeoutError) as exc:
            log.warning("couldn't capture settings for the take: %s", exc)
    status = rec.start(msg.get("name"), dry=bool(msg.get("dry")), sidecar=sidecar)
    if status.get("error"):
        toast(status["error"], "error")
    elif backing.include_in_takes and backing.player.playing:
        def feed():
            # the recorder's ports appear a moment after ffmpeg starts
            for _ in range(30):
                if rec.recorder_inputs():
                    return backing.recording_started(rec)
                time.sleep(0.1)
        socketio.start_background_task(feed)
    socketio.emit("rec", rec_payload())


def _reamp_call(fn, op=None, fail="Couldn't do that"):
    try:
        if fn() is False:
            raise BackingError("the player wouldn't accept the command")
    except (ReampError, BackingError, OSError) as exc:
        toast("%s: %s" % (fail, exc), "error")
        socketio.emit("rec", rec_payload())
        return done(op, False)
    socketio.emit("rec", rec_payload())
    done(op, True)


@socketio.on("reamp_start")
def client_reamp_start(msg):
    """Play a take's dry recording through the amp, in real time."""
    msg = msg or {}
    op = msg.get("op")

    def run():
        _reamp_call(lambda: reamp.start(msg.get("take"), msg.get("dry"),
                                        mode=msg.get("mode", "wet"),
                                        record=bool(msg.get("record")),
                                        loop=bool(msg.get("loop"))),
                    op, "Couldn't reamp it")

    socketio.start_background_task(run)


@socketio.on("reamp_mode")
def client_reamp_mode(msg):
    _reamp_call(lambda: reamp.set_mode("dry" if (msg or {}).get("mode") == "dry" else "wet"),
                (msg or {}).get("op"), "Couldn't switch the amp")


@socketio.on("reamp_pause")
def client_reamp_pause(msg):
    _reamp_call(lambda: reamp.pause(bool((msg or {}).get("paused", True))))


@socketio.on("reamp_loop")
def client_reamp_loop(msg):
    def run():
        if reamp.set_loop(bool((msg or {}).get("loop"))) is False:
            raise ReampError("the player wouldn't change its looping")
    _reamp_call(run, (msg or {}).get("op"), "Couldn't change looping")


@socketio.on("reamp_stop")
def client_reamp_stop(msg=None):
    _reamp_call(reamp.stop, (msg or {}).get("op"))


def _loaded_now():
    """
    The preset guitarix has loaded, asked of guitarix itself. The app's own
    record can be stale: the engine doesn't echo a client's own changes back
    to it, so anything that changes the preset without the app hearing about
    it would leave that record wrong -- and "put things back" would restore
    the wrong preset.
    """
    try:
        bank, preset = rpc.current_preset()
        if bank and preset:
            return bank, preset
    except (RpcError, OSError, TimeoutError):
        pass
    with state.lock:
        return state.bank, state.preset


def _snapshot_state(lane=gx_rpc.HIGH):
    """Everything that's playing now: the loaded preset and every setting."""
    params = _engine_parameters() or {}
    prev = _loaded_now()
    return {"prev": prev,
            "values": rpc.get(presets_io.export_ids(params), lane=lane) if params else {}}


def _restore_state(snap):
    bank, preset = snap["prev"]
    if bank and preset:
        rpc.set_preset(bank, preset)
        time.sleep(0.5)
    if snap["values"]:
        _apply(snap["values"])                    # unsaved tweaks included
    if bank and preset:
        with state.lock:
            state.bank, state.preset = bank, preset
        socketio.emit("preset", {"bank": bank, "preset": preset})
    _resync()


@socketio.on("export_start")
def client_export_start(msg):
    """
    Render a take through chosen settings, in the background, to a new file.

    It has to play through the live amp in real time, so the rig is busy --
    guitar disconnected -- for the length of the take. Afterwards everything
    is put back: the loaded preset and every setting, unsaved tweaks included.
    """
    msg = msg or {}
    op = msg.get("op")
    take, dry = msg.get("take"), msg.get("dry")
    source = str(msg.get("source") or "live")
    fmt = "mp3" if msg.get("format") == "mp3" else "wav"
    with state.lock:
        auditioning = state.audition is not None

    problem = None
    if not take or not dry:
        problem = "That take has no dry recording to export from."
    elif _export_job():
        problem = "An export is already running."
    elif reamp.active:
        problem = "Stop the reamp first."
    elif rec.recording:
        problem = "Stop the recording first."
    elif auditioning:
        problem = "Save or discard the audition first."
    if problem:
        toast(problem, "error")
        return done(op, False)

    stem = take.rsplit(".", 1)[0]
    if source == "recorded":
        label, tag = "the settings it was recorded with", "export as recorded"
    elif source.startswith("preset:"):
        label = source[len("preset:"):]
        tag = "export - " + label.split("/", 1)[-1]
    else:
        source, label, tag = "live", "the current settings", "export"
    # the checks above are only a fast path; this is the atomic check-and-claim
    if not _export_claim({"take": take, "label": label, "format": fmt}):
        toast("An export is already running.", "error")
        return done(op, False)
    socketio.emit("rec", rec_payload())

    def run():
        snap, ok, out = None, False, None
        try:
            snap = _snapshot_state(lane=gx_rpc.LOW)
            if source == "recorded":
                path = rec._resolve(stem + ".json")
                if not path:
                    raise presets_io.ImportProblem("This take has no recorded settings.")
                with open(path) as f:
                    preset = presets_io.parse(f.read())["presets"][0]
                changes, _ = _plan_live(preset, _engine_parameters(), False, reset_defaults=False)
                _apply(changes)
            elif source.startswith("preset:"):
                base = presets_io.resolve_base(label, _banks_now())
                rpc.set_preset(*base)
            time.sleep(0.6)                        # let the settings land before playing

            if not _export_cancelled():
                out = reamp.start(take, dry, record=True, name="%s (%s)" % (stem, tag))
            length = next((t["duration"] for t in rec.listing() if t["name"] == take), None) or 600
            deadline = time.time() + length + 30
            while reamp.active and time.time() < deadline:
                time.sleep(0.25)
            if reamp.active:
                reamp.stop()
                raise RuntimeError("playback ran long and was stopped")
            for _ in range(40):                    # belt and braces: the file must be closed
                if not rec.recording:
                    break
                time.sleep(0.25)

            if _export_cancelled():
                if out:
                    rec.delete(out)
                out = None
                toast("Export cancelled. Nothing was kept.", "ok")
            else:
                if fmt == "mp3" and out:
                    wav = os.path.join(rec.dir, out)
                    mp3 = rec._unique(os.path.splitext(wav)[0] + ".mp3")
                    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", wav,
                                    "-c:a", "libmp3lame", "-b:a", "192k", mp3],
                                   check=True, capture_output=True, timeout=600)
                    os.remove(wav)
                    out = os.path.basename(mp3)
                ok = True
        except (presets_io.ImportProblem, ReampError, RpcError, OSError, TimeoutError,
                RuntimeError, subprocess.SubprocessError) as exc:
            toast("Export failed: %s" % exc, "error")
        finally:
            if snap:
                try:
                    _restore_state(snap)
                except (RpcError, OSError, TimeoutError) as exc:
                    toast("The export finished, but the settings couldn't be put back: %s" % exc, "error")
            _export_release()
            push_recordings()
        if ok:
            toast("Exported %s" % out, "ok")
            socketio.emit("export_done", {"file": out})
        done(op, ok)

    socketio.start_background_task(run)


@socketio.on("export_cancel")
def client_export_cancel(msg=None):
    if _export_request_cancel():
        reamp.stop()


@socketio.on("backing_play")
def client_backing_play(msg):
    msg = msg or {}
    op = msg.get("op")

    def run():
        _reamp_call(lambda: backing.play(msg.get("name"), rec,
                                         kind="take" if msg.get("kind") == "take" else "backing",
                                         volume=int(msg.get("volume", 80)),
                                         loop=bool(msg.get("loop", True))),
                    op, "Couldn't play it")

    socketio.start_background_task(run)


@socketio.on("backing_stop")
def client_backing_stop(msg=None):
    _reamp_call(backing.stop, (msg or {}).get("op"))


@socketio.on("backing_pause")
def client_backing_pause(msg):
    _reamp_call(lambda: backing.player.pause(bool((msg or {}).get("paused", True))))


@socketio.on("backing_volume")
def client_backing_volume(msg):
    msg = msg or {}

    def set_volume():
        raw = msg.get("volume", 80)
        try:
            if isinstance(raw, bool) or not isinstance(raw, (int, str)):
                raise ValueError
            volume = int(raw)
            if not 0 <= volume <= 100:
                raise ValueError
        except ValueError:
            raise BackingError("invalid volume")
        return backing.player.set_volume(volume)

    _reamp_call(set_volume, msg.get("op"), "Couldn't change volume")


@socketio.on("backing_loop")
def client_backing_loop(msg):
    def run():
        if not backing.player.set_loop(bool((msg or {}).get("loop"))):
            raise BackingError("the player wouldn't change its looping")
    _reamp_call(run, (msg or {}).get("op"), "Couldn't change looping")


@socketio.on("backing_include")
def client_backing_include(msg):
    backing.include_in_takes = bool((msg or {}).get("include"))
    if backing.include_in_takes and rec.recording:
        backing.recording_started(rec)
    socketio.emit("rec", rec_payload())


@socketio.on("backing_delete")
def client_backing_delete(msg):
    _reamp_call(lambda: backing.delete((msg or {}).get("name")),
                (msg or {}).get("op"), "Couldn't delete it")


@socketio.on("record_stop")
def client_record_stop(msg=None):
    socketio.emit("rec", rec.stop())


@socketio.on("rec_rename")
def client_rec_rename(msg):
    op = (msg or {}).get("op")
    try:
        new = rec.rename((msg or {}).get("name"), (msg or {}).get("new"))
    except (FileNotFoundError, OSError) as exc:
        toast("Couldn't rename it: %s" % exc, "error")
        return done(op, False)
    toast("Renamed to %s" % new, "ok")
    done(op, True)


@socketio.on("rec_delete")
def client_rec_delete(msg):
    op = (msg or {}).get("op")
    try:
        rec.delete((msg or {}).get("name"))
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        toast("Couldn't delete it: %s" % exc, "error")
        return done(op, False)
    toast("Deleted the take", "ok")
    done(op, True)


@socketio.on("rec_delete_many")
def client_rec_delete_many(msg):
    op = (msg or {}).get("op")
    names = (msg or {}).get("names")
    if not isinstance(names, list) or not names:
        toast("Couldn't delete: no takes were chosen", "error")
        return done(op, False)
    deleted, failed = rec.delete_many(names)
    if failed and not deleted:
        toast("Couldn't delete them: %s" % failed[0][1], "error")
    elif failed:
        toast("Deleted %d of %d takes; couldn't delete %s: %s"
              % (len(deleted), len(deleted) + len(failed), failed[0][0], failed[0][1]),
              "error")
    else:
        toast("Deleted %d take%s" % (len(deleted), "" if len(deleted) == 1 else "s"), "ok")
    done(op, not failed)


_services_started = False
_services_lock = threading.Lock()


def start_services():
    """Start the guitarix connection and the background loops, once.

    `python3 app.py` calls this itself. Under gunicorn `__main__` never runs,
    so wsgi.py calls it instead: without it the page loads but nothing ever
    connects to guitarix or pushes an update.
    """
    global _services_started
    with _services_lock:
        if _services_started:
            return
        _services_started = True
    if DEMO_ONLY:
        # nothing to connect to, nothing to record: just serve the demo
        log.info("demo only, on port %d -- guitarix is never contacted", WEB_PORT)
    else:
        rpc.start()
        socketio.start_background_task(flusher)
        socketio.start_background_task(ticker)
# `python3 app.py` serves with Werkzeug; gunicorn imports wsgi.py instead.
if __name__ == "__main__":
    start_services()
    socketio.run(app, host="0.0.0.0", port=WEB_PORT, allow_unsafe_werkzeug=True)
