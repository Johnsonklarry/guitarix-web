"""
Hearing the rig through the web page.

One ffmpeg reads a JACK client of its own ("gxweb-mon") and encodes what it
hears to MP3, and every browser that's listening gets a copy of the stream --
an internet radio station, in effect. A plain <audio> element plays it, which
works over plain http in every browser, keeps playing on a locked phone, and
needs no special browser APIs. The cost is delay: a second or three, mostly
the browser's own buffering.

What it hears is chosen by the caller: a function returning the JACK output
ports to tap -- the amp, a backing track, a reamp with the amp switched off.
It's re-checked every second, so something that starts playing later is
picked up, and something that shouldn't be heard any more (a reamp switched
back through the amp, which the amp's output already carries) is dropped.
Tapping an output never disturbs its existing connections: JACK outputs can
feed any number of inputs.

The encoder only runs while at least one browser is listening, and runs at a
lower priority than guitarix, so encoding can never take CPU from the amp.
"""

import logging
import queue
import shutil
import subprocess
import threading
import time

import jackutil

log = logging.getLogger("monitor")

CLIENT = "gxweb-mon"
FFMPEG = "ffmpeg"
BITRATE = "160k"
BYTES_PER_SEC = 160000 // 8     # nominal MP3 payload rate at BITRATE
CHUNK = 1024                    # read1() size: ~51 ms of audio, never wait to fill 4096
# Lag budget: the most audio a listener may have queued here, in seconds. A slower
# listener loses its oldest whole MP3 frames until it is back inside the budget.
# This bounds only the queue in this process, not ffmpeg/browser/proxy buffering.
# It is not a fixed constant: the bound is derived from the adaptive budget the
# caller configures, so a listener can be given more or less slack than the
# default without editing this module.
DEFAULT_LAG_BUDGET = 0.5
STDERR_TAIL = 2048              # bytes of encoder stderr kept for the log

_KBPS = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0)   # MPEG-1 layer III
_HZ = (44100, 48000, 32000, 0)


def _read1(stream, size):
    """One read that returns what is there instead of waiting for `size` bytes."""
    read = getattr(stream, "read1", None) or stream.read
    return read(size)


def _frame_size(buf):
    """Length of the MPEG-1 layer III frame whose header starts buf, or 0."""
    if len(buf) < 4 or buf[0] != 0xff or (buf[1] & 0xfe) != 0xfa:
        return 0
    kbps, hz = _KBPS[buf[2] >> 4], _HZ[(buf[2] >> 2) & 3]
    if not kbps or not hz:
        return 0
    return 144000 * kbps // hz + ((buf[2] >> 1) & 1)


def _take_frames(pending):
    """
    Remove from `pending` (a bytearray) every complete MP3 frame and return
    them as a list of bytes. Bytes that are not a frame are returned as they
    are, never dropped, so only whole frames are ever the unit of shedding.
    """
    out = []
    while len(pending) >= 4:
        size = _frame_size(pending)
        if size:
            if len(pending) < size:
                break                              # wait for the rest of the frame
            out.append(bytes(pending[:size]))
            del pending[:size]
            continue
        nxt = pending.find(b"\xff", 1)
        end = len(pending) if nxt < 0 else nxt
        out.append(bytes(pending[:end]))
        del pending[:end]
    return out


def _offer(q, item, max_lag_bytes):
    """Queue `item`, first shedding the oldest items until q is inside the lag budget."""
    while True:
        with q.mutex:
            queued = sum(len(c) for c in q.queue if c)
        if queued + len(item) <= max_lag_bytes:
            break
        try:
            q.get_nowait()                         # drop the oldest: stay near live
        except queue.Empty:
            break
    q.put_nowait(item)


def encoder_args():
    args = [FFMPEG, "-hide_banner", "-loglevel", "error",
            "-f", "jack", "-i", CLIENT, "-ac", "2",
            "-c:a", "libmp3lame", "-b:a", BITRATE,
            # no bit reservoir: every frame decodes on its own, so a frame
            # shed for a slow listener cannot damage the ones after it
            "-reservoir", "0", "-write_xing", "0", "-id3v2_version", "0",
            "-f", "mp3", "-flush_packets", "1", "pipe:1"]
    if shutil.which("nice"):
        args = ["nice", "-n", "10"] + args        # the amp's realtime work comes first
    return args


