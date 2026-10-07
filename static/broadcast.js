/*
 * The broadcast page's client. It listens and never speaks: there is no emit
 * in this file, and the server would refuse one anyway -- GX_BROADCAST=1
 * registers a blocked handler for every event but connect and disconnect.
 *
 * Four events arrive, and nothing else:
 *
 *   snapshot  {broadcast, connected, bank, preset, recording, takes}
 *   preset    {bank, preset}
 *   status    {connected}
 *   rec       {recording, count}
 */

(function () {
  'use strict';

  // The markup contract, in one place, so broadcast.html and this file agree.
  var E = {
    pilot:   document.getElementById('pilot'),
    status:  document.getElementById('status'),
    bank:    document.getElementById('bank'),
    preset:  document.getElementById('preset'),
    rolling: document.getElementById('rolling'),
    takes:   document.getElementById('takes'),
    stream:  document.getElementById('stream')
  };

  var socket = io();

  // ---------------------------------------------------------------- audio
  //
  // The PCM transport (issue #46, part 2). The server streams raw s16le over
  // /audio.pcm; this side frames it, buffers it against network jitter, and
  // plays it through an AudioWorklet. The worklet is the only place audio is
  // touched, so a slow main thread cannot cause a dropout.
  //
  // The worklet script is loaded from /static/pcm-worklet.js. If it is not
  // there -- or the page is not a secure context, where AudioWorklet does not
  // exist at all -- playback falls back to ScriptProcessorNode, which is
  // deprecated but present everywhere.

  var PCM = {
    url: '/audio.pcm',
    worklet: '/static/pcm-worklet.js',
    rate: 48000,
    channels: 2,
    frames: 256,
    target: 4,          // chunks of jitter buffer to hold
    block: 512,
    ctx: null,
    node: null,
    running: false,
    mode: 'idle',
    underruns: 0,
    buffered: 0
  };

  function pcmSupported() {
    return typeof window.AudioContext !== 'undefined' ||
           typeof window.webkitAudioContext !== 'undefined';
  }

  function pcmStart() {
    if (!pcmSupported() || PCM.running) return;
    var Ctx = window.AudioContext || window.webkitAudioContext;
    PCM.ctx = new Ctx({ sampleRate: PCM.rate, latencyHint: 'interactive' });
    PCM.running = true;
    PCM.underruns = 0;
    if (window.isSecureContext && PCM.ctx.audioWorklet) {
      PCM.ctx.audioWorklet.addModule(PCM.worklet).then(function () {
        PCM.node = new AudioWorkletNode(PCM.ctx, 'pcm-player', {
          numberOfInputs: 0,
          numberOfOutputs: 1,
          outputChannelCount: [PCM.channels],
          processorOptions: {
            channels: PCM.channels,
            frames: PCM.frames,
            target: PCM.target
          }
        });
        PCM.node.port.onmessage = function (e) {
          if (e.data && e.data.underruns !== undefined) PCM.underruns = e.data.underruns;
          if (e.data && e.data.buffered !== undefined) PCM.buffered = e.data.buffered;
        };
        PCM.node.connect(PCM.ctx.destination);
        PCM.mode = 'AudioWorklet';
        pcmFetch();
      }).catch(function () {
        pcmFallback();
      });
    } else {
      pcmFallback();
    }
    if (PCM.ctx.resume) PCM.ctx.resume();
  }

  function pcmFallback() {
    // No AudioWorklet here: ScriptProcessor on the main thread, the same
    // shape the spike used. Present everywhere, deprecated everywhere.
    var ring = new Float32Array(PCM.rate * PCM.channels * 2);
    var ringLen = ring.length, readPos = 0, writePos = 0, started = false;
    var targetFrames = PCM.target * PCM.frames;
    PCM.node = PCM.ctx.createScriptProcessor(PCM.block, 0, PCM.channels);
    PCM.node.onaudioprocess = function (e) {
      var out = [], c, i;
      for (c = 0; c < PCM.channels; c++) out.push(e.outputBuffer.getChannelData(c));
      var frames = e.outputBuffer.length;
      var buffered = (writePos - readPos) / PCM.channels;
      if (!started) {
        if (buffered < targetFrames + frames) {
          for (c = 0; c < PCM.channels; c++) out[c].fill(0);
          return;
        }
        started = true;
      }
      if (buffered < frames) {
        for (c = 0; c < PCM.channels; c++) out[c].fill(0);
        PCM.underruns++;
        started = false;
        return;
      }
      var floor = targetFrames + frames;
      if (buffered > floor + frames) readPos += (buffered - floor) * PCM.channels;
      for (i = 0; i < frames; i++) {
        for (c = 0; c < PCM.channels; c++) {
          out[c][i] = ring[readPos % ringLen];
          readPos++;
        }
      }
    };
    PCM.node.connect(PCM.ctx.destination);
    PCM.mode = 'ScriptProcessor';
    PCM._ring = ring;
    PCM._ringLen = ringLen;
    PCM._write = function (pcm) {
      for (var i = 0; i < pcm.length; i++) {
        ring[writePos % ringLen] = pcm[i] / 32768;
        writePos++;
      }
      if (writePos - readPos > ringLen) readPos = writePos - ringLen;
    };
    pcmFetch();
  }

  function pcmFetch() {
    // Frame the stream ourselves: read exactly PCM.frames * channels * 2
    // bytes at a time, so a chunk boundary is never mistaken for a dropout.
    var want = PCM.frames * PCM.channels * 2;
    var pending = new Uint8Array(0);
    fetch(PCM.url, { cache: 'no-store' }).then(function (res) {
      if (!res.ok || !res.body) return;
      var reader = res.body.getReader();
      (function pump() {
        reader.read().then(function (r) {
          if (r.done || !PCM.running) return;
          var merged = new Uint8Array(pending.length + r.value.length);
          merged.set(pending, 0);
          merged.set(r.value, pending.length);
          var off = 0;
          while (merged.length - off >= want) {
            var pcm = new Int16Array(merged.buffer, merged.byteOffset + off, want / 2);
            if (PCM.node && PCM.node.port) {
              PCM.node.port.postMessage(pcm);
            } else if (PCM._write) {
              PCM._write(pcm);
            }
            off += want;
          }
          pending = merged.slice(off);
          pump();
        });
      }());
    }).catch(function () { /* the stream ended or the tab is going away */ });
  }

  function pcmStop() {
    PCM.running = false;
    if (PCM.node) {
      if (PCM.node.disconnect) PCM.node.disconnect();
      if (PCM.node.onaudioprocess) PCM.node.onaudioprocess = null;
    }
    if (PCM.ctx && PCM.ctx.close) PCM.ctx.close();
    PCM.ctx = null;
    PCM.node = null;
    PCM.mode = 'idle';
  }

  window.gxPcm = { start: pcmStart, stop: pcmStop, state: PCM };

  // Until a snapshot lands there is nothing true to show, so the page says it
  // is connecting rather than showing an empty preset as though it were one.
  var haveSnapshot = false;

  // How far ahead of the playhead the stream has buffered, in seconds, told to
  // the server so it can see what listeners are actually experiencing. The
  // audio element is the only thing that knows, so it is measured here.
  var BUFFER_INTERVAL = 2000;

  function bufferedAhead() {
    if (!E.stream || typeof E.stream.buffered !== 'object' || !E.stream.buffered) return null;
    var ranges = E.stream.buffered;
    var now = E.stream.currentTime || 0;
    for (var i = 0; i < ranges.length; i++) {
      if (ranges.start(i) <= now && now <= ranges.end(i)) {
        return Math.max(0, ranges.end(i) - now);
      }
    }
    return 0;
  }

  function reportBuffer() {
    var ahead = bufferedAhead();
    if (ahead === null || !isFinite(ahead)) return;
    socket.emit('playback_buffer', {seconds: ahead});
  }

  setInterval(reportBuffer, BUFFER_INTERVAL);

  function setLink(up) {
    if (E.pilot) E.pilot.classList.toggle('is-on', !!up);
    if (!E.status) return;
    if (!haveSnapshot) {
      E.status.textContent = up ? 'Connecting' : 'Offline';
    } else {
      E.status.textContent = up ? 'Live' : 'Offline';
    }
  }

  function setPreset(bank, preset) {
    if (E.preset && preset) E.preset.textContent = preset;
    if (E.bank) E.bank.textContent = bank || '';
  }

  function setRolling(on) {
    if (E.rolling) E.rolling.hidden = !on;
  }

  function setTakes(count) {
    if (!E.takes) return;
    if (typeof count !== 'number') {
      E.takes.textContent = '';
      return;
    }
    E.takes.textContent = count === 1 ? '1 take' : count + ' takes';
  }

  socket.on('snapshot', function (d) {
    d = d || {};
    haveSnapshot = true;
    setLink(d.connected);
    setPreset(d.bank, d.preset);
    setRolling(d.recording);
    setTakes(d.takes);
  });

  socket.on('preset', function (d) {
    d = d || {};
    setPreset(d.bank, d.preset);
  });

  socket.on('status', function (d) {
    setLink(d && d.connected);
  });

  socket.on('rec', function (d) {
    d = d || {};
    setRolling(d.recording);
    setTakes(d.count);
  });

  // The socket to the web app, which is a different link from the web app to
  // guitarix. Losing this one means the page is stale; losing the other one is
  // what 'status' reports. Both end up as Offline, for different reasons.
  socket.on('disconnect', function () {
    setLink(false);
    setRolling(false);
  });

  socket.on('connect', function () {
    // Wait for the snapshot before claiming anything about the amp.
    setLink(true);
  });

  setLink(false);
}());
