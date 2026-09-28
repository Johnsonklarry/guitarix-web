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

  // Until a snapshot lands there is nothing true to show, so the page says it
  // is connecting rather than showing an empty preset as though it were one.
  var haveSnapshot = false;

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
