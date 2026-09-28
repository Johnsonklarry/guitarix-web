/*
 * Runs static/app.js against a stub DOM and pushes fake server events through
 * it, so a runtime error at load or during a render fails here instead of in
 * the browser.
 *
 *     node check_js.js
 *
 * `node --check` only parses. It cannot see a variable read before its `let`
 * runs, which is exactly the kind of mistake that leaves the page frozen on
 * its template defaults with nothing in the console but one line.
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

function el(name) {
  // Just enough DOM: a real child list, so remove(), first/lastElementChild,
  // innerHTML = '' and moving a node between parents all behave.
  const self = {
    _name: name, _on: {}, children: [], dataset: {}, style: {}, parentNode: null,
    hidden: false, disabled: false, inert: false, open: false, textContent: '', value: '',
    colSpan: 0, src: '', href: '', title: '', type: '', controls: false, checked: false,
    preload: '', min: 0, max: 1, step: 0.1, offsetWidth: 0, currentTime: 0, paused: true,
    classList: (function () {
      const set = new Set();
      return {
        add() { for (const c of arguments) set.add(c); },
        remove() { for (const c of arguments) set.delete(c); },
        toggle(c, on) { const want = on === undefined ? !set.has(c) : !!on; want ? set.add(c) : set.delete(c); return want; },
        contains(c) { return set.has(c); },
      };
    })(),
    addEventListener(ev, fn) { self._on[ev] = fn; },
    appendChild(c) {
      if (c && c.parentNode) c.remove();
      if (c) c.parentNode = self;
      self.children.push(c);
      return c;
    },
    remove() {
      if (!self.parentNode) return;
      const kids = self.parentNode.children;
      const i = kids.indexOf(self);
      if (i >= 0) kids.splice(i, 1);
      self.parentNode = null;
    },
    after() {}, focus() {}, select() {}, click() {},
    showModal() { self.open = true; },
    close() { self.open = false; if (self._on.close) self._on.close({}); },
    setAttribute() {}, removeAttribute() {}, getAttribute() { return null; },
    closest() { return el('closest'); },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    play() { return Promise.resolve(); },
    get isConnected() { return self.parentNode !== null || !!document_cache[String(self._name).slice(1)]; },
    get firstChild() { return self.children[0] || null; },
    get firstElementChild() { return self.children[0] || el('first'); },
    get lastElementChild() { return self.children[self.children.length - 1] || el('last'); },
    get nextElementSibling() { return null; },
    get innerHTML() { return self._html || ''; },
    set innerHTML(v) {
      self._html = v;
      self.children.slice().forEach(function (k) { if (k && k.remove) k.remove(); });
    },
  };
  return self;
}

function tagged(sel, count, key) {
  return Array.from({ length: count }, function (_, i) {
    const e = el(sel + i);
    if (key) e.dataset[key] = ['presets', 'eq', 'fx', 'rec'][i];
    return e;
  });
}

const handlers = {};
const emitted = [];
const acks = {};              // event -> function(payload) returning the server's answer

const audios = [];
function FakeAudio() {
  const a = el('<audio>');
  a.paused = true; a.currentTime = 0; a.buffered = { length: 0 };
  a.play = function () { a.paused = false; return Promise.resolve(); };
  a.pause = function () { a.paused = true; };
  a.load = function () {};
  a.removeAttribute = function (n) { if (n === 'src') a.src = ''; };
  audios.push(a);
  return a;
}

const store = {};
const sandbox = {
  navigator: {},
  localStorage: {
    getItem(k) { return k in store ? store[k] : null; },
    setItem(k, v) { store[k] = String(v); },
    removeItem(k) { delete store[k]; },
  },
  Audio: FakeAudio,
  console,
  setTimeout, clearTimeout, setInterval, clearInterval,
  Promise, Date, Math, JSON, Number, String, Array, Object, isFinite,
  requestAnimationFrame(fn) { return setTimeout(fn, 0); },
  cancelAnimationFrame(id) { clearTimeout(id); },
  Blob: function (parts) { this.parts = parts; },
  URL: { createObjectURL() { return 'blob:test'; }, revokeObjectURL() {} },
  io() {
    return {
      on(ev, fn) { handlers[ev] = fn; },
      emit(ev, msg, ack) {
        emitted.push([ev, msg]);
        if (typeof ack === 'function' && acks[ev]) ack(acks[ev](msg));
      },
    };
  },
  window: {
    matchMedia(q) {
      return { matches: /min-width/.test(q), media: q, addEventListener() {} };
    },
  },
  document: {
    getElementById(id) { return (document_cache[id] ||= el('#' + id)); },
    createElement(tag) { return el('<' + tag + '>'); },
    // no dialog is open unless a test says so -- a browser returns null here
    querySelector(sel) { return sel === 'dialog[open]' ? null : el(sel); },
    querySelectorAll(sel) {
      if (sel === '.tab') {
        return tagged('.tab', 4).map(function (e, i) {
          e.dataset.tab = ['presets', 'eq', 'fx', 'rec'][i];
          return e;
        });
      }
      if (sel === '.pane') return tagged('.pane', 4, 'pane');
      if (sel === '.preset') {
        const p = document_cache['presets'];
        return p ? p.children.filter(function (t) { return t.dataset && t.dataset.preset; }) : [];
      }
      if (sel === '.bank') {
        const b = document_cache['banks'];
        return b ? b.children.slice() : [];
      }
      return [];
    },
    body: el('body'),
    addEventListener(ev, fn) { (docListeners[ev] ||= []).push(fn); },
  },
};
const document_cache = {};
const docListeners = {};
sandbox.window.document = sandbox.document;
sandbox.globalThis = sandbox;

const code = fs.readFileSync(path.join(__dirname, 'static', 'app.js'), 'utf8');

let failed = false;
function step(what, fn) {
  try {
    fn();
    console.log('  ok    ' + what);
  } catch (err) {
    failed = true;
    console.log('  FAIL  ' + what);
    console.log('        ' + (err && err.stack ? err.stack.split('\n')[0] : err));
    if (err && err.stack) {
      console.log('        ' + (err.stack.split('\n')[1] || '').trim());
    }
  }
}

console.log('app.js smoke test\n');

step('loads without throwing', function () {
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox, { filename: 'app.js' });
});

if (!handlers.snapshot) {
  console.log('\nscript did not finish loading, so no handlers were registered');
  process.exit(1);
}

const control = function (id, name, min, max, value) {
  return { id: id, name: name, min: min, max: max, step: 0.1, value: value };
};

const SNAP = {
  connected: true,
  engine: '127.0.0.1:7000',
  bank: 'Larry', preset: 'Clean',
  banks: [{ name: 'Larry', presets: ['Clean', 'Crunch'] },
          { name: 'Empty', presets: [] }],
  can: { save_current: true, save_as: true, rename: true, delete: true,
         new_bank: true, delete_bank: true, move: true },
  rec: { recording: false, elapsed: 0, available: true, wiring: null },
  backing_items: [],
  recordings: [{ name: 'take.wav', size: 1024, duration: 12.5,
                 modified: Date.now() / 1000, active: false }],
  eq: [{ title: 'EQ', blurb: '', layout: 'rack', toggle: 'eqs.on_off',
         controls: [control('eqs.f1', '31 Hz', -12, 12, 0)] }],
  fx: [{ title: 'Reverb', blurb: '', layout: 'rows', toggle: 'freeverb.on_off',
         controls: [control('freeverb.wet', 'Mix', 0, 1, 0.3)] },
       { title: 'Switch only', blurb: '', layout: 'rows', toggle: 'low_highpass.on_off',
         controls: [] },
       { title: 'Cut', blurb: '', layout: 'rows', toggle: null, collapsed: true,
         controls: [
           control('low_highpass.freq', 'Freq', 20, 20000, 400),
           { id: 'low_highpass.s_h', name: 's_h', type: 'BoolParameter',
             min: 0, max: 1, step: 1, value: 1, desc: 'engine tooltip' },
           { id: 'amp.model', name: 'Model', type: 'Enum', min: 0, max: 2, step: 1,
             value: 1, options: [{ value: 0, key: 'clean', label: 'Clean' },
                                 { value: 1, key: 'crunch', label: 'Crunch' },
                                 { value: 2, key: 'lead', label: 'Lead' }] },
         ] }],
  values: { 'eqs.on_off': 1, 'freeverb.on_off': 0 },
};

function readoutShowsPreset() {
  return /Now playing/.test(document_cache['readout'].innerHTML || '');
}

// On a real page load the server sends its snapshot as soon as the socket
// opens, which can land before the browser's own connect event fires.
step('snapshot lands before connect: readout still ends up on the preset', function () {
  handlers.snapshot(SNAP);
  handlers.connect();
  if (!readoutShowsPreset()) {
    throw new Error('readout stuck on: ' + JSON.stringify(
      document_cache['readout'].innerHTML || document_cache['readout'].textContent));
  }
});

step('browser link up', function () { handlers.connect(); });
step('snapshot, engine up', function () { handlers.snapshot(SNAP); });

function banner() {
  return { shown: !document_cache['offline'].hidden,
           text: document_cache['offline-text'].textContent };
}

step('snapshot, engine down', function () {
  const down = Object.assign({}, SNAP, {
    connected: false, banks: [], eq: [], fx: [], bank: null, preset: null,
  });
  handlers.status({ connected: false });
  handlers.snapshot(down);
});

step('snapshot, engine up with no banks at all', function () {
  handlers.status({ connected: true });
  handlers.snapshot(Object.assign({}, SNAP, { banks: [], bank: null, preset: null }));
});

step('back to a populated snapshot', function () { handlers.snapshot(SNAP); });
step('parameter push', function () { handlers.params({ 'eqs.f1': 3.5 }); });
step('preset push', function () { handlers.preset({ bank: 'Larry', preset: 'Crunch' }); });
step('bank list push', function () {
  handlers.banks({ banks: SNAP.banks, bank: 'Larry', preset: 'Crunch' });
});
step('recording started', function () {
  handlers.rec({ recording: true, elapsed: 3, available: true, wiring: null });
});
step('recordings list', function () {
  handlers.recordings({ rec: SNAP.rec, items: SNAP.recordings });
});
step('empty recordings list', function () {
  handlers.recordings({ rec: SNAP.rec, items: [] });
});
step('toast', function () { handlers.toast({ text: 'hello', kind: 'ok' }); });

step('switch and choice values pushed from the engine', function () {
  handlers.params({ 'low_highpass.s_h': 0, 'amp.model': 2 });
});

step('choice reported by name instead of index', function () {
  handlers.params({ 'amp.model': 'crunch' });
});
step('choice reported as something unrecognised', function () {
  handlers.params({ 'amp.model': 'mystery' });
});

const LOCKED = ['presets-toolbar', 'presets', 'groups-eq', 'groups-fx'];
function lockState() {
  return LOCKED.map(function (id) { return !!document_cache[id].inert; });
}

step('engine drops: banner says so, readout says No connection', function () {
  handlers.status({ connected: false });
  const b = banner();
  if (!b.shown || !/reach guitarix/.test(b.text)) throw new Error('got: ' + JSON.stringify(b));
  const r = document_cache['readout'].innerHTML;
  if (r !== 'No connection') throw new Error('readout: ' + r);
});
step('engine down: presets, EQ and effects locked', function () {
  const s = lockState();
  if (s.indexOf(false) >= 0) throw new Error('not locked: ' + LOCKED.filter((_, i) => !s[i]));
});
step('engine down: recorder stays usable', function () {
  if (document_cache['btn-rec'].inert || document_cache['takes'].inert) {
    throw new Error('the recorder got locked too');
  }
});
step('engine down: a re-render does not unlock anything', function () {
  handlers.snapshot(Object.assign({}, SNAP, { connected: false }));
  if (lockState().indexOf(false) >= 0) throw new Error('re-render unlocked a container');
});
step('engine back on its own: readout returns to the preset', function () {
  handlers.status({ connected: true });
  if (!readoutShowsPreset()) throw new Error('readout did not recover');
});
step('engine back: banner clears and controls unlock', function () {
  handlers.status({ connected: true });
  handlers.snapshot(SNAP);
  if (banner().shown) throw new Error('banner still showing');
  if (lockState().indexOf(true) >= 0) throw new Error('still locked');
});
step('browser link drops: banner blames the link, not guitarix', function () {
  handlers.disconnect();
  const b = banner();
  if (!b.shown || !/web app/.test(b.text) || /reach guitarix/.test(b.text)) {
    throw new Error('got: ' + JSON.stringify(b));
  }
});
step('browser link down: controls locked too', function () {
  if (lockState().indexOf(false) >= 0) throw new Error('not locked while the link was down');
});
step('browser link back', function () {
  handlers.connect();
  if (banner().shown) throw new Error('banner still showing');
  if (!readoutShowsPreset()) throw new Error('readout did not recover');
  if (lockState().indexOf(true) >= 0) throw new Error('still locked');
});

function click(id) {
  const e = document_cache[id];
  if (!e || !e._on.click) throw new Error('no click handler on #' + id);
  return e._on.click({ stopPropagation() {}, preventDefault() {} });
}

function tiles() {
  return document_cache['presets'].children.filter(function (t) {
    return t.dataset && t.dataset.preset;
  });
}

step('organise on', function () { click('btn-organize'); });
step('pick a preset in organise mode, without loading it', function () {
  const t = tiles();
  if (!t.length) throw new Error('no preset tiles were rendered');
  const before = emitted.length;
  t[t.length - 1]._on.click({ stopPropagation() {} });
  const sent = emitted.slice(before).map(function (e) { return e[0]; });
  if (sent.indexOf('set_preset') >= 0) {
    throw new Error('organise mode loaded the preset instead of picking it');
  }
});
step('open rename or move on the pick', function () { click('org-rename'); });
step('open delete on the pick', function () { click('org-delete'); });
step('open new bank', function () { click('org-new-bank'); });
step('open delete bank', function () { click('org-del-bank'); });
step('bank list changes under the pick', function () {
  handlers.banks({ banks: [{ name: 'Larry', presets: [] }], bank: 'Larry', preset: null });
});
step('organise off', function () { click('btn-organize'); });
step('tap a preset normally loads it', function () {
  handlers.banks({ banks: SNAP.banks, bank: 'Larry', preset: 'Clean' });
  const before = emitted.length;
  const t = tiles();
  t[t.length - 1]._on.click({});
  const sent = emitted.slice(before).map(function (e) { return e[0]; });
  if (sent.indexOf('set_preset') < 0) throw new Error('expected set_preset, got ' + sent);
});

// ---------------------------------------------------------------- record tab
function walk(node, out) {
  out = out || [];
  (node.children || []).forEach(function (ch) { out.push(ch); walk(ch, out); });
  return out;
}
function buttonsIn(id, label) {
  return walk(document_cache[id]).filter(function (n) {
    return n._on && n._on.click && (n.textContent || '').trim() === label;
  });
}
function lastEmit(name) {
  for (let i = emitted.length - 1; i >= 0; i--) if (emitted[i][0] === name) return emitted[i][1];
  return null;
}
const TAKES = [
  { name: 'Attempt 2.wav', size: 900000, duration: 5, modified: Date.now() / 1000,
    active: false, dry: 'Attempt 2 (dry).wav', settings: 'Attempt 2.json' },
  { name: 'Attempt 1.wav', size: 800000, duration: 4, modified: Date.now() / 1000 - 60,
    active: false, dry: null, settings: null },
];
const REC = { recording: false, elapsed: 0, available: true, next_attempt: 3,
              free_bytes: 5e9, reamp: null, backing: { playing: false, include: false, volume: 80 } };

step('record tab: takes, header and backing render', function () {
  handlers.recordings({ rec: REC, items: TAKES,
                        backing_items: [{ name: 'Jam.wav', size: 1, duration: 60 }] });
  const head = document_cache['rec-attempt'].textContent;
  if (head !== 'Attempt 3') throw new Error('header says ' + head);
  if (!/2 takes/.test(document_cache['rec-stats'].textContent)) {
    throw new Error('stats: ' + document_cache['rec-stats'].textContent);
  }
});
step('a take with a dry twin offers Reamp; one without does not', function () {
  const reamps = buttonsIn('takes', 'Reamp');
  if (reamps.length !== 1) throw new Error('expected 1 Reamp button, got ' + reamps.length);
  if (buttonsIn('takes', 'Loop').length !== 2) throw new Error('every take should offer Loop');
});
step('Reamp sends the take and its dry file', function () {
  buttonsIn('takes', 'Reamp')[0]._on.click({});
  const m = lastEmit('reamp_start');
  if (!m || m.take !== 'Attempt 2.wav' || m.dry !== 'Attempt 2 (dry).wav') {
    throw new Error('sent ' + JSON.stringify(m));
  }
});
step('a live reamp shows the bar, and the amp switch flips it', function () {
  handlers.rec(Object.assign({}, REC, { reamp: { take: 'Attempt 2.wav', mode: 'wet', record: false,
    output: null, position: 1, duration: 5, paused: false, loop: true } }));
  if (document_cache['reampbar'].hidden) throw new Error('reamp bar hidden');
  document_cache['reamp-amp']._on.click({});
  const m = lastEmit('reamp_mode');
  if (!m || m.mode !== 'dry') throw new Error('amp switch sent ' + JSON.stringify(m));
});
step('a recorded pass locks the amp switch and the record button', function () {
  handlers.rec(Object.assign({}, REC, { recording: true, reamp: { take: 'Attempt 2.wav',
    mode: 'wet', record: true, output: 'Attempt 2 (render).wav', position: 1, duration: 5 } }));
  if (!document_cache['reamp-amp'].disabled) throw new Error('amp switch not locked');
  if (!document_cache['btn-rec'].disabled) throw new Error('record button not locked');
});
step('backing playing shows its strip; Stop stops it', function () {
  handlers.rec(Object.assign({}, REC, { backing: { playing: true, kind: 'backing', name: 'Jam.wav',
    position: 3, duration: 60, paused: false, volume: 70, loop: true, include: false } }));
  if (document_cache['backing-now'].hidden) throw new Error('backing strip hidden');
  document_cache['backing-stop']._on.click({});
  if (!lastEmit('backing_stop')) throw new Error('no backing_stop sent');
});
step('everything quiet again: bars hide', function () {
  handlers.rec(REC);
  if (!document_cache['reampbar'].hidden || !document_cache['backing-now'].hidden) {
    throw new Error('a bar stayed up');
  }
});
step('audition bar renders (it once threw on first use)', function () {
  handlers.audition({ name: 'Sultans Clean', bank: 'Claude', base: 'Warm/Clean Warm', replaces: false });
  const t = document_cache['audition-text'].innerHTML;
  if (!/Sultans Clean/.test(t) || !/Warm\/Clean Warm/.test(t)) throw new Error('bar says: ' + t);
  handlers.audition(null);
});

step('Listen starts a stream from the Pi, and stops it again', function () {
  document_cache['btn-listen']._on.click({});
  const a = audios[audios.length - 1];
  if (!a || !/^\/monitor\.mp3\?t=/.test(a.src)) throw new Error('no stream started: ' + (a && a.src));
  if (document_cache['listen-label'].textContent !== 'Connecting') throw new Error('should say Connecting first');
  a._on.playing({});
  if (document_cache['listen-label'].textContent !== 'Listening') throw new Error('should say Listening once sound arrives');
  document_cache['btn-listen']._on.click({});
  if (a.src !== '' || document_cache['listen-label'].textContent !== 'Listen') {
    throw new Error('stopping should drop the stream so the Pi stops encoding');
  }
});
step('Export offers the settings sources and sends the choice', function () {
  handlers.recordings({ rec: REC, items: TAKES, backing_items: [] });
  const more = buttonsIn('takes', 'More')[0];
  more._on.click({});
  // the More box isn't attached in the fake DOM (row.after is a no-op), so
  // reach the export dialog the way the button does
  const opts = function () { return walk(document_cache['export-source']).filter(function (o) { return o.value; }); };
  document_cache['export-dlg'].showModal = function () { this.open = true; };
  sandbox.openExport(TAKES[0]);
  const values = opts().map(function (o) { return o.value; });
  if (values[0] !== 'live' || values.indexOf('recorded') < 0 || !values.some(function (v) { return /^preset:/.test(v); })) {
    throw new Error('sources offered: ' + values);
  }
  document_cache['export-source'].value = 'recorded';
  document_cache['export-format'].value = 'mp3';
  document_cache['export-form']._on.submit({ preventDefault() {} });
  const m = lastEmit('export_start');
  if (!m || m.take !== 'Attempt 2.wav' || m.source !== 'recorded' || m.format !== 'mp3') {
    throw new Error('sent ' + JSON.stringify(m));
  }
});
step('during an export the bar says so, and Stop becomes Cancel', function () {
  handlers.rec(Object.assign({}, REC, { recording: true,
    export: { take: 'Attempt 2.wav', label: 'the settings it was recorded with', format: 'mp3' },
    reamp: { take: 'Attempt 2.wav', mode: 'wet', record: true, output: 'x.wav', position: 1, duration: 5 } }));
  if (!/Exporting/.test(document_cache['reamp-text'].innerHTML)) throw new Error('bar: ' + document_cache['reamp-text'].innerHTML);
  if (document_cache['reamp-stop'].textContent !== 'Cancel') throw new Error('stop button says ' + document_cache['reamp-stop'].textContent);
  document_cache['reamp-stop']._on.click({});
  if (!lastEmit('export_cancel')) throw new Error('Cancel sent nothing');
  handlers.rec(REC);
});

// ---------------------------------------------------------------- live mode
function key(k, extra) {
  (docListeners.keydown || []).forEach(function (fn) {
    fn(Object.assign({ key: k, repeat: false, target: {}, preventDefault() {} }, extra || {}));
  });
}
step('Live: entering shows the loaded preset, and is remembered', function () {
  handlers.status({ connected: true });
  handlers.banks({ banks: [{ name: 'Larry', presets: ['Clean', 'Crunch', 'Lead_Stack'] }],
                   bank: 'Larry', preset: 'Clean' });
  document_cache['btn-live']._on.click({});
  if (document_cache['live'].hidden) throw new Error('live view still hidden');
  if (document_cache['live-name'].textContent !== 'Clean') throw new Error('shows ' + document_cache['live-name'].textContent);
  if (store['gx-live'] !== '1') throw new Error('not remembered');
});
step('Live: next and previous step through the bank, wrapping at the ends', function () {
  document_cache['live-next']._on.click({});
  if (lastEmit('set_preset').preset !== 'Crunch') throw new Error('next went to ' + lastEmit('set_preset').preset);
  document_cache['live-prev']._on.click({});
  document_cache['live-prev']._on.click({});
  const m = lastEmit('set_preset');
  if (m.preset !== 'Lead_Stack') throw new Error('back past the start went to ' + m.preset);
  if (document_cache['live-name'].textContent !== 'Lead Stack') throw new Error('name shows ' + document_cache['live-name'].textContent);
});
step('Live: pedal keys change presets; a held key does not repeat', function () {
  const before = emitted.length;
  key('ArrowRight');
  key('ArrowRight', { repeat: true });
  const sent = emitted.slice(before).filter(function (e) { return e[0] === 'set_preset'; });
  if (sent.length !== 1 || sent[0][1].preset !== 'Clean') throw new Error('sent ' + JSON.stringify(sent));
});
step('Live: keys typed into a field are left alone', function () {
  const before = emitted.length;
  key('ArrowRight', { target: { tagName: 'INPUT' } });
  if (emitted.length !== before) throw new Error('a key in a text field changed the preset');
});
step('Live: Space records, with the dry setting', function () {
  document_cache['dry-check'].checked = true;
  key(' ');
  const m = lastEmit('record_start');
  if (!m || m.dry !== true) throw new Error('record_start sent ' + JSON.stringify(m));
});
step('Live: the loop pad loops the newest take', function () {
  handlers.recordings({ rec: REC, items: TAKES, backing_items: [{ name: 'Jam.wav', size: 1, duration: 60 }] });
  document_cache['live-loop']._on.click({});
  const m = lastEmit('backing_play');
  if (!m || m.kind !== 'take' || m.name !== 'Attempt 2.wav') throw new Error('sent ' + JSON.stringify(m));
});
step('Live: the backing pad plays the picked track', function () {
  document_cache['live-backing-pick'].value = 'Jam.wav';
  key('b');
  const m = lastEmit('backing_play');
  if (!m || m.name !== 'Jam.wav' || m.kind === 'take') throw new Error('sent ' + JSON.stringify(m));
});
step('Live: recording turns the pad into Stop, with the clock', function () {
  handlers.rec(Object.assign({}, REC, { recording: true, elapsed: 75 }));
  if (document_cache['live-rec-label'].textContent !== 'Stop') throw new Error('label ' + document_cache['live-rec-label'].textContent);
  if (document_cache['live-rec-sub'].textContent !== '01:15') throw new Error('clock ' + document_cache['live-rec-sub'].textContent);
  handlers.rec(REC);
});
step('Live: with guitarix gone, the preset controls lock', function () {
  handlers.status({ connected: false });
  if (!document_cache['live-preset'].inert || !document_cache['live-grid'].inert) throw new Error('not locked');
  handlers.status({ connected: true });
  if (document_cache['live-preset'].inert) throw new Error('still locked');
});
step('Live: Escape leaves, and that is remembered too', function () {
  key('Escape');
  if (!document_cache['live'].hidden || store['gx-live'] !== '0') throw new Error('still in live mode');
});

step('socket dropped', function () { handlers.disconnect(); });

console.log('\n' + (failed ? 'FAILED' : 'all good'));
process.exit(failed ? 1 : 0);
