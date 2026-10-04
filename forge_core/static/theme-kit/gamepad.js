/* theme-kit/gamepad.js: shared gamepad (XInput / W3C standard mapping) navigation. Opt in with one tag:
   <script src="theme-kit/gamepad.js" defer></script>. Polls only between gamepadconnected and gamepaddisconnected
   and never while document.hidden; mouse, touch and keyboard are untouched. D-pad / left stick = spatial focus
   (deadzone, hold-to-repeat), A = activate, B = back, Y = theme switcher, LB/RB = tab or [data-nav-group],
   Start = main nav, right stick = scroll. Markup: [data-nav-group] [data-nav-default] [data-nav-main]
   [data-nav-theme] [data-nav-back] [data-nav-skip]. Tag options: data-nav-auto="false", data-nav-toast="false".
   Focused control gets class gp-focus (ring from --focus-ring); <html data-gamepad="true"> while a pad is on.
   Pure helpers live on GamepadNav.pure for node tests (tests/smoke_gamepad_nav.mjs). Docs: theme-kit/README.md. */
(function (global) {
  "use strict";

  var STANDARD_MAPPING = {
    BUTTON_A: 0, BUTTON_B: 1, BUTTON_X: 2, BUTTON_Y: 3,
    BUTTON_LB: 4, BUTTON_RB: 5, TRIGGER_LT: 6, TRIGGER_RT: 7,
    BUTTON_VIEW: 8, BUTTON_MENU: 9, STICK_L_PRESS: 10, STICK_R_PRESS: 11,
    DPAD_UP: 12, DPAD_DOWN: 13, DPAD_LEFT: 14, DPAD_RIGHT: 15,
    AXIS_LX: 0, AXIS_LY: 1, AXIS_RX: 2, AXIS_RY: 3
  };

  // Button index -> action name. Directions also come from the left stick.
  var ACTIONS = ["activate", "back", "x", "theme", "prev", "next", "lt", "rt",
    "view", "menu", "ls", "rs", "up", "down", "left", "right"];
  var DIRECTIONS = ["up", "down", "left", "right"];

  var defaults = {
    deadzone: 0.3,
    repeatDelayMs: 400,
    repeatIntervalMs: 110,
    scrollSpeed: 1200,      // px per second at full right-stick deflection
    cone: 2,                // off-axis gap allowed per px of forward distance (Infinity disables)
    autoStart: true,
    toast: true,
    themeSwitcher: null,    // selector, element or function; default: [data-nav-theme] or the ThemeKit select
    mainNav: null,          // selector, element or function; default: [data-nav-main], nav, [role=navigation], header
    onAction: null          // function(action) -> false vetoes the default handling
  };

  var FOCUSABLE = "a[href],button,input,select,textarea,summary,[tabindex],[contenteditable='true']";
  var RING_CSS = ".gp-focus{outline:2px solid transparent;outline-offset:2px;box-shadow:var(--focus-ring,0 0 0 3px var(--accent,#f5a524))}" +
    ".gp-focus.gp-editing{outline:2px dashed var(--accent,#f5a524)}";

  /* ---------- pure helpers (no DOM) ---------- */

  function normRect(r) {
    if (!r) return null;
    var left = r.left != null ? r.left : r.x;
    var top = r.top != null ? r.top : r.y;
    var width = r.width != null ? r.width : r.right - left;
    var height = r.height != null ? r.height : r.bottom - top;
    if (!isFinite(left) || !isFinite(top) || !isFinite(width) || !isFinite(height)) return null;
    return { left: left, top: top, right: left + width, bottom: top + height,
      width: width, height: height, cx: left + width / 2, cy: top + height / 2 };
  }

  function perpGap(a0, a1, b0, b1) {
    var overlap = Math.min(a1, b1) - Math.max(a0, b0);
    return overlap > 0 ? 0 : -overlap;
  }

  // Lower is better; Infinity means "not in that direction".
  function directionScore(direction, from, rect, cone) {
    var primary, gap, offset;
    if (direction === "right") {
      if (rect.cx <= from.cx || rect.left <= from.left) return Infinity;
      primary = Math.max(0, rect.left - from.right);
      gap = perpGap(from.top, from.bottom, rect.top, rect.bottom);
      offset = Math.abs(rect.cy - from.cy);
    } else if (direction === "left") {
      if (rect.cx >= from.cx || rect.right >= from.right) return Infinity;
      primary = Math.max(0, from.left - rect.right);
      gap = perpGap(from.top, from.bottom, rect.top, rect.bottom);
      offset = Math.abs(rect.cy - from.cy);
    } else if (direction === "down") {
      if (rect.cy <= from.cy || rect.top <= from.top) return Infinity;
      primary = Math.max(0, rect.top - from.bottom);
      gap = perpGap(from.left, from.right, rect.left, rect.right);
      offset = Math.abs(rect.cx - from.cx);
    } else if (direction === "up") {
      if (rect.cy >= from.cy || rect.bottom >= from.bottom) return Infinity;
      primary = Math.max(0, from.top - rect.bottom);
      gap = perpGap(from.left, from.right, rect.left, rect.right);
      offset = Math.abs(rect.cx - from.cx);
    } else {
      return Infinity;
    }
    if (cone != null && gap > (primary + 1) * cone) return Infinity;
    return primary + gap * 2.5 + offset * 0.15;
  }

  // candidates: array of rects or of objects with a .rect; returns the winning entry or null.
  function pickNearest(direction, from, candidates, options) {
    var cone = options && options.cone != null ? options.cone : defaults.cone;
    var origin = normRect(from && from.rect ? from.rect : from);
    if (!origin || !candidates) return null;
    var best = null, bestScore = Infinity;
    for (var i = 0; i < candidates.length; i++) {
      var c = candidates[i];
      if (!c || c === from) continue;
      var r = normRect(c.rect || c);
      if (!r) continue;
      if (r.left === origin.left && r.top === origin.top && r.right === origin.right && r.bottom === origin.bottom) continue;
      var s = directionScore(direction, origin, r, cone);
      if (s < bestScore) { bestScore = s; best = c; }
    }
    return best;
  }

  function stickDirection(axes, deadzone) {
    var x = (axes && axes[0]) || 0, y = (axes && axes[1]) || 0;
    if (Math.sqrt(x * x + y * y) < deadzone) return null;
    if (Math.abs(x) > Math.abs(y)) return x > 0 ? "right" : "left";
    return y > 0 ? "down" : "up";
  }

  function buttonPressed(b) {
    if (!b) return false;
    if (b === true || b.pressed) return true;
    return typeof b.value === "number" && b.value > 0.5;
  }

  // Snapshot of a Gamepad (or a fake {buttons, axes}) -> { held: {action: true}, scroll: {x, y} }.
  function readPad(pad, deadzone) {
    var held = {}, buttons = (pad && pad.buttons) || [], axes = (pad && pad.axes) || [];
    for (var i = 0; i < ACTIONS.length; i++) {
      if (buttonPressed(buttons[i])) held[ACTIONS[i]] = true;
    }
    var dir = stickDirection([axes[0], axes[1]], deadzone);
    if (dir) held[dir] = true;
    var rx = axes[2] || 0, ry = axes[3] || 0;
    return { held: held, scroll: { x: Math.abs(rx) >= deadzone ? rx : 0, y: Math.abs(ry) >= deadzone ? ry : 0 } };
  }

  function newPresses(prevHeld, held) {
    var out = [];
    for (var k in held) if (held[k] && !(prevHeld && prevHeld[k])) out.push(k);
    return out;
  }

  // Hold-to-repeat for one direction: fire on press, again after repeatDelayMs, then every repeatIntervalMs.
  function repeatTick(held, prevHeld, nextAt, now, cfg) {
    if (!held) return { fire: false, nextAt: 0 };
    if (!prevHeld) return { fire: true, nextAt: now + cfg.repeatDelayMs };
    if (now >= nextAt) return { fire: true, nextAt: now + cfg.repeatIntervalMs };
    return { fire: false, nextAt: nextAt };
  }

  // One poll frame, pure: returns the actions to fire, the scroll vector and the next state.
  function stepFrame(pad, state, now, cfg) {
    cfg = cfg || defaults;
    var read = readPad(pad, cfg.deadzone), held = read.held;
    var prev = (state && state.held) || {}, repeatAt = (state && state.repeatAt) || {};
    var next = {}, actions = [];
    for (var d = 0; d < DIRECTIONS.length; d++) {
      var dir = DIRECTIONS[d];
      var r = repeatTick(!!held[dir], !!prev[dir], repeatAt[dir] || 0, now, cfg);
      if (r.fire) actions.push(dir);
      if (held[dir]) next[dir] = r.nextAt;
    }
    var presses = newPresses(prev, held);
    for (var p = 0; p < presses.length; p++) {
      if (DIRECTIONS.indexOf(presses[p]) === -1) actions.push(presses[p]);
    }
    return { actions: actions, scroll: read.scroll, state: { held: held, repeatAt: next } };
  }

  /* ---------- DOM layer ---------- */

  var win = global;
  var doc = typeof global.document !== "undefined" ? global.document : null;
  var config = {};
  for (var dk in defaults) config[dk] = defaults[dk];
  var listeners = [];
  var pads = {};
  var started = false, frame = null, lastTime = 0, editing = false, marked = null;
  var suppressFocusin = false, synthetic = false, styleEl = null, toastEl = null, toastTimer = null;
  var state = { held: {}, repeatAt: {} };

  function filter(list, fn) { var out = []; for (var i = 0; list && i < list.length; i++) if (fn(list[i])) out.push(list[i]); return out; }
  function hasPad() { for (var k in pads) if (pads[k]) return true; return false; }
  function isPolling() { return frame !== null; }
  function root() { return doc && doc.documentElement; }

  function resolve(target) {
    if (!target || !doc) return null;
    if (typeof target === "function") return target() || null;
    if (typeof target === "string") return doc.querySelector(target);
    return target.nodeType === 1 ? target : null;
  }

  function isFocusable(el) {
    if (!el || el.nodeType !== 1) return false;
    if (el.disabled || el.hidden || el.getAttribute("aria-hidden") === "true") return false;
    var ti = el.getAttribute("tabindex");
    if (ti !== null && parseInt(ti, 10) < 0) return false;
    if (el.closest && el.closest("[inert],[data-nav-skip]")) return false;
    if (typeof el.getBoundingClientRect === "function") {
      var r = el.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) return false;
    }
    if (typeof win.getComputedStyle === "function") {
      var cs = win.getComputedStyle(el);
      if (cs && (cs.visibility === "hidden" || cs.display === "none")) return false;
    }
    return true;
  }

  function scopeRoot() {
    // Only a MODAL dialog (showModal) traps focus; a modeless <dialog open> must not (gpt-review).
    var open = doc.querySelectorAll("dialog[open]");
    for (var i = open.length - 1; i >= 0; i--) {
      var d = open[i], modal = false;
      try { modal = d.matches(":modal"); } catch (e) { modal = d.getAttribute("aria-modal") === "true"; }
      if (modal) return d;
    }
    return doc;
  }

  function focusables(container) {
    return filter(container.querySelectorAll(FOCUSABLE), isFocusable);
  }

  function editable(el) {
    if (!el) return false;
    if (el.tagName === "SELECT") return true;
    return el.tagName === "INPUT" && el.type === "range";
  }

  function fire(el, type) {
    try { el.dispatchEvent(new win.Event(type, { bubbles: true })); } catch (e) { /* no Event constructor */ }
  }

  function mark(el) {
    if (marked && marked !== el) {
      marked.classList.remove("gp-focus");
      marked.classList.remove("gp-editing");
    }
    marked = el;
    el.classList.add("gp-focus");
    if (editing) el.classList.add("gp-editing"); else el.classList.remove("gp-editing");
  }

  function clearMark() {
    editing = false;
    if (marked) {
      marked.classList.remove("gp-focus");
      marked.classList.remove("gp-editing");
      marked = null;
    }
  }

  function focusEl(el) {
    if (!el || typeof el.focus !== "function") return false;
    suppressFocusin = true;
    try { el.focus({ preventScroll: true }); } catch (e) { el.focus(); }
    suppressFocusin = false;
    mark(el);
    if (typeof el.scrollIntoView === "function") {
      try { el.scrollIntoView({ block: "nearest", inline: "nearest" }); } catch (e2) { /* old engines */ }
    }
    return true;
  }

  function enterGroup(group) {
    if (!group) return false;
    var def = group.querySelector("[data-nav-default]");
    if (def && isFocusable(def)) return focusEl(def);
    var list = focusables(group);
    return list.length ? focusEl(list[0]) : false;
  }

  // Editing mode: directions change a select or range instead of moving focus.
  function adjustControl(el, direction) {
    var step = direction === "right" || direction === "down" ? 1 : -1;
    if (el.tagName === "SELECT") {
      var n = el.options ? el.options.length : 0;
      if (!n) return false;
      var i = Math.min(n - 1, Math.max(0, el.selectedIndex + step));
      if (i === el.selectedIndex) return true;
      el.selectedIndex = i;
    } else if (el.tagName === "INPUT" && el.type === "range") {
      try { if (step > 0) el.stepUp(); else el.stepDown(); } catch (e) { return false; }
    } else {
      return false;
    }
    fire(el, "input");
    fire(el, "change");
    return true;
  }

  function currentFocus(scope) {
    var active = doc.activeElement;
    if (!active || active === doc.body || !isFocusable(active)) return null;
    if (scope !== doc && !scope.contains(active)) return null;
    return active;
  }

  function moveFocus(direction) {
    if (!doc || DIRECTIONS.indexOf(direction) === -1) return false;
    var scope = scopeRoot();
    var current = currentFocus(scope);
    if (!current) {
      var def = scope.querySelector("[data-nav-default]");
      if (def && isFocusable(def)) return focusEl(def);
      var all = focusables(scope);
      return all.length ? focusEl(all[0]) : false;
    }
    if (editing && marked === current && editable(current)) return adjustControl(current, direction);
    var candidates = [];
    var list = focusables(scope);
    for (var i = 0; i < list.length; i++) {
      if (list[i] === current || current.contains(list[i]) || list[i].contains(current)) continue;
      candidates.push({ el: list[i], rect: list[i].getBoundingClientRect() });
    }
    var pick = pickNearest(direction, current.getBoundingClientRect(), candidates, config);
    return pick ? focusEl(pick.el) : false;
  }

  function clickQuiet(el) {
    if (!el || typeof el.click !== "function") return;
    synthetic = true;
    try { el.click(); } finally { synthetic = false; }
  }

  function activate() {
    var scope = scopeRoot();
    var el = currentFocus(scope);
    if (!el) return moveFocus("down");
    if (editable(el)) {
      editing = !editing;
      mark(el);
      return true;
    }
    var clickable = /^(A|BUTTON|INPUT|SELECT|TEXTAREA|SUMMARY)$/.test(el.tagName) ||
      typeof el.onclick === "function" || el.getAttribute("onclick") !== null ||
      el.getAttribute("role") === "button" || el.getAttribute("role") === "link";
    if (clickable) clickQuiet(el);
    else if (typeof win.KeyboardEvent === "function") {
      synthetic = true;
      try {
        el.dispatchEvent(new win.KeyboardEvent("keydown", { key: "Enter", code: "Enter", keyCode: 13, bubbles: true, cancelable: true }));
        el.dispatchEvent(new win.KeyboardEvent("keyup", { key: "Enter", code: "Enter", keyCode: 13, bubbles: true, cancelable: true }));
      } finally { synthetic = false; }
    }
    return true;
  }

  function goBack() {
    if (editing && marked) { editing = false; mark(marked); return true; }
    var dlg = doc.querySelector("dialog[open]");
    if (dlg) {
      if (typeof dlg.close === "function") dlg.close(); else dlg.removeAttribute("open");
      return true;
    }
    var active = doc.activeElement;
    var expanded = (active && active.getAttribute && active.getAttribute("aria-expanded") === "true")
      ? active : doc.querySelector("[aria-expanded='true']");
    if (expanded) {
      clickQuiet(expanded);
      if (isFocusable(expanded)) focusEl(expanded);
      return true;
    }
    var details = active && active.closest ? active.closest("details[open]") : null;
    if (details) {
      details.removeAttribute("open");
      var summary = details.querySelector("summary");
      if (summary && isFocusable(summary)) focusEl(summary);
      return true;
    }
    var backEl = doc.querySelector("[data-nav-back]");
    if (backEl) { clickQuiet(backEl); return true; }
    if (win.history && typeof win.history.back === "function") { win.history.back(); return true; }
    return false;
  }

  function openThemeSwitcher() {
    var target = resolve(config.themeSwitcher) || doc.querySelector("[data-nav-theme]") ||
      doc.querySelector("select[id^='theme-kit-select-']");
    if (target && isFocusable(target)) {
      focusEl(target);
      if (editable(target)) { editing = true; mark(target); } else clickQuiet(target);
      return true;
    }
    var kit = win.ThemeKit;
    if (kit && kit.themes && typeof kit.set === "function" && typeof kit.get === "function") {
      var ids = [], names = {};
      for (var i = 0; i < kit.themes.length; i++) { ids.push(kit.themes[i].id); names[kit.themes[i].id] = kit.themes[i].name; }
      var next = ids[(ids.indexOf(kit.get().id) + 1) % ids.length];
      kit.set(next);
      toast("Theme: " + (names[next] || next));
      return true;
    }
    return false;
  }

  function cycleTabs(tabs, from, step) {
    if (tabs.length < 2) return false;
    var tab = tabs[(tabs.indexOf(from) + step + tabs.length) % tabs.length];
    focusEl(tab);
    clickQuiet(tab);
    return true;
  }

  function cycleGroup(step) {
    var active = doc.activeElement;
    var tab = active && active.closest ? active.closest("[role='tab']") : null;
    if (tab) {
      var list = tab.closest("[role='tablist']");
      if (list && cycleTabs(filter(list.querySelectorAll("[role='tab']"), isFocusable), tab, step)) return true;
    }
    var groups = filter(doc.querySelectorAll("[data-nav-group]"), function (g) { return focusables(g).length > 0; });
    if (!groups.length) {
      var tabs = filter(doc.querySelectorAll("[role='tab']"), isFocusable);
      var selected = filter(tabs, function (t) { return t.getAttribute("aria-selected") === "true"; })[0] || tabs[0];
      return tabs.length ? cycleTabs(tabs, selected, step) : false;
    }
    var current = -1;
    for (var k = 0; k < groups.length; k++) if (active && groups[k].contains(active)) current = k;
    var index = current === -1 ? (step > 0 ? 0 : groups.length - 1) : (current + step + groups.length) % groups.length;
    return enterGroup(groups[index]);
  }

  function focusMainNav() {
    var nav = resolve(config.mainNav) || doc.querySelector("[data-nav-main]") ||
      doc.querySelector("nav,[role='navigation'],header");
    return nav ? enterGroup(nav) : false;
  }

  function handleAction(action) {
    if (!doc) return false;
    switch (action) {
      case "up": case "down": case "left": case "right": return moveFocus(action);
      case "activate": return activate();
      case "back": return goBack();
      case "theme": return openThemeSwitcher();
      case "prev": return cycleGroup(-1);
      case "next": return cycleGroup(1);
      case "menu": return focusMainNav();
      default: return false;
    }
  }

  function dispatch(action) {
    if (typeof config.onAction === "function" && config.onAction(action) === false) return false;
    var subs = listeners.slice();
    for (var i = 0; i < subs.length; i++) if (subs[i](action) === false) return false;
    return handleAction(action);
  }

  function scrollTarget() {
    var el = doc.activeElement;
    while (el && el !== doc.body && el !== doc.documentElement && typeof win.getComputedStyle === "function") {
      var cs = win.getComputedStyle(el);
      if (cs && /(auto|scroll)/.test(String(cs.overflowY) + String(cs.overflowX)) &&
        (el.scrollHeight > el.clientHeight || el.scrollWidth > el.clientWidth)) return el;
      el = el.parentElement;
    }
    return win;
  }

  function scrollBy(vec, dt) {
    var target = scrollTarget();
    if (!target || typeof target.scrollBy !== "function") return;
    var k = config.scrollSpeed * dt / 1000;
    target.scrollBy(vec.x * Math.abs(vec.x) * k, vec.y * Math.abs(vec.y) * k);
  }

  function toast(text) {
    if (!config.toast || !doc || !doc.body) return;
    if (!toastEl) {
      toastEl = doc.createElement("div");
      toastEl.className = "gp-toast forge-gamepad-toast";
      toastEl.setAttribute("role", "status");
      toastEl.style.cssText = "position:fixed;bottom:24px;right:24px;background:var(--surface-3,#222);color:var(--text,#fff);" +
        "padding:8px 16px;border-radius:var(--radius-md,8px);box-shadow:var(--shadow-2,0 4px 12px rgba(0,0,0,0.5));" +
        "font-family:var(--font-body,sans-serif);font-size:14px;z-index:99999;transition:opacity 0.3s ease;opacity:0;pointer-events:none;";
      doc.body.appendChild(toastEl);
    }
    toastEl.textContent = text;
    toastEl.style.opacity = "1";
    win.clearTimeout(toastTimer);
    toastTimer = win.setTimeout(function () { if (toastEl) toastEl.style.opacity = "0"; }, 2500);
  }

  function ensureStyle() {
    if (styleEl || !doc || !doc.head || typeof doc.createElement !== "function") return;
    styleEl = doc.createElement("style");
    styleEl.id = "gp-nav-style";
    styleEl.textContent = RING_CSS;
    doc.head.appendChild(styleEl);
  }

  function firstPad() {
    var nav = win.navigator;
    if (!nav || typeof nav.getGamepads !== "function") return null;
    var list = nav.getGamepads();
    for (var i = 0; list && i < list.length; i++) {
      if (list[i] && list[i].connected !== false) return list[i];
    }
    return null;
  }

  function schedule() {
    if (frame === null && typeof win.requestAnimationFrame === "function") frame = win.requestAnimationFrame(tick);
  }

  function cancel() {
    if (frame !== null && typeof win.cancelAnimationFrame === "function") win.cancelAnimationFrame(frame);
    frame = null;
    lastTime = 0;
  }

  function shouldPoll() {
    return started && hasPad() && !(doc && doc.hidden);
  }

  function syncPolling() {
    if (shouldPoll()) schedule(); else cancel();
  }

  function tick(timestamp) {
    frame = null;
    if (!shouldPoll()) return;
    var now = typeof timestamp === "number" ? timestamp : Date.now();
    var pad = firstPad();
    if (pad) {
      var dt = lastTime ? Math.min(100, Math.max(0, now - lastTime)) : 16;
      var out = stepFrame(pad, state, now, config);
      state = out.state;
      for (var i = 0; i < out.actions.length; i++) dispatch(out.actions[i]);
      if (out.scroll.x || out.scroll.y) scrollBy(out.scroll, dt);
    } else {
      state = { held: {}, repeatAt: {} };
    }
    lastTime = now;
    schedule();
  }

  function padIndex(event) {
    var gp = event && event.gamepad;
    return gp && typeof gp.index === "number" ? gp.index : 0;
  }

  function setRootFlag(on) {
    var el = root();
    if (!el) return;
    if (on && el.setAttribute) el.setAttribute("data-gamepad", "true");
    if (!on && el.removeAttribute) el.removeAttribute("data-gamepad");
  }

  function onConnected(event) {
    pads[padIndex(event)] = true;
    setRootFlag(true);
    toast("Controller connected");
    syncPolling();
  }

  function onDisconnected(event) {
    delete pads[padIndex(event)];
    if (!hasPad()) {
      clearMark();
      state = { held: {}, repeatAt: {} };
      setRootFlag(false);
    }
    toast("Controller disconnected");
    syncPolling();
  }

  function onVisibility() { syncPolling(); }
  function onUserInput() { if (!synthetic) clearMark(); }
  function onKeydown(event) {
    if (/^(Tab|ArrowUp|ArrowDown|ArrowLeft|ArrowRight|Escape)$/.test(event.key)) onUserInput();
  }
  function onFocusin() { if (!suppressFocusin && !synthetic) clearMark(); }

  function configure(options) {
    if (options) for (var k in options) if (options[k] !== undefined) config[k] = options[k];
    return config;
  }

  function start(options) {
    if (options) configure(options);
    if (started || !doc) return api;
    started = true;
    ensureStyle();
    win.addEventListener("gamepadconnected", onConnected);
    win.addEventListener("gamepaddisconnected", onDisconnected);
    doc.addEventListener("visibilitychange", onVisibility);
    doc.addEventListener("pointerdown", onUserInput, true);
    doc.addEventListener("keydown", onKeydown, true);
    doc.addEventListener("focusin", onFocusin, true);
    var existing = firstPad();
    if (existing) {
      pads[typeof existing.index === "number" ? existing.index : 0] = true;
      setRootFlag(true);
    }
    syncPolling();
    return api;
  }

  function stop() {
    if (!started) return api;
    started = false;
    cancel();
    win.removeEventListener("gamepadconnected", onConnected);
    win.removeEventListener("gamepaddisconnected", onDisconnected);
    doc.removeEventListener("visibilitychange", onVisibility);
    doc.removeEventListener("pointerdown", onUserInput, true);
    doc.removeEventListener("keydown", onKeydown, true);
    doc.removeEventListener("focusin", onFocusin, true);
    clearMark();
    if (styleEl) { styleEl.remove(); styleEl = null; }
    win.clearTimeout(toastTimer);
    toastTimer = null;
    if (toastEl) { toastEl.remove(); toastEl = null; }
    pads = {};
    state = { held: {}, repeatAt: {} };
    setRootFlag(false);
    return api;
  }

  function onAction(fn) {
    if (typeof fn !== "function") throw new TypeError("Expected a function");
    listeners.push(fn);
    return function () {
      var i = listeners.indexOf(fn);
      if (i !== -1) listeners.splice(i, 1);
    };
  }

  var api = {
    STANDARD_MAPPING: STANDARD_MAPPING,
    ACTIONS: ACTIONS.slice(),
    defaults: defaults,
    pure: {
      normRect: normRect,
      directionScore: directionScore,
      pickNearest: pickNearest,
      stickDirection: stickDirection,
      readPad: readPad,
      newPresses: newPresses,
      repeatTick: repeatTick,
      stepFrame: stepFrame
    },
    configure: configure,
    start: start,
    stop: stop,
    onAction: onAction,
    handleAction: handleAction,
    moveFocus: moveFocus,
    focus: focusEl,
    isStarted: function () { return started; },
    isPolling: isPolling,
    hasPad: hasPad,
    isEditing: function () { return editing; }
  };

  // Script-tag options.
  var script = doc && doc.currentScript;
  if (script && script.getAttribute) {
    if (script.getAttribute("data-nav-auto") === "false") config.autoStart = false;
    if (script.getAttribute("data-nav-toast") === "false") config.toast = false;
    if (script.getAttribute("data-nav-theme-target")) config.themeSwitcher = script.getAttribute("data-nav-theme-target");
    if (script.getAttribute("data-nav-main-target")) config.mainNav = script.getAttribute("data-nav-main-target");
  }

  global.GamepadNav = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;

  if (doc && config.autoStart) {
    if (doc.readyState === "loading" && typeof doc.addEventListener === "function") {
      doc.addEventListener("DOMContentLoaded", function () { start(); });
    } else {
      start();
    }
  }
})(typeof window !== "undefined" ? window : globalThis);
