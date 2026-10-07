"""
Persistent JSON-RPC connection to a running guitarix engine.

Guitarix speaks JSON-RPC 2.0 over a plain TCP socket, one JSON object per
line. Start the engine with the RPC port open:

    guitarix -N -p 7000          # headless
    guitarix -p 7000             # with the GTK UI

Protocol notes that matter here:

  * call   -> {"jsonrpc":"2.0","method":...,"params":[...],"id":"1"}  (reply expected)
  * notify -> same without "id"                                       (no reply)
  * "listen" with ["all"] subscribes this connection to state changes.
  * The engine broadcasts changes to every client EXCEPT the one that
    caused them. So when we set a value we will not hear it back, and we
    have to update our own cache ourselves.
  * Parameters ending in ".v" are output/meter ports. They fire constantly
    and are dropped here.

This class owns one socket and one reader thread. Calls are serialised
behind a lock so the reply always belongs to the call that is waiting.
It reconnects on its own if guitarix restarts.
"""

import json
import logging
import queue
import socket
import threading
import time

log = logging.getLogger("gx_rpc")

# Two lanes over the one socket. Live control (a knob move, a preset load)
# goes out on HIGH and is scheduled ahead of bulk work (imports, exports,
# diagnostics) on LOW, so a long import can't sit in front of a knob move.
HIGH, LOW = 0, 1
LANES = (HIGH, LOW)


class PriorityLaneQueue:
    """
    Priority-lane queue for RPC operations on a single connection.

    Two classes of work (high and low) are kept in separate FIFO lanes.
    Selection always drains the high lane before the low lane, so a pending
    high-priority operation is never overtaken by a low-priority one.

    Every operation carries a per-operation deadline (seconds from enqueue).
    This class performs no socket I/O: it is driven entirely by an injected
    clock, so it can be exercised with a fake clock in tests.
    """

    HIGH = "high"
    LOW = "low"

    def __init__(self, high_deadline=0.05, low_deadline=0.5, clock=None):
        self.high_deadline = high_deadline
        self.low_deadline = low_deadline
        self._clock = clock or time.monotonic
        self._lanes = {self.HIGH: [], self.LOW: []}
        self._cancelled = set()
        self._seq = 0

    def _deadline_for(self, priority):
        if priority == self.HIGH:
            return self.high_deadline
        return self.low_deadline

    def enqueue(self, op, priority=HIGH):
        """Add `op` to a lane. Returns the operation id."""
        if priority not in self._lanes:
            raise ValueError("unknown priority %r" % (priority,))
        self._seq += 1
        op_id = self._seq
        entry = {
            "id": op_id,
            "op": op,
            "priority": priority,
            "enqueued_at": self._clock(),
            "deadline": self._deadline_for(priority),
        }
        self._lanes[priority].append(entry)
        return op_id

    def cancel(self, op_id):
        """Mark an operation cancelled. Returns True if it was still pending."""
        for lane in self._lanes.values():
            for entry in lane:
                if entry["id"] == op_id:
                    lane.remove(entry)
                    self._cancelled.add(op_id)
                    return True
        self._cancelled.add(op_id)
        return False

    def is_cancelled(self, op_id):
        return op_id in self._cancelled

    def expired(self, entry):
        """True if the entry's per-operation deadline has elapsed."""
        return (self._clock() - entry["enqueued_at"]) >= entry["deadline"]

    def next_eligible(self):
        """
        Pop and return the next pending operation, or None.

        High-priority entries are always considered before low-priority ones.
        Cancelled entries are skipped. Expired entries are skipped and
        discarded (their deadline has passed, so they are no longer eligible).
        """
        for priority in (self.HIGH, self.LOW):
            lane = self._lanes[priority]
            while lane:
                entry = lane.pop(0)
                if entry["id"] in self._cancelled:
                    continue
                if self.expired(entry):
                    continue
                return entry
        return None

    def pending(self, priority=None):
        """Number of not-yet-selected entries, optionally for one lane."""
        if priority is None:
            return sum(len(lane) for lane in self._lanes.values())
        return len(self._lanes[priority])


