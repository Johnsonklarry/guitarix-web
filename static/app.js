/* Guitarix control surface.
 *
 * The server holds the truth. This file renders whatever snapshot arrives,
 * sends user gestures up, and applies pushed changes back down without
 * fighting a slider the user happens to be holding.
 */

const socket = io();

const els = {
  pilot:    document.getElementById('pilot'),
  readout:  document.getElementById('readout'),
  link:     document.getElementById('link-state'),
  banks:    document.getElementById('banks'),
  presets:  document.getElementById('presets'),
  eq:       document.getElementById('groups-eq'),
  fx:       document.getElementById('groups-fx'),
  takes:    document.getElementById('takes'),
  toasts:   document.getElementById('toasts'),
  recBtn:   document.getElementById('btn-rec'),
  recLabel: document.getElementById('rec-label'),
  recClock: document.getElementById('rec-clock'),
  dryCheck: document.getElementById('dry-check'),
  recAttempt: document.getElementById('rec-attempt'),
  recStats: document.getElementById('rec-stats'),
  reampbar: document.getElementById('reampbar'),
  reampText: document.getElementById('reamp-text'),
  reampClock: document.getElementById('reamp-clock'),
  reampProgress: document.getElementById('reamp-progress'),
  backingList: document.getElementById('backing-list'),
  backingNow: document.getElementById('backing-now'),
  backingName: document.getElementById('backing-name'),
  backingClock: document.getElementById('backing-clock'),
  backingVolume: document.getElementById('backing-volume'),
  backingInclude: document.getElementById('backing-include'),
  backingFile: document.getElementById('backing-file'),
  recNote:  document.getElementById('rec-note'),
  recDot:   document.getElementById('rec-dot'),
  recLamp:  document.getElementById('rec-lamp'),
  midtabs:  document.getElementById('midtabs'),
  offline:  document.getElementById('offline'),
  audition: document.getElementById('audition'),
  auditionText: document.getElementById('audition-text'),
  auditionActions: document.getElementById('audition-actions'),
  impSource: document.getElementById('imp-source'),
  impReview: document.getElementById('imp-review'),
  impOffline: document.getElementById('imp-offline'),
  toolbar:  document.getElementById('presets-toolbar'),
  offlineText: document.getElementById('offline-text'),
};

let organising = false;
let picked = null;           // { bank, name } chosen while organising
let can = {};

let state = { banks: [], bank: null, preset: null, values: {}, version: 0 };
let online = false;          // engine reachable? read during the first render,
                             // so it has to be declared before applyLayout runs
let linked = false;          // browser <-> web app socket up?
let selectedBank = null;

const sliders = {};          // param id -> <input type=range>
const readouts = {};         // param id -> value <span>
const switches = {};         // param id -> <button>
const dragging = new Set();  // params the user is holding right now

/* ------------------------------------------------------------------ tabs */

/* Narrow screens page through one section at a time. A 16:9 window has room
   for all four side by side, so the tabs step aside and every pane shows. */
const wide = window.matchMedia('(min-width: 1000px)');
let currentTab = 'presets';

function showTab(name) {
  currentTab = name;
  if (name === 'eq' || name === 'fx') midTab = name;
  document.querySelectorAll('.tab').forEach(function (t) {
    const on = t.dataset.tab === name;
    t.classList.toggle('is-current', on);
    t.setAttribute('aria-selected', String(on));
  });
  if (wide.matches) return;
  document.querySelectorAll('.pane').forEach(function (pane) {
    pane.hidden = pane.dataset.pane !== name;
  });
}

/* The middle column is one scroller with two tabs rather than EQ stacked on
   effects. Two scrollers sharing the height meant neither had enough. */
let midTab = 'eq';

function showMid(name) {
  midTab = name;
  document.querySelectorAll('.midtab').forEach(function (t) {
    const on = t.dataset.mid === name;
    t.classList.toggle('is-current', on);
    t.setAttribute('aria-selected', String(on));
  });
  if (!wide.matches) return;
  document.querySelectorAll('.pane').forEach(function (pane) {
    if (pane.dataset.pane === 'eq' || pane.dataset.pane === 'fx') {
      pane.hidden = pane.dataset.pane !== name;
    }
  });
}

function applyLayout() {
  document.body.classList.toggle('is-wide', wide.matches);
  els.midtabs.hidden = !wide.matches;
  if (wide.matches) {
    document.querySelectorAll('.pane').forEach(function (pane) { pane.hidden = false; });
    showMid(midTab);
  } else {
    showTab(currentTab);
  }
  renderPresets();
}

document.querySelectorAll('.tab').forEach(function (tab) {
  tab.addEventListener('click', function () { showTab(tab.dataset.tab); });
});

document.querySelectorAll('.midtab').forEach(function (tab) {
  tab.addEventListener('click', function () { showMid(tab.dataset.mid); });
});

wide.addEventListener('change', applyLayout);
applyLayout();

/* ------------------------------------------------------------------ status */

/* Two different links can drop, and they need different fixes:
     browser -> web app   the Pi, the network, or Flask itself
     web app -> engine    guitarix, or its RPC port
   The banner says which one it is. */
function setStatus(connected) {
  online = !!connected;
  renderLinkState();
}

function setLinked(ok) {
  const was = linked;
  linked = !!ok;
  if (was && !linked) abandonInFlight();
  renderLinkState();
}

function renderLinkState() {
  const healthy = linked && online;
  document.body.classList.toggle('is-offline', !healthy);
  els.pilot.classList.toggle('is-live', healthy);
  els.pilot.classList.toggle('is-fault', !healthy);
  els.offline.hidden = healthy;

  if (!linked) {
    els.link.textContent = 'reconnecting';
    els.offlineText.textContent =
      'Lost the connection to the web app on the Pi. Reconnecting on its own; '
      + 'if it doesn\'t come back, check the Pi is up and app.py is running.';

  } else if (!online) {
    els.link.textContent = 'no connection';
    els.offlineText.textContent =
      'The web app is up, but it can\'t reach guitarix'
      + (state.engine ? ' at ' + state.engine : '')
      + '. Presets, EQ and effects are locked until it reconnects. '
      + 'Recording still works.';

  } else {
    els.link.textContent = 'linked';
  }

  lockControls(!healthy);
  renderReadout();
}

/* The header line, worked out from the current state every time rather than
   written piecemeal. It used to be set in three places that each assumed the
   others had run in a particular order -- and on page load the preset can
   arrive before the link is marked up, which left it stuck on "Reconnecting"
   with everything actually healthy. */
function renderReadout() {
  let html;
  if (!linked) {
    html = 'Reconnecting';
  } else if (!online) {
    html = 'No connection';
  } else if (state.audition) {
    html = 'Auditioning <b>' + escapeHtml(displayName(state.audition.name)) + '</b>';
  } else if (state.preset) {
    html = 'Now playing <b>' + escapeHtml(displayName(state.preset)) + '</b>'
         + (state.dirty ? ' <span class="readout__tag">edited</span>' : '');
  } else {
    html = 'Pick a sound to start';
  }
  if (els.readout.innerHTML !== html) {
    els.readout.innerHTML = html;
    flash(els.readout, 'is-changed');
  }
  // unsaved changes: the Save button is the way out, so point at it
  if (saveBtnRef()) saveBtnRef().classList.toggle('is-armed', !!(state.dirty && !state.audition && state.preset));
  renderLive();
}

function saveBtnRef() { return document.getElementById('btn-save'); }

/* Everything that talks to guitarix goes inert while it can't be reached.
   `inert` rather than CSS pointer-events, because pointer-events still lets
   you Tab to a button and press it. The containers are locked, not the
   controls inside them, so re-rendering can't quietly unlock anything.
   Tabs and the recorder stay live: one only changes the view, the other
   doesn't go through guitarix at all. */
function lockControls(locked) {
  [els.toolbar, els.presets, els.eq, els.fx, document.getElementById('live-preset'),
   document.getElementById('live-grid'), els.auditionActions,
   els.impSource, els.impReview].forEach(function (el) {
    if (el) el.inert = locked;
  });
  if (els.impOffline) els.impOffline.hidden = !locked;
}

/* What an empty section should say. "Nothing here yet, add the plugin" is
   actively misleading when the real reason is that the engine never answered. */
function emptyText(whenOnline) {
  return online ? whenOnline : 'Waiting for the guitarix engine.';
}

function setPreset(bank, preset) {
  state.bank = bank;
  state.preset = preset;
  renderReadout();
  markPlayingBank();

  document.querySelectorAll('.preset').forEach(function (btn) {
    const on = btn.dataset.bank === bank && btn.dataset.preset === preset;
    // lit from somewhere else -- another device, the GTK window -- rather
    // than by a tap here: pulse once so the change is noticed
    if (on && !btn.classList.contains('is-active') && !btn.classList.contains('is-pending')) {
      flash(btn, 'is-arriving');
    }
    btn.classList.toggle('is-active', on);
    btn.classList.remove('is-pending');
    clearTimeout(btn._pendingTimer);
    if (on) btn.setAttribute('aria-current', 'true');
    else btn.removeAttribute('aria-current');
  });
}

/* ------------------------------------------------------------------ presets */

let knownBanks = null;          // names from the last render, to spot new ones

const CHIP_LIMIT = 4;        // more banks than this and chips become a wall

function pickBank(name) {
  selectedBank = name;
  renderBanks();
  renderPresets();
  applyOrganise();
}

function renderBanks() {
  els.banks.innerHTML = '';
  const seen = knownBanks;
  knownBanks = {};
  const many = state.banks.length > CHIP_LIMIT;
  els.banks.classList.toggle('is-picker', many);

  if (many) {
    // A dozen banks as chips is a wall of pills. One control instead, with
    // the bank you're playing from marked so it doesn't get lost.
    const label = document.createElement('span');
    label.className = 'banks__label';
    label.textContent = 'Bank';
    els.banks.appendChild(label);

    const sel = document.createElement('select');
    sel.className = 'choice banks__pick';
    sel.setAttribute('aria-label', 'Bank');
    state.banks.forEach(function (bank) {
      const o = document.createElement('option');
      o.value = bank.name;
      o.textContent = bank.name + (bank.name === state.bank ? '  \u25cf' : '')
                    + '   (' + bank.presets.length + ')';
      if (bank.name === selectedBank) o.selected = true;
      sel.appendChild(o);
      knownBanks[bank.name] = true;
    });
    sel.addEventListener('change', function () { pickBank(sel.value); });
    els.banks.appendChild(sel);
    markPlayingBank();
    return;
  }

  state.banks.forEach(function (bank) {
    const btn = document.createElement('button');
    btn.className = 'bank' + (bank.name === selectedBank ? ' is-current' : '')
                  + (seen && !seen[bank.name] ? ' is-new' : '');
    btn.type = 'button';
    btn.dataset.bank = bank.name;
    btn.textContent = bank.name;
    knownBanks[bank.name] = true;
    btn.addEventListener('click', function () { pickBank(bank.name); });
    els.banks.appendChild(btn);
  });
  markPlayingBank();
}

/* The bank you're playing from carries a lit dot, so after browsing other
   banks you can find your way back to the preset that's actually on. */
function markPlayingBank() {
  document.querySelectorAll('.bank').forEach(function (chip) {
    const on = chip.dataset.bank === state.bank;
    chip.classList.toggle('is-playing', on);
    chip.title = on ? 'The preset you\'re playing is in this bank' : '';
  });
  // as a dropdown, the marker lives in the option text, which has to be redrawn
  const pick = els.banks.querySelector ? els.banks.querySelector('.banks__pick') : null;
  if (!pick) return;
  Array.prototype.forEach.call(pick.options, function (o) {
    const bank = state.banks.find(function (b) { return b.name === o.value; });
    o.textContent = o.value + (o.value === state.bank ? '  \u25cf' : '')
                  + '   (' + (bank ? bank.presets.length : 0) + ')';
  });
  pick.classList.toggle('is-playing', pick.value === state.bank);
}

