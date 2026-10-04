(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const api = new URL('./api/', location.href);
  const grid = $('designer-grid');
  let registry = null;
  let preset = null;
  let saved = null;
  let selected = null;
  let entries = [];
  let active = null;
  let preview = null;
  let drag = null;
  const tileTypes = () => registry.tiles || [];
  const definition = type => tileTypes().find(item => item.type === type);
  const announce = message => { $('status').textContent = message; };
  function errors(messages) {
    const box = $('errors');
    box.replaceChildren();
    if (!messages.length) { box.hidden = true; return; }
    const list = document.createElement('ul');
    for (const message of messages) {
      const item = document.createElement('li');
      item.textContent = message;
      list.append(item);
    }
    box.append(list);
    box.hidden = false;
  }
  async function request(path, method = 'GET', body) {
    const response = await fetch(new URL(path, api), {
      method,
      headers: body === undefined ? {} : {'Content-Type': 'application/json'},
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: 'no-store'
    });
    const data = response.status === 204 ? null : await response.json();
    if (!response.ok) {
      const problem = new Error((data && data.errors && data.errors.join('; ')) || (data && data.error) || `Request failed (${response.status})`);
      problem.messages = data && Array.isArray(data.errors) ? data.errors : [problem.message];
      throw problem;
    }
    return data;
  }
  function dirty() { return !!preset && (!saved || JSON.stringify(preset) !== JSON.stringify(saved)); }
  function guard() { return !dirty() || window.confirm('Discard unsaved changes?'); }
  function changed(message) {
    errors([]);
    announce(message);
    document.title = `${dirty() ? '* ' : ''}Forge layout designer`;
    if (!$('preview-panel').hidden) showPreview();
  }
  function slug(value) { return value.toLowerCase().trim().replace(/[^a-z0-9-]+/g, '-').replace(/^-+|-+$/g, ''); }
  function uniqueId(base, ids) {
    const root = slug(base) || 'preset';
    let id = root;
    for (let n = 2; ids.has(id); n++) id = `${root}-${n}`;
    return id;
  }
  function newPreset(name, id) {
    return {version: 1, id, name, dashboard: registry.dashboard, columns: 12, row_height: 80, tiles: []};
  }
  function populatePicker() {
    const picker = $('preset-picker');
    picker.replaceChildren();
    const draft = document.createElement('option');
    draft.value = '';
    draft.textContent = 'Unsaved preset';
    picker.append(draft);
    for (const entry of entries) {
      const option = document.createElement('option');
      option.value = entry.id;
      option.textContent = `${entry.name}${entry.id === active ? ' (active)' : ''}`;
      picker.append(option);
    }
    picker.value = saved && entries.some(entry => entry.id === preset.id) ? preset.id : '';
  }
  function load(value, isSaved) {
    preset = structuredClone(value);
    saved = isSaved ? structuredClone(value) : null;
    selected = null;
    $('preset-name').value = preset.name;
    $('columns').value = preset.columns;
    $('row-height').value = preset.row_height;
    populatePicker();
    drawGrid();
    drawOptions();
    changed(isSaved ? `Loaded ${preset.name}.` : `Created ${preset.name}.`);
  }
  function drawPalette() {
    const term = $('palette-search').value.trim().toLowerCase();
    const container = $('palette');
    container.replaceChildren();
    for (const item of tileTypes()) {
      if (!`${item.title} ${item.description || ''}`.toLowerCase().includes(term)) continue;
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'palette-item';
      button.dataset.type = item.type;
      button.setAttribute('aria-label', `Add ${item.title}; drag to grid or press Enter`);
      const title = document.createElement('strong');
      title.textContent = item.title;
      const description = document.createElement('span');
      description.textContent = item.description || '';
      button.append(title, description);
      button.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          addFirst(item);
        }
      });
      button.addEventListener('pointerdown', event => startPalette(event, button, item));
      container.append(button);
    }
  }
  function intersects(a, b) {
    return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
  }
  function fits(candidate, exclude) {
    return candidate.x >= 0 && candidate.y >= 0 && candidate.w > 0 && candidate.h > 0 &&
      candidate.x + candidate.w <= preset.columns &&
      !preset.tiles.some(tile => tile.id !== exclude && intersects(candidate, tile));
  }
  function nextTileId() { return uniqueId('t1', new Set(preset.tiles.map(tile => tile.id))); }
  function add(item, x, y) {
    if (preset.tiles.length >= 100) { announce('Cannot add more than 100 tiles.'); return false; }
    const w = item.default_w || item.min_w || 1;
    const h = item.default_h || item.min_h || 1;
    const candidate = {x, y, w, h};
    if (!fits(candidate)) { announce('That position is outside the grid or overlaps another tile.'); return false; }
    const options = {};
    for (const [key, option] of Object.entries(item.options || {})) {
      if (Object.hasOwn(option, 'default')) options[key] = option.default;
    }
    const tile = {id: nextTileId(), type: item.type, ...candidate, options};
    preset.tiles.push(tile);
    selected = tile.id;
    drawGrid();
    drawOptions();
    changed(`Added ${item.title} at column ${x + 1}, row ${y + 1}.`);
    grid.querySelector(`[data-id='${tile.id}']`)?.focus();
    return true;
  }
  function addFirst(item) {
    const w = item.default_w || item.min_w || 1;
    const h = item.default_h || item.min_h || 1;
    for (let y = 0; y < 1000; y++) {
      for (let x = 0; x <= preset.columns - w; x++) {
        if (fits({x, y, w, h})) { add(item, x, y); return; }
      }
    }
    announce('No space for that tile in the current columns.');
  }
  function select(id) {
    if (selected === id) return;
    selected = id;
    grid.querySelectorAll('.designer-tile').forEach(el => el.classList.toggle('is-selected', el.dataset.id === id));
    drawOptions();
    const tile = preset.tiles.find(item => item.id === id);
    if (tile) announce(`Selected ${definition(tile.type)?.title || tile.type}.`);
  }
  function position(el, tile) {
    el.style.gridColumn = `${tile.x + 1} / span ${tile.w}`;
    el.style.gridRow = `${tile.y + 1} / span ${tile.h}`;
  }
  function drawGrid() {
    grid.style.setProperty('--columns', preset.columns);
    grid.style.setProperty('--row-height', `${preset.row_height}px`);
    const rows = Math.max(4, ...preset.tiles.map(tile => tile.y + tile.h + 2));
    grid.style.gridTemplateRows = `repeat(${rows}, var(--row-height))`;
    grid.replaceChildren();
    for (const tile of preset.tiles) {
      const el = document.createElement('div');
      el.className = `designer-tile${selected === tile.id ? ' is-selected' : ''}`;
      el.tabIndex = 0;
      el.setAttribute('role', 'group');
      el.dataset.id = tile.id;
      const name = definition(tile.type)?.title || tile.type;
      el.setAttribute('aria-label', `${name}, column ${tile.x + 1}, row ${tile.y + 1}, width ${tile.w}, height ${tile.h}. Arrows move; Shift plus arrows resize; Delete removes.`);
      position(el, tile);
      const title = document.createElement('strong');
      title.textContent = name;
      const detail = document.createElement('small');
      detail.textContent = `${tile.w} × ${tile.h}`;
      const handle = document.createElement('button');
      handle.type = 'button';
      handle.className = 'resize-handle';
      handle.setAttribute('aria-label', `Drag to resize ${name}`);
      handle.textContent = '◢';
      el.append(title, detail, handle);
      el.addEventListener('focus', () => select(tile.id));
      el.addEventListener('click', () => select(tile.id));
      el.addEventListener('keydown', event => tileKey(event, tile));
      el.addEventListener('pointerdown', event => startTile(event, el, tile));
      grid.append(el);
    }
  }
  function tileKey(event, tile) {
    const directions = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1]};
    if (event.key === 'Delete' || event.key === 'Backspace') {
      event.preventDefault();
      const index = preset.tiles.indexOf(tile);
      if (index < 0) return;
      preset.tiles.splice(index, 1);
      selected = null;
      drawGrid();
      drawOptions();
      changed(`Removed ${definition(tile.type)?.title || tile.type}.`);
      grid.focus();
      return;
    }
    if (!directions[event.key]) return;
    event.preventDefault();
    const [dx, dy] = directions[event.key];
    const def = definition(tile.type);
    const candidate = {...tile};
    if (event.shiftKey) {
      candidate.w += dx;
      candidate.h += dy;
      if (candidate.w < (def.min_w || 1) || candidate.w > (def.max_w || 24) ||
          candidate.h < (def.min_h || 1) || candidate.h > (def.max_h || 1000)) {
        announce('Tile size is outside its allowed range.'); return;
      }
    } else { candidate.x += dx; candidate.y += dy; }
    if (!fits(candidate, tile.id)) { announce('Move refused: outside the grid or overlapping another tile.'); return; }
    Object.assign(tile, {x: candidate.x, y: candidate.y, w: candidate.w, h: candidate.h});
    drawGrid();
    grid.querySelector(`[data-id='${tile.id}']`)?.focus();
    changed(`${def.title} ${event.shiftKey ? 'resized' : 'moved'}: column ${tile.x + 1}, row ${tile.y + 1}, width ${tile.w}, height ${tile.h}.`);
  }
  function drawOptions() {
    const form = $('options-form');
    form.replaceChildren();
    const tile = preset.tiles.find(item => item.id === selected);
    $('options-empty').hidden = !!tile;
    form.hidden = !tile;
    if (!tile) return;
    const heading = document.createElement('p');
    heading.textContent = definition(tile.type)?.title || tile.type;
    form.append(heading);
    for (const [key, schema] of Object.entries(definition(tile.type)?.options || {})) {
      const field = document.createElement('div');
      field.className = `option-field${schema.type === 'boolean' ? ' checkbox-field' : ''}`;
      const label = document.createElement('label');
      const id = `option-${key}`;
      label.htmlFor = id;
      label.textContent = schema.label || key;
      let input;
      if (schema.type === 'choice') {
        input = document.createElement('select');
        for (const choice of schema.choices || []) {
          const option = document.createElement('option');
          option.value = choice;
          option.textContent = choice;
          input.append(option);
        }
      } else {
        input = document.createElement('input');
        input.type = schema.type === 'boolean' ? 'checkbox' : schema.type === 'number' ? 'number' : 'text';
        if (schema.type === 'number') input.step = 'any';
      }
      input.id = id;
      const value = Object.hasOwn(tile.options || {}, key) ? tile.options[key] : schema.default;
      if (schema.type === 'boolean') input.checked = !!value;
      else input.value = value ?? '';
      input.addEventListener('change', () => {
        if (!tile.options) tile.options = {};
        const next = schema.type === 'boolean' ? input.checked : schema.type === 'number' ? input.value === '' ? '' : Number(input.value) : input.value;
        tile.options[key] = next;
        changed(`${label.textContent} updated.`);
      });
      field.append(label, input);
      form.append(field);
    }
  }
  function cellAt(clientX, clientY) {
    const rect = grid.getBoundingClientRect();
    if (clientX < rect.left || clientX >= rect.right || clientY < rect.top || clientY >= rect.bottom) return null;
    return {x: Math.floor((clientX - rect.left) / (rect.width / preset.columns)), y: Math.floor((clientY - rect.top) / preset.row_height)};
  }
  function dropCell(cell, item) {
    // Snap a drop near the right edge inside the grid instead of refusing it.
    const w = Math.min(item.default_w || item.min_w || 1, preset.columns);
    return {x: Math.max(0, Math.min(cell.x, preset.columns - w)), y: cell.y, w, h: item.default_h || item.min_h || 1};
  }
  function startPalette(event, button, item) {
    if (event.button !== 0 || drag) return;
    button.setPointerCapture(event.pointerId);
    const ghost = document.createElement('div');
    ghost.className = 'palette-ghost';
    ghost.textContent = item.title;
    ghost.hidden = true;
    document.body.append(ghost);
    drag = {kind: 'palette', pointer: event.pointerId, element: button, item, ghost, startX: event.clientX, startY: event.clientY};
  }
  function startTile(event, element, tile) {
    if (event.button !== 0 || drag) return;
    select(tile.id);
    element.setPointerCapture(event.pointerId);
    drag = {kind: event.target.closest('.resize-handle') ? 'resize' : 'move', pointer: event.pointerId, element, tile,
      startX: event.clientX, startY: event.clientY, original: {...tile}, candidate: null};
    event.preventDefault();
  }
  document.addEventListener('pointermove', event => {
    if (!drag || event.pointerId !== drag.pointer) return;
    const d = drag;
    if (d.kind === 'palette') {
      const moved = Math.hypot(event.clientX - d.startX, event.clientY - d.startY) > 5;
      if (!moved && d.ghost.hidden) return;
      d.ghost.hidden = false;
      d.ghost.style.left = `${event.clientX + 12}px`;
      d.ghost.style.top = `${event.clientY + 12}px`;
      const cell = cellAt(event.clientX, event.clientY);
      d.ghost.classList.toggle('is-invalid', !cell || !fits(dropCell(cell, d.item)));
      return;
    }
    const dx = Math.round((event.clientX - d.startX) / (grid.getBoundingClientRect().width / preset.columns));
    const dy = Math.round((event.clientY - d.startY) / preset.row_height);
    const candidate = {...d.original};
    if (d.kind === 'resize') {
      candidate.w += dx;
      candidate.h += dy;
    } else { candidate.x += dx; candidate.y += dy; }
    const def = definition(d.tile.type);
    const valid = fits(candidate, d.tile.id) && (d.kind !== 'resize' ||
      (candidate.w >= (def.min_w || 1) && candidate.w <= (def.max_w || 24) &&
       candidate.h >= (def.min_h || 1) && candidate.h <= (def.max_h || 1000)));
    d.candidate = valid ? candidate : null;
    d.element.classList.toggle('is-invalid', !valid);
    if (valid) position(d.element, candidate);
  });
  function endDrag(event, cancelled) {
    if (!drag || event.pointerId !== drag.pointer) return;
    const d = drag;
    drag = null;
    if (d.kind === 'palette') {
      d.ghost.remove();
      if (!cancelled && !d.ghost.hidden) {
        const cell = cellAt(event.clientX, event.clientY);
        if (cell) { const at = dropCell(cell, d.item); add(d.item, at.x, at.y); }
        else announce('Drop a tile inside the grid.');
      }
      return;
    }
    if (!cancelled && d.candidate) {
      const before = d.original;
      const after = d.candidate;
      if (before.x !== after.x || before.y !== after.y || before.w !== after.w || before.h !== after.h) {
        Object.assign(d.tile, {x: after.x, y: after.y, w: after.w, h: after.h});
        drawGrid();
        grid.querySelector(`[data-id='${d.tile.id}']`)?.focus();
        changed(`${definition(d.tile.type).title} ${d.kind === 'resize' ? 'resized' : 'moved'} to column ${after.x + 1}, row ${after.y + 1}, width ${after.w}, height ${after.h}.`);
        return;
      }
    } else if (!cancelled && d.element.classList.contains('is-invalid')) announce('Change refused: outside the grid, invalid size, or overlapping another tile.');
    position(d.element, d.original);
    d.element.classList.remove('is-invalid');
  }
  document.addEventListener('pointerup', event => endDrag(event, false));
  document.addEventListener('pointercancel', event => endDrag(event, true));
  function showPreview() {
    if (preview) { preview.destroy(); preview = null; }
    if ($('preview-panel').hidden) return;
    if (!window.ForgeLayout) { errors(['Preview renderer is unavailable.']); return; }
    const renderers = {};
    for (const item of tileTypes()) {
      renderers[item.type] = body => { body.textContent = `Preview: ${item.title}`; };
    }
    preview = window.ForgeLayout.render($('preview-grid'), structuredClone(preset), renderers, {registry});
  }
  async function refreshList() {
    entries = await request('presets');
    populatePicker();
  }
  async function action(callback) {
    try { await callback(); }
    catch (error) { errors(error.messages || [error.message]); announce('Request failed.'); }
  }
  async function init() {
    registry = await request('registry');
    $('dashboard-label').textContent = registry.title || registry.dashboard;
    const expected = new URLSearchParams(location.search).get('dashboard');
    if (expected && expected !== registry.dashboard) announce(`This designer edits ${registry.dashboard}, not ${expected}.`);
    entries = await request('presets');
    active = (await request('active')).id;
    drawPalette();
    const first = entries.find(item => item.id === active) || entries[0];
    if (first) load(await request(`presets/${encodeURIComponent(first.id)}`), true);
    else load(newPreset('New preset', uniqueId('new-preset', new Set())), false);
  }
  $('palette-search').addEventListener('input', () => { if (registry) drawPalette(); });
  $('preset-picker').addEventListener('change', event => {
    const id = event.target.value;
    event.target.value = saved && entries.some(entry => entry.id === preset.id) ? preset.id : '';
    if (!id || !guard()) return;
    action(async () => load(await request(`presets/${encodeURIComponent(id)}`), true));
  });
  $('new-preset').addEventListener('click', () => {
    if (!guard()) return;
    const name = prompt('Name for the new preset:', 'New preset');
    if (name === null) return;
    const id = uniqueId(name, new Set(entries.map(item => item.id)));
    load(newPreset(name, id), false);
  });
  $('duplicate-preset').addEventListener('click', () => {
    const name = prompt('Name for the duplicate:', `${preset.name} copy`);
    if (name === null || !guard()) return;
    const copy = structuredClone(preset);
    copy.name = name;
    copy.id = uniqueId(name, new Set(entries.map(item => item.id)));
    delete copy.updated_at;
    load(copy, false);
  });
  $('save-preset').addEventListener('click', () => action(async () => {
    const value = await request(`presets/${encodeURIComponent(preset.id)}`, 'PUT', preset);
    entries = await request('presets');
    load(value, true);
    announce(`Saved ${value.name}.`);
  }));
  $('delete-preset').addEventListener('click', () => action(async () => {
    if (!entries.some(item => item.id === preset.id)) { announce('This preset has not been saved.'); return; }
    if (!window.confirm(`Delete ${preset.name}?`)) return;
    await request(`presets/${encodeURIComponent(preset.id)}`, 'DELETE');
    if (active === preset.id) active = null;
    entries = await request('presets');
    const first = entries.find(item => item.id === active) || entries[0];
    if (first) load(await request(`presets/${encodeURIComponent(first.id)}`), true);
    else load(newPreset('New preset', uniqueId('new-preset', new Set())), false);
    announce('Preset deleted.');
  }));
  $('set-active').addEventListener('click', () => action(async () => {
    if (dirty() || !entries.some(item => item.id === preset.id)) { announce('Save this preset before setting it active.'); return; }
    await request('active', 'PUT', {id: preset.id});
    active = preset.id;
    populatePicker();
    announce(`${preset.name} is active.`);
  }));
  $('preset-name').addEventListener('input', event => { preset.name = event.target.value; changed('Preset name changed.'); });
  for (const [id, key] of [['columns', 'columns'], ['row-height', 'row_height']]) {
    $(id).addEventListener('change', event => {
      const value = Number(event.target.value);
      if (!Number.isInteger(value) || value < (key === 'columns' ? 1 : 20) || value > (key === 'columns' ? 24 : 400)) {
        announce('Enter a valid whole number.'); event.target.value = preset[key]; return;
      }
      if (key === 'columns' && preset.tiles.some(tile => tile.x + tile.w > value)) {
        announce('Move tiles inside the new column count first.'); event.target.value = preset[key]; return;
      }
      preset[key] = value;
      drawGrid();
      changed(`${key === 'columns' ? 'Columns' : 'Row height'} set to ${value}.`);
    });
  }
  $('options-form').addEventListener('submit', event => event.preventDefault());
  $('preview-toggle').addEventListener('click', () => {
    const panel = $('preview-panel');
    panel.hidden = !panel.hidden;
    $('preview-toggle').setAttribute('aria-expanded', String(!panel.hidden));
    if (panel.hidden) { if (preview) preview.destroy(); preview = null; $('preview-grid').replaceChildren(); }
    else showPreview();
  });
  window.addEventListener('beforeunload', event => { if (dirty()) { event.preventDefault(); event.returnValue = ''; } });
  action(init);
})();