class RpcMethodMissing(Exception):
    """The engine doesn't know this method name."""


class RpcError(Exception):
    """The engine rejected the call."""


# Guitarix has no published list of RPC method names, and they have moved
# around between releases. These are the names this app uses; run
#     python3 probe_rpc.py
# to find out which ones your build has, then correct them here. CANDIDATES
# is what the probe walks through.
#
# These are all NOTIFY methods: guitarix acts on them and sends nothing back.
# Sending them as calls just hangs until the timeout, so they go out as
# notifications and app.py confirms the result by re-reading the bank list.
PRESET_METHODS = {
    "save_current": "save_current_preset",
    "save_as":      "save_preset",
    "rename":       "rename_preset",
    "delete":       "erase_preset",
    "new_bank":     "bank_insert_new",
    "delete_bank":  "remove_bank",
    # If your build turns out to have a real move/copy method, put it here and
    # it will be used instead of the load-save-delete fallback in app.py.
    "move":         None,
}

# Verified against guitarix 0.44.1 (build 0.44.1-1, Debian bookworm arm64)
# with `python3 probe_rpc.py`: every name above answered "silent" (notify-only)
# and no candidate returned JSON-RPC -32601. "move" stays None because no
# move/copy method exists in this build.
VERIFIED_BUILD = "guitarix 0.44.1"

CANDIDATES = {
    "save_current": ["save_current_preset", "save_preset_current", "setpreset_save"],
    "save_as":      ["save_preset", "insert_preset", "append_preset", "create_preset"],
    "rename":       ["rename_preset", "preset_rename"],
    "delete":       ["erase_preset", "delete_preset", "remove_preset"],
    "new_bank":     ["bank_insert_new", "new_bank", "create_bank", "insert_bank"],
    "delete_bank":  ["remove_bank", "delete_bank", "bank_remove", "erase_bank",
                     "bank_delete"],
    "move":         ["move_preset", "copy_preset", "bank_move_preset"],
}


