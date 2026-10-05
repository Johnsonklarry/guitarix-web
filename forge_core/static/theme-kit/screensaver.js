(function () {
  "use strict";

  var root = document.documentElement;
  var overlay = document.createElement("div");
  var clock = document.createElement("div");
  var time = document.createElement("div");
  var date = document.createElement("div");
  var glance = document.createElement("div");
  var visible = false;
  var minuteTimer = null;
  var gamepadTimer = null;
  var observer = null;

  overlay.className = "tk-screensaver";
  overlay.setAttribute("aria-hidden", "true");
  clock.className = "tk-screensaver-clock";
  time.className = "tk-screensaver-time";
  date.className = "tk-screensaver-date";
  glance.className = "tk-screensaver-glance";
  clock.appendChild(time);
  clock.appendChild(date);
  clock.appendChild(glance);
  overlay.appendChild(clock);
  root.appendChild(overlay);

  function enabled() {
    var setting = root.getAttribute("data-screensaver");
    return setting === "on" ||
      (setting !== "off" &&
        (root.getAttribute("data-mode") === "oled" ||
          root.getAttribute("data-oled") === "true"));
  }

  function updateClock() {
    var now = new Date();
    var minute = Math.floor(now.getTime() / 60000);
    var positions = [
      [0, 0], [5, -4], [-4, -7], [-7, 2], [2, 6],
      [8, 3], [3, -5], [-5, 5], [-8, -3], [1, 7],
      [7, -2], [-2, -6], [-6, 4]
    ];
    var position = positions[minute % positions.length];
    var source = document.querySelector("[data-screensaver-glance]");

    time.textContent = now.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    date.textContent = now.toLocaleDateString([], {
      weekday: "long", month: "long", day: "numeric"
    });
    glance.textContent = source ? source.textContent.trim() : "";
    glance.hidden = !glance.textContent;
    clock.style.transform = "translate3d(" + position[0] + "vw, " +
      position[1] + "vh, 0)";
  }

  function scheduleClock() {
    window.clearTimeout(minuteTimer);
    if (!visible) return;
    updateClock();
    minuteTimer = window.setTimeout(scheduleClock, 60000 - (Date.now() % 60000) + 20);
  }

  function gamepadInput() {
    if (!visible || !window.navigator ||
        typeof window.navigator.getGamepads !== "function") return;
    var pads = window.navigator.getGamepads();
    if (!pads) return;
    for (var i = 0; i < pads.length; i++) {
      var pad = pads[i];
      if (!pad) continue;
      var buttons = pad.buttons || [];
      var axes = pad.axes || [];
      for (var j = 0; j < buttons.length; j++) {
        if (buttons[j] && buttons[j].pressed) {
          dismiss();
          return;
        }
      }
      for (var k = 0; k < axes.length; k++) {
        if (Math.abs(axes[k]) > 0.25) {
          dismiss();
          return;
        }
      }
    }
  }

  function show() {
    if (visible) return;
    visible = true;
    scheduleClock();
    overlay.setAttribute("aria-hidden", "false");
    overlay.classList.add("tk-screensaver-visible");
    if (window.navigator && typeof window.navigator.getGamepads === "function") {
      gamepadTimer = window.setInterval(gamepadInput, 100);
    }
  }

  function hide() {
    if (!visible) return;
    visible = false;
    overlay.classList.remove("tk-screensaver-visible");
    overlay.setAttribute("aria-hidden", "true");
    window.clearTimeout(minuteTimer);
    window.clearInterval(gamepadTimer);
    minuteTimer = null;
    gamepadTimer = null;
  }

  function dismiss() {
    hide();
    root.setAttribute("data-idle", "false");
  }

  function sync() {
    if (root.getAttribute("data-idle") === "true" && enabled()) show();
    else hide();
  }

  function enable(min) {
    if (min !== undefined) {
      if (!Number.isFinite(min) || min <= 0) {
        throw new RangeError("Idle minutes must be a positive number");
      }
      root.setAttribute("data-idle-minutes", String(min));
    }
    root.setAttribute("data-screensaver", "on");
    sync();
  }

  function disable() {
    root.setAttribute("data-screensaver", "off");
    dismiss();
  }

  ["keydown", "pointerdown", "touchstart"].forEach(function (name) {
    document.addEventListener(name, function () {
      if (visible) dismiss();
    }, true);
  });
  window.addEventListener("gamepadconnected", function () {
    if (visible) dismiss();
  });

  if (typeof MutationObserver === "function") {
    observer = new MutationObserver(sync);
    observer.observe(root, {
      attributes: true,
      attributeFilter: ["data-idle", "data-mode", "data-oled", "data-screensaver"]
    });
  }

  window.ThemeKit = window.ThemeKit || {};
  window.ThemeKit.screensaver = {
    enable: enable,
    disable: disable,
    show: show,
    hide: hide
  };
  sync();
}());
