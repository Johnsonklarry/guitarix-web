#!/usr/bin/env python3
"""
PHASE 0 SPIKE -- throwaway. Answers one question before we design around it:

    how far behind is low-latency audio to a browser, over plain http?

It streams raw PCM from JACK to the page over a websocket and measures where
the delay actually goes. Run it next to the real app; it touches nothing the
app owns.

    python3 spike_latency.py            # tap the amp through JACK
    python3 spike_latency.py --fake     # a generated tone, no JACK needed
    python3 spike_latency.py --source system:capture_1

then open http://<pi>:5055 from the machine you'd actually listen on.

--fake first. It tells you whether the browser half works before JACK is
involved at all, which halves the search space when something is wrong.

WHY PCM AND NOT SOMETHING CLEVERER
An <audio> element (what Listen uses today) buffers seconds by design and
won't give it back. WebRTC would be lower still, but it needs a media stack
on the Pi and a secure context to be dependable. Raw PCM into Web Audio is
the shortest path that works on plain http.

WHAT THE BROWSER FORCES ON US
Measured, not assumed: on plain http from a LAN address the page is NOT a
secure context, and AudioWorklet is simply absent there. It exists only on
localhost. So playback uses ScriptProcessorNode -- deprecated, runs on the
main thread, but present everywhere. The page reports which one it got.
"""

import argparse
import math
import os
import shutil
import struct
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from flask import Flask, Response
    from flask_socketio import SocketIO
except ImportError:
    sys.exit("needs flask and flask-socketio: pip install -r requirements.txt")

import jackutil

RATE = 48000
CHANNELS = 2
FRAMES = 256                      # per chunk: 5.3 ms at 48k
CHUNK = FRAMES * CHANNELS * 2     # bytes, s16le
CLIENT = "gxspike"

app = Flask(__name__)
app.config["SECRET_KEY"] = "spike"
socketio = SocketIO(app, async_mode="threading", cors_allowed_origins="*")

state = {"listeners": 0, "source": None, "fake": False, "proc": None, "wired": None}
# Socket.IO handlers run on separate threads (async_mode="threading"), so the
# read-modify-write on state["listeners"] must be serialized by ONE shared lock.
listeners_lock = threading.Lock()


def now_ms():
    return time.monotonic() * 1000.0


# ---------------------------------------------------------------- the source

def ffmpeg_pcm():
    """Raw PCM straight off JACK. No container, no encoder, nothing to buffer."""
    return [shutil.which("ffmpeg") or "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "jack", "-i", CLIENT,
            "-ac", str(CHANNELS), "-ar", str(RATE),
            "-f", "s16le", "-"]


def wire(source):
    """Connect the source's outputs into ffmpeg's inputs once they appear."""
    if not jackutil.available():
        state["wired"] = "jack tools missing"
        return
    ins = []
    deadline = time.time() + 5
    while time.time() < deadline:
        ins = jackutil.ports(CLIENT, "input")
        if ins:
            break
        time.sleep(0.1)
    if not ins:
        state["wired"] = "ffmpeg never appeared in JACK"
        return
    outs = ([source] if ":" in (source or "") else jackutil.ports(source or "", "output"))
    if not outs:
        state["wired"] = 'no output ports on "%s"' % source
        return
    done = [jackutil.connect(a, b) for a, b in jackutil.pairs(outs, ins)]
    state["wired"] = "connected %s" % ", ".join(outs[:2]) if any(done) else "could not connect"