function renderPresets() {
  if (!els.presets) return;
  const bank = state.banks.find(function (b) { return b.name === selectedBank; });
  els.presets.innerHTML = '';
  els.presets.classList.toggle('is-organising', organising);

  if (!state.banks.length) {
    els.presets.innerHTML = '<p class="empty">' + emptyText(
      'The engine reports no banks at all. Check ~/.config/guitarix/banks.'
    ) + '</p>';
    return;
  }
  if (!bank || !bank.presets.length) {
    els.presets.innerHTML = '<p class="empty">No presets in this bank.</p>';
    return;
  }

  // one or two presets shouldn't each become a screen-tall button
  const columns = (wide.matches || window.matchMedia('(max-width: 520px)').matches) ? 1 : 2;
  els.presets.classList.toggle('is-sparse',
    Math.ceil(bank.presets.length / columns) < 3);

  bank.presets.forEach(function (name) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'preset';
    btn.dataset.bank = bank.name;
    btn.dataset.preset = name;
    btn.innerHTML =
      '<span class="lamp" aria-hidden="true"></span>' +
      '<span class="preset__name">' + escapeHtml(displayName(name)) + '</span>' +
      '<span class="preset__tag" aria-hidden="true">ON</span>';

    const isPicked = picked && picked.bank === bank.name && picked.name === name;
    if (isPicked) btn.classList.add('is-picked');

    btn.addEventListener('click', function () {
      if (organising) {
        // In organise mode a tap picks the preset instead of loading it, and
        // the bar above acts on it. Nothing moves inside the tile.
        picked = isPicked ? null : { bank: bank.name, name: name };
        renderPresets();
        applyOrganise();
        return;
      }
      // optimistic: light this one immediately, blink until the amp confirms
      document.querySelectorAll('.preset').forEach(function (p) {
        p.classList.remove('is-active', 'is-pending');
        p.removeAttribute('aria-current');
      });
      btn.classList.add('is-active', 'is-pending');
      btn.setAttribute('aria-current', 'true');
      socket.emit('set_preset', { bank: bank.name, preset: name });
      // no answer at all -- the link dropped as you tapped -- put the lit
      // tile back where the amp actually is instead of blinking forever
      clearTimeout(btn._pendingTimer);
      btn._pendingTimer = setTimeout(function () {
        // a re-render may have replaced this tile, and the amp may well have
        // confirmed already -- only complain if it genuinely didn't
        if (!btn.isConnected || !btn.classList.contains('is-pending')) return;
        if (state.bank === bank.name && state.preset === name) return;
        setPreset(state.bank, state.preset);
        showToast('The amp didn\'t confirm ' + displayName(name) + '. It\'s still on '
                  + (state.preset ? displayName(state.preset) : 'the last preset') + '.', 'error');
      }, 5000);
    });

    els.presets.appendChild(btn);
  });

  if (state.bank === bank.name) setPreset(state.bank, state.preset);
}

/* ------------------------------------------------------------------ knobs */

/* One table of every parameter -> its view binding. Sliders, readouts,
   switches and selects used to live in four maps that makeRange, buildDiscrete
   and renderGroups each had to keep in step; a control is remembered here
   once, so a re-render only has to forget it once. */
const ControlRegistry = new Map();

function bindControl(id, binding) {
  ControlRegistry.set(id, binding);
  return binding;
}

/* Rebuilding a container throws its controls away. Forget the ones inside it,
   or the maps keep the detached nodes (and their listeners) alive, and a
   glide frame can keep running on a dead slider. */
function releaseControls(into) {
  [sliders, readouts, switches, selects].forEach(function (map) {
    Object.keys(map).forEach(function (id) {
      const el = map[id];
      if (!el || !into.contains(el)) return;
      if (el._glide) { cancelAnimationFrame(el._glide); el._glide = null; }
      if (map === sliders) dragging.delete(id);
      delete map[id];
    });
  });

  ControlRegistry.forEach(function (binding, id) {
    if (binding.el && into.contains(binding.el)) ControlRegistry.delete(id);
  });
}

function renderGroups(into, groups) {
  releaseControls(into);
  into.innerHTML = '';
  if (!groups.length) {
    into.innerHTML = '<p class="empty">' + emptyText(
      'Nothing here yet. Add the plugin to your guitarix rack, or edit controls.py.'
    ) + '</p>';
    return;
  }

  groups.forEach(function (group) {
    const empty = !group.controls || !group.controls.length;
    const box = document.createElement('section');
    box.className = 'group' + (group.collapsed ? ' is-folded' : '')
                  + (empty ? ' is-switch-only' : '');

    const head = document.createElement('div');
    head.className = 'group__head';

    // The whole title area folds the group. One control where there used to
    // be a Show/Hide pill competing with the bypass switch for the same row.
    const disclose = document.createElement('button');
    disclose.type = 'button';
    disclose.className = 'group__toggle';
    disclose.setAttribute('aria-expanded', String(!group.collapsed));
    disclose.innerHTML =
      '<span class="chev" aria-hidden="true"></span>' +
      '<span class="group__text">' +
        '<span class="group__title">' + escapeHtml(group.title) + '</span>' +
        (group.blurb ? '<span class="group__blurb">' + escapeHtml(group.blurb) + '</span>' : '') +
      '</span>';
    if (empty) {
      disclose.disabled = true;           // nothing underneath to fold
      disclose.removeAttribute('aria-expanded');
    } else {
      disclose.addEventListener('click', function () {
        const folded = box.classList.toggle('is-folded');
        disclose.setAttribute('aria-expanded', String(!folded));
        // folded controls are out of sight, so keep Tab out of them too --
        // and a bypassed unit stays out of reach whether it's folded or not
        if (box._inner) box._inner.inert = folded || box.classList.contains('is-bypassed');
      });
    }
    head.appendChild(disclose);

    if (group.toggle) {
      const sw = document.createElement('button');
      sw.type = 'button';
      sw.className = 'switch';
      sw.dataset.param = group.toggle;
      sw.dataset.role = 'bypass';
      sw.innerHTML = '<span class="lamp" aria-hidden="true"></span><span>on</span>';
      sw.setAttribute('aria-pressed', 'false');
      sw.addEventListener('click', function () {
        const next = sw.classList.contains('is-on') ? 0 : 1;
        applyToggle(group.toggle, next);
        command(group.toggle, next, 'discrete');
      });
      switches[group.toggle] = sw;
      head.appendChild(sw);
    }

    const body = document.createElement('div');
    body.className = 'group__body';

    // Switches and choices get their own strip; a fader for an on/off value
    // or a slider over enum indexes just hides what the control is for.
    const discrete = group.controls.filter(isDiscrete);
    const continuous = group.controls.filter(function (c) { return !isDiscrete(c); });

    if (continuous.length) {
      if (group.layout === 'rack') {
        const rack = document.createElement('div');
        rack.className = 'rack';
        continuous.forEach(function (ctrl) { rack.appendChild(buildBand(ctrl)); });
        body.appendChild(rack);
      } else {
        continuous.forEach(function (ctrl) { body.appendChild(buildKnob(ctrl)); });
      }
    }

    if (discrete.length) {
      const strip = document.createElement('div');
      strip.className = 'discrete';
      const hasOptions = function (c) { return !!(c.options && c.options.length); };
      discrete.filter(hasOptions)
        .concat(discrete.filter(function (c) { return !hasOptions(c); }))
        .forEach(function (ctrl) { strip.appendChild(buildDiscrete(ctrl)); });
      body.appendChild(strip);
    }

    // One wrapper inside the body lets the fold animate to the content's real
    // height (grid rows 1fr to 0fr) rather than snapping.
    const inner = document.createElement('div');
    inner.className = 'group__inner';
    while (body.firstChild) inner.appendChild(body.firstChild);
    body.appendChild(inner);
    box._inner = inner;
    if (group.collapsed) inner.inert = true;

    box.appendChild(head);
    if (!empty) box.appendChild(body);
    into.appendChild(box);
  });
}

/* One <input type=range> wired to a parameter, shared by both layouts. */
function makeRange(ctrl, valueEl) {
  const min = Number(ctrl.min);
  const max = Number(ctrl.max);
  const step = Number(ctrl.step) > 0 ? Number(ctrl.step) : (max - min) / 200;

  const input = document.createElement('input');
  input.type = 'range';
  input.min = min;
  input.max = max;
  input.step = step;
  input.value = ctrl.value;
  input.dataset.param = ctrl.id;
  input.setAttribute('aria-label', ctrl.name);

  // hold off remote updates while the user has hold of this one
  ['pointerdown', 'keydown'].forEach(function (evt) {
    input.addEventListener(evt, function () { dragging.add(ctrl.id); });
  });
  ['pointerup', 'pointercancel', 'blur', 'keyup'].forEach(function (evt) {
    input.addEventListener(evt, function () { dragging.delete(ctrl.id); });
  });

  const fmt = formatter(ctrl);
  valueEl._fmt = fmt;

  input.addEventListener('input', function () {
    const v = Number(input.value);
    valueEl.textContent = fmt(v);
    valueEl.classList.remove('is-remote');
    send(ctrl.id, v);
  });

  sliders[ctrl.id] = input;
  readouts[ctrl.id] = valueEl;
  bindControl(ctrl.id, { kind: 'range', el: input, readout: valueEl });
  valueEl.textContent = fmt(Number(ctrl.value));
  return input;
}

/* Effects layout: label and value on one line, slider underneath. */
function buildKnob(ctrl) {
  const wrap = document.createElement('div');
  wrap.className = 'knob';

  const value = document.createElement('span');
  value.className = 'knob__value';

  const label = document.createElement('span');
  label.className = 'knob__label' + (ctrl.desc ? ' has-desc' : '');
  label.textContent = ctrl.name;
  label.title = ctrl.desc ? ctrl.desc + ' (' + ctrl.id + ')' : ctrl.id;

  const head = document.createElement('div');
  head.className = 'knob__head';
  head.appendChild(label);
  head.appendChild(value);

  wrap.appendChild(head);
  wrap.appendChild(makeRange(ctrl, value));
  return wrap;
}

/* EQ layout: a vertical fader in a column, bands sitting side by side. */
function buildBand(ctrl) {
  const band = document.createElement('div');
  band.className = 'band';

  const value = document.createElement('span');
  value.className = 'band__value';

  const fader = document.createElement('div');
  fader.className = 'band__fader';
  if (Number(ctrl.min) < 0 && Number(ctrl.max) > 0) fader.classList.add('is-bipolar');
  fader.appendChild(makeRange(ctrl, value));

  const label = document.createElement('span');
  label.className = 'band__label' + (ctrl.desc ? ' has-desc' : '');
  label.textContent = ctrl.name;
  label.title = ctrl.desc ? ctrl.desc + ' (' + ctrl.id + ')' : ctrl.id;

  band.appendChild(value);
  band.appendChild(fader);
  band.appendChild(label);
  return band;
}

/* Throttle to one message per animation frame per parameter. */
const outbox = {};
let frameQueued = false;

function send(id, value) {
  outbox[id] = value;
  if (frameQueued) return;
  frameQueued = true;
  requestAnimationFrame(function () {
    frameQueued = false;
    Object.keys(outbox).forEach(function (pid) {
      socket.emit('set_param', { id: pid, value: outbox[pid] });
      delete outbox[pid];
    });
  });
}

/* One way in for every parameter write. Continuous values (sliders) are
   coalesced through the outbox so a drag sends at most one message per
   animation frame; discrete values (toggles, enums) are one-shot gestures
   and go out immediately, since batching them would only add latency. */
function command(id, value, kind) {
  if (kind === 'discrete') {
    socket.emit('set_param', { id: id, value: value });
    return;
  }
  send(id, value);
}

function applyToggle(id, value) {
  const sw = switches[id];
  if (!sw) return;
  const on = Number(value) > 0;
  sw.classList.toggle('is-on', on);
  sw.setAttribute('aria-pressed', String(on));
  if (!sw.dataset.named) sw.lastElementChild.textContent = on ? 'on' : 'off';
  // only the group's own bypass switch dims the group
  if (sw.dataset.role === 'bypass') {
    const group = sw.closest('.group');
    if (group) {
      group.classList.toggle('is-bypassed', !on);
      // switched off means off: nothing inside is reachable, by mouse or keyboard
      if (group._inner) group._inner.inert = !on || group.classList.contains('is-folded');
    }
  }
}

/* ------------------------------------------------------------------ discrete */

const selects = {};          // param id -> <select>

/* Engines report an enum as either its index or its name. Match on either,
   and remember which, so a change goes back in the same form. If nothing
   matches, show the raw value rather than an empty box. */
function setChoice(sel, v) {
  const opts = sel._options || [];
  const s = String(v);
  let hit = opts.find(function (o) { return String(o.value) === s; });
  if (hit) {
    sel._kind = 'index';
  } else {
    hit = opts.find(function (o) { return o.key != null && String(o.key) === s; })
       || opts.find(function (o) { return o.label === s; });
    if (hit) sel._kind = 'key';
  }

  const stray = sel.querySelector ? sel.querySelector('option[data-stray]') : null;
  if (stray) stray.remove();

  if (hit) {
    sel.value = String(hit.value);
    return;
  }
  const opt = document.createElement('option');
  opt.value = '__stray__';
  opt.textContent = v == null || v === '' ? 'unknown' : s;
  opt.dataset.stray = '1';
  opt.disabled = true;
  sel.appendChild(opt);
  sel.value = '__stray__';
}