class Monitor:
    def __init__(self, sources, lag_budget=DEFAULT_LAG_BUDGET):
        self.sources = sources                    # () -> [[port, ...], ...]
        self.lag_budget = lag_budget              # seconds of audio a listener may queue
        self.max_lag_bytes = int(lag_budget * BYTES_PER_SEC)
        self._listeners = set()
        self._proc = None
        self._lock = threading.Lock()

    @staticmethod
    def available():
        return shutil.which(FFMPEG) is not None and jackutil.available()

    def status(self):
        return {"listeners": len(self._listeners),
                "running": self._proc is not None and self._proc.poll() is None}

    def listen(self):
        """A generator of MP3 bytes, for one listener, for as long as they stay."""
        q = queue.Queue()                         # bounded by _offer, in seconds
        with self._lock:
            fresh = self._proc is None or self._proc.poll() is not None
            if fresh:
                # A new encoder run gets its own listener set. The old run's
                # pump keeps the old set and cannot reach these listeners.
                self._listeners = set()
            self._listeners.add(q)
            if fresh:
                self._start()
        try:
            while True:
                try:
                    chunk = q.get(timeout=10)
                except queue.Empty:
                    if not self.status()["running"]:
                        return
                    continue
                if chunk is None:
                    return                        # the encoder stopped
                yield chunk
        finally:
            # the browser went away (paused, closed the tab, lost wifi)
            with self._lock:
                self._listeners.discard(q)
                if not self._listeners:
                    self._stop()

    # ------------------------------------------------------------ internals

    def _start(self):
        try:
            self._proc = subprocess.Popen(encoder_args(), stdin=subprocess.DEVNULL,
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as exc:
            log.error("couldn't start the monitor encoder: %s", exc)
            self._proc = None
            return
        proc = self._proc
        threading.Thread(target=self._pump, args=(proc, self._listeners),
                         daemon=True).start()
        threading.Thread(target=self._wire, args=(proc,), daemon=True).start()
        log.info("monitor started")

    def _stop(self):
        proc, self._proc = self._proc, None
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
            log.info("monitor stopped: nobody listening")

    def _pump(self, proc, owned):
        """
        Encoder output, as whole MP3 frames, to the listeners of this encoder
        run (`owned`). A slow one loses its oldest audio, never blocks, and is
        kept within the adaptive lag budget.
        """
        tail = bytearray()

        def drain_stderr():
            # continuously, so a chatty encoder can never fill the pipe and stall
            if not proc.stderr:
                return
            while True:
                data = _read1(proc.stderr, CHUNK)
                if not data:
                    return
                tail.extend(data)
                del tail[:-STDERR_TAIL]

        drainer = threading.Thread(target=drain_stderr, daemon=True)
        drainer.start()
        pending = bytearray()
        while True:
            data = _read1(proc.stdout, CHUNK) if proc.stdout else b""
            if not data:
                break
            pending.extend(data)
            for item in _take_frames(pending):
                with self._lock:
                    listeners = list(owned)
                for q in listeners:
                    _offer(q, item, self.max_lag_bytes)
        drainer.join(timeout=1)
        err = bytes(tail).decode("utf-8", "replace").strip()
        if err:
            log.warning("monitor encoder: %s", err.splitlines()[-1])
        with self._lock:
            for q in owned:
                q.put_nowait(None)                # queues are unbounded: never Full

    def _wire(self, proc):
        """
        Keep the encoder's inputs fed from whatever should be heard right now.
        Checks every tenth of a second until the first connection is made --
        otherwise the first second of listening is silence -- then every second.
        """
        wired = False
        while proc.poll() is None:
            inputs = jackutil.ports(CLIENT, "input")
            if inputs:
                wanted = set()
                for outs in self.sources():
                    wanted.update(jackutil.pairs(outs, inputs))
                for dst in inputs:
                    for src in jackutil.connections(dst):
                        if (src, dst) not in wanted:
                            jackutil.disconnect(src, dst)
                for src, dst in wanted:
                    if src not in jackutil.connections(dst):
                        jackutil.connect(src, dst)
                wired = wired or bool(wanted)
            time.sleep(1 if wired else 0.1)