def pump_jack(source):
    try:
        proc = subprocess.Popen(ffmpeg_pcm(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as e:
        state["proc"] = None
        state["wired"] = "ffmpeg could not start: %s" % e
        print(state["wired"])
        return
    state["proc"] = proc
    threading.Thread(target=wire, args=(source,), daemon=True).start()
    seq = 0
    while True:
        buf = proc.stdout.read(CHUNK)
        if not buf or len(buf) < CHUNK:
            break
        send(seq, buf)
        seq += 1
    err = (proc.stderr.read() or b"").decode("utf-8", "replace").strip()
    print("ffmpeg stopped." + (" " + err.splitlines()[-1] if err else ""))


def pump_fake():
    """A generated tone, paced in real time, so the browser half can be tested alone."""
    print("generating a tone -- JACK is not involved")
    seq, phase, start = 0, 0.0, time.monotonic()
    step = 2 * math.pi * 440.0 / RATE
    while True:
        frames = []
        for _ in range(FRAMES):
            v = int(9000 * math.sin(phase))
            phase = (phase + step) % (2 * math.pi)
            frames.append(v)
        # a tick at the top of each second: something to listen for
        if (seq * FRAMES) % RATE < FRAMES:
            frames = [min(30000, v * 3) for v in frames]
        buf = struct.pack("<%dh" % (FRAMES * CHANNELS),
                          *[v for v in frames for _ in range(CHANNELS)])
        send(seq, buf)
        seq += 1
        # pace to the clock rather than sleeping a fixed amount, so it can't drift
        target = start + (seq * FRAMES) / RATE
        gap = target - time.monotonic()
        if gap > 0:
            time.sleep(gap)


def send(seq, buf):
    if state["listeners"] <= 0:
        return
    def on_error():
        with listeners_lock:
            state["listeners"] = max(0, state["listeners"] - 1)
    socketio.emit("pcm", {"seq": seq, "t": now_ms(), "buf": buf}, timeout=1.0, error_callback=on_error)


# ---------------------------------------------------------------- the page

@socketio.on("connect")
def on_connect():
    with listeners_lock:
        state["listeners"] += 1
    socketio.emit("hello", {"rate": RATE, "channels": CHANNELS, "frames": FRAMES,
                            "source": state["source"], "fake": state["fake"],
                            "wired": state["wired"], "t": now_ms()})


@socketio.on("disconnect")
def on_disconnect():
    with listeners_lock:
        state["listeners"] = max(0, state["listeners"] - 1)


@socketio.on("ping2")
def on_ping(msg):
    """Round trip, so the page can line its clock up with ours."""
    client = msg.get("t") if isinstance(msg, dict) else None
    return {"client": client, "server": now_ms()}


PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>latency spike</title><style>
 body{margin:0;padding:24px;background:#12100e;color:#e7ddc9;
      font:15px/1.5 "Helvetica Neue",Helvetica,Arial,sans-serif}
 h1{font-size:1.1rem;letter-spacing:.14em;text-transform:uppercase;color:#9a8e83;margin:0 0 18px}
 button{font:inherit;padding:14px 26px;border-radius:999px;border:1px solid #5c4f42;
        background:#2e2822;color:#e7ddc9;cursor:pointer;font-weight:700;letter-spacing:.1em}
 button.on{background:#3a2c1c;border-color:#ff9b2f;color:#fff8ec}
 table{border-collapse:collapse;margin-top:22px;width:100%;max-width:560px}
 td{padding:7px 10px 7px 0;border-bottom:1px solid #241f1b;font-variant-numeric:tabular-nums}
 td:first-child{color:#9a8e83}
 .big{font-size:2.1rem;font-weight:700;color:#ff9b2f}
 .row{display:flex;gap:16px;align-items:center;flex-wrap:wrap}
 label{color:#9a8e83;font-size:.85rem}
 select{font:inherit;padding:8px 12px;border-radius:6px;background:#171310;
         color:#e7ddc9;border:1px solid #3b322a}
 .warn{color:#efc792} .bad{color:#e8867e} .note{color:#857a70;font-size:.85rem;max-width:560px}
</style></head><body>
<h1>Low-latency audio spike</h1>
<div class="row">
  <button id="go">Start</button>
  <label>jitter buffer <select id="target">
    <option value="2">2 chunks (11 ms)</option><option value="4" selected>4 chunks (21 ms)</option>
    <option value="8">8 chunks (43 ms)</option><option value="16">16 chunks (85 ms)</option>
  </select></label>
  <label>block <select id="block">
    <option value="256">256</option><option value="512" selected>512</option>
    <option value="1024">1024</option><option value="2048">2048</option>
  </select></label>
</div>
<table>
 <tr><td>total, ear to ear</td><td class="big" id="total">--</td></tr>
 <tr><td>network, one way</td><td id="net">--</td></tr>
 <tr><td>jitter buffer, actual</td><td id="buf">--</td></tr>
 <tr><td>browser output</td><td id="out">--</td></tr>
 <tr><td>dropouts</td><td id="under">0</td></tr>
 <tr><td>arriving</td><td id="rate">--</td></tr>
 <tr><td>clock drift</td><td id="drift">--</td></tr>
 <tr><td>playing via</td><td id="mode">--</td></tr>
 <tr><td>source</td><td id="src">--</td></tr>
</table>
<p class="note" id="note"></p>
<p class="note">Total is network + jitter buffer + the browser's own output latency.
It leaves out what JACK and ffmpeg hold on the Pi before any of this &mdash; so the
real figure is this plus your JACK buffer, typically a few ms.</p>
<script src="/socket.io.js"></script>
<script>
const $ = function (id) { return document.getElementById(id); };
let ctx, node, ring, ringLen = 0, readPos = 0, writePos = 0, running = false;
let offset = null, lastNet = 0, underruns = 0, chunks = 0, lastCount = 0, info = {};
let started = false, targetFrames = 0, played = 0, sinceReset = 0;
const socket = io();

socket.on('hello', function (h) {
  info = h;
  $('src').textContent = h.fake ? 'generated tone (no JACK)'
    : (h.source || 'jack') + (h.wired ? ' -- ' + h.wired : '');
  if (!h.fake && h.wired && !/^connected/.test(h.wired)) $('src').className = 'bad';
});

/* Line our clock up with the server's: round trip, halve it, average a few. */
function sync(n, done) {
  const samples = [];
  (function next(i) {
    if (i >= n) {
      samples.sort(function (a, b) { return a - b; });
      offset = samples[Math.floor(samples.length / 2)];   // median beats mean for this
      return done();
    }
    const sent = performance.now();
    socket.emit('ping2', { t: sent }, function (r) {
      const rtt = performance.now() - sent;
      samples.push(r.server - (sent + rtt / 2));
      next(i + 1);
    });
  })(0);
}

socket.on('pcm', function (m) {
  if (!running) return;
  chunks++;
  if (offset !== null) {
    const oneWay = performance.now() - (m.t - offset);
    lastNet = lastNet ? lastNet * 0.9 + oneWay * 0.1 : oneWay;   // smooth the jitter
  }
  const pcm = new Int16Array(m.buf.buffer || m.buf);
  for (let i = 0; i < pcm.length; i++) {
    ring[writePos % ringLen] = pcm[i] / 32768;
    writePos++;
  }
  if (writePos - readPos > ringLen) readPos = writePos - ringLen;   // too far behind: skip
});

function buffered() { return (writePos - readPos) / info.channels; }   // frames

function start() {
  const block = parseInt($('block').value, 10);
  const target = parseInt($('target').value, 10);
  ctx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: info.rate,
        latencyHint: 'interactive' });
  ringLen = info.rate * info.channels * 2;          // two seconds is plenty
  ring = new Float32Array(ringLen);
  readPos = writePos = 0; underruns = 0; chunks = 0; started = false;
  played = 0; sinceReset = performance.now();
  targetFrames = target * info.frames;

  node = ctx.createScriptProcessor(block, 0, info.channels);
  node.onaudioprocess = function (e) {
    const out = [];
    for (let c = 0; c < info.channels; c++) out.push(e.outputBuffer.getChannelData(c));
    const frames = e.outputBuffer.length;
    const silence = function () {
      for (let c = 0; c < info.channels; c++) out[c].fill(0);
    };

    // hold until the jitter buffer has filled once, or the first block plays
    // a stutter and the buffer never settles where it was asked to
    if (!started) {
      if (buffered() < targetFrames + frames) { silence(); return; }
      started = true;
    }
    if (buffered() < frames) {          // ran dry: the network fell behind
      silence();
      underruns++;
      started = false;                  // refill before trying again
      return;
    }
    // Pull back toward the target. Without this the buffer keeps whatever
    // backlog it happened to start with, and lowering the target does nothing.
    // The gap between ceiling and floor is hysteresis: with matched clocks it
    // corrects once and then sits still, rather than clicking continuously.
    const floor = targetFrames + frames;
    if (buffered() > floor + frames) readPos += (buffered() - floor) * info.channels;

    for (let i = 0; i < frames; i++) {
      for (let c = 0; c < info.channels; c++) {
        out[c][i] = ring[readPos % ringLen];
        readPos++;
      }
    }
    played += frames;
  };
  node.connect(ctx.destination);

  $('mode').textContent = (window.isSecureContext ? '' : 'insecure context, so ')
    + 'ScriptProcessor, ' + block + ' frames'
    + (window.isSecureContext && ctx.audioWorklet ? ' (AudioWorklet is available here)' : '');
  if (!window.isSecureContext) {
    $('note').innerHTML = 'This page is not a secure context, so <b>AudioWorklet does not exist</b> '
      + 'and playback falls back to ScriptProcessor on the main thread. That is the expected path '
      + 'for plain http over a LAN, and is what the real feature would use.';
    $('note').className = 'note warn';
  }
  running = true;
  ctx.resume();
}

function stop() {
  running = false;
  if (node) { node.disconnect(); node.onaudioprocess = null; }
  if (ctx) ctx.close();
}

$('go').addEventListener('click', function () {
  if (running) { stop(); $('go').textContent = 'Start'; $('go').className = ''; return; }
  $('go').textContent = 'Stop'; $('go').className = 'on';
  sync(7, start);
});
$('target').addEventListener('change', function () {
  // live: tearing the context down to change a number would itself cause dropouts
  targetFrames = parseInt($('target').value, 10) * (info.frames || 256);
  // going deeper needs audio that hasn't arrived yet, so refill first: a
  // short gap now, in exchange for the headroom you asked for
  if (targetFrames > buffered()) started = false;
  underruns = 0;
});
$('block').addEventListener('change', function () {
  // a ScriptProcessor's block size is fixed when it's made, so this one restarts
  if (running) { stop(); sync(5, start); }
});

setInterval(function () {
  if (!running || !ctx) return;
  const bufMs = buffered() / info.rate * 1000;
  const outMs = (ctx.outputLatency || ctx.baseLatency || 0) * 1000;
  $('net').textContent = lastNet.toFixed(1) + ' ms';
  $('buf').textContent = bufMs.toFixed(1) + ' ms';
  $('out').textContent = outMs.toFixed(1) + ' ms';
  $('total').textContent = (lastNet + bufMs + outMs).toFixed(0) + ' ms';
  $('under').textContent = underruns + (underruns ? '  (raise the jitter buffer or the block)' : '');
  $('under').className = underruns > 3 ? 'bad' : '';
  const secs = (performance.now() - sinceReset) / 1000;
  const ratio = secs > 2 ? played / (secs * info.rate) : 0;
  // the Pi's audio clock and this device's will never match exactly; over
  // minutes that shows up as a slow drift in one direction
  $('drift').textContent = ratio ? ((ratio - 1) * 1e6).toFixed(0) + ' ppm ('
      + (ratio > 1 ? 'playing faster than it arrives' : 'playing slower than it arrives') + ')' : '--';
  $('rate').textContent = (chunks - lastCount) + ' chunks/s, '
    + ((chunks - lastCount) * info.frames / info.rate * 100).toFixed(0) + '% of real time';
  lastCount = chunks;
}, 1000);
</script></body></html>
"""


@app.route("/")
def index():
    return PAGE


@app.route("/socket.io.js")
def socketio_js():
    """
    Served from here so the spike works with no internet. Falls back to the
    CDN only if the package isn't installed.
    """
    try:
        import socketio as _sio
        path = os.path.join(os.path.dirname(_sio.__file__), "static", "socket.io.min.js")
        if os.path.exists(path):
            with open(path) as f:
                return Response(f.read(), mimetype="application/javascript")
    except Exception:
        pass
    return Response('document.write(\'<script src="https://cdn.socket.io/4.7.5/socket.io.min.js">'
                    '<\\/script>\');', mimetype="application/javascript")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[3].strip())
    ap.add_argument("--fake", action="store_true", help="generate a tone instead of using JACK")
    ap.add_argument("--source", default="gx_head_amp", help="JACK client or port to tap")
    ap.add_argument("--port", type=int, default=5055)
    args = ap.parse_args()

    state["fake"] = args.fake
    state["source"] = None if args.fake else args.source
    target = pump_fake if args.fake else (lambda: pump_jack(args.source))
    threading.Thread(target=target, daemon=True).start()

    print("spike on http://0.0.0.0:%d  (%s)"
          % (args.port, "generated tone" if args.fake else "tapping " + args.source))
    socketio.run(app, host="0.0.0.0", port=args.port, allow_unsafe_werkzeug=True)


if __name__ == "__main__":
    main()