function isDiscrete(ctrl) {
  if (ctrl.options && ctrl.options.length) return true;
  if (/bool/i.test(ctrl.type || '')) return true;
  return Number(ctrl.min) === 0 && Number(ctrl.max) === 1 && Number(ctrl.step) >= 1;
}

function buildDiscrete(ctrl) {
  const tip = ctrl.desc ? ctrl.desc + ' (' + ctrl.id + ')' : ctrl.id;

  // A choice gets a labelled field: small caption over the dropdown.
  if (ctrl.options && ctrl.options.length) {
    const field = document.createElement('label');
    field.className = 'choice-field';

    const caption = document.createElement('span');
    caption.className = 'choice-field__label' + (ctrl.desc ? ' has-desc' : '');
    caption.textContent = ctrl.name;
    caption.title = tip;
    field.appendChild(caption);

    const sel = document.createElement('select');
    sel.className = 'choice';
    ctrl.options.forEach(function (o) {
      const opt = document.createElement('option');
      opt.value = String(o.value);
      opt.textContent = o.label;
      sel.appendChild(opt);
    });
    sel._options = ctrl.options;
    sel._kind = 'index';
    setChoice(sel, ctrl.value);
    sel.addEventListener('change', function () {
      const o = sel._options.find(function (x) { return String(x.value) === sel.value; });
      if (!o) return;
      // answer in the same terms the engine used: a name if it reported a
      // name, the index if it reported an index
      const value = sel._kind === 'key' && o.key != null ? o.key : o.value;
      command(ctrl.id, value, 'discrete');
    });
    selects[ctrl.id] = sel;
    bindControl(ctrl.id, {
      kind: 'select',
      el: sel,
      apply: function (value, remote) {
        const before = sel.value;
        setChoice(sel, value);
        if (remote && before !== sel.value) flash(sel, 'is-remote');
      }
    });
    field.appendChild(sel);
    return field;
  }

  // A switch carries its own name, lamp lit when on -- the same pattern as
  // the bypass switches, so the name can't drift away from the control.
  const sw = document.createElement('button');
  sw.type = 'button';
  sw.className = 'switch is-named' + (ctrl.desc ? ' has-desc' : '');
  sw.dataset.param = ctrl.id;
  sw.dataset.named = '1';
  sw.title = tip;
  sw.innerHTML = '<span class="lamp" aria-hidden="true"></span>'
               + '<span class="switch__name">' + escapeHtml(ctrl.name) + '</span>';
  sw.addEventListener('click', function () {
    const next = sw.classList.contains('is-on') ? 0 : 1;
    applyToggle(ctrl.id, next);
    command(ctrl.id, next, 'discrete');
  });
  switches[ctrl.id] = sw;
  bindControl(ctrl.id, {
    kind: 'switch',
    el: sw,
    apply: function (value, remote) {
      const was = sw.classList.contains('is-on');
      applyToggle(ctrl.id, value);
      if (remote && was !== sw.classList.contains('is-on')) flash(sw, 'is-remote');
    }
  });
  applyToggle(ctrl.id, ctrl.value);
  return sw;
}

function applyValues(values, remote) {
  Object.keys(values).forEach(function (id) {
    state.values[id] = values[id];

    // One lookup decides who owns this parameter: a switch or a choice answers
    // through its binding, and a fader keeps the path below, because it has to
    // know whether the user is still holding it.
    const bound = ControlRegistry.get(id);
    if (bound && bound.kind !== 'range') {
      bound.apply(values[id], remote);
      return;
    }

    const input = bound ? bound.el : sliders[id];
    if (!input || dragging.has(id)) return;   // don't yank a knob mid-turn

    const out = readouts[id];
    const target = Number(values[id]);
    // A change from elsewhere -- another device, a preset loading -- glides
    // there, so you can see what moved. Your own dragging is never animated.
    if (remote && String(input.value) !== String(target)) glide(input, target);
    else input.value = target;
    if (!out) return;
    out.textContent = (out._fmt || format)(target);
    // flash() restarts the highlight on every change, so a second update
    // just after the first isn't cut short by the first one's timer
    if (remote) flash(out, 'is-remote');
  });
}

const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

function glide(input, target) {
  if (input._glide) cancelAnimationFrame(input._glide);
  const from = Number(input.value);
  if (reducedMotion.matches || !isFinite(from)) { input.value = target; return; }
  const start = Date.now();
  const length = 180;
  const step = function () {
    if (dragging.has(input.dataset.param)) { input._glide = null; return; }   // you took hold of it
    const t = Math.min(1, (Date.now() - start) / length);
    const eased = 1 - Math.pow(1 - t, 3);
    input.value = from + (target - from) * eased;
    input._glide = t < 1 ? requestAnimationFrame(step) : null;
    if (t >= 1) input.value = target;
  };
  input._glide = requestAnimationFrame(step);
}

// A drag that ends outside the slider doesn't always send the slider its
// pointerup, which would leave it ignoring every remote update after.
document.addEventListener('pointerup', function () { dragging.clear(); });
document.addEventListener('pointercancel', function () { dragging.clear(); });


/* ------------------------------------------------------------------ actions */

/* Every action that waits on the server goes through run(). The button that
   started it goes busy -- which also stops a double click sending it twice --
   and settles with a short confirm or fail flash when the server reports
   back. The timeout is a backstop so a button can never stay stuck. */
let opSeq = 0;
const pending = {};

/* Where the page gets audio and files from. Normally the Pi; in demo mode the
   stand-in answers instead, and nothing is fetched from the Pi at all. */
function mediaUrl(kind, name) {
  if (window.GX_DEMO_MEDIA) return window.GX_DEMO_MEDIA(kind, name);
  if (kind === 'monitor') return '/monitor.mp3?t=' + Date.now();      // never a cached copy
  if (kind === 'download') return '/recordings/' + encodeURIComponent(name) + '?dl=1';
  return '/recordings/' + encodeURIComponent(name);
}

function run(event, payload, btn, then, timeoutMs) {
  const op = 'op' + (++opSeq);
  setBusy(btn, true);
  pending[op] = {
    btn: btn,
    then: then,
    timer: setTimeout(function () { finish(op, false); }, timeoutMs || 15000),
  };
  socket.emit(event, Object.assign({}, payload || {}, { op: op }));
  return op;
}

function finish(op, ok) {
  const p = pending[op];
  if (!p) return;
  clearTimeout(p.timer);
  delete pending[op];
  setBusy(p.btn, false);
  flash(p.btn, ok ? 'is-done' : 'is-failed');
  if (p.then) p.then(ok);
}

/* A question that expects an answer back. An acknowledgement never arrives
   if the connection drops mid-request, so this gives up after a while
   rather than leaving a button spinning. */
const waiting = new Set();      // unanswered request() calls

function request(event, payload, cb) {
  let answered = false;
  const answer = function (res) {
    if (answered) return;
    answered = true;
    clearTimeout(timer);
    waiting.delete(answer);
    cb(res);
  };
  const timer = setTimeout(function () {
    answer({ ok: false, error: 'The amp didn\'t answer. Check the connection and try again.' });
  }, 12000);
  waiting.add(answer);
  socket.emit(event, payload || {}, function (res) {
    answer(res || { ok: false, error: 'No answer came back.' });
  });
}

/* The link to the Pi dropped: nothing that was in flight can be answered
   now, so settle it straight away rather than leave buttons spinning until
   their timeouts. (If guitarix drops instead, the server is still here and
   fails those operations itself.) */
function abandonInFlight() {
  Object.keys(pending).forEach(function (op) { finish(op, false); });
  Array.from(waiting).forEach(function (answer) {
    answer({ ok: false, error: 'Lost the connection to the Pi before it answered.' });
  });
}

function setBusy(btn, busy) {
  if (!btn) return;
  btn.classList.toggle('is-busy', busy);
  btn.disabled = busy || btn.dataset.disabledByCaps === '1';
  if (busy) btn.setAttribute('aria-busy', 'true');
  else btn.removeAttribute('aria-busy');
}

/* Restart a one-shot CSS animation, even if it's already running. */
function flash(el, cls) {
  if (!el) return;
  el.classList.remove(cls);
  void el.offsetWidth;                    // reflow, so re-adding restarts it
  el.classList.add(cls);
  clearTimeout(el['_t_' + cls]);
  el['_t_' + cls] = setTimeout(function () { el.classList.remove(cls); }, 900);
}

/* ------------------------------------------------------------------ toolbar */

const saveBtn = document.getElementById('btn-save');

/* Saving over a preset is routine, so it asks once and moves on. Typing the
   name out is kept for deletes, which can't be undone by saving again. */
saveBtn.addEventListener('click', function () {
  if (!state.preset) {
    showToast('No preset is loaded, so there\'s nothing to save over. Use Save as.', 'error');
    return;
  }
  ask({
    confirm: true,
    title: 'Save over ' + displayName(state.preset) + '?',
    note: state.dirty
      ? 'Your changes replace what\'s stored in it.'
      : 'Nothing has changed since it loaded, so this saves it as it is.',
    ok: 'Save',
  }).then(function (r) {
    if (r) run('preset_save', {}, saveBtn);
  });
});

document.getElementById('btn-save-as').addEventListener('click', function () {
  ask({
    title: 'Save as new preset',
    label: 'Preset name',
    value: '',
    select: state.banks.map(function (b) { return b.name; }),
    selectValue: selectedBank,
    selectLabel: 'Bank',
    ok: 'Save',
  }).then(function (r) {
    if (r && r.value) {
      run('preset_save_as', { bank: r.select, name: r.value },
          document.getElementById('btn-save-as'));
    }
  });
});

const organiseBtn = document.getElementById('btn-organize');
const org = {
  bar:     document.getElementById('organise'),
  hint:    document.getElementById('organise-hint'),
  rename:  document.getElementById('org-rename'),
  del:     document.getElementById('org-delete'),
  newBank: document.getElementById('org-new-bank'),
  delBank: document.getElementById('org-del-bank'),
};

organiseBtn.addEventListener('click', function () {
  organising = !organising;
  if (!organising) picked = null;
  organiseBtn.classList.toggle('is-on', organising);
  organiseBtn.setAttribute('aria-pressed', String(organising));
  renderPresets();
  applyOrganise();
});

function applyOrganise() {
  org.bar.hidden = !organising;
  if (!organising) return;

  // a pick can vanish underneath us if another browser renames or deletes it
  if (picked) {
    const b = state.banks.find(function (x) { return x.name === picked.bank; });
    if (!b || b.presets.indexOf(picked.name) < 0) picked = null;
  }

  org.hint.textContent = picked
    ? displayName(picked.name) + ' in ' + picked.bank
    : 'Pick a preset to rename, move or delete it.';
  org.hint.classList.toggle('is-picked', !!picked);

  org.rename.disabled = !picked || !(can.rename || can.move);
  org.del.disabled = !picked || !can.delete;
  org.newBank.hidden = !can.new_bank;
  org.delBank.hidden = !can.delete_bank;
  org.delBank.disabled = !selectedBank;
}

org.rename.addEventListener('click', function () {
  if (!picked) return;
  const from = picked;
  ask({
    title: 'Rename or move',
    label: 'Name',
    value: from.name,
    select: state.banks.map(function (b) { return b.name; }),
    selectValue: from.bank,
    selectLabel: 'Bank',
    note: 'Changing the bank loads this preset first, so the amp will '
        + 'switch to it while it moves.',
    ok: 'Rename',
  }).then(function (r) {
    if (!r) return;
    if (r.select && r.select !== from.bank) {
      run('preset_move',
        { bank: from.bank, name: from.name, to_bank: r.select, new: r.value }, org.rename);
      picked = { bank: r.select, name: r.value };
    } else if (r.value !== from.name) {
      run('preset_rename', { bank: from.bank, old: from.name, new: r.value }, org.rename);
      picked = { bank: from.bank, name: r.value };
    }
  });
});

org.del.addEventListener('click', function () {
  if (!picked) return;
  const target = picked;
  ask({
    title: 'Delete ' + displayName(target.name) + '?',
    label: 'Type the name to confirm',
    value: '',
    note: 'Removes it from ' + target.bank + '. The name is ' + target.name + '.',
    ok: 'Delete',
    danger: true,
  }).then(function (r) {
    if (!r) return;
    if (r.value !== target.name && r.value !== displayName(target.name)) {
      showToast('Name did not match', 'error');
      return;
    }
    run('preset_delete', { bank: target.bank, name: target.name }, org.del);
    picked = null;
    applyOrganise();
  });
});

