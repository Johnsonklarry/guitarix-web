(function () {
  "use strict";

  var root = document.documentElement;
  var storageKey = "theme-kit-choice";
  var themes = [
    { id: "bag-end", name: "Fireside" },
    { id: "middle-earth", name: "Middle-earth" },
    { id: "plex-amber", name: "Amber" },
    { id: "amp-lamp", name: "Amp lamp" },
    { id: "studio-glass", name: "Studio" },
    { id: "mission-control", name: "Console" },
    { id: "light", name: "Light" },
    { id: "dark", name: "Dark" },
    { id: "shire", name: "The Shire" },
    { id: "pipeweed", name: "Pipeweed" },
    { id: "woodland-realm", name: "Woodland Realm" },
    { id: "minas-tirith", name: "White City" },
    { id: "mount-doom", name: "Mount Doom" },
    { id: "balrog", name: "Balrog" }
  ];
  var listeners = [];
  var choice = null;
  var followsSystem = false;
  var colorPreference = window.matchMedia
    ? window.matchMedia("(prefers-color-scheme: dark)")
    : null;

  function validId(id) {
    return themes.some(function (theme) { return theme.id === id; });
  }

  function readChoice() {
    try {
      var value = window.localStorage.getItem(storageKey);
      if (!value) return null;
      var parsed = JSON.parse(value);
      if (parsed && validId(parsed.id) && typeof parsed.oled === "boolean") {
        return { id: parsed.id, oled: parsed.oled };
      }
    } catch (error) {
      // Storage can be unavailable or contain an obsolete value.
    }
    return null;
  }

  function saveChoice(value) {
    try {
      window.localStorage.setItem(storageKey, JSON.stringify(value));
    } catch (error) {
      // The current page can still use the selected theme.
    }
  }

  function notify() {
    var current = get();
    listeners.slice().forEach(function (listener) {
      listener(current);
    });
  }

  function apply(id, oled) {
    var changed = root.getAttribute("data-theme") !== id ||
      root.getAttribute("data-oled") !== String(oled);
    root.setAttribute("data-theme", id);
    root.setAttribute("data-oled", String(oled));
    choice = { id: id, oled: oled };
    if (changed) notify();
  }

  function get() {
    return { id: choice.id, oled: choice.oled };
  }

  function set(id, options) {
    if (!validId(id)) throw new RangeError("Unknown theme: " + id);
    var oled = options && Object.prototype.hasOwnProperty.call(options, "oled")
      ? Boolean(options.oled)
      : choice.oled;
    followsSystem = false;
    apply(id, oled);
    saveChoice(choice);
    return get();
  }

  function toggleOled() {
    return set(choice.id, { oled: !choice.oled });
  }

  function onChange(fn) {
    if (typeof fn !== "function") throw new TypeError("Expected a function");
    listeners.push(fn);
    return function () {
      var index = listeners.indexOf(fn);
      if (index !== -1) listeners.splice(index, 1);
    };
  }

  var switcherCount = 0;

  function mountSwitcher(container) {
    if (!container || typeof container.appendChild !== "function") {
      throw new TypeError("Expected a container element");
    }

    var wrapper = document.createElement("div");
    var label = document.createElement("label");
    var select = document.createElement("select");
    var button = document.createElement("button");
    var selectId = "theme-kit-select-" + (++switcherCount);

    label.setAttribute("for", selectId);
    label.textContent = "Theme";
    select.id = selectId;
    themes.forEach(function (theme) {
      var option = document.createElement("option");
      option.value = theme.id;
      option.textContent = theme.name;
      select.appendChild(option);
    });

    wrapper.className = "tk-bar";
    select.className = "tk-tap";
    button.className = "tk-tap";
    button.type = "button";
    button.textContent = "OLED mode";

    function update(value) {
      select.value = value.id;
      button.setAttribute("aria-pressed", String(value.oled));
    }

    select.addEventListener("change", function () {
      set(select.value, { oled: get().oled });
    });
    button.addEventListener("click", toggleOled);

    wrapper.appendChild(label);
    wrapper.appendChild(select);
    wrapper.appendChild(button);
    container.appendChild(wrapper);
    update(get());

    var unsubscribe = onChange(update);
    return function () {
      unsubscribe();
      wrapper.remove();
    };
  }

  function animate(fn) {
    if (typeof fn !== "function") throw new TypeError("Expected a function");
    var reduced = window.matchMedia
      ? window.matchMedia("(prefers-reduced-motion: reduce)")
      : null;
    var frame = null;
    var previous = null;
    var stopped = false;

    function stopFrame() {
      if (frame !== null) window.cancelAnimationFrame(frame);
      frame = null;
      previous = null;
    }

    function tick(timestamp) {
      frame = null;
      if (stopped || document.hidden) return;
      var delta = previous === null ? 0 : Math.max(0, timestamp - previous);
      previous = timestamp;
      fn(delta);
      if (!stopped && !document.hidden) frame = window.requestAnimationFrame(tick);
    }

    function resume() {
      stopFrame();
      if (stopped || document.hidden) return;
      if (reduced && reduced.matches) {
        // Infinity lets time-based animations clamp directly to their final state.
        fn(Infinity);
      } else {
        frame = window.requestAnimationFrame(tick);
      }
    }

    function visibilityChanged() {
      if (document.hidden) stopFrame();
      else resume();
    }

    document.addEventListener("visibilitychange", visibilityChanged);
    if (reduced) {
      if (reduced.addEventListener) reduced.addEventListener("change", resume);
      else if (reduced.addListener) reduced.addListener(resume);
    }
    resume();

    return function () {
      stopped = true;
      stopFrame();
      document.removeEventListener("visibilitychange", visibilityChanged);
      if (reduced) {
        if (reduced.removeEventListener) reduced.removeEventListener("change", resume);
        else if (reduced.removeListener) reduced.removeListener(resume);
      }
    };
  }

  function burnInGuard(options) {
    options = options || {};
    var shiftPx = Number.isFinite(options.shiftPx) ? Math.max(0, options.shiftPx) : 2;
    var everyMs = Number.isFinite(options.everyMs) ? Math.max(1, options.everyMs) : 120000;
    var dimAfterMs = Number.isFinite(options.dimAfterMs)
      ? Math.max(1, options.dimAfterMs) : 600000;
    var originalTranslate = root.style.translate;
    var originalFilter = root.style.filter;
    var shiftTimer = null;
    var dimTimer = null;
    var step = 0;
    var disposed = false;
    var positions = [[0, 0], [1, 0], [1, 1], [0, 1], [-1, 1],
      [-1, 0], [-1, -1], [0, -1], [1, -1]];

    function clearTimers() {
      window.clearTimeout(shiftTimer);
      window.clearTimeout(dimTimer);
      shiftTimer = null;
      dimTimer = null;
    }

    function restore() {
      root.style.translate = originalTranslate;
      root.style.filter = originalFilter;
    }

    function shift() {
      if (disposed || !get().oled) return;
      step = (step + 1) % positions.length;
      root.style.translate = (positions[step][0] * shiftPx) + "px " +
        (positions[step][1] * shiftPx) + "px";
      shiftTimer = window.setTimeout(shift, everyMs);
    }

    function reset() {
      clearTimers();
      restore();
      step = 0;
      if (disposed || !get().oled) return;
      shiftTimer = window.setTimeout(shift, everyMs);
      dimTimer = window.setTimeout(function () {
        if (!disposed && get().oled) root.style.filter = "brightness(0.75)";
      }, dimAfterMs);
    }

    var unsubscribe = onChange(reset);
    ["pointerdown", "keydown", "touchstart", "focusin"].forEach(function (event) {
      document.addEventListener(event, reset, { passive: true });
    });
    reset();

    return function () {
      disposed = true;
      unsubscribe();
      clearTimers();
      restore();
      ["pointerdown", "keydown", "touchstart", "focusin"].forEach(function (event) {
        document.removeEventListener(event, reset);
      });
    };
  }

  var stored = readChoice();
  var initialId = root.getAttribute("data-theme");
  var initialOled = root.getAttribute("data-oled") === "true";
  if (stored) {
    apply(stored.id, stored.oled);
  } else if (validId(initialId)) {
    apply(initialId, initialOled);
  } else if (root.getAttribute("data-theme-auto") === "true" && colorPreference) {
    followsSystem = true;
    apply(colorPreference.matches ? "dark" : "light", initialOled);
  } else {
    apply("bag-end", initialOled);
  }

  if (colorPreference) {
    var systemChanged = function (event) {
      if (followsSystem) apply(event.matches ? "dark" : "light", get().oled);
    };
    if (colorPreference.addEventListener) {
      colorPreference.addEventListener("change", systemChanged);
    } else if (colorPreference.addListener) {
      colorPreference.addListener(systemChanged);
    }
  }

  window.ThemeKit = {
    themes: themes,
    get: get,
    set: set,
    toggleOled: toggleOled,
    mountSwitcher: mountSwitcher,
    onChange: onChange,
    animate: animate,
    burnInGuard: burnInGuard
  };
}());
