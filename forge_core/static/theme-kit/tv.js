/* Load after theme-kit/theme.js and theme-kit/gamepad.js. Add tv.css separately. */
(function (win) {
  "use strict";

  var doc = win.document;
  var kit = win.ThemeKit;
  var nav = win.GamepadNav;
  if (!doc || !kit || !nav) return;

  var storageKey = "theme-kit-tv";
  var cursor = null;
  var positionFrame = null;
  var active = false;
  var ltHeld = false;
  var rtHeld = false;
  var panicTimer = null;
  var panicArmed = true;

  function cancelPanicTimer() {
    if (panicTimer !== null) {
      win.clearTimeout(panicTimer);
      panicTimer = null;
    }
  }

  function panic() {
    panicTimer = null;
    if (!active || !ltHeld || !rtHeld) return;
    panicArmed = false;

    // Hosts can provide actions directly or mark their existing controls.
    // Separate actions let a host make mute idempotent rather than toggling it.
    if (typeof kit.tv.bypassAll === "function") {
      kit.tv.bypassAll();
    } else {
      var bypass = doc.querySelector("[data-tv-panic-bypass]");
      if (bypass) bypass.click();
    }
    if (typeof kit.tv.muteOutput === "function") {
      kit.tv.muteOutput();
    } else {
      var mute = doc.querySelector("[data-tv-panic-mute]");
      if (mute) mute.click();
    }
    doc.dispatchEvent(new win.CustomEvent("tv:panic", { bubbles: true }));
  }

  function onTriggers(lt, rt) {
    ltHeld = !!lt;
    rtHeld = !!rt;
    if (!ltHeld || !rtHeld) cancelPanicTimer();
    if (!ltHeld && !rtHeld) panicArmed = true;
    if (active && ltHeld && rtHeld && panicArmed && panicTimer === null) {
      panicTimer = win.setTimeout(panic, 500);
    }
  }

  function storedOn() {
    try {
      return win.localStorage.getItem(storageKey) === "on";
    } catch (error) {
      return false;
    }
  }

  function persist(value) {
    try {
      win.localStorage.setItem(storageKey, value ? "on" : "off");
    } catch (error) {
      // TV mode still works for this page.
    }
  }

  function cursorEnabled() {
    return active && doc.documentElement.getAttribute("data-tv-cursor") === "on";
  }

  function removeCursor() {
    if (positionFrame !== null) {
      win.cancelAnimationFrame(positionFrame);
      positionFrame = null;
    }
    if (cursor) {
      cursor.remove();
      cursor = null;
    }
  }

  function positionCursor() {
    positionFrame = null;
    if (!cursorEnabled() || !cursor) return;
    var focused = doc.activeElement;
    if (!focused || !focused.classList || !focused.classList.contains("gp-focus") ||
        typeof focused.getBoundingClientRect !== "function") {
      cursor.style.opacity = "0";
      return;
    }
    var rect = focused.getBoundingClientRect();
    if (!rect.width && !rect.height) {
      cursor.style.opacity = "0";
      return;
    }
    cursor.style.transform = "translate3d(" +
      Math.round(rect.left + rect.width / 2) + "px," +
      Math.round(rect.top + rect.height / 2) + "px,0)";
    cursor.style.opacity = "1";
  }

  function scheduleCursor() {
    if (!cursorEnabled() || positionFrame !== null) return;
    positionFrame = win.requestAnimationFrame(positionCursor);
  }

  function syncCursor() {
    if (!cursorEnabled() || !doc.body) {
      removeCursor();
      return;
    }
    if (!cursor) {
      cursor = doc.createElement("div");
      cursor.className = "tk-tv-cursor";
      cursor.setAttribute("aria-hidden", "true");
      doc.body.appendChild(cursor);
    }
    scheduleCursor();
  }

  function enable(save) {
    active = true;
    if (ltHeld && rtHeld && panicArmed && panicTimer === null) {
      panicTimer = win.setTimeout(panic, 500);
    }
    doc.documentElement.setAttribute("data-tv", "on");
    syncCursor();
    if (save) persist(true);
    return true;
  }

  function disable(save) {
    active = false;
    cancelPanicTimer();
    doc.documentElement.removeAttribute("data-tv");
    removeCursor();
    if (save) persist(false);
    return false;
  }

  var keys = {
    ArrowUp: "up",
    ArrowDown: "down",
    ArrowLeft: "left",
    ArrowRight: "right",
    Enter: "activate",
    NumpadEnter: "activate",
    Escape: "back",
    Backspace: "back",
    BrowserBack: "back",
    GoBack: "back",
    Select: "activate",
    OK: "activate",
    MediaSelect: "activate",
    MediaPlayPause: "activate",
    MediaStop: "back"
  };

  function isEditing(target) {
    if (!target || target.nodeType !== 1) return false;
    if (target.isContentEditable) return true;
    if (target.closest && target.closest("[contenteditable]:not([contenteditable='false'])")) return true;
    return /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName);
  }

  function onKeydown(event) {
    if (!active || event.defaultPrevented || event.altKey || event.ctrlKey ||
        event.metaKey || event.shiftKey || isEditing(event.target)) return;
    var action = keys[event.key] || (event.code === "NumpadEnter" ? "activate" : null);
    if (!action) return;
    if (nav.handleAction(action)) {
      event.preventDefault();
      scheduleCursor();
    }
  }

  doc.addEventListener("keydown", onKeydown);
  doc.addEventListener("focusin", scheduleCursor);
  doc.addEventListener("scroll", scheduleCursor, true);
  win.addEventListener("resize", scheduleCursor);

  kit.tv = {
    on: function () { return enable(true); },
    off: function () { return disable(true); },
    toggle: function () { return active ? disable(true) : enable(true); }
  };
  nav.onTriggers(onTriggers);

  var queryOn = false;
  try {
    queryOn = new win.URLSearchParams(win.location.search).get("tv") === "1";
  } catch (error) {
    // An unavailable or malformed location does not prevent attribute or saved opt-in.
  }
  if (doc.documentElement.getAttribute("data-tv") === "on" || queryOn || storedOn()) {
    enable(false);
  }
})(window);