org.newBank.addEventListener('click', function () {
  ask({ title: 'New bank', label: 'Bank name', value: '', ok: 'Create' }).then(function (r) {
    if (r) run('bank_create', { name: r.value }, org.newBank);
  });
});

// Deleting a bank takes every preset in it, so the name has to be typed out.
org.delBank.addEventListener('click', function () {
  const count = (state.banks.find(function (b) { return b.name === selectedBank; })
                 || { presets: [] }).presets.length;
  ask({
    title: 'Delete bank ' + selectedBank + '?',
    label: 'Type the bank name to confirm',
    value: '',
    note: 'This removes the bank and the ' + count + ' preset'
        + (count === 1 ? '' : 's') + ' in it.',
    ok: 'Delete bank',
    danger: true,
  }).then(function (r) {
    if (!r) return;
    if (r.value !== selectedBank) { showToast('Name did not match', 'error'); return; }
    run('bank_delete', { name: selectedBank }, org.delBank);
  });
});

function capDisable(btn, off) {
  btn.dataset.disabledByCaps = off ? '1' : '';
  if (!btn.classList.contains('is-busy')) btn.disabled = off;
}

function applyCapabilities() {
  capDisable(saveBtn, !can.save_current);
  capDisable(document.getElementById('btn-save-as'), !can.save_as);
  capDisable(document.getElementById('btn-import'), !can.save_as);
  organiseBtn.disabled = !(can.rename || can.delete || can.new_bank || can.delete_bank);
  applyOrganise();
}

/* ------------------------------------------------------------------ recording */

let recState = { recording: false, elapsed: 0 };

(function () {
  try {
    els.dryCheck.checked = localStorage.getItem('gx-record-dry') === '1';
  } catch (e) { /* private browsing, or storage blocked -- default stays off */ }
})();
els.dryCheck.addEventListener('change', function () {
  try { localStorage.setItem('gx-record-dry', els.dryCheck.checked ? '1' : '0'); } catch (e) {}
});

els.recBtn.addEventListener('click', function () {
  // busy until the recorder reports its new state: a quick double tap would
  // otherwise start and immediately stop a take
  if (els.recBtn.classList.contains('is-busy')) return;
  setBusy(els.recBtn, true);
  clearTimeout(els.recBtn._busyTimer);
  els.recBtn._busyTimer = setTimeout(function () { setBusy(els.recBtn, false); }, 8000);
  if (recState.recording) { socket.emit('record_stop', {}); return; }
  socket.emit('record_start', { dry: els.dryCheck.checked });
});

function applyRec(status) {
  recState = status || {};
  clearTimeout(els.recBtn._busyTimer);
  setBusy(els.recBtn, false);
  const on = !!recState.recording;
  els.recBtn.classList.toggle('is-live', on);
  els.recBtn.setAttribute('aria-pressed', String(on));
  els.recLabel.textContent = on ? 'Stop' : 'Record';
  els.recClock.textContent = clock(recState.elapsed || 0);
  els.recDot.hidden = !on;
  if (els.recLamp) els.recLamp.hidden = !on;

  // a recorded reamp pass owns the recorder until it finishes
  const reampRecording = !!(recState.reamp && recState.reamp.record);
  els.recBtn.disabled = reampRecording;
  els.dryCheck.disabled = reampRecording || on;

  const problem = recState.error
    || (recState.available === false ? 'ffmpeg not found on the Pi' : '');
  els.recNote.textContent = problem || recState.wiring || '';
  els.recNote.classList.toggle('is-error', !!problem);

  applyReamp(recState.reamp);
  applyBacking(recState.backing);
  markTakeRows();
  renderRecHead();
  renderLive();
}


let knownTakes = null;          // names from the last render, to spot new ones

let lastTakes = [];
const selectedTakes = new Set();    // take names ticked for "Delete selected"
let takeRows = {};          // take name -> its row and the buttons whose state changes

function renderTakes(items) {
  takeRows = {};
  lastTakes = items || [];
  renderRecHead();
  renderLive();
  // A re-render -- another device renaming a take, say -- mustn't cut off
  // whatever is playing. Note it, and put it back afterwards.
  const openAudio = els.takes.querySelector ? els.takes.querySelector('.takes__player audio') : null;
  const resume = openAudio && openAudio.dataset ? {
    name: openAudio.dataset.take, time: openAudio.currentTime, paused: openAudio.paused,
  } : null;

  els.takes.innerHTML = '';
  const seen = knownTakes;
  knownTakes = {};
  if (!items || !items.length) {
    selectedTakes.clear();
    els.takes.innerHTML = '<p class="empty">No takes yet. Press Record and play.</p>';
    return;
  }

  // Bulk delete: tick takes, then "Delete selected". Ticks survive a re-render
  // (another device changing the list), but only for takes that still exist.
  const eligible = lastTakes.filter(function (t) { return !t.active; });
  Array.from(selectedTakes).forEach(function (n) {
    if (!eligible.some(function (t) { return t.name === n; })) selectedTakes.delete(n);
  });
  const boxes = [];
  const bulk = document.createElement('div');
  bulk.className = 'takes__bulk';
  const bulkCount = document.createElement('span');
  bulkCount.className = 'takes__bulk-count';
  const selectAll = mini('Select all', function () {
    const every = eligible.length > 0 && selectedTakes.size === eligible.length;
    boxes.forEach(function (b) {
      b.input.checked = !every;
      if (b.input.checked) selectedTakes.add(b.name); else selectedTakes.delete(b.name);
    });
    updateBulk();
  });
  const deleteSelected = mini('Delete selected', function () {
    const names = Array.from(selectedTakes);
    if (!names.length) return;
    const shown = names.slice(0, 5).join(', ') +
      (names.length > 5 ? ' and ' + (names.length - 5) + ' more' : '');
    ask({ title: 'Delete ' + plural(names.length, 'take', 'takes') + '?',
          label: 'Type DELETE to confirm', value: '', note: shown,
          ok: 'Delete ' + names.length, danger: true })
      .then(function (r) {
        if (!r) return;
        if (r.value.trim().toUpperCase() !== 'DELETE') {
          showToast('Not deleted: type DELETE to confirm.', 'error');
          return;
        }
        // the list pushed back by the server removes the rows (and the free-space
        // readout in the header updates with it)
        run('rec_delete_many', { names: names }, deleteSelected);
      });
  }, 'is-danger');
  function updateBulk() {
    bulk.hidden = !eligible.length;
    bulkCount.textContent = selectedTakes.size ? selectedTakes.size + ' selected' : '';
    deleteSelected.disabled = !selectedTakes.size;
    selectAll.textContent = eligible.length && selectedTakes.size === eligible.length
      ? 'Select none' : 'Select all';
  }
  function takeCheckbox(item) {
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.className = 'takes__check';
    input.checked = selectedTakes.has(item.name);
    input.setAttribute('aria-label', 'Select ' + item.name);
    input.addEventListener('change', function () {
      if (input.checked) selectedTakes.add(item.name); else selectedTakes.delete(item.name);
      updateBulk();
    });
    boxes.push({ name: item.name, input: input });
    return input;
  }
  bulk.appendChild(selectAll);
  bulk.appendChild(deleteSelected);
  bulk.appendChild(bulkCount);
  els.takes.appendChild(bulk);
  updateBulk();

  const table = document.createElement('table');
  table.className = 'takes__table';
  table.innerHTML =
    '<thead><tr><th>Take</th><th>Recorded</th><th>Length</th><th>Size</th><th></th></tr></thead>';

  const body = document.createElement('tbody');
  let restore = null;

  items.forEach(function (item) {
    const tr = document.createElement('tr');
    tr.className = (item.active ? 'is-live' : '') + (seen && !seen[item.name] ? ' is-new' : '');
    knownTakes[item.name] = true;

    const nameTd = takeNameCell(item);
    if (!item.active) nameTd.insertBefore(takeCheckbox(item), nameTd.firstChild);
    tr.appendChild(nameTd);
    tr.appendChild(cell(when(item.modified)));
    tr.appendChild(cell(item.active ? 'recording' : clock(item.duration)));
    tr.appendChild(cell(bytes(item.size)));

    const actions = document.createElement('td');
    actions.className = 'takes__actions is-spread';

    if (!item.active) {
      const moreBox = document.createElement('div');
      moreBox.className = 'takes__morebox';
      const play = mini('Play', function () { togglePlay(tr, item, play); });
      actions.appendChild(play);
      // "Download", not "Save": everywhere else in the app Save means
      // storing something in guitarix, and this puts the file on your device
      if (item.dry) {
        moreBox.appendChild(mini('Export\u2026', function () { openExport(item); }));
      }
      moreBox.appendChild(link('Download', mediaUrl('download', item.name)));
      let reampBtn = null;
      if (item.dry) {
        reampBtn = mini('Reamp', function () {
          run('reamp_start', { take: item.name, dry: item.dry, loop: true }, reampBtn);
        });
        actions.appendChild(reampBtn);
      }
      // a toggle, not a start button: pressing it again stops the loop
      const loopBtn = mini('Loop', function () {
        if (loopingNow() === item.name) run('backing_stop', {}, loopBtn);
        else run('backing_play', { name: item.name, kind: 'take', loop: true }, loopBtn);
      });
      actions.appendChild(loopBtn);
      actions.appendChild(mini('More', function () { toggleMore(tr, moreBox); }));
      const rename = mini('Rename', function () {
        ask({ title: 'Rename take', label: 'New name', ok: 'Rename',
              value: item.name.replace(/\.[^.]+$/, '') })
          .then(function (r) {
            if (r) run('rec_rename', { name: item.name, new: r.value }, rename);
          });
      });
      moreBox.appendChild(rename);
      const del = mini('Delete', function () {
        ask({ title: 'Delete this take?', label: 'Type DELETE to confirm', value: '',
              note: item.name, ok: 'Delete', danger: true })
          .then(function (r) {
            if (!r) return;
            if (r.value.trim().toUpperCase() !== 'DELETE') {
              showToast('Not deleted: type DELETE to confirm.', 'error');
              return;
            }
            // fade it straight away; the list from the server removes it
            tr.classList.add('is-leaving');
            run('rec_delete', { name: item.name }, del, function (ok) {
              if (!ok) tr.classList.remove('is-leaving');
            });
          });
      }, 'is-danger');
      moreBox.appendChild(del);
      takeRows[item.name] = { tr: tr, loop: loopBtn, reamp: reampBtn };
      if (resume && resume.name === item.name) restore = { tr: tr, item: item, btn: play };
    }

    tr.appendChild(actions);
    body.appendChild(tr);
  });

  table.appendChild(body);
  els.takes.appendChild(table);
  markTakeRows();

  if (restore) {
    const audio = togglePlay(restore.tr, restore.item, restore.btn, true);
    if (audio) {
      audio.currentTime = resume.time;
      if (!resume.paused) audio.play().catch(function () {});
    }
  }
}

function togglePlay(row, item, btn, quiet) {
  const existing = row.nextElementSibling;
  if (existing && existing.classList.contains('takes__player')) {
    existing.remove();
    if (btn) btn.textContent = 'Play';
    return null;
  }
  document.querySelectorAll('.takes__player').forEach(function (p) { p.remove(); });
  document.querySelectorAll('.takes__actions .mini').forEach(function (b) {
    if (b.textContent === 'Hide') b.textContent = 'Play';
  });

  const tr = document.createElement('tr');
  tr.className = 'takes__player';
  const td = document.createElement('td');
  td.colSpan = 5;
  const audio = document.createElement('audio');
  audio.controls = true;
  audio.preload = 'none';
  audio.src = mediaUrl('take', item.name);
  audio.dataset.take = item.name;
  td.appendChild(audio);
  tr.appendChild(td);
  row.after(tr);
  if (btn) btn.textContent = 'Hide';
  if (!quiet) audio.play().catch(function () { /* the controls are right there */ });
  return audio;
}

