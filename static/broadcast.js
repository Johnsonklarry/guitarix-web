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

  // --- stale audio --------------------------------------------------------
  //
  // The amp streams audio as a sequence of buffers and the page plays them
  // out one at a time. Whenever the page falls behind -- a slow frame, a link
  // that stalls -- the buffers pile up and the audio drifts further behind
  // live. A queue of a few buffers is normal and is played out; past the
  // ceiling it is a backlog, and the buffer at the front of the queue is the
  // one furthest behind the position the speaker is at, so it is discarded
  // rather than played late.
  var MAX_BACKLOG = 3;     // buffers of lag this page is willing to play out
  var audioQueue = [];     // buffers received but not yet played, oldest first

  // How far behind live the page has fallen.
  function audioBacklog() {
    return audioQueue.length;
  }

  // 'ceiling' defaults to the lag the page tolerates while the link is up.
  // A disconnect passes 0, because nothing already queued is still current by
  // the time the link is back.
  function discardStaleAudio(ceiling) {
    var limit = (typeof ceiling === 'number') ? ceiling : MAX_BACKLOG;
    var dropped = 0;
    while (audioBacklog() > limit) {
      audioQueue.shift();
      dropped++;
    }
    return dropped;
  }

  // Called with each audio buffer the amp sends, in the order it was captured.
  function queueAudioBuffer(buffer) {
    if (!buffer || typeof buffer.seq !== 'number') return;
    audioQueue.push(buffer);
    discardStaleAudio();
  }

  // Until a snapshot lands there is nothing true to show, so the page says it
  // is connecting rather than showing an empty preset as though it were one.
  var haveSnapshot = false;

  // How far ahead of the playhead the stream has buffered, in seconds, told to
  // the server so it can see what listeners are actually experiencing. The
  // audio element is the only thing that knows, so it is measured here.
  var BUFFER_INTERVAL = 2000;
  var bufferTimer = null;   // the running report timer, or null when stopped

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
    // While the link is down an emit would only be queued by socket.io and
    // delivered, stale, once the socket is back, so nothing is said until it is
    // up again.
    if (!socket.connected) return;
    var ahead = bufferedAhead();
    if (ahead === null || !isFinite(ahead)) return;
    socket.emit('playback_buffer', {seconds: ahead});
  }

  function startBufferReports() {
    if (bufferTimer !== null) return;
    reportBuffer();
    bufferTimer = setInterval(reportBuffer, BUFFER_INTERVAL);
  }

  function stopBufferReports() {
    if (bufferTimer === null) return;
    clearInterval(bufferTimer);
    bufferTimer = null;
  }

  // The link may already be up by the time this script runs.
  if (socket.connected) startBufferReports();

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
    // The page is away from the live stream: what is still queued was
    // captured while it was gone and would be heard late on reconnect, so the
    // whole of it goes rather than being replayed. Nothing queued is still
    // current, so the ceiling for this discard is zero.
    discardStaleAudio(0);
    stopBufferReports();
    setLink(false);
    setRolling(false);
  });

  socket.on('connect', function () {
    // Wait for the snapshot before claiming anything about the amp.
    setLink(true);
    startBufferReports();
  });

  setLink(false);
}());
