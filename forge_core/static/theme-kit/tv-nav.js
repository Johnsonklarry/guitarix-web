/* theme-kit/tv-nav.js: ten-foot living-room TV navigation. Installs a document-level keydown listener
   that moves focus to the nearest focusable element in the pressed arrow direction using
   getBoundingClientRect() geometry, calls .focus() on the winner and preventDefault() on the event.
   Entry point: window.tvNav.moveFocus(direction) with direction one of 'left'|'right'|'up'|'down'.
   The gamepad layer (theme-kit/gamepad.js) dispatches synthetic ArrowLeft/ArrowRight/ArrowUp/ArrowDown
   KeyboardEvents, so loading this file after gamepad.js lets a controller drive spatial focus. */
(function (global) {
  "use strict";

  var doc = typeof global.document !== "undefined" ? global.document : null;

  var DIRECTIONS = ["left", "right", "up", "down"];

  var KEY_TO_DIRECTION = {
    ArrowLeft: "left",
    ArrowRight: "right",
    ArrowUp: "up",
    ArrowDown: "down"
  };

  var FOCUSABLE = "a[href],button,input,select,textarea,summary,[tabindex],[contenteditable='true']";

  var defaults = {
    cone: 2,          // off-axis gap allowed per px of forward distance (Infinity disables)
    deadzone: 1       // minimum forward travel in px for a candidate to count
  };

  var config = {};
  for (var dk in defaults) config[dk] = defaults[dk];

  /* ---------- pure helpers (no DOM) ---------- */

  function normRect(r) {
    if (!r) return null;
    var left = r.left != null ? r.left : r.x;
    var top = r.top != null ? r.top : r.y;
    var width = r.width != null ? r.width : r.right - left;
    var height = r.height != null ? r.height : r.bottom - top;
    if (!isFinite(left) || !isFinite(top) || !isFinite(width) || !isFinite(height)) return null;
    return {
      left: left, top: top, right: left + width, bottom: top + height,
      width: width, height: height, cx: left + width / 2, cy: top + height / 2
    };
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
    var cone = options && options.cone != null ? options.cone : config.cone;
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

  /* ---------- DOM layer ---------- */

  function filter(list, fn) {
    var out = [];
    for (var i = 0; list && i < list.length; i++) if (fn(list[i])) out.push(list[i]);
    return out;
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
    if (typeof global.getComputedStyle === "function") {
      var cs = global.getComputedStyle(el);
      if (cs && (cs.visibility === "hidden" || cs.display === "none")) return false;
    }
    return true;
  }

  function scopeRoot() {
    // Only a MODAL dialog (showModal) traps focus; a modeless <dialog open> must not.
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

  function focusEl(el) {
    if (!el || typeof el.focus !== "function") return false;
    try { el.focus({ preventScroll: true }); } catch (e) { el.focus(); }
    if (typeof el.scrollIntoView === "function") {
      try { el.scrollIntoView({ block: "nearest", inline: "nearest" }); } catch (e2) { /* old engines */ }
    }
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
      var all = focusables(scope);
      return all.length ? focusEl(all[0]) : false;
    }
    var candidates = [];
    var list = focusables(scope);
    for (var i = 0; i < list.length; i++) {
      if (list[i] === current || current.contains(list[i]) || list[i].contains(current)) continue;
      candidates.push({ el: list[i], rect: list[i].getBoundingClientRect() });
    }
    var pick = pickNearest(direction, current.getBoundingClientRect(), candidates, config);
    return pick ? focusEl(pick.el) : false;
  }

  function onKeydown(event) {
    if (!event || event.defaultPrevented) return;
    var direction = KEY_TO_DIRECTION[event.key];
    if (!direction) return;
    if (moveFocus(direction) && typeof event.preventDefault === "function") event.preventDefault();
  }

  function configure(options) {
    if (options) for (var k in options) if (options[k] !== undefined) config[k] = options[k];
    return config;
  }

  function start() {
    if (!doc || started) return api;
    started = true;
    doc.addEventListener("keydown", onKeydown);
    return api;
  }

  function stop() {
    if (!doc || !started) return api;
    started = false;
    doc.removeEventListener("keydown", onKeydown);
    return api;
  }

  var started = false;

  var api = {
    DIRECTIONS: DIRECTIONS.slice(),
    KEY_TO_DIRECTION: KEY_TO_DIRECTION,
    defaults: defaults,
    pure: {
      normRect: normRect,
      directionScore: directionScore,
      pickNearest: pickNearest
    },
    configure: configure,
    start: start,
    stop: stop,
    moveFocus: moveFocus,
    isStarted: function () { return started; }
  };

  global.tvNav = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;

  if (doc) {
    if (doc.readyState === "loading" && typeof doc.addEventListener === "function") {
      doc.addEventListener("DOMContentLoaded", function () { start(); });
    } else {
      start();
    }
  }
})(typeof window !== "undefined" ? window : globalThis);