function takeNameCell(item) {
  const td = document.createElement('td');
  td.className = 'takes__name';

  const name = document.createElement('span');
  name.className = 'takes__name-text';
  name.textContent = item.name;
  td.appendChild(name);

  if (item.dry || item.settings) {
    const badges = document.createElement('span');
    badges.className = 'takes__badges';
    if (item.dry) {
      badges.appendChild(smallBadge('Dry', 'Also has the unprocessed signal, so it can be re-rendered'));
    }
    if (item.settings) {
      const b = smallBadge('Settings', 'What was live when this was recorded — tap to load it');
      b.classList.add('is-action');
      // Keyboard access: a span is not focusable or announced as a control, so
      // give it button semantics and Enter/Space activation.
      b.setAttribute('role', 'button');
      b.tabIndex = 0;
      b.setAttribute('aria-label', 'Load the settings recorded with ' + item.name);
      b.addEventListener('click', function () { loadTakeSettings(item.settings); });
      b.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ' || e.key === 'Spacebar') {
          e.preventDefault();
          loadTakeSettings(item.settings);
        }
      });
      badges.appendChild(b);
    }
    td.appendChild(badges);
  }
  return td;
}

function smallBadge(text, title) {
  const b = document.createElement('span');
  b.className = 'takes__badge';
  b.textContent = text;
  if (title) b.title = title;
  return b;
}

/* Pulls a take's settings sidecar into the importer, exactly as if it had
   been pasted in. From there it's the same reviewed, audited path as any
   other import -- nothing about a render applies settings on its own. */
function loadTakeSettings(filename) {
  fetch('/recordings/' + encodeURIComponent(filename))
    .then(function (r) { if (!r.ok) throw new Error(); return r.text(); })
    .then(function (text) {
      imp.text.value = text;
      openImporter();
      checkImport(true);
    })
    .catch(function () { showToast('Couldn\'t read that take\'s settings.', 'error'); });
}

function toggleMore(row, box) {
  const next = row.nextElementSibling;
  if (next && next.classList.contains('takes__more')) { next.remove(); return; }
  const tr = document.createElement('tr');
  tr.className = 'takes__more';
  const td = document.createElement('td');
  td.colSpan = 5;
  td.appendChild(box);
  tr.appendChild(td);
  row.after(tr);
}

function loopingNow() {
  const b = recState.backing || {};
  return b.playing && b.kind === 'take' ? b.name : null;
}

/* Rows show what they're doing, and offer only what would work. Updated in
   place rather than by redrawing, so a playing take isn't cut off. */
function markTakeRows() {
  const looping = loopingNow();
  const reamping = recState.reamp ? recState.reamp.take : null;
  const busy = !!(recState.reamp || recState.export);
  Object.keys(takeRows).forEach(function (name) {
    const r = takeRows[name];
    const isLooping = name === looping;
    const isReamping = name === reamping;
    r.tr.classList.toggle('is-looping', isLooping);
    r.tr.classList.toggle('is-reamping', isReamping);
    if (r.loop) {
      r.loop.textContent = isLooping ? 'Stop' : 'Loop';
      r.loop.classList.toggle('is-on', isLooping);
      r.loop.title = isLooping ? 'Stop looping this take'
                               : 'Play this take on repeat and play over it';
    }
    if (r.reamp) {
      const blocked = busy && !isReamping;
      r.reamp.disabled = blocked;
      r.reamp.classList.toggle('is-on', isReamping);
      r.reamp.title = isReamping ? 'This take is playing through the amp now'
        : blocked ? (recState.export ? 'An export is running' : 'Something else is already playing through the amp')
        : 'Play the dry recording through the amp, live — change anything while it plays';
    }
  });
}

/* ------------------------------------------------------------------ header */

function renderRecHead() {
  if (!els.recAttempt) return;
  els.recAttempt.textContent = 'Attempt ' + (recState.next_attempt || 1);
  const takes = lastTakes.filter(function (t) { return !t.active; });
  const seconds = takes.reduce(function (n, t) { return n + (t.duration || 0); }, 0);
  const size = takes.reduce(function (n, t) { return n + (t.size || 0); }, 0);
  const parts = [plural(takes.length, 'take', 'takes')];
  if (takes.length) parts.push(clock(seconds) + ' recorded', bytes(size));
  if (recState.free_bytes != null) parts.push(bytes(recState.free_bytes) + ' free');
  els.recStats.textContent = parts.join(' · ');
  // under a gigabyte left, say so: a take dies mid-song when the card fills
  els.recStats.classList.toggle('is-low', recState.free_bytes != null && recState.free_bytes < 1e9);
}

/* ------------------------------------------------------------------ reamp */

const rb = {
  amp: document.getElementById('reamp-amp'),
  pause: document.getElementById('reamp-pause'),
  loop: document.getElementById('reamp-loop'),
  record: document.getElementById('reamp-record'),
  stop: document.getElementById('reamp-stop'),
};
let reampState = null;

function applyReamp(r) {
  reampState = r || null;
  els.reampbar.hidden = !r;
  if (!r) return;
  const wet = r.mode !== 'dry';
  const ex = recState.export;
  rb.stop.textContent = ex ? 'Cancel' : 'Stop';
  if (ex) {
    els.reampText.innerHTML = 'Exporting <b>' + escapeHtml(displayName(ex.take.replace(/\.[^.]+$/, '')))
      + '</b> with ' + escapeHtml(ex.label) + ' as ' + ex.format.toUpperCase()
      + '. Your guitar and settings come back when it\'s done.';
    els.reampClock.textContent = clock(r.position || 0) + ' / ' + clock(r.duration || 0);
    els.reampProgress.style.width = (r.duration ? Math.min(100, 100 * (r.position || 0) / r.duration) : 0) + '%';
    // nothing but Cancel can act on an export, so nothing else is shown
    [rb.amp, rb.pause, rb.loop, rb.record].forEach(function (b) { b.hidden = true; });
    return;
  }
  [rb.amp, rb.pause, rb.loop, rb.record].forEach(function (b) { b.hidden = false; });
  els.reampText.innerHTML = (r.record ? 'Recording a pass of <b>' : 'Reamping <b>')
    + escapeHtml(displayName(r.take.replace(/\.[^.]+$/, ''))) + '</b>'
    + (wet ? ' through the amp.' : ', amp off.')
    + (r.record ? ' Saves as ' + escapeHtml(displayName((r.output || '').replace(/\.[^.]+$/, ''))) + ' when it ends.'
                : ' Change anything — you\'ll hear it straight away.');
  els.reampClock.textContent = clock(r.position || 0) + ' / ' + clock(r.duration || 0);
  const pct = r.duration ? Math.min(100, 100 * (r.position || 0) / r.duration) : 0;
  els.reampProgress.style.width = pct + '%';
  rb.amp.classList.toggle('is-on', wet);
  rb.amp.setAttribute('aria-pressed', String(wet));
  rb.pause.textContent = r.paused ? 'Resume' : 'Pause';
  rb.loop.classList.toggle('is-on', !!r.loop);
  rb.loop.setAttribute('aria-pressed', String(!!r.loop));
  // a recorded pass has to run start to finish, untouched
  [rb.amp, rb.pause, rb.loop, rb.record].forEach(function (b) { b.disabled = !!r.record; });
}

rb.amp.addEventListener('click', function () {
  if (!reampState) return;
  run('reamp_mode', { mode: reampState.mode === 'dry' ? 'wet' : 'dry' }, rb.amp);
});
rb.pause.addEventListener('click', function () {
  if (reampState) socket.emit('reamp_pause', { paused: !reampState.paused });
});
rb.loop.addEventListener('click', function () {
  if (reampState) socket.emit('reamp_loop', { loop: !reampState.loop });
});
rb.stop.addEventListener('click', function () {
  if (recState.export) { socket.emit('export_cancel', {}); return; }
  run('reamp_stop', {}, rb.stop);
});
rb.record.addEventListener('click', function () {
  if (!reampState) return;
  const take = lastTakes.find(function (t) { return t.name === reampState.take; });
  if (!take || !take.dry) return;
  ask({
    confirm: true, title: 'Record a pass of ' + displayName(take.name) + '?',
    note: 'Starts again from the top and records it through what\'s dialed in now, as '
        + displayName(take.name.replace(/\.[^.]+$/, '')) + ' (render). It runs to the end: '
        + 'the amp switch, pause and loop are locked until it finishes.',
    ok: 'Record it',
  }).then(function (r) {
    if (!r) return;
    run('reamp_stop', {}, null, function () {
      run('reamp_start', { take: take.name, dry: take.dry, record: true }, rb.record, null,
          Math.max(60000, (take.duration || 0) * 1000 + 30000));
    });
  });
});

/* ------------------------------------------------------------------ export */

const exp = {
  dlg: document.getElementById('export-dlg'),
  form: document.getElementById('export-form'),
  title: document.getElementById('export-title'),
  source: document.getElementById('export-source'),
  format: document.getElementById('export-format'),
  note: document.getElementById('export-note'),
  item: null,
};

function openExport(item) {
  exp.item = item;
  exp.title.textContent = 'Export ' + displayName(item.name.replace(/\.[^.]+$/, ''));
  exp.source.innerHTML = '';
  const add = function (parent, value, text) {
    const o = document.createElement('option');
    o.value = value; o.textContent = text;
    parent.appendChild(o);
  };
  add(exp.source, 'live', 'As it sounds now');
  if (item.settings) add(exp.source, 'recorded', 'As it was recorded');
  state.banks.forEach(function (b) {
    if (!b.presets.length) return;
    const g = document.createElement('optgroup');
    g.label = b.name;
    b.presets.forEach(function (p) { add(g, 'preset:' + b.name + '/' + p, displayName(p)); });
    exp.source.appendChild(g);
  });
  exp.note.textContent = 'It plays through the amp in real time'
    + (item.duration ? ', about ' + clock(item.duration) : '')
    + ', and your guitar is disconnected until it\'s done. Whatever you have dialed in now is '
    + 'put back afterwards, unsaved tweaks included.';
  exp.dlg.showModal();
}

document.getElementById('export-close').addEventListener('click', function () { exp.dlg.close(); });
exp.form.addEventListener('submit', function (e) {
  e.preventDefault();
  const item = exp.item;
  exp.dlg.close();
  if (!item) return;
  run('export_start', { take: item.name, dry: item.dry, source: exp.source.value,
                        format: exp.format.value }, null, null,
      Math.max(60000, (item.duration || 0) * 1000 + 60000));
});

/* ------------------------------------------------------------------ listen */

/* The rig through this page: an MP3 stream in a plain <audio> element, so it
   works over http and keeps playing with the screen locked. The browser
   buffers a second or two; if it drifts further behind than that, jump
   forward to the live edge rather than let the delay grow. If the stream
   drops -- the Pi restarting, wifi blinking -- try again a few times. */
const listen = {
  btn: document.getElementById('btn-listen'),
  label: document.getElementById('listen-label'),
  audio: null, wanted: false, retries: 0, timer: null,
};

function listenState(s) {
  listen.btn.classList.toggle('is-on', s === 'on');
  listen.btn.classList.toggle('is-connecting', s === 'connecting');
  listen.btn.setAttribute('aria-pressed', String(s !== 'off'));
  listen.label.textContent = s === 'off' ? 'Listen' : s === 'connecting' ? 'Connecting' : 'Listening';
  renderLive();
}

function startListening() {
  stopAudio();
  const a = new Audio();
  a.preload = 'none';
  a.src = mediaUrl('monitor');
  a.loop = !!window.GX_DEMO;                        // the demo clip goes round
  listen.audio = a;
  listenState('connecting');
  a.addEventListener('playing', function () { listen.retries = 0; listenState('on'); });
  a.addEventListener('waiting', function () { if (listen.wanted) listenState('connecting'); });
  const dropped = function () {
    if (!listen.wanted || listen.audio !== a) return;
    if (listen.retries++ < 5) {
      listenState('connecting');
      setTimeout(function () { if (listen.wanted) startListening(); }, 1500 * listen.retries);
    } else {
      stopListening();
      showToast('Lost the audio from the Pi. Press Listen to try again.', 'error');
    }
  };
  a.addEventListener('error', dropped);
  a.addEventListener('ended', dropped);
  a.play().catch(function () {
    if (listen.audio !== a) return;
    stopListening();
    showToast('This browser wouldn\'t start the audio. Press Listen again.', 'error');
  });
  clearInterval(listen.timer);
  listen.timer = setInterval(function () {
    const b = a.buffered;
    if (!b || !b.length || a.paused) return;
    const edge = b.end(b.length - 1);
    if (edge - a.currentTime > 3) a.currentTime = edge - 0.5;
  }, 2000);
}

function stopAudio() {
  clearInterval(listen.timer);
  const a = listen.audio;
  listen.audio = null;
  if (a) {
    a.pause();
    a.removeAttribute('src');
    a.load();                   // closes the connection, so the Pi stops encoding
  }
}

function stopListening() {
  listen.wanted = false;
  stopAudio();
  listenState('off');
}

function toggleListen() {
  if (listen.wanted) { stopListening(); return; }
  listen.wanted = true;
  listen.retries = 0;
  startListening();
}
listen.btn.addEventListener('click', toggleListen);