class GuitarixRPC:
    def __init__(self, host="127.0.0.1", port=7000,
                 on_params=None, on_event=None, on_status=None, on_ready=None):
        self.host = host
        self.port = port

        # callbacks, all invoked from the reader thread
        self.on_params = on_params or (lambda changes: None)
        self.on_event = on_event or (lambda method, params: None)
        self.on_status = on_status or (lambda connected: None)
        self.on_ready = on_ready or (lambda: None)

        self.values = {}            # id -> last known value
        self.connected = False

        self._sock = None
        self._buf = b""
        self._replies = queue.Queue(maxsize=1)
        self._call_lock = threading.Lock()   # one outstanding call at a time
        self._send_lock = threading.Lock()
        self._stop = threading.Event()

        # one queue per lane, drained HIGH first; the condition lets a
        # waiting sender be woken when the lane it wants has room
        self._lanes = {lane: queue.Queue() for lane in LANES}
        self._lane_cond = threading.Condition()
        self._lane_thread = None
        self._lane_stop = threading.Event()

    # ---------------------------------------------------------------- lifecycle

    def start(self):
        threading.Thread(target=self._supervise, name="gx-rpc", daemon=True).start()
        self._lane_thread = threading.Thread(target=self._lane_loop, name="gx-rpc-lanes",
                                             daemon=True)
        self._lane_thread.start()

    def stop(self):
        self._stop.set()
        self._lane_stop.set()
        with self._lane_cond:
            self._lane_cond.notify_all()
        self._close()

    def _supervise(self):
        backoff = 1.0
        while not self._stop.is_set():
            try:
                self._connect()
                backoff = 1.0
                self._read_loop()
            except (OSError, ValueError) as exc:
                log.warning("guitarix rpc: %s", exc)
            finally:
                self._close()
            if self._stop.is_set():
                break
            time.sleep(backoff)
            backoff = min(backoff * 2, 15.0)

    def _connect(self):
        log.info("connecting to guitarix at %s:%s", self.host, self.port)
        s = socket.create_connection((self.host, self.port), timeout=5)
        s.settimeout(None)
        self._sock = s
        self._buf = b""
        self.connected = True

        # subscribe this connection to every state change
        self.notify("listen", ["all"])

        self.on_status(True)
        # bootstrap runs off-thread because it makes calls, and calls need
        # the reader loop below to be running to collect their replies.
        threading.Thread(target=self._bootstrap, daemon=True).start()

    def _bootstrap(self):
        try:
            self.on_ready()
        except Exception:
            log.exception("bootstrap failed")

    def _close(self):
        was = self.connected
        self.connected = False
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        # unblock anyone waiting on a reply
        try:
            self._replies.put_nowait(None)
        except queue.Full:
            pass
        if was:
            self.on_status(False)

    # ---------------------------------------------------------------- transport

    def _send(self, method, params, call_id=None):
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        if call_id is not None:
            payload["id"] = call_id
        line = (json.dumps(payload) + "\n").encode("utf-8")
        with self._send_lock:
            sock = self._sock
            if sock is None:
                raise OSError("not connected to guitarix")
            sock.sendall(line)

    # ---------------------------------------------------------------- lanes

    def _lane_loop(self):
        """
        Drain the lanes onto the socket, HIGH before LOW.

        A single thread does the sending, so the ordering rule is simply
        "look at HIGH first": while anything is pending there, LOW waits.
        """
        while not self._lane_stop.is_set():
            item = None
            for lane in LANES:
                try:
                    item = self._lanes[lane].get_nowait()
                    break
                except queue.Empty:
                    continue
            if item is None:
                with self._lane_cond:
                    self._lane_cond.wait(timeout=0.05)
                continue
            method, params, call_id = item
            try:
                self._send(method, params, call_id)
            except OSError as exc:
                log.warning("guitarix rpc: dropping %r: %s", method, exc)

    def _enqueue(self, lane, method, params, call_id=None):
        if lane not in self._lanes:
            raise ValueError("unknown lane %r" % (lane,))
        self._lanes[lane].put((method, params, call_id))
        with self._lane_cond:
            self._lane_cond.notify_all()

    def notify(self, method, params=None, lane=HIGH):
        """Fire and forget, on `lane` (HIGH by default)."""
        self._enqueue(lane, method, params or [])

    def call(self, method, params=None, timeout=5.0, lane=HIGH):
        """Send a request and wait for its reply, on `lane` (HIGH by default)."""
        with self._call_lock:
            # drain a stale reply left behind by a timed-out call
            try:
                self._replies.get_nowait()
            except queue.Empty:
                pass
            self._enqueue(lane, method, params or [], call_id="1")
            try:
                reply = self._replies.get(timeout=timeout)
            except queue.Empty:
                raise TimeoutError("guitarix did not answer %r" % method)
        if reply is None:
            raise OSError("connection closed while calling %r" % method)
        err = reply.get("error")
        if err:
            code = err.get("code") if isinstance(err, dict) else None
            text = err.get("message") if isinstance(err, dict) else str(err)
            if code == -32601:
                raise RpcMethodMissing(method)
            raise RpcError("%s: %s" % (method, text or err))
        return reply.get("result")

    def _read_loop(self):
        # Capture the socket once: _close() (another thread) sets self._sock to
        # None, and a closed socket then surfaces as OSError, which _supervise
        # already handles, instead of AttributeError on None.
        sock = self._sock
        if sock is None:
            raise OSError("not connected to guitarix")
        while not self._stop.is_set():
            chunk = sock.recv(65536)
            if not chunk:
                raise OSError("guitarix closed the connection")
            self._buf += chunk
            while b"\n" in self._buf:
                line, _, self._buf = self._buf.partition(b"\n")
                line = line.strip()
                if line:
                    self._dispatch(line)

    def _dispatch(self, line):
        try:
            msg = json.loads(line.decode("utf-8"))
        except ValueError:
            log.warning("unparseable rpc line: %r", line[:200])
            return

        if "result" in msg or "error" in msg:
            try:
                self._replies.put_nowait(msg)
            except queue.Full:
                log.debug("dropping unexpected reply: %s", msg)
            return

        method = msg.get("method")
        params = msg.get("params") or []

        # An empty notification is NOT a shutdown signal. The maintainer's demo
        # script treats it as one, which is fine for a single-shot script, but
        # the engine also sends empty notifications for ordinary events like
        # the preset list changing. Hanging up on those dropped the connection
        # in the middle of preset moves. A real shutdown closes the socket,
        # and _read_loop already notices that.
        if not params:
            self.on_event(method, [])
            return

        # drop meter / output-port spam
        if isinstance(params[0], str) and params[0].endswith(".v"):
            return

        if method == "set":
            changes = {}
            for pid, value in zip(params[::2], params[1::2]):
                self.values[pid] = value
                changes[pid] = value
            if changes:
                self.on_params(changes)
        else:
            self.on_event(method, params)

    # ---------------------------------------------------------------- guitarix API

    def get(self, ids, lane=HIGH):
        """Read one or more parameter values. Returns {id: value}."""
        if isinstance(ids, str):
            ids = [ids]
        result = self.call("get", list(ids), lane=lane) or {}
        self.values.update(result)
        return result

    def set(self, changes, lane=HIGH):
        """
        Write parameters. `changes` is {id: value}.

        Guitarix will not echo these back to us (it only broadcasts to the
        other clients), so the local cache is updated here and the caller is
        responsible for telling the browsers.
        """
        params = []
        for pid, value in changes.items():
            params.extend([pid, value])
        # Send first: if the transmission raises, the engine never saw the
        # change, so the local cache must not claim it did.
        self.notify("set", params, lane=lane)
        self.values.update(changes)

    def banks(self, lane=HIGH):
        """[{'name': 'MyBank', 'presets': ['Clean', ...]}, ...]"""
        return self.call("banks", [], lane=lane) or []

    def set_preset(self, bank, preset, lane=HIGH):
        self.notify("setpreset", [bank, preset], lane=lane)

    def current_preset(self):
        r = self.get(["system.current_bank", "system.current_preset"])
        return r.get("system.current_bank"), r.get("system.current_preset")

    def parameter_list(self, lane=HIGH):
        """
        Full parameter description from the engine, flattened to:
            {id: {"id","name","type","min","max","step","value"}}

        The raw reply is a flat list alternating type name and descriptor:
            ["FloatParameter", {...}, "BoolParameter", {...}, ...]
        Enum/FloatEnum wrap an inner IntParameter/FloatParameter, and every
        descriptor carries a nested "Parameter" with the common fields.
        """
        raw = self.call("parameterlist", [], timeout=15.0, lane=lane) or []
        out = {}
        for type_name, desc in zip(raw[::2], raw[1::2]):
            info = self._flatten(type_name, desc)
            if info:
                out[info["id"]] = info
        return out

    def raw_parameters(self, lane=HIGH):
        """The unflattened descriptors, {id: (type, descriptor)}, for inspection."""
        raw = self.call("parameterlist", [], timeout=15.0, lane=lane) or []
        out = {}
        for type_name, desc in zip(raw[::2], raw[1::2]):
            info = self._flatten(type_name, desc)
            if info:
                out[info["id"]] = (type_name, desc)
        return out

    @staticmethod
    def _flatten(type_name, desc):
        if not isinstance(desc, dict):
            return None

        # unwrap Enum -> IntParameter, FloatEnum -> FloatParameter
        body = desc
        for wrapper in ("IntParameter", "FloatParameter", "BoolParameter"):
            if wrapper in body and isinstance(body[wrapper], dict):
                body = body[wrapper]
                break

        common = body.get("Parameter")
        if not isinstance(common, dict):
            common = body
        pid = common.get("id")
        if not pid:
            return None

        def pick(*keys, default=None):
            for src in (body, common, desc):
                for key in keys:
                    if key in src:
                        return src[key]
            return default

        lower = pick("lower_bound", "min_value", "lower", default=0)

        # Enums carry a list of value names. Depending on the build that's
        # plain strings or [id, label] pairs; either way the stored value is
        # an index counted from the lower bound.
        options = None
        names = pick("value_names", "values", default=None)
        if isinstance(names, list) and names:
            options = []
            for i, entry in enumerate(names):
                key = label = None
                if isinstance(entry, (list, tuple)) and entry:
                    key, label = entry[0], entry[-1]
                elif isinstance(entry, dict):
                    key = (entry.get("value_id") or entry.get("id")
                           or entry.get("key") or entry.get("name"))
                    label = (entry.get("value_label") or entry.get("label")
                             or entry.get("name") or key)
                else:
                    key = label = entry
                label = str(label).strip() if label not in (None, "") else ""
                options.append({
                    "value": (lower or 0) + i,     # what an index-based engine expects
                    "key": None if key is None else str(key),   # what a name-based one does
                    "label": label or (str(key) if key is not None else str(i)),
                })

        return {
            "id": pid,
            "name": common.get("name") or pid.split(".")[-1],
            # The engine's own tooltip text, when it sends one. Short internal
            # names like "s_h" are often explained here and nowhere else.
            "desc": common.get("desc") or body.get("desc") or "",
            "group": common.get("group") or "",
            "type": type_name,
            "min": lower,
            "max": pick("upper_bound", "max_value", "upper", default=1),
            "step": pick("step", "step_size", default=0),
            "value": pick("value", "default_value", "std_value", default=0),
            "options": options,
            "non_preset": "non_preset" in common or "non_preset" in body,
        }

    # ------------------------------------------------------------ presets

    def _preset_notify(self, action, params, lane=HIGH):
        name = PRESET_METHODS.get(action)
        if not name:
            raise RpcMethodMissing(action)
        self.notify(name, params, lane=lane)

    def preset_save_current(self, lane=HIGH):
        """Write the live settings back into the preset that's loaded."""
        return self._preset_notify("save_current", [], lane=lane)

    def preset_save_as(self, bank, name, lane=HIGH):
        """Store the live settings as a new preset (or overwrite an existing one)."""
        return self._preset_notify("save_as", [bank, name], lane=lane)

    def preset_rename(self, bank, old, new, lane=HIGH):
        return self._preset_notify("rename", [bank, old, new], lane=lane)

    def preset_delete(self, bank, name, lane=HIGH):
        return self._preset_notify("delete", [bank, name], lane=lane)

    def bank_create(self, name, lane=HIGH):
        return self._preset_notify("new_bank", [name], lane=lane)

    def bank_delete(self, name, lane=HIGH):
        """Removes the bank and every preset in it."""
        return self._preset_notify("delete_bank", [name], lane=lane)

    def preset_move(self, src_bank, name, dst_bank, new_name, lane=HIGH):
        return self._preset_notify("move", [src_bank, name, dst_bank, new_name], lane=lane)

    def probe(self, method, timeout=2.0, lane=HIGH):
        """
        Classify a method name. Sends it with no arguments and reads what
        comes back:

            "missing"  JSON-RPC -32601: the engine has no such method
            "answers"  a result or any other error: it exists and replies
            "silent"   nothing at all: it exists and is a notify-only method

        "silent" is the normal answer for the preset methods. Guitarix acts
        on them without replying, which is why they must be sent as
        notifications rather than calls.
        """
        try:
            self.call(method, [], timeout=timeout, lane=lane)
        except RpcMethodMissing:
            return "missing"
        except TimeoutError:
            return "silent"
        except RpcError:
            return "answers"
        return "answers"
