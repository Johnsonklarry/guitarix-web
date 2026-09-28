const IO = io();

const E = {
  broadcast: document.getElementById('broadcast'),
  connected: document.getElementById('connected'),
  bank: document.getElementById('bank'),
  preset: document.getElementById('preset'),
  recording: document.getElementById('recording'),
  takes: document.getElementById('takes'),
  listen: document.getElementById('listen'),
  audio: document.getElementById('audio'),
};

let snapshotReceived = false;
let connected = false;

function updateListen() {
  if (E.listen && E.audio) {
    if (connected) {
      E.listen.disabled = false;
      E.listen.classList.remove('disabled');
    } else {
      E.listen.disabled = true;
      E.listen.classList.add('disabled');
    }
  }
}

function updateAudio() {
  if (E.audio && E.listen) {
    if (E.listen.checked) {
      E.audio.play().catch(function () {});
    } else {
      E.audio.pause();
    }
  }
}

E.listen.addEventListener('change', updateAudio);

IO.on('snapshot', function (data) {
  snapshotReceived = true;
  if (data.broadcast) E.broadcast.textContent = data.broadcast;
  if (data.connected !== undefined) {
    connected = data.connected;
    if (data.connected) {
      E.connected.textContent = 'Connected';
      E.connected.classList.remove('disconnected');
      E.connected.classList.add('connected');
    } else {
      E.connected.textContent = 'Disconnected';
      E.connected.classList.remove('connected');
      E.connected.classList.add('disconnected');
    }
  }
  if (data.bank) E.bank.textContent = data.bank;
  if (data.preset) E.preset.textContent = data.preset;
  if (data.recording) {
    E.recording.textContent = data.recording ? 'Recording' : 'Not recording';
    E.recording.classList.toggle('recording', data.recording);
  }
  if (data.takes && Array.isArray(data.takes)) {
    E.takes.innerHTML = '';
    data.takes.forEach(function (take) {
      const li = document.createElement('li');
      li.textContent = take.name;
      E.takes.appendChild(li);
    });
  }
  updateListen();
});

IO.on('preset', function (data) {
  if (data.bank) E.bank.textContent = data.bank;
  if (data.preset) E.preset.textContent = data.preset;
});

IO.on('status', function (data) {
  if (data.connected !== undefined) {
    connected = data.connected;
    if (data.connected) {
      E.connected.textContent = 'Connected';
      E.connected.classList.remove('disconnected');
      E.connected.classList.add('connected');
    } else {
      E.connected.textContent = 'Disconnected';
      E.connected.classList.remove('connected');
      E.connected.classList.add('disconnected');
    }
  }
  updateListen();
});

IO.on('rec', function (data) {
  if (data.recording !== undefined) {
    E.recording.textContent = data.recording ? 'Recording' : 'Not recording';
    E.recording.classList.toggle('recording', data.recording);
  }
  if (data.count !== undefined) {
    // count is not used in broadcast mode
  }
});

// Handle initial connection state
IO.on('connect', function () {
  connected = true;
  E.connected.textContent = 'Connected';
  E.connected.classList.remove('disconnected');
  E.connected.classList.add('connected');
  updateListen();
});

IO.on('disconnect', function () {
  connected = false;
  E.connected.textContent = 'Disconnected';
  E.connected.classList.remove('connected');
  E.connected.classList.add('disconnected');
  updateListen();
});