/* ------------------------------------------------------------------ demo */

(function () {
  const btn = document.getElementById('btn-demo');
  const bar = document.getElementById('demo-bar');
  const exit = document.getElementById('demo-exit');
  if (window.GX_DEMO) {
    if (bar) bar.hidden = false;
    if (btn) btn.hidden = true;
    // a demo-only server has no real mode to go back to
    if (exit) exit.hidden = !!window.GX_DEMO_ONLY;
    if (exit) exit.addEventListener('click', function () { location.href = location.pathname; });
  } else if (btn) {
    btn.addEventListener('click', function () { location.href = location.pathname + '?demo'; });
  }
})();

/* ------------------------------------------------------------------ live */

/* Live mode: big targets for playing. It keeps no state of its own -- it
   draws from the same state as the rest of the page, and every render point
   (preset, takes, backing, recorder, link) calls renderLive() -- so it can't
   drift out of step with what the amp is actually doing. */
const lv = {
  root: document.getElementById('live'),
  bank: document.getElementById('live-bank'),
  stateLine: document.getElementById('live-state'),
  preset: document.getElementById('live-preset'),
  name: document.getElementById('live-name'),
  grid: document.getElementById('live-grid'),
  rec: document.getElementById('live-rec'),
  recLabel: document.getElementById('live-rec-label'),
  recSub: document.getElementById('live-rec-sub'),
  loop: document.getElementById('live-loop'),
  loopSub: document.getElementById('live-loop-sub'),
  backing: document.getElementById('live-backing'),
  backingSub: document.getElementById('live-backing-sub'),
  listen: document.getElementById('live-listen'),
  listenSub: document.getElementById('live-listen-sub'),
  pick: document.getElementById('live-backing-pick'),
  on: false, gridSig: '', pickSig: '', bankSig: '', wake: null,
};

let livePick = null;        // a bank you're browsing in Live, if not the one playing

function liveBank() {
  const name = livePick || state.bank || selectedBank;
  return state.banks.find(function (b) { return b.name === name; })
      || state.banks.find(function (b) { return b.name === (state.bank || selectedBank); })
      || null;
}

function newestTake() {
  return lastTakes.find(function (t) { return !t.active; }) || null;
}

function renderLive() {
  if (!lv.root) return;
  if (!lv.on) return;
  const bank = liveBank();

  // the picker only redraws when the banks change, so it can be used mid-song
  const bankSig = state.banks.map(function (b) { return b.name; }).join('|');
  if (bankSig !== lv.bankSig) {
    lv.bankSig = bankSig;
    lv.bank.innerHTML = '';
    state.banks.forEach(function (b) {
      const o = document.createElement('option');
      o.value = b.name;
      o.textContent = b.name;
      lv.bank.appendChild(o);
    });
  }
  if (bank && lv.bank.value !== bank.name) lv.bank.value = bank.name;
  lv.bank.classList.toggle('is-browsing', !!bank && !!state.bank && bank.name !== state.bank);
  lv.name.textContent = state.preset ? displayName(state.preset) : 'Pick a preset';
  lv.stateLine.textContent = !linked ? 'Reconnecting' : !online ? 'No connection' : '';

  // tiles only rebuild when the bank's list changes, not on every tick
  const sig = bank ? bank.name + '|' + bank.presets.join('|') : '';
  if (sig !== lv.gridSig) {
    lv.gridSig = sig;
    lv.grid.innerHTML = '';
    (bank ? bank.presets : []).forEach(function (p) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'live__tile';
      b.dataset.preset = p;
      b.textContent = displayName(p);
      b.addEventListener('click', function () { loadLive(bank.name, p); });
      lv.grid.appendChild(b);
    });
  }
  walkTiles(function (t) {
    t.classList.toggle('is-active', !!bank && state.bank === bank.name && state.preset === t.dataset.preset);
  });

  // record
  const reampRecording = !!(recState.reamp && recState.reamp.record);
  lv.rec.classList.toggle('is-live', !!recState.recording && !reampRecording);
  lv.rec.disabled = reampRecording;
  lv.recLabel.textContent = recState.recording && !reampRecording ? 'Stop' : 'Record';
  lv.recSub.textContent = recState.recording && !reampRecording
    ? clock(recState.elapsed || 0)
    : 'Attempt ' + (recState.next_attempt || 1) + (els.dryCheck.checked ? ' + dry' : '');

  // loop the newest take / play a backing track
  const b = recState.backing || {};
  const looping = !!(b.playing && b.kind === 'take');
  const backingOn = !!(b.playing && b.kind === 'backing');
  const take = newestTake();
  lv.loop.classList.toggle('is-on', looping);
  lv.loop.disabled = !take && !looping;
  lv.loopSub.textContent = looping ? displayName((b.name || '').replace(/\.[^.]+$/, ''))
    : take ? displayName(take.name.replace(/\.[^.]+$/, '')) : 'No takes yet';
  lv.backing.classList.toggle('is-on', backingOn);

  const tracks = lastBackingItems || [];
  const psig = tracks.map(function (t) { return t.name; }).join('|');
  if (psig !== lv.pickSig) {
    lv.pickSig = psig;
    const keep = lv.pick.value;
    lv.pick.innerHTML = '';
    tracks.forEach(function (t) {
      const o = document.createElement('option');
      o.value = t.name;
      o.textContent = t.name.replace(/\.[^.]+$/, '');
      lv.pick.appendChild(o);
    });
    if (keep) lv.pick.value = keep;
  }
  lv.pick.hidden = !tracks.length;
  lv.backing.disabled = !tracks.length && !backingOn;
  lv.backingSub.textContent = backingOn ? displayName((b.name || '').replace(/\.[^.]+$/, ''))
    : tracks.length ? (lv.pick.value || tracks[0].name).replace(/\.[^.]+$/, '') : 'None added';

  // listen mirrors the header button
  lv.listen.classList.toggle('is-on', listen.btn.classList.contains('is-on'));
  lv.listen.classList.toggle('is-connecting', listen.btn.classList.contains('is-connecting'));
  lv.listenSub.textContent = listen.label.textContent === 'Listen' ? 'Off' : listen.label.textContent;
}

function walkTiles(fn) {
  Array.prototype.forEach.call(lv.grid.children || [], fn);
}

function loadLive(bank, preset) {
  if (!online || !bank || !preset) return;
  livePick = null;                   // you've committed to this bank
  socket.emit('set_preset', { bank: bank, preset: preset });
  state.bank = bank;
  state.preset = preset;                 // optimistic: the name changes under your foot
  renderLive();
}

function stepPreset(dir) {
  const bank = liveBank();
  if (!bank || !bank.presets.length) return;
  const n = bank.presets.length;
  const i = bank.presets.indexOf(state.preset);
  // nothing from this bank loaded yet: forward starts at the first, back at the last
  const next = i < 0 ? (dir > 0 ? 0 : n - 1) : (i + dir + n) % n;
  loadLive(bank.name, bank.presets[next]);
}

function liveRecord() {
  if (lv.rec.disabled) return;
  if (recState.recording) socket.emit('record_stop', {});
  else socket.emit('record_start', { dry: els.dryCheck.checked });
}

function liveLoop() {
  const b = recState.backing || {};
  if (b.playing && b.kind === 'take') { socket.emit('backing_stop', {}); return; }
  const take = newestTake();
  if (take) socket.emit('backing_play', { name: take.name, kind: 'take', loop: true });
}

function liveBacking() {
  const b = recState.backing || {};
  if (b.playing && b.kind === 'backing') { socket.emit('backing_stop', {}); return; }
  const name = lv.pick.value || ((lastBackingItems || [])[0] || {}).name;
  if (name) socket.emit('backing_play', { name: name, loop: true,
                                          volume: Number(els.backingVolume.value) || 80 });
}

function setLive(on) {
  lv.on = !!on;
  lv.root.hidden = !lv.on;
  document.body.classList.toggle('is-live-mode', lv.on);
  try { localStorage.setItem('gx-live', lv.on ? '1' : '0'); } catch (e) {}
  if (lv.on) {
    lv.gridSig = '';
    renderLive();
    keepAwake();
  } else if (lv.wake) {
    lv.wake.release().catch(function () {});
    lv.wake = null;
  }
}

/* Keep the screen on while playing. Browsers only allow this on HTTPS; over
   plain http it quietly does nothing, and the phone's own sleep setting wins. */
function keepAwake() {
  if (!lv.on || !navigator.wakeLock || !window.isSecureContext) return;
  navigator.wakeLock.request('screen').then(function (lock) { lv.wake = lock; })
    .catch(function () {});
}
document.addEventListener('visibilitychange', function () {
  if (document.visibilityState === 'visible') keepAwake();
});

document.getElementById('btn-live').addEventListener('click', function () { setLive(true); });
document.getElementById('live-exit').addEventListener('click', function () { setLive(false); });
document.getElementById('live-prev').addEventListener('click', function () { stepPreset(-1); });
document.getElementById('live-next').addEventListener('click', function () { stepPreset(1); });
lv.rec.addEventListener('click', liveRecord);
lv.loop.addEventListener('click', liveLoop);
lv.backing.addEventListener('click', liveBacking);
lv.listen.addEventListener('click', toggleListen);
lv.pick.addEventListener('change', renderLive);
lv.bank.addEventListener('change', function () {
  livePick = lv.bank.value;          // browse another bank without loading from it
  lv.gridSig = '';
  renderLive();
});

/* Foot pedals: Bluetooth page turners send arrow or page keys. Held keys
   repeat, so a repeat is ignored -- one press, one action. */
document.addEventListener('keydown', function (e) {
  if (!lv.on || e.repeat || e.metaKey || e.ctrlKey || e.altKey) return;
  const t = e.target || {};
  if (/^(INPUT|SELECT|TEXTAREA)$/.test(t.tagName || '')) return;
  if (document.querySelector && document.querySelector('dialog[open]')) return;
  const k = e.key;
  let handled = true;
  if (k === 'ArrowLeft' || k === 'PageUp' || k === 'ArrowUp') stepPreset(-1);
  else if (k === 'ArrowRight' || k === 'PageDown' || k === 'ArrowDown') stepPreset(1);
  else if (k === ' ' || k === 'Enter') liveRecord();
  else if (k === 'b' || k === 'B') liveBacking();
  else if (k === 'l' || k === 'L') liveLoop();
  else if (k === 'Escape') setLive(false);
  else handled = false;
  if (handled && e.preventDefault) e.preventDefault();
});

try { if (localStorage.getItem('gx-live') === '1') setTimeout(function () { setLive(true); }, 0); } catch (e) {}

/* ------------------------------------------------------------------ backing */

let backingState = null;

function renderBacking(items) {
  const list = els.backingList;
  if (!list) return;
  list.innerHTML = '';
  if (!items || !items.length) {
    list.innerHTML = '<li class="empty">No backing tracks yet. Add some — mp3, wav and flac all work.</li>';
    return;
  }
  items.forEach(function (item) {
    const li = document.createElement('li');
    li.className = 'backing__item';
    const playingThis = backingState && backingState.playing && backingState.kind === 'backing'
                        && backingState.name === item.name;
    if (playingThis) li.classList.add('is-playing');

    const name = document.createElement('span');
    name.className = 'backing__item-name';
    name.textContent = item.name.replace(/\.[^.]+$/, '');
    li.appendChild(name);

    const len = document.createElement('span');
    len.className = 'backing__item-len';
    len.textContent = clock(item.duration);
    li.appendChild(len);

    const playBtn = mini(playingThis ? 'Stop' : 'Play', function () {
      if (playingThis) run('backing_stop', {}, playBtn);
      else run('backing_play', { name: item.name, volume: Number(els.backingVolume.value) || 80,
                                 loop: true }, playBtn);
    });
    li.appendChild(playBtn);
    const del = mini('Delete', function () {
      ask({ confirm: true, title: 'Delete ' + item.name + '?', ok: 'Delete', danger: true })
        .then(function (r) { if (r) run('backing_delete', { name: item.name }, del); });
    }, 'is-danger');
    li.appendChild(del);
    list.appendChild(li);
  });
}

