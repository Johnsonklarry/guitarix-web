/* forge_core/static/gamepad.js: compatibility layer for the ForgeGamepad API over the shared kit
   (theme-kit/gamepad.js, vendored at ./theme-kit/gamepad.js next to this file). ForgeGamepad.enable(opts) and
   disable() keep working for existing callers; the kit does the navigation. If a page includes only this file,
   it loads the kit itself with auto-start off, so enable() still decides when navigation runs.
   New pages should include theme-kit/gamepad.js directly and use window.GamepadNav. */
(function (global) {
  "use strict";

  var STANDARD_MAPPING = {
    BUTTON_A: 0, BUTTON_B: 1, BUTTON_X: 2, BUTTON_Y: 3,
    BUTTON_LB: 4, BUTTON_RB: 5, TRIGGER_LT: 6, TRIGGER_RT: 7,
    BUTTON_VIEW: 8, BUTTON_MENU: 9, STICK_L_PRESS: 10, STICK_R_PRESS: 11,
    DPAD_UP: 12, DPAD_DOWN: 13, DPAD_LEFT: 14, DPAD_RIGHT: 15,
    AXIS_LX: 0, AXIS_LY: 1, AXIS_RX: 2, AXIS_RY: 3
  };

  // Kit action names -> the names legacy enable({onAction}) callers were given, and back.
  var TO_LEGACY = { activate: "select", prev: "prev_tab", next: "next_tab", view: "toggle_tv" };
  var FROM_LEGACY = { select: "activate", prev_tab: "prev", next_tab: "next", toggle_tv: "view" };

  var doc = global.document;
  var script = doc && doc.currentScript;
  var kitSrc = script && script.src ? script.src.replace(/gamepad\.js(\?.*)?$/, "theme-kit/gamepad.js") : null;
  var pending = [];
  var waiting = false;
  var failed = false;
  var enabled = false;

  function kit() { return global.GamepadNav || null; }

  function ensureKit() {
    if (kit() || !doc || !kitSrc || doc.querySelector("script[data-gamepad-kit]")) return;
    var tag = doc.createElement("script");
    tag.src = kitSrc;
    tag.defer = true;
    tag.setAttribute("data-gamepad-kit", "");
    tag.setAttribute("data-nav-auto", "false");
    (doc.head || doc.documentElement).appendChild(tag);
  }

  function flush() {
    var k = kit();
    if (!k) return false;
    var queue = pending.splice(0);
    for (var i = 0; i < queue.length; i++) queue[i](k);
    return true;
  }

  function whenReady(fn) {
    var k = kit();
    if (k) { fn(k); return; }
    if (failed) return;
    pending.push(fn);
    ensureKit();
    if (waiting) return;
    waiting = true;
    var tries = 0;
    (function check() {
      if (flush()) { waiting = false; return; }
      if (++tries > 600) {
        waiting = false;
        failed = true;
        pending.length = 0;
        console.warn("ForgeGamepad: theme-kit/gamepad.js did not load");
        return;
      }
      if (typeof global.requestAnimationFrame === "function") global.requestAnimationFrame(check);
      else global.setTimeout(check, 16);
    })();
  }

  function wrapOnAction(fn) {
    if (typeof fn !== "function") return null;
    return function (action) { return fn(TO_LEGACY[action] || action); };
  }

  var ForgeGamepad = {
    STANDARD_MAPPING: STANDARD_MAPPING,
    enable: function (opts) {
      if (enabled) return;
      opts = opts || {};
      enabled = true;
      if (global.navigator && typeof global.navigator.getGamepads !== "function") {
        console.warn("ForgeGamepad: this browser has no Gamepad API (navigator.getGamepads)");
      }
      whenReady(function (k) {
        if (!enabled) return;
        k.configure({ onAction: wrapOnAction(opts.onAction) });
        if (!k.isStarted()) k.start();
      });
    },
    disable: function () {
      if (!enabled) return;
      enabled = false;
      whenReady(function (k) { if (!enabled && k.isStarted()) k.stop(); });
    },
    _handleAction: function (action) {
      var k = kit();
      return k ? k.handleAction(FROM_LEGACY[action] || action) : false;
    },
    _moveFocus: function (direction) {
      var k = kit();
      return k ? k.moveFocus(direction) : false;
    }
  };

  // Say so in the console when a controller appears but the kit never arrived (for example a 404 on theme-kit/gamepad.js).
  if (typeof global.addEventListener === "function") {
    global.addEventListener("gamepadconnected", function () {
      if (enabled && !kit()) console.warn("ForgeGamepad: controller connected but theme-kit/gamepad.js is not loaded");
    });
  }

  global.ForgeGamepad = ForgeGamepad;
})(typeof window !== "undefined" ? window : globalThis);
