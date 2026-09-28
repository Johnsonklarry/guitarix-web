/* Demo mode: a stand-in for the Pi, running entirely in this browser.

   Loaded instead of the real Socket.IO client, it provides the io() the page
   calls, and answers every message the way the server would -- presets that
   move the faders when you switch, recording that ticks and produces a new
   "Attempt N", reamps and backing tracks that play through, exports that
   finish, imports that audition. The real connection is never opened, so
   nothing done here can reach guitarix, the recorder, or any file on the Pi.

   The only sound is one synthesized clip (static/demo/jam.mp3): Listen plays
   it on a loop, and so does Play on any take. */

window.GX_DEMO = true;

const DEMO = (function () {
  const CLIP = window.GX_DEMO_CLIP || 'static/demo/jam.mp3';
  const now = function () { return Date.now() / 1000; };

  /* ------------------------------------------------------------ the rig */

  const banks = [
    { name: 'Clean & Warm', presets: ['Glass Clean', 'Warm Jazz Box', 'Chicken Pickin', 'Folksy Fingerpicking'] },
    { name: 'Rock', presets: ['Big Dumb Rock', 'Crunch Rhythm', 'Lead Stack', 'Sweet Solo'] },
    { name: 'Ambient', presets: ['Ambient Wash', 'Shimmer Pad', 'Slow Swell'] },
    { name: 'Folk', presets: ['Fingerpicking', 'Open Tuning', 'Bluegrass Flatpick'] },
    { name: 'Heavy', presets: ['Drop D Chug', 'Doom', 'Djent'] },
    { name: 'Scratchpad', presets: ['Untitled idea', 'That thing from Tuesday'] },
  ];
  const cur = { bank: 'Rock', preset: 'Crunch Rhythm' };

  const band = function (id, name, min, max, step, extra) {
    return Object.assign({ id: id, name: name, min: min, max: max, step: step, value: 0,
                           type: 'FloatParameter', desc: '' }, extra || {});
  };
  const sw = function (id, name, desc) {
    return { id: id, name: name, min: 0, max: 1, step: 1, value: 0, type: 'BoolParameter', desc: desc || '' };
  };
  const BANDS = ['31.25 Hz', '62.5 Hz', '125 Hz', '250 Hz', '500 Hz', '1 kHz', '2 kHz', '4 kHz', '8 kHz', '16 kHz'];
  const STACKS = ['Bassman', 'Twin Reverb', 'Princeton', 'JCM-800', 'AC-30', 'Triple Giant'];

  const eq = [
    { prefix: 'eqs.', title: 'Graphic EQ', blurb: 'Cut or boost, one band at a time.', layout: 'rack',
      toggle: 'eqs.on_off', collapsed: false,
      controls: BANDS.map(function (b, i) { return band('eqs.fs' + i, b, -12, 12, 0.1); }) },
    { prefix: 'eqs.', title: 'Band width', blurb: 'How wide each band reaches. Rarely worth touching.',
      layout: 'rack', toggle: null, collapsed: true,
      controls: BANDS.map(function (b, i) { return band('eqs.Qs' + i, b, 0, 100, 1, { value: 50 }); }) },
    { prefix: 'low_highpass.', title: 'High and low cut', blurb: 'Trim the mud and the fizz.',
      layout: 'rows', toggle: 'low_highpass.on_off', collapsed: false, controls: [] },
    { prefix: 'amp.', title: 'Amp', blurb: 'Levels, drive and the tone stack.', layout: 'rack',
      toggle: 'amp.on_off', collapsed: false,
      controls: [
        band('amp.fuzz', 'Fuzz', 0, 1, 0.01), band('amp.bass', 'Bass', 0, 1, 0.01),
        band('amp.middle', 'Middle', 0, 1, 0.01), band('amp.treble', 'Treble', 0, 1, 0.01),
        band('amp.balance', 'Balance', -1, 1, 0.01), band('amp.wet_dry', 'Wet / dry', 0, 100, 1),
        band('amp.out_amp', 'Amp out', -20, 4, 0.1), band('amp.out_master', 'Master', -20, 4, 0.1),
        sw('amp.reverb_on_of', 'Reverb'),
        sw('amp.highgain', 'Highgain', 'Adds a second gain stage ahead of the tone stack'),
        { id: 'amp.tonestack.select', name: 'Tone stack', type: 'Enum', min: 0, max: STACKS.length - 1,
          step: 1, value: 0, desc: 'Which amp the tone controls are modelled on',
          options: STACKS.map(function (s, i) { return { value: i, key: s.toLowerCase(), label: s }; }) },
      ] },
  ];
  const fx = [
    { prefix: 'freeverb.', title: 'Reverb', blurb: 'Room size and mix.', layout: 'rows', toggle: 'freeverb.on_off',
      controls: [band('freeverb.RoomSize', 'Room size', 0, 1, 0.01), band('freeverb.damp', 'Damping', 0, 1, 0.01),
                 band('freeverb.wet_dry', 'Mix', 0, 100, 1)] },
    { prefix: 'echo.', title: 'Echo', blurb: 'Simple repeats.', layout: 'rows', toggle: 'echo.on_off',
      controls: [band('echo.time', 'Time', 1, 2000, 1), band('echo.percent', 'Feedback', 0, 100, 1)] },
    { prefix: 'delay.', title: 'Delay', blurb: 'Longer, tempo-ish repeats.', layout: 'rows', toggle: 'delay.on_off',
      controls: [band('delay.delay', 'Delay', 0, 2000, 1), band('delay.gain', 'Level', -40, 0, 0.5)] },
    { prefix: 'chorus.', title: 'Chorus', blurb: 'Thickens a clean tone.', layout: 'rows', toggle: 'chorus.on_off',
      controls: [band('chorus.level', 'Level', 0, 1, 0.01), band('chorus.depth', 'Depth', 0, 1, 0.01),
                 band('chorus.freq', 'Speed', 0.1, 10, 0.1)] },
  ];
  const ALL = [].concat.apply([], eq.concat(fx).map(function (g) { return g.controls; }));
  const IDS = {};
  ALL.forEach(function (c) { IDS[c.id] = c; });
  eq.concat(fx).forEach(function (g) {
    if (g.toggle) IDS[g.toggle] = { id: g.toggle, name: 'On', min: 0, max: 1, step: 1, type: 'BoolParameter' };
  });

  /* Each preset gets settings that fit its name: clean ones low on drive,
     rock ones high, ambient ones swimming in reverb and delay -- so switching
     presets visibly moves the faders the way it would on the real amp. */
  function seeded(text) {
    let h = 2166136261;
    for (let i = 0; i < text.length; i++) { h ^= text.charCodeAt(i); h = Math.imul(h, 16777619); }
    return function () {
      h ^= h << 13; h ^= h >>> 17; h ^= h << 5;
      return ((h >>> 0) % 10000) / 10000;
    };
  }
  const round = function (v, step) { return Math.round(v / step) * step; };

  function presetValues(bank, preset) {
    const r = seeded(bank + '/' + preset);
    const style = bank === 'Rock' ? 'rock' : bank === 'Ambient' ? 'ambient' : 'clean';
    const pick = function (lo, hi, step) { return +round(lo + r() * (hi - lo), step).toFixed(3); };
    const v = {};
    BANDS.forEach(function (_, i) {
      const tilt = style === 'rock' ? (i > 2 && i < 7 ? 2 : -1) : style === 'ambient' ? (i > 6 ? 2 : -2) : 0;
      v['eqs.fs' + i] = Math.max(-12, Math.min(12, +round(tilt + (r() - 0.5) * 8, 0.1).toFixed(1)));
      v['eqs.Qs' + i] = 50;
    });
    v['eqs.on_off'] = 1;
    v['low_highpass.on_off'] = style === 'rock' ? 1 : 0;
    v['amp.on_off'] = 1;
    v['amp.fuzz'] = style === 'rock' ? pick(0.55, 0.95, 0.01) : style === 'ambient' ? pick(0.05, 0.25, 0.01) : pick(0, 0.12, 0.01);
    v['amp.bass'] = pick(0.35, 0.75, 0.01);
    v['amp.middle'] = style === 'rock' ? pick(0.55, 0.85, 0.01) : pick(0.3, 0.6, 0.01);
    v['amp.treble'] = pick(0.4, 0.8, 0.01);
    v['amp.balance'] = 0;
    v['amp.wet_dry'] = pick(0, 30, 1);
    v['amp.out_amp'] = pick(-4, 0, 0.1);
    v['amp.out_master'] = pick(-2, 3, 0.1);
    v['amp.reverb_on_of'] = style === 'clean' ? 1 : 0;
    v['amp.highgain'] = style === 'rock' ? 1 : 0;
    v['amp.tonestack.select'] = style === 'rock' ? (r() < 0.5 ? 3 : 5) : style === 'clean' ? (r() < 0.5 ? 1 : 0) : 4;
    v['freeverb.on_off'] = style === 'rock' ? 0 : 1;
    v['freeverb.RoomSize'] = style === 'ambient' ? pick(0.75, 0.98, 0.01) : pick(0.2, 0.5, 0.01);
    v['freeverb.damp'] = pick(0.3, 0.7, 0.01);
    v['freeverb.wet_dry'] = style === 'ambient' ? pick(45, 70, 1) : pick(10, 30, 1);
    v['echo.on_off'] = style === 'clean' && r() < 0.5 ? 1 : 0;
    v['echo.time'] = pick(180, 420, 1);
    v['echo.percent'] = pick(15, 45, 1);
    v['delay.on_off'] = style === 'ambient' || preset === 'Sweet Solo' ? 1 : 0;
    v['delay.delay'] = pick(300, 700, 1);
    v['delay.gain'] = pick(-18, -8, 0.5);
    v['chorus.on_off'] = style === 'ambient' || preset === 'Glass Clean' ? 1 : 0;
    v['chorus.level'] = pick(0.3, 0.7, 0.01);
    v['chorus.depth'] = pick(0.2, 0.6, 0.01);
    v['chorus.freq'] = pick(0.4, 2.5, 0.1);
    return v;
  }

  let values = presetValues(cur.bank, cur.preset);
  let dirty = false;
  let audition = null;           // { name, bank, base, replaces, before, file }
  let lastCheck = null;          // the file behind the last import check

  /* ------------------------------------------------------------ recordings */

  let takes = [
    { name: 'Attempt 3.wav', size: 17.4e6, duration: 96.4, modified: now() - 600,
      active: false, dry: 'Attempt 3 (dry).wav', settings: 'Attempt 3.json' },
    { name: 'Attempt 2.wav', size: 4.9e6, duration: 26.7, modified: now() - 88000,
      active: false, dry: 'Attempt 2 (dry).wav', settings: 'Attempt 2.json' },
    { name: 'Attempt 1.wav', size: 61.9e6, duration: 338.1, modified: now() - 260000,
      active: false, dry: null, settings: null },
  ];
  let backingItems = [
    { name: 'Slow Blues in A.mp3', size: 7.1e6, duration: 214 },
    { name: 'Drum Loop 90bpm.wav', size: 3.4e6, duration: 32 },
    { name: 'Funk Groove in E.mp3', size: 5.2e6, duration: 176 },
  ];
  const R = { recording: false, elapsed: 0, available: true, wiring: null, error: null, dry: false,
              next_attempt: 4, free_bytes: 11.2e9, reamp: null, export: null,
              backing: { playing: false, kind: null, name: null, position: 0, duration: 0, paused: false,
                         volume: 80, loop: true, include: false, available: true } };
  let recStarted = 0, recDry = false;

  /* ------------------------------------------------------------ plumbing */

  const handlers = {};
  function fire(event, data) { if (handlers[event]) handlers[event](data); }
  function later(ms, fn) { setTimeout(fn, ms); }
  function done(msg, ok) { if (msg && msg.op) later(350, function () { fire('op_done', { op: msg.op, ok: ok !== false }); }); }
  function toast(text, kind) { fire('toast', { text: text, kind: kind || 'ok' }); }
  function rec() { return JSON.parse(JSON.stringify(R)); }
  function pushRec() { fire('rec', rec()); }
  function pushTakes() { fire('recordings', { rec: rec(), items: takes.slice(), backing_items: backingItems.slice() }); }
  function pushBanks() { fire('banks', { banks: JSON.parse(JSON.stringify(banks)), bank: cur.bank, preset: cur.preset }); }
  function findBank(name) { return banks.find(function (b) { return b.name === name; }); }
  const stem = function (n) { return n.replace(/\.[^.]+$/, ''); };

  function snapshot() {
    return { connected: true, engine: 'demo', bank: cur.bank, preset: cur.preset,
             banks: JSON.parse(JSON.stringify(banks)),
             can: { save_current: true, save_as: true, rename: true, delete: true,
                    new_bank: true, delete_bank: true, move: true },
             eq: eq, fx: fx, values: Object.assign({}, values), dirty: dirty,
             audition: audition ? publicAudition() : null,
             rec: rec(), recordings: takes.slice(), backing_items: backingItems.slice() };
  }
  function publicAudition() {
    return { name: audition.name, bank: audition.bank, base: audition.base, replaces: audition.replaces };
  }
  function setDirty(on) { dirty = on; fire('dirty', { dirty: on }); }

  function load(bank, preset) {
    cur.bank = bank; cur.preset = preset;
    values = presetValues(bank, preset);
    fire('preset', { bank: bank, preset: preset });
    fire('params', Object.assign({}, values));
    setDirty(false);
  }

  function addTake(name, seconds, extra) {
    let base = stem(name), ext = (name.match(/\.[^.]+$/) || ['.wav'])[0], n = 2;
    let final = base + ext;
    while (takes.some(function (t) { return t.name === final; })) final = base + ' (' + (n++) + ')' + ext;
    takes.unshift(Object.assign({ name: final, size: Math.round(seconds * 176400), duration: seconds,
                                  modified: now(), active: false, dry: null, settings: null }, extra || {}));
    return final;
  }

  /* one clock for everything that moves */
  setInterval(function () {
    let moving = false;
    if (R.recording) { R.elapsed = R.reamp ? 0 : Math.round(now() - recStarted); moving = true; }
    const r = R.reamp;
    if (r && !r.paused) {
      r.position = +(r.position + 1).toFixed(1);
      moving = true;
      if (r.position >= r.duration) {
        if (r.loop && !r.record) r.position = 0;
        else finishReamp(false);
      }
    }
    const b = R.backing;
    if (b.playing && !b.paused) {
      b.position = +(b.position + 1).toFixed(1);
      moving = true;
      if (b.position >= b.duration) { if (b.loop) b.position = 0; else { b.playing = false; } }
    }
    if (moving) pushRec();
  }, 1000);

  function finishReamp(cancelled) {
    const r = R.reamp, ex = R.export;
    R.reamp = null; R.export = null; R.recording = false; R.elapsed = 0;
    if (!r) return;
    if (ex && !cancelled) {
      const name = addTake(stem(ex.take) + ' (' + ex.tag + ').' + ex.format, r.duration);
      toast('Exported ' + name);
      if (ex.restore) { values = ex.restore; fire('params', Object.assign({}, values)); }
    } else if (ex && cancelled) {
      toast('Export cancelled. Nothing was kept.');
      if (ex.restore) { values = ex.restore; fire('params', Object.assign({}, values)); }
    } else if (r.record && !cancelled) {
      toast('Saved ' + addTake(stem(r.take) + ' (render).wav', r.duration));
    }
    pushTakes();
  }

  /* ------------------------------------------------------------ imports */

  function parseImport(text) {
    const a = String(text || '').indexOf('{'), z = String(text || '').lastIndexOf('}');
    if (a < 0 || z < a) throw new Error('That doesn\'t look like a preset file. Paste the JSON Claude sent.');
    let data;
    try { data = JSON.parse(text.slice(a, z + 1)); }
    catch (e) { throw new Error('The JSON is broken: ' + e.message); }
    const list = Array.isArray(data.presets) ? data.presets : data.params ? [data] : null;
    if (!list || !list.length) throw new Error('There are no presets in that file.');
    return { bank: data.bank, base: data.base, presets: list };
  }

  function report(p, i, file, bankName) {
    const params = p.params || {};
    const rejected = [], set = [];
    Object.keys(params).forEach(function (id) {
      if (IDS[id]) set.push(id);
      else {
        const guess = Object.keys(IDS).find(function (k) { return k.split('.')[0] === id.split('.')[0]; });
        rejected.push({ id: id, reason: 'isn\'t a parameter on this amp', suggestion: guess || null });
      }
    });
    const base = p.base || file.base || null;
    const baseOk = !base || banks.some(function (b) { return b.presets.some(function (x) { return base === b.name + '/' + x || base === x; }); });
    const bank = findBank(bankName);
    return { index: i, name: p.name || 'Untitled', notes: p.notes || '', set: set.length, reset: 0, inherited: 0,
             switched_on: [], switched_off: [], rejected: rejected, clamped: [],
             bank: bankName, bank_exists: !!bank, replaces: !!(bank && bank.presets.indexOf(p.name) >= 0),
             base: base, base_error: baseOk ? null : 'The base preset "' + base + '" isn\'t in your banks.' };
  }

  function applyPreset(p, file) {
    const base = p.base || file.base;
    if (base) {
      const hit = banks.find(function (b) { return b.presets.some(function (x) { return base === b.name + '/' + x || base === x; }); });
      if (hit) {
        const name = base.indexOf('/') >= 0 ? base.split('/').slice(1).join('/') : base;
        values = presetValues(hit.name, name);
      }
    }
    Object.keys(p.params || {}).forEach(function (id) {
      if (!IDS[id]) return;
      let v = p.params[id];
      const c = IDS[id];
      if (c.options) {
        const o = c.options.find(function (o) { return String(o.label).toLowerCase() === String(v).toLowerCase() || o.value === v; });
        v = o ? o.value : 0;
      } else if (typeof v === 'string') { v = /^(on|true|yes)$/i.test(v) ? 1 : /^(off|false|no)$/i.test(v) ? 0 : Number(v) || 0; }
      values[id] = Math.max(c.min, Math.min(c.max, Number(v)));
    });
    fire('params', Object.assign({}, values));
  }

  function savePreset(bank, name) {
    let b = findBank(bank);
    if (!b) { b = { name: bank, presets: [] }; banks.push(b); }
    if (b.presets.indexOf(name) < 0) b.presets.push(name);
    cur.bank = bank; cur.preset = name;
  }

  /* ------------------------------------------------------------ messages */

  const on = {
    set_preset: function (m) { later(250, function () { load(m.bank, m.preset); }); },
    set_param: function (m) { values[m.id] = m.value; if (!dirty) setDirty(true); },

    preset_save: function (m) { setDirty(false); toast('Saved ' + cur.preset); done(m); },
    preset_save_as: function (m) { savePreset(m.bank, m.name); setDirty(false); pushBanks(); toast('Saved ' + m.name); done(m); },
    preset_rename: function (m) {
      const b = findBank(m.bank), i = b ? b.presets.indexOf(m.old) : -1;
      if (i >= 0) b.presets[i] = m.new;
      if (cur.preset === m.old) cur.preset = m.new;
      pushBanks(); toast('Renamed to ' + m.new); done(m);
    },
    preset_delete: function (m) {
      const b = findBank(m.bank);
      if (b) b.presets = b.presets.filter(function (p) { return p !== m.name; });
      pushBanks(); toast('Deleted ' + m.name); done(m);
    },
    preset_move: function (m) {
      on.preset_delete({ bank: m.bank, name: m.name });
      savePreset(m.to_bank, m.new || m.name);
      pushBanks(); done(m);
    },
    bank_create: function (m) { banks.push({ name: m.name, presets: [] }); pushBanks(); toast('Created bank ' + m.name); done(m); },
    bank_delete: function (m) {
      if (m.name === cur.bank) { toast(m.name + ' is the bank you\'re playing from. Switch to another preset first.', 'error'); return done(m, false); }
      const i = banks.findIndex(function (b) { return b.name === m.name; });
      if (i >= 0) banks.splice(i, 1);
      pushBanks(); toast('Deleted bank ' + m.name); done(m);
    },

    record_start: function (m) {
      if (R.recording) return;
      R.recording = true; R.elapsed = 0; R.dry = !!m.dry; recStarted = now(); recDry = !!m.dry;
      pushRec();
    },
    record_stop: function () {
      if (!R.recording) return;
      const seconds = Math.max(1, Math.round(now() - recStarted));
      R.recording = false; R.elapsed = 0;
      const n = R.next_attempt++;
      addTake('Attempt ' + n + '.wav', seconds,
              { dry: recDry ? 'Attempt ' + n + ' (dry).wav' : null, settings: 'Attempt ' + n + '.json' });
      R.free_bytes -= seconds * 176400 * (recDry ? 2 : 1);
      pushTakes();
    },
    rec_rename: function (m) {
      const t = takes.find(function (t) { return t.name === m.name; });
      if (t) {
        const ext = (t.name.match(/\.[^.]+$/) || [''])[0];
        t.name = m.new + ext;
        if (t.dry) t.dry = m.new + ' (dry)' + ext;
        if (t.settings) t.settings = m.new + '.json';
      }
      pushTakes(); toast('Renamed to ' + m.new + '.wav'); done(m);
    },
    rec_delete: function (m) { takes = takes.filter(function (t) { return t.name !== m.name; }); pushTakes(); toast('Deleted'); done(m); },

    reamp_start: function (m) {
      // the server refuses a second one; a demo that allowed it would hide that
      if (R.reamp) { toast('Already reamping ' + stem(R.reamp.take) + '.', 'error'); return done(m, false); }
      if (R.recording) { toast('Stop the current recording first.', 'error'); return done(m, false); }
      const t = takes.find(function (x) { return x.name === m.take; });
      R.reamp = { take: m.take, mode: 'wet', record: !!m.record, loop: !!m.loop && !m.record,
                  output: m.record ? stem(m.take) + ' (render).wav' : null,
                  position: 0, duration: t ? Math.min(t.duration, 45) : 30, paused: false };
      if (m.record) R.recording = true;
      pushRec(); done(m);
    },
    reamp_mode: function (m) { if (R.reamp) R.reamp.mode = m.mode; pushRec(); done(m); },
    reamp_pause: function (m) { if (R.reamp) R.reamp.paused = !!m.paused; pushRec(); },
    reamp_loop: function (m) { if (R.reamp) R.reamp.loop = !!m.loop; pushRec(); },
    reamp_stop: function (m) { finishReamp(true); pushRec(); done(m); },

    export_start: function (m) {
      const t = takes.find(function (x) { return x.name === m.take; });
      const src = m.source || 'live';
      const label = src === 'live' ? 'the current settings' : src === 'recorded' ? 'the settings it was recorded with'
                  : src.replace('preset:', '');
      const tag = src === 'live' ? 'export' : src === 'recorded' ? 'export as recorded'
                : 'export - ' + src.split('/').slice(1).join('/');
      R.export = { take: m.take, label: label, format: m.format === 'mp3' ? 'mp3' : 'wav', tag: tag,
                   restore: Object.assign({}, values) };
      R.reamp = { take: m.take, mode: 'wet', record: true, loop: false, output: 'x',
                  position: 0, duration: t ? Math.min(t.duration, 20) : 15, paused: false };
      R.recording = true;
      pushRec();
      if (m.op) {
        const wait = setInterval(function () {
          if (!R.export) { clearInterval(wait); fire('op_done', { op: m.op, ok: true }); }
        }, 500);
      }
    },
    export_cancel: function () { finishReamp(true); pushRec(); },

    backing_play: function (m) {
      const kind = m.kind === 'take' ? 'take' : 'backing';
      const item = (kind === 'take' ? takes : backingItems).find(function (x) { return x.name === m.name; });
      Object.assign(R.backing, { playing: true, kind: kind, name: m.name, position: 0,
                                 duration: item ? item.duration : 60, paused: false, loop: m.loop !== false,
                                 volume: m.volume || R.backing.volume });
      pushRec(); done(m);
    },
    backing_stop: function (m) { R.backing.playing = false; pushRec(); done(m); },
    backing_pause: function (m) { R.backing.paused = !!m.paused; pushRec(); },
    backing_loop: function (m) { R.backing.loop = !!m.loop; pushRec(); },
    backing_volume: function (m) { R.backing.volume = m.volume; },
    backing_include: function (m) { R.backing.include = !!m.include; pushRec(); },
    backing_delete: function (m) {
      backingItems = backingItems.filter(function (b) { return b.name !== m.name; });
      if (R.backing.name === m.name) R.backing.playing = false;
      pushTakes(); done(m);
    },

    import_check: function (m, ack) {
      try {
        const file = parseImport(m.text);
        lastCheck = file;
        const bankName = m.bank || file.bank || 'Imported';
        ack({ ok: true, presets: file.presets.map(function (p, i) { return report(p, i, file, p.bank || bankName); }),
              others_off: !!m.others_off, bank: bankName, can_create_bank: true });
      } catch (e) {
        ack({ ok: false, error: e.message });
      }
    },
    import_audition: function (m) {
      let file;
      try { file = parseImport(m.text); } catch (e) { toast(e.message, 'error'); return done(m, false); }
      const p = file.presets[m.index || 0];
      const bank = m.bank || p.bank || file.bank || 'Imported';
      if (!audition) audition = { before: Object.assign({}, values), prev: Object.assign({}, cur) };
      applyPreset(p, file);
      Object.assign(audition, { name: p.name, bank: bank, base: p.base || file.base || null,
                                replaces: !!(findBank(bank) && findBank(bank).presets.indexOf(p.name) >= 0) });
      fire('audition', publicAudition());
      done(m);
    },
    audition_save: function (m) {
      if (!audition) return done(m, false);
      savePreset(audition.bank, audition.name);
      toast('Saved ' + audition.name + ' to ' + audition.bank);
      audition = null;
      fire('audition', null); pushBanks(); fire('preset', { bank: cur.bank, preset: cur.preset });
      done(m);
    },
    audition_discard: function (m) {
      if (!audition) return done(m, false);
      values = audition.before; cur.bank = audition.prev.bank; cur.preset = audition.prev.preset;
      const name = audition.name;
      audition = null;
      fire('audition', null); fire('params', Object.assign({}, values));
      fire('preset', { bank: cur.bank, preset: cur.preset });
      toast('Discarded ' + name + '. Everything is back how it was.');
      done(m);
    },
    import_save: function (m) {
      let file;
      try { file = parseImport(m.text); } catch (e) { toast(e.message, 'error'); return done(m, false); }
      const p = file.presets[m.index || 0];
      const bank = m.bank || p.bank || file.bank || 'Imported';
      applyPreset(p, file); savePreset(bank, p.name); pushBanks();
      toast('Saved ' + p.name + ' to ' + bank); done(m);
    },
    import_save_all: function (m) {
      let file;
      try { file = parseImport(m.text); } catch (e) { toast(e.message, 'error'); return done(m, false); }
      file.presets.forEach(function (p) {
        applyPreset(p, file);
        savePreset(m.bank || p.bank || file.bank || 'Imported', p.name);
      });
      pushBanks();
      toast('Saved ' + file.presets.length + ' preset' + (file.presets.length === 1 ? '' : 's'));
      done(m);
    },
    export_preset: function (m, ack) {
      ack({ ok: true, filename: cur.preset + '.json',
            data: { format: 'gxweb-presets/1', bank: cur.bank,
                    presets: [{ name: cur.preset, notes: 'Exported from the demo', params: Object.assign({}, values) }] } });
    },
  };

  /* ------------------------------------------------------------ the socket */

  function socket() {
    const s = {
      on: function (event, fn) { handlers[event] = fn; return s; },
      emit: function (event, msg, ack) {
        const fn = on[event];
        if (fn) fn(msg || {}, typeof ack === 'function' ? ack : function () {});
        else if (msg && msg.op) done(msg);     // anything unhandled still settles its button
        return s;
      },
    };
    later(150, function () {
      fire('connect');
      fire('status', { connected: true });
      fire('snapshot', snapshot());
    });
    return s;
  }

  /* ------------------------------------------------------------ files */

  /* Takes, downloads and Listen all get the demo clip; settings files and
     uploads are answered here; nothing goes to the network. */
  function media(kind, name) {
    if (kind === 'download' && /\.json$/.test(name || '')) return settingsUrl(name);
    return CLIP;
  }
  function settingsJson(name) {
    return JSON.stringify({ format: 'gxweb-presets/1', bank: cur.bank,
      presets: [{ name: stem(name || 'Take'), notes: 'The settings this take was recorded with.',
                  params: presetValues(cur.bank, cur.preset) }] }, null, 2);
  }
  function settingsUrl(name) {
    return URL.createObjectURL(new Blob([settingsJson(name)], { type: 'application/json' }));
  }

  const realFetch = window.fetch ? window.fetch.bind(window) : null;
  function fetchStandIn(url, opts) {
    const u = String(url);
    const reply = function (body, status) {
      return Promise.resolve(new Response(typeof body === 'string' ? body : JSON.stringify(body),
        { status: status || 200, headers: { 'Content-Type': 'application/json' } }));
    };
    if (/^\/recordings\/.+\.json$/.test(u)) return reply(settingsJson(decodeURIComponent(u.split('/').pop())));
    if (u === '/backing' && opts && opts.method === 'POST') {
      const saved = [];
      if (opts.body && opts.body.getAll) {
        opts.body.getAll('file').forEach(function (f) {
          const name = f.name || 'track.mp3';
          backingItems.push({ name: name, size: f.size || 0, duration: 120 + Math.round(Math.random() * 120) });
          saved.push(name);
        });
      }
      later(400, pushTakes);
      return reply({ ok: true, saved: saved });
    }
    if (/^\/(api|recordings|backing|monitor)/.test(u)) return reply({ ok: false, error: 'Not in the demo.' }, 404);
    return realFetch ? realFetch(url, opts) : Promise.reject(new Error('no fetch'));
  }

  function paramsExport() {
    const params = Object.keys(IDS).map(function (id) {
      const c = IDS[id];
      return { id: id, name: c.name, type: c.type, min: c.min, max: c.max, step: c.step,
               value: values[id], options: c.options ? c.options.map(function (o) { return o.label; }) : undefined };
    });
    return JSON.stringify({ format: 'gxweb-params/1', note: 'A demo amp -- not a real parameter list.',
                            banks: banks.reduce(function (o, b) { o[b.name] = b.presets; return o; }, {}),
                            parameters: params }, null, 2);
  }

  return { socket: socket, media: media, fetch: fetchStandIn, paramsExport: paramsExport };
})();

/* what the page calls instead of the real Socket.IO client */
function io() { return DEMO.socket(); }
window.fetch = DEMO.fetch;
window.GX_DEMO_MEDIA = DEMO.media;

(function () {
  const link = document.getElementById('imp-params');
  if (link) link.href = URL.createObjectURL(new Blob([DEMO.paramsExport()], { type: 'application/json' }));
})();