function applyBacking(b) {
  const was = backingState && backingState.playing && backingState.name;
  backingState = b || null;
  const on = !!(b && b.playing);
  els.backingNow.hidden = !on;
  if (els.backingInclude && b) els.backingInclude.checked = !!b.include;
  if (on) {
    els.backingName.textContent = (b.kind === 'take' ? 'Looping ' : '')
      + displayName((b.name || '').replace(/\.[^.]+$/, ''));
    els.backingClock.textContent = clock(b.position || 0) + ' / ' + clock(b.duration || 0);
    if (document.activeElement !== els.backingVolume) els.backingVolume.value = b.volume;
    document.getElementById('backing-pause').textContent = b.paused ? 'Resume' : 'Pause';
    const loop = document.getElementById('backing-loop');
    loop.classList.toggle('is-on', !!b.loop);
    loop.setAttribute('aria-pressed', String(!!b.loop));
  }
  // the list marks what's playing, so redraw it when that changes
  if (was !== (on && b.name) && lastBackingItems) renderBacking(lastBackingItems);
}

let lastBackingItems = null;

els.backingFile.addEventListener('change', function () {
  const files = Array.from(els.backingFile.files || []);
  if (!files.length) return;
  const add = document.getElementById('backing-add');
  const form = new FormData();
  files.forEach(function (f) { form.append('file', f, f.name); });
  setBusy(add, true);
  fetch('/backing', { method: 'POST', body: form })
    .then(function (r) { return r.json().catch(function () { return { ok: false, error: 'Upload failed.' }; }); })
    .then(function (res) {
      setBusy(add, false);
      flash(add, res.ok ? 'is-done' : 'is-failed');
      showToast(res.ok ? 'Added ' + res.saved.join(', ') : res.error, res.ok ? 'ok' : 'error');
    })
    .catch(function () {
      setBusy(add, false);
      flash(add, 'is-failed');
      showToast('Upload failed. Is the Pi still reachable?', 'error');
    });
  els.backingFile.value = '';          // picking the same file again should still upload
});

// volume while dragging: at most one message per frame
let volFrame = false;
els.backingVolume.addEventListener('input', function () {
  if (volFrame) return;
  volFrame = true;
  requestAnimationFrame(function () {
    volFrame = false;
    socket.emit('backing_volume', { volume: Number(els.backingVolume.value) });
  });
});
document.getElementById('backing-loop').addEventListener('click', function () {
  if (backingState) socket.emit('backing_loop', { loop: !backingState.loop });
});
document.getElementById('backing-pause').addEventListener('click', function () {
  if (backingState) socket.emit('backing_pause', { paused: !backingState.paused });
});
document.getElementById('backing-stop').addEventListener('click', function () {
  run('backing_stop', {}, document.getElementById('backing-stop'));
});
els.backingInclude.addEventListener('change', function () {
  socket.emit('backing_include', { include: els.backingInclude.checked });
});

function cell(text, cls) {
  const td = document.createElement('td');
  if (cls) td.className = cls;
  td.textContent = text;
  return td;
}

function mini(text, fn, cls) {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = 'mini' + (cls ? ' ' + cls : '');
  b.textContent = text;
  b.addEventListener('click', fn);
  return b;
}

function link(text, href) {
  const a = document.createElement('a');
  a.className = 'mini';
  a.textContent = text;
  a.href = href;
  a.setAttribute('download', '');
  return a;
}

/* ------------------------------------------------------------------ import */

const imp = {
  dlg:     document.getElementById('importer'),
  source:  document.getElementById('imp-source'),
  review:  document.getElementById('imp-review'),
  text:    document.getElementById('imp-text'),
  file:    document.getElementById('imp-file'),
  error:   document.getElementById('imp-error'),
  check:   document.getElementById('imp-check'),
  bank:    document.getElementById('imp-bank'),
  banks:   document.getElementById('imp-banks'),
  bankNote: document.getElementById('imp-bank-note'),
  othersOff: document.getElementById('imp-others-off'),
  list:    document.getElementById('imp-list'),
  back:    document.getElementById('imp-back'),
  saveAll: document.getElementById('imp-save-all'),
  close:   document.getElementById('imp-close'),
  exportBtn: document.getElementById('imp-export'),
  params:  document.getElementById('imp-params'),
};

// the file being worked on, and what the amp said about it
let lastImport = null;      // { text, res, saved: {name: true} }

function openImporter() {
  if (imp.dlg.open) return;
  showImportStep(lastImport ? 'review' : 'source');
  imp.dlg.showModal();
  if (!lastImport) setTimeout(function () { imp.text.focus(); }, 30);
}

function showImportStep(step) {
  imp.source.hidden = step !== 'source';
  imp.review.hidden = step !== 'review';
  imp.error.hidden = true;
}

function importError(text) {
  imp.error.textContent = text;
  imp.error.hidden = false;
  flash(imp.error, 'is-changed');
}

document.getElementById('btn-import').addEventListener('click', openImporter);
imp.close.addEventListener('click', function () { imp.dlg.close(); });
imp.back.addEventListener('click', function () { showImportStep('source'); });

imp.file.addEventListener('change', function () {
  const f = imp.file.files && imp.file.files[0];
  imp.file.value = '';                 // choosing the same file again still fires
  if (!f) return;
  f.text().then(function (text) {
    imp.text.value = text;
    checkImport(true);
  }, function () {
    importError('That file couldn\'t be read.');
  });
});

imp.check.addEventListener('click', function () { checkImport(true); });

// Changing the bank or the switch-off option changes what the report says --
// whether a save replaces something, what gets switched off -- so re-check.
let recheckTimer = null;
imp.bank.addEventListener('input', function () {
  clearTimeout(recheckTimer);
  recheckTimer = setTimeout(function () { checkImport(false); }, 350);
});
imp.othersOff.addEventListener('change', function () { checkImport(false); });

function checkImport(fromSource) {
  const text = fromSource ? imp.text.value : (lastImport && lastImport.text);
  if (!text || !text.trim()) {
    importError('Paste a presets file first, or choose one.');
    return;
  }
  const payload = { text: text };
  if (!fromSource) {
    payload.bank = imp.bank.value.trim();
    payload.others_off = imp.othersOff.checked;
  }
  const btn = fromSource ? imp.check : null;
  setBusy(btn, true);
  request('import_check', payload, function (res) {
    setBusy(btn, false);
    if (!res.ok) {
      if (fromSource) { importError(res.error); flash(imp.check, 'is-failed'); }
      else showToast(res.error, 'error');
      return;
    }
    const saved = (fromSource || !lastImport) ? {} : lastImport.saved;
    lastImport = { text: text, res: res, saved: saved };
    if (fromSource) {
      imp.bank.value = res.bank;
      imp.othersOff.checked = !!res.others_off;
    }
    renderImport();
    showImportStep('review');
  });
}

function importPayload(extra) {
  return Object.assign({
    text: lastImport.text,
    bank: imp.bank.value.trim(),
    others_off: imp.othersOff.checked,
  }, extra || {});
}

function listWords(items) {
  if (items.length <= 1) return items.join('');
  return items.slice(0, -1).join(', ') + ' and ' + items[items.length - 1];
}

function plural(n, one, many) { return n + ' ' + (n === 1 ? one : many); }

function renderImport() {
  const res = lastImport.res;
  const presets = res.presets;
  const bank = imp.bank.value.trim() || res.bank;

  imp.banks.innerHTML = '';
  state.banks.forEach(function (b) {
    const o = document.createElement('option');
    o.value = b.name;
    imp.banks.appendChild(o);
  });

  const exists = presets.length && presets[0].bank_exists;
  imp.bankNote.hidden = !!exists;
  imp.bankNote.textContent = exists ? '' : (res.can_create_bank
    ? 'There\'s no bank called ' + bank + ' yet. Saving creates it.'
    : 'There\'s no bank called ' + bank + ', and this amp can\'t create banks from here. Choose one that exists.');
  imp.bankNote.classList.toggle('is-bad', !exists && !res.can_create_bank);

  imp.list.innerHTML = '';
  presets.forEach(function (p) { imp.list.appendChild(importCard(p, bank)); });

  const usable = presets.filter(usablePreset);
  const blocked = !exists && !res.can_create_bank;
  imp.saveAll.hidden = presets.length < 2;
  imp.saveAll.textContent = 'Save all ' + usable.length;
  imp.saveAll.dataset.disabledByCaps = (!usable.length || blocked) ? '1' : '';
  if (!imp.saveAll.classList.contains('is-busy')) imp.saveAll.disabled = !usable.length || blocked;
}

/* Importable: something in it matched this amp, and its base preset exists.
   A missing base isn't a detail -- without it the preset would land on
   whatever happens to be playing, which is exactly what a base prevents. */
function usablePreset(p) {
  return p.set > 0 && !p.base_error;
}

function importCard(p, bank) {
  const li = document.createElement('li');
  li.className = 'imp-card' + (usablePreset(p) ? '' : ' is-empty')
               + (lastImport.saved[p.name] ? ' is-saved' : '');

  const head = document.createElement('div');
  head.className = 'imp-card__head';
  const name = document.createElement('span');
  name.className = 'imp-card__name';
  name.textContent = displayName(p.name);
  head.appendChild(name);
  if (lastImport.saved[p.name]) {
    head.appendChild(badge('Saved', 'is-ok'));
  } else if (p.replaces) {
    head.appendChild(badge('Replaces the one in ' + bank, 'is-warn'));
  }
  li.appendChild(head);

  if (p.notes) {
    const notes = document.createElement('p');
    notes.className = 'imp-card__notes';
    notes.textContent = p.notes;
    li.appendChild(notes);
  }

  const meta = document.createElement('p');
  meta.className = 'imp-card__meta';
  if (p.base_error) {
    meta.textContent = p.base_error + ' It can\'t be imported until it names a preset you have.';
  } else if (!p.set) {
    meta.textContent = 'None of these settings match this amp, so there\'s nothing to import.';
  } else {
    const parts = [];
    if (p.base) parts.push('Starts from ' + p.base + '.');
    parts.push('Sets ' + plural(p.set, 'setting', 'settings') + '.');
    if (p.switched_on.length) parts.push('Turns on ' + listWords(p.switched_on) + '.');
    if (p.switched_off.length) {
      parts.push(p.switched_off.length > 3
        ? 'Turns off ' + p.switched_off.length + ' other units.'
        : 'Turns off ' + listWords(p.switched_off) + '.');
    }
    if (!p.base && p.reset) parts.push('Puts ' + p.reset + ' more back to ' + (p.reset === 1 ? 'its default.' : 'their defaults.'));
    if (!p.base) parts.push('No base preset, so anything it doesn\'t set carries over from what\'s playing.');
    if (p.inherited) parts.push('Leaves ' + p.inherited + ' as ' + (p.inherited === 1 ? 'it is' : 'they are')
                                + ', since the amp doesn\'t say what the default is.');
    meta.textContent = parts.join(' ');
  }
  li.appendChild(meta);

  if (p.rejected.length || p.clamped.length) {
    const issues = document.createElement('ul');
    issues.className = 'imp-card__issues';
    p.rejected.forEach(function (r) {
      const item = document.createElement('li');
      item.className = 'is-bad';
      item.innerHTML = '<code>' + escapeHtml(r.id) + '</code> ' + escapeHtml(r.reason)
        + (r.suggestion ? '. Did you mean <code>' + escapeHtml(r.suggestion) + '</code>?' : '.');
      issues.appendChild(item);
    });
    p.clamped.forEach(function (c) {
      const item = document.createElement('li');
      item.className = 'is-warn';
      item.innerHTML = '<code>' + escapeHtml(c.id) + '</code> was ' + escapeHtml(String(c.given))
        + ', outside ' + escapeHtml(String(c.range[0])) + ' to ' + escapeHtml(String(c.range[1]))
        + ', so it uses ' + escapeHtml(String(c.used)) + '.';
      issues.appendChild(item);
    });
    li.appendChild(issues);
  }

  const actions = document.createElement('div');
  actions.className = 'imp-card__actions';
  const audition = document.createElement('button');
  audition.type = 'button';
  audition.className = 'act';
  audition.textContent = 'Audition';
  audition.disabled = !usablePreset(p);
  audition.addEventListener('click', function () {
    run('import_audition', importPayload({ index: p.index }), audition, function (ok) {
      if (ok) imp.dlg.close();       // the audition bar takes it from here
    });
  });
  const save = document.createElement('button');
  save.type = 'button';
  save.className = 'act';
  save.textContent = 'Save';
  save.disabled = !usablePreset(p) || !!lastImport.saved[p.name];
  save.addEventListener('click', function () {
    const go = function () {
      run('import_save', importPayload({ index: p.index }), save, function (ok) {
        if (!ok) return;
        lastImport.saved[p.name] = true;
        checkImport(false);          // the bank changed; refresh what replaces what
      });
    };
    if (!p.replaces) return go();
    ask({ confirm: true, title: 'Replace ' + displayName(p.name) + ' in ' + bank + '?',
          note: 'There\'s already a preset with this name there. Saving replaces it.',
          ok: 'Replace', danger: true })
      .then(function (r) { if (r) go(); });
  });
  actions.appendChild(audition);
  actions.appendChild(save);
  li.appendChild(actions);
  return li;
}

