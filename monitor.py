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
CHUNK = 4096
BACKLOG = 48            # chunks a slow listener may fall behind before losing the oldest


def encoder_args():
    args = [FFMPEG, "-hide_banner", "-loglevel", "error",
            "-f", "jack", "-i", CLIENT, "-ac", "2",
            "-c:a", "libmp3lame", "-b:a", BITRATE,
            "-f", "mp3", "-flush_packets", "1", "pipe:1"]
    if shutil.which("nice"):
        args = ["nice", "-n", "10"] + args        # the amp's realtime work comes first
    return args


class Monitor:
    def __init__(self, sources):
        self.sources = sources                    # () -> [[port, ...], ...]
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
        q = queue.Queue(maxsize=BACKLOG)
        with self._lock:
            self._listeners.add(q)
            if self._proc is None or self._proc.poll() is not None:
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
        threading.Thread(target=self._pump, args=(proc,), daemon=True).start()
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

    def _pump(self, proc):
        """Encoder output to every listener. A slow one loses its oldest audio, never blocks."""
        while True:
            chunk = proc.stdout.read(CHUNK) if proc.stdout else b""
            if not chunk:
                break
            with self._lock:
                listeners = list(self._listeners)
            for q in listeners:
                try:
                    q.put_nowait(chunk)
                except queue.Full:
                    try:
                        q.get_nowait()            # drop the oldest: stay near live
                        q.put_nowait(chunk)
                    except (queue.Empty, queue.Full):
                        pass
        err = proc.stderr.read().decode("utf-8", "replace").strip() if proc.stderr else ""
        if err:
            log.warning("monitor encoder: %s", err.splitlines()[-1])
        with self._lock:
            for q in self._listeners:
                try:
                    q.put_nowait(None)
                except queue.Full:
                    pass

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