function badge(text, cls) {
  const b = document.createElement('span');
  b.className = 'imp-badge ' + cls;
  b.textContent = text;
  return b;
}

imp.saveAll.addEventListener('click', function () {
  const res = lastImport.res;
  const usable = res.presets.filter(usablePreset);
  const replacing = usable.filter(function (p) { return p.replaces; });
  const bank = imp.bank.value.trim() || res.bank;
  const go = function () {
    run('import_save_all', importPayload(), imp.saveAll, function (ok) {
      if (ok) { lastImport = null; imp.dlg.close(); }
    });
  };
  if (!replacing.length) return go();
  ask({ confirm: true,
        title: 'Save ' + plural(usable.length, 'preset', 'presets') + ' to ' + bank + '?',
        note: listWords(replacing.map(function (p) { return displayName(p.name); }))
            + (replacing.length === 1 ? ' is' : ' are') + ' already there, and will be replaced.',
        ok: 'Save all', danger: true })
    .then(function (r) { if (r) go(); });
});

imp.exportBtn.addEventListener('click', function () {
  setBusy(imp.exportBtn, true);
  request('export_preset', {}, function (res) {
    setBusy(imp.exportBtn, false);
    if (!res.ok) { flash(imp.exportBtn, 'is-failed'); showToast(res.error, 'error'); return; }
    download(res.filename, JSON.stringify(res.data, null, 2));
    flash(imp.exportBtn, 'is-done');
  });
});

imp.params.addEventListener('click', function (e) {
  if (!(linked && online)) {
    e.preventDefault();
    showToast('Guitarix isn\'t connected, so there\'s no parameter list to download yet.', 'error');
  }
});

function download(filename, text) {
  const blob = new Blob([text], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
}

/* ------------------------------------------------------------------ audition */

const aud = {
  save:    document.getElementById('aud-save'),
  discard: document.getElementById('aud-discard'),
  back:    document.getElementById('aud-back'),
};

function renderAudition() {
  const a = state.audition;
  els.audition.hidden = !a;
  if (!a) return;
  els.auditionText.innerHTML =
    'Auditioning <b>' + escapeHtml(displayName(a.name)) + '</b>'
    + (a.base ? ', built on ' + escapeHtml(a.base) : '') + '. Not saved yet: '
    + 'tweak anything, then save it or discard it.'
    + (a.replaces ? ' Saving replaces the one already in ' + escapeHtml(a.bank) + '.' : '');
  aud.save.textContent = 'Save to ' + a.bank;
  aud.back.hidden = !lastImport;
}

aud.save.addEventListener('click', function () {
  const a = state.audition;
  if (!a) return;
  const go = function () { run('audition_save', {}, aud.save); };
  if (!a.replaces) return go();
  ask({ confirm: true, title: 'Replace ' + displayName(a.name) + ' in ' + a.bank + '?',
        note: 'There\'s already a preset with this name there. Saving replaces it.',
        ok: 'Replace', danger: true })
    .then(function (r) { if (r) go(); });
});

aud.discard.addEventListener('click', function () {
  run('audition_discard', {}, aud.discard);
});

aud.back.addEventListener('click', openImporter);

/* ------------------------------------------------------------------ prompt */

const dlg = document.getElementById('ask');
const askOk = document.getElementById('ask-ok');
let askResolve = null;
let askConfirm = false;

document.getElementById('ask-cancel').addEventListener('click', function () {
  dlg.close();
});

document.getElementById('ask-form').addEventListener('submit', function (e) {
  e.preventDefault();
  const value = document.getElementById('ask-input').value.trim();
  const select = document.getElementById('ask-select').value;
  const resolve = askResolve;
  askResolve = null;
  dlg.close();
  if (!resolve) return;
  if (askConfirm) resolve({ confirmed: true, select: select });
  else resolve(value ? { value: value, select: select } : null);
});

dlg.addEventListener('close', function () {
  // `close` fires asynchronously. If a new question has already reopened
  // the dialog by then, this event belongs to the old one: leave it be.
  if (dlg.open) return;
  if (askResolve) { const r = askResolve; askResolve = null; r(null); }
});

/* One question at a time. opts:
     title, note          what's being asked
     confirm: true        yes/no, no text field
     label, value         the text field (when not confirm)
     select, selectValue, selectLabel   an optional dropdown
     ok, danger           the confirm button: its label says what it does */
function ask(opts) {
  if (askResolve) { const r = askResolve; askResolve = null; r(null); }
  if (dlg.open) dlg.close();

  askConfirm = !!opts.confirm;
  document.getElementById('ask-title').textContent = opts.title || '';
  document.getElementById('ask-label').textContent = opts.label || 'Name';
  document.getElementById('ask-input-wrap').hidden = askConfirm;
  askOk.textContent = opts.ok || 'OK';
  askOk.classList.toggle('is-danger', !!opts.danger);
  askOk.classList.toggle('is-primary', !opts.danger);

  const note = document.getElementById('ask-note');
  note.textContent = opts.note || '';
  note.hidden = !opts.note;

  const wrap = document.getElementById('ask-select-wrap');
  const sel = document.getElementById('ask-select');
  if (opts.select && opts.select.length) {
    sel.innerHTML = '';
    opts.select.forEach(function (name) {
      const o = document.createElement('option');
      o.value = name; o.textContent = name;
      if (name === opts.selectValue) o.selected = true;
      sel.appendChild(o);
    });
    wrap.hidden = false;
    wrap.firstElementChild.textContent = opts.selectLabel || 'Bank';
  } else {
    wrap.hidden = true;
  }

  const input = document.getElementById('ask-input');
  input.value = opts.value || '';

  return new Promise(function (resolve) {
    askResolve = resolve;
    dlg.showModal();
    setTimeout(function () {
      if (askConfirm) { askOk.focus(); } else { input.focus(); input.select(); }
    }, 30);
  });
}

/* ------------------------------------------------------------------ toasts */

const TOASTS_MAX = 3;

function showToast(text, kind) {
  // the same message twice in a row: refresh it rather than stack a copy
  const last = els.toasts.lastElementChild;
  if (last && last.textContent === text && !last.classList.contains('is-going')) {
    flash(last, 'is-again');
    clearTimeout(last._t1); clearTimeout(last._t2);
    retire(last);
    return;
  }
  // a burst of toasts would bury the controls; keep the newest few
  while (els.toasts.children.length >= TOASTS_MAX) els.toasts.firstElementChild.remove();

  const el = document.createElement('div');
  el.className = 'toast' + (kind ? ' is-' + kind : '');
  el.setAttribute('role', kind === 'error' ? 'alert' : 'status');
  el.textContent = text;
  els.toasts.appendChild(el);
  retire(el, kind === 'error' ? 6000 : 3200);
}

function retire(el, after) {
  const wait = after || 3200;
  el._t1 = setTimeout(function () { el.classList.add('is-going'); }, wait);
  el._t2 = setTimeout(function () { el.remove(); }, wait + 400);
}

/* ------------------------------------------------------------------ format */

function clock(seconds) {
  if (seconds === null || seconds === undefined || !isFinite(seconds)) return '--:--';
  const s = Math.floor(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const pad = function (n) { return String(n).padStart(2, '0'); };
  return h ? h + ':' + pad(m) + ':' + pad(sec) : pad(m) + ':' + pad(sec);
}

function bytes(n) {
  if (!n) return '0 B';
  const units = ['B', 'kB', 'MB', 'GB'];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return (i ? n.toFixed(1) : n) + ' ' + units[i];
}

function when(epoch) {
  if (!epoch) return '';
  const d = new Date(epoch * 1000);
  const today = new Date();
  const sameDay = d.toDateString() === today.toDateString();
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return sameDay ? 'Today ' + time : d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' + time;
}

/* ------------------------------------------------------------------ socket */

socket.on('connect', function () {
  setLinked(true);
  // A reconnect may have missed deltas while the socket was down, so tell the
  // server where we left off and let it decide between a delta and a snapshot.
  socket.emit('resync', { version: state.version || 0 });
});
socket.on('disconnect', function () { setLinked(false); });

socket.on('status', function (msg) {
  setStatus(msg.connected);
  if (!msg.connected) {
    renderPresets();
    renderGroups(els.eq, []);
    renderGroups(els.fx, []);
  }
});

socket.on('snapshot', function (snap) {
  // A copy, not the message itself: adopting it means anything else holding
  // the same object sees every later change to the page's state.
  state = Object.assign({}, snap);
  state.version = snap.version || 0;
  setStatus(snap.connected);
  if (!selectedBank || !snap.banks.some(function (b) { return b.name === selectedBank; })) {
    selectedBank = snap.bank || (snap.banks[0] && snap.banks[0].name) || null;
  }
  renderBanks();
  renderPresets();
  renderGroups(els.eq, snap.eq || []);
  renderGroups(els.fx, snap.fx || []);
  applyValues(snap.values || {}, false);
  setPreset(snap.bank, snap.preset);
  can = snap.can || {};
  applyCapabilities();
  applyRec(snap.rec);
  renderTakes(snap.recordings);
  lastBackingItems = snap.backing_items || [];
  renderBacking(lastBackingItems);
  renderAudition();
  renderReadout();
});

socket.on('preset', function (msg) { setPreset(msg.bank, msg.preset); });

socket.on('params', function (changes) { applyValues(changes, true); });

/* Deltas arrive in order and carry the version they produce. If one is
   missing -- a dropped frame, a reconnect -- the versions stop lining up and
   the only safe move is to ask for the whole picture again. */
socket.on('delta', function (msg) {
  const expected = (state.version || 0) + 1;
  if (msg.version !== expected) {
    socket.emit('resync', { version: state.version || 0 });
    return;
  }
  applyValues(msg.changes || {}, true);
  state.version = msg.version;
});

socket.on('banks', function (msg) {
  state.banks = (msg.banks || []).slice();
  if (!state.banks.some(function (b) { return b.name === selectedBank; })) {
    selectedBank = msg.bank || (state.banks[0] && state.banks[0].name) || null;
  }
  renderBanks();
  renderPresets();
  setPreset(msg.bank, msg.preset);
  applyOrganise();
});

socket.on('rec', applyRec);

socket.on('recordings', function (msg) {
  applyRec(msg.rec);
  renderTakes(msg.items);
  lastBackingItems = msg.backing_items || [];
  renderBacking(lastBackingItems);
  renderLive();
});

socket.on('toast', function (msg) { showToast(msg.text, msg.kind); });

socket.on('op_done', function (msg) { finish(msg.op, msg.ok); });

socket.on('dirty', function (msg) {
  state.dirty = !!msg.dirty;
  renderReadout();
});

socket.on('audition', function (a) {
  state.audition = a || null;
  renderAudition();
  renderReadout();
});

/* ------------------------------------------------------------------ utils */

function format(v) {
  if (!isFinite(v)) return '—';
  if (Number.isInteger(v)) return String(v);
  return v.toFixed(Math.abs(v) < 10 ? 2 : 1);
}

/* Decimal places follow the control's step, not the value: a fader that moves
   in 0.1s always shows one decimal, so "2" doesn't sit next to "0.15" and a
   readout doesn't change width as it moves. */
function decimalsFor(ctrl) {
  const step = Number(ctrl.step);
  if (step > 0) {
    if (step >= 1) return 0;
    return Math.min(3, Math.max(1, Math.ceil(-Math.log10(step) - 1e-9)));
  }
  const span = Math.abs(Number(ctrl.max) - Number(ctrl.min));
  return span <= 2 ? 2 : span <= 20 ? 1 : 0;
}

function formatter(ctrl) {
  const places = decimalsFor(ctrl);
  const bipolar = Number(ctrl.min) < 0 && Number(ctrl.max) > 0;
  return function (v) {
    if (!isFinite(v)) return '—';
    const tiny = 0.5 * Math.pow(10, -places);
    if (Math.abs(v) < tiny) v = 0;                 // no "-0.0"
    const text = v.toFixed(places);
    // cut/boost controls read better signed: +3.5, 0.0, -2.0
    return bipolar && v > 0 ? '+' + text : text;
  };
}

/* Preset names often use underscores for spaces. Show them as spaces so long
   names wrap between words instead of mid-word; the real name is untouched
   and is what goes back to the engine. */
function displayName(name) {
  return String(name == null ? '' : name).replace(/_+/g, ' ').trim();
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
}
