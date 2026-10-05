(function () {
  "use strict";

  var root = document.documentElement;
  var storageKey = "theme-kit-v2";
  var legacyStorageKey = "theme-kit-choice";
  // native: the look the theme was drawn in (no data-mode = that look). Only the three
  // daytime themes (shire, minas-tirith, bag-end-doorway) are light-native; every other theme is dark-native.
  var themes = [
    { id: "bag-end", name: "Fireside", native: "dark" },
    { id: "middle-earth", name: "Middle-earth", native: "dark" },
    { id: "plex-amber", name: "Amber", native: "dark" },
    { id: "amp-lamp", name: "Amp lamp", native: "dark" },
    { id: "studio-glass", name: "Studio", native: "dark" },
    { id: "mission-control", name: "Console", native: "dark" },
    { id: "shire", name: "The Shire", native: "light" },
    { id: "pipeweed", name: "Pipeweed", native: "dark" },
    { id: "woodland-realm", name: "Woodland Realm", native: "dark" },
    { id: "minas-tirith", name: "White City", native: "light" },
    { id: "mount-doom", name: "Mount Doom", native: "dark" },
    { id: "balrog", name: "Balrog", native: "dark" },
    { id: "bag-end-doorway", name: "Bag End Doorway", native: "light" }
  ];
  var modes = ["light", "dark", "oled", "system"];
  var modeNames = { light: "Light", dark: "Dark", oled: "OLED", system: "System" };
  // The old generic themes are now bag-end in the matching mode.
  var legacyThemeModes = { light: "light", dark: "dark" };
  var catalogStorageKey = "theme-kit-catalog";
  var mountedSwitchers = [];
  var listeners = [];
  var choice = null;
  var hiddenIds = readHidden();
  var colorPreference = window.matchMedia
    ? window.matchMedia("(prefers-color-scheme: dark)")
    : null;

  function validId(id) {
    return themes.some(function (theme) { return theme.id === id; });
  }

  function validMode(mode) {
    return modes.indexOf(mode) !== -1 && (mode !== "system" || colorPreference !== null);
  }

  function nativeMode(id) {
    for (var i = 0; i < themes.length; i++) {
      if (themes[i].id === id) return themes[i].native || "dark";
    }
    return "dark";
  }

  function resolveMode(mode) {
    if (mode === "system") return colorPreference && colorPreference.matches ? "dark" : "light";
    return mode;
  }

  function validateThemeEntry(entry) {
    if (!entry || typeof entry !== "object") return false;
    if (typeof entry.id !== "string" || !/^[a-z0-9-]{1,40}$/.test(entry.id)) return false;
    if (typeof entry.name !== "string") return false;
    if (entry.native !== "light" && entry.native !== "dark") return false;
    return true;
  }

  function mergeCatalog(entries) {
    if (!Array.isArray(entries)) return false;
    var changed = false;
    entries.forEach(function (entry) {
      if (!validateThemeEntry(entry)) return;
      if (!validId(entry.id)) {
        themes.push({ id: entry.id, name: entry.name, native: entry.native });
        changed = true;
      }
    });
    return changed;
  }

  function notifyCatalog() {
    mountedSwitchers.forEach(function (fn) {
      fn();
    });
    document.dispatchEvent(new CustomEvent("themekit:catalog", { detail: themes.slice() }));
  }

  function applyCatalogData(entries) {
    var changed = mergeCatalog(entries);
    if (changed) {
      notifyCatalog();
    }
    return changed;
  }

  function loadCachedCatalog() {
    var cached = readStorage(catalogStorageKey);
    if (Array.isArray(cached) && mergeCatalog(cached)) {
      // Switchers mount after this runs; tell them (and listeners) once the page has set up.
      setTimeout(notifyCatalog, 0);
    }
  }

  function loadCatalog(url) {
    url = url || "/forge/theme-kit/themes.json";
    if (typeof window.fetch !== "function") {
      return Promise.resolve(themes);
    }
    return window.fetch(url)
      .then(function (response) {
        if (!response.ok) return null;
        return response.json();
      })
      .then(function (data) {
        // themes.json is {version, themes: [...]}; a bare array is accepted too.
        var list = Array.isArray(data) ? data : (data && Array.isArray(data.themes) ? data.themes : null);
        if (list) {
          var validEntries = list.filter(validateThemeEntry);
          try {
            window.localStorage.setItem(catalogStorageKey, JSON.stringify(validEntries));
          } catch (error) {
            // Storage unavailable
          }
          applyCatalogData(validEntries);
        }
        return themes;
      })
      .catch(function () {
        return themes;
      });
  }

  function normalise(id, mode, back) {
    if (!validMode(mode)) mode = nativeMode(id);
    if (!validMode(back) || back === "oled") back = mode === "oled" ? nativeMode(id) : mode;
    return { id: id, mode: mode, back: back };
  }

  function readStorage(key) {
    try {
      var value = window.localStorage.getItem(key);
      return value ? JSON.parse(value) : null;
    } catch (error) {
      return null; // Storage can be unavailable or hold an obsolete value.
    }
  }

  // v2 value: {id, mode, back}; back = the mode toggleOled() returns to.
  function readChoice() {
    var parsed = readStorage(storageKey);
    if (parsed && validId(parsed.id)) return normalise(parsed.id, parsed.mode, parsed.back);
    return null;
  }

  // Legacy "theme-kit-choice" {id, oled}: oled true -> mode oled; the removed generic
  // "light" / "dark" themes become bag-end in that mode; otherwise the theme's native mode.
  function readLegacyChoice() {
    var parsed = readStorage(legacyStorageKey);
    if (!parsed || typeof parsed.id !== "string") return null;
    var id = parsed.id;
    var mode = null;
    if (Object.prototype.hasOwnProperty.call(legacyThemeModes, id)) {
      mode = legacyThemeModes[id];
      id = "bag-end";
    }
    if (!validId(id)) return null;
    if (mode === null) mode = nativeMode(id);
    if (parsed.oled === true) return normalise(id, "oled", mode);
    return normalise(id, mode, mode);
  }

  function saveChoice(value) {
    try {
      window.localStorage.setItem(storageKey, JSON.stringify(value));
    } catch (error) {
      // The current page can still use the selected theme.
    }
  }

  function readHidden() {
    var stored = readStorage("theme-kit-hidden");
    if (!Array.isArray(stored)) return [];
    return stored.filter(function (id, index) {
      return validId(id) && stored.indexOf(id) === index;
    });
  }

  function hidden() {
    return hiddenIds.slice();
  }

  function visibleThemes() {
    return themes.filter(function (theme) {
      return hiddenIds.indexOf(theme.id) === -1;
    });
  }

  function hiddenChanged() {
    try {
      window.localStorage.setItem("theme-kit-hidden", JSON.stringify(hiddenIds));
    } catch (error) {
      // Hiding still works on the current page when storage is unavailable.
    }
    document.dispatchEvent(new CustomEvent("themekit:hidden", { detail: hidden() }));
  }

  function hide(id) {
    if (!validId(id)) throw new RangeError("Unknown theme: " + id);
    if (hiddenIds.indexOf(id) === -1 && visibleThemes().length > 1) {
      hiddenIds.push(id);
      hiddenChanged();
    }
    return hidden();
  }

  function unhide(id) {
    if (!validId(id)) throw new RangeError("Unknown theme: " + id);
    var index = hiddenIds.indexOf(id);
    if (index !== -1) {
      hiddenIds.splice(index, 1);
      hiddenChanged();
    }
    return hidden();
  }

  function notify() {
    var current = get();
    listeners.slice().forEach(function (listener) {
      listener(current);
    });
  }

  function apply(next) {
    var resolved = resolveMode(next.mode);
    var changed = root.getAttribute("data-theme") !== next.id ||
      root.getAttribute("data-mode") !== resolved ||
      !choice || choice.mode !== next.mode;
    root.setAttribute("data-theme", next.id);
    root.setAttribute("data-mode", resolved);
    // data-oled stays in sync for consumers that still key their own CSS on it.
    root.setAttribute("data-oled", String(resolved === "oled"));
    choice = next;
    if (changed) notify();
  }

  function get() {
    var resolved = resolveMode(choice.mode);
    return { id: choice.id, mode: choice.mode, resolvedMode: resolved, oled: resolved === "oled" };
  }

  function set(id, options) {
    if (!validId(id)) throw new RangeError("Unknown theme: " + id);
    var mode = choice.mode;
    var back = choice.back;
    if (options && Object.prototype.hasOwnProperty.call(options, "mode")) {
      if (!validMode(options.mode)) throw new RangeError("Unknown mode: " + options.mode);
      mode = options.mode;
      if (mode !== "oled") back = mode;
    } else if (options && Object.prototype.hasOwnProperty.call(options, "oled")) {
      // Old call shape: set(id, {oled}).
      mode = options.oled ? "oled" : (choice.mode === "oled" ? choice.back : choice.mode);
      if (mode !== "oled") back = mode;
    }
    apply(normalise(id, mode, back));
    saveChoice(choice);
    return get();
  }

  function setMode(mode) {
    return set(choice.id, { mode: mode });
  }

  function toggleOled() {
    return setMode(choice.mode === "oled" ? choice.back : "oled");
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

  // Two controls: a Theme <select> and a Mode group of segmented buttons
  // (Light / Dark / OLED / System), 44px targets via .tk-tap, aria-pressed on the active mode.
  function mountSwitcher(container, options) {
    if (!container || typeof container.appendChild !== "function") {
      throw new TypeError("Expected a container element");
    }
    options = options || {};

    var wrapper = document.createElement("div");
    var label = document.createElement("label");
    var select = document.createElement("select");
    var group = document.createElement("fieldset");
    var groupLabel = document.createElement("legend");
    var selectId = "theme-kit-select-" + (++switcherCount);
    var modeName = "tk-mode-" + switcherCount;
    var radios = [];
    var details = document.createElement("details");
    var summary = document.createElement("summary");
    var checkboxes = [];

    label.setAttribute("for", selectId);
    label.textContent = "Theme";
    select.id = selectId;

    summary.textContent = "Manage themes…";
    summary.style.minHeight = "44px";
    summary.style.color = "var(--text)";
    details.appendChild(summary);
    themes.forEach(function (theme) {
      var row = document.createElement("label");
      var checkbox = document.createElement("input");
      var text = document.createElement("span");
      row.style.display = "flex";
      row.style.alignItems = "center";
      row.style.minHeight = "44px";
      row.style.color = "var(--text)";
      checkbox.type = "checkbox";
      checkbox.style.minWidth = "44px";
      checkbox.style.minHeight = "44px";
      checkbox.setAttribute("aria-label", "Show " + theme.name);
      text.textContent = "Show " + theme.name;
      checkbox.addEventListener("change", function () {
        if (checkbox.checked) unhide(theme.id);
        else hide(theme.id);
        update(get());
      });
      row.appendChild(checkbox);
      row.appendChild(text);
      details.appendChild(row);
      checkboxes.push({ id: theme.id, checkbox: checkbox });
    });

    groupLabel.textContent = "Mode";
    group.className = "tk-modes";
    group.appendChild(groupLabel);
    modes.forEach(function (mode) {
      if (!validMode(mode)) return; // "system" needs matchMedia
      var label = document.createElement("label");
      var radio = document.createElement("input");
      var face = document.createElement("span");
      var text = document.createElement("span");
      var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      var shape = document.createElementNS("http://www.w3.org/2000/svg", "path");
      var icons = {
        light: "M12 2v2m0 16v2M4.93 4.93l1.42 1.42m11.3 11.3 1.42 1.42M2 12h2m16 0h2M4.93 19.07l1.42-1.42m11.3-11.3 1.42-1.42M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z",
        dark: "M20.3 15.6A8.5 8.5 0 0 1 8.4 3.7 8.5 8.5 0 1 0 20.3 15.6Z",
        oled: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Zm0 4 .8 2.2L15 9l-2.2.8L12 12l-.8-2.2L9 9l2.2-.8L12 6Z",
        system: "M12 2a10 10 0 1 0 0 20V2Zm0 0a10 10 0 0 1 0 20"
      };
      label.className = "tk-mode";
      label.setAttribute("data-mode", mode);
      radio.type = "radio";
      radio.name = modeName;
      radio.value = mode;
      radio.setAttribute("aria-label", modeNames[mode]);
      radio.addEventListener("change", function () {
        if (radio.checked) setMode(mode);
      });
      face.className = "tk-mode-face";
      face.setAttribute("aria-hidden", "true");
      svg.setAttribute("viewBox", "0 0 24 24");
      svg.setAttribute("width", "20");
      svg.setAttribute("height", "20");
      svg.setAttribute("fill", "none");
      svg.setAttribute("stroke", "currentColor");
      svg.setAttribute("stroke-width", "1.8");
      svg.setAttribute("stroke-linecap", "round");
      svg.setAttribute("stroke-linejoin", "round");
      shape.setAttribute("d", icons[mode]);
      svg.appendChild(shape);
      face.appendChild(svg);
      text.className = "tk-mode-text";
      text.textContent = modeNames[mode];
      label.appendChild(radio);
      label.appendChild(face);
      label.appendChild(text);
      group.appendChild(label);
      radios.push(radio);
    });

    wrapper.className = "tk-bar";
    select.className = "tk-tap";

    function update(value) {
      var shown = visibleThemes();
      var ids = shown.map(function (theme) { return theme.id; });
      while (select.firstChild) select.removeChild(select.firstChild);
      themes.forEach(function (theme) {
        if (ids.indexOf(theme.id) === -1 && theme.id !== value.id) return;
        var option = document.createElement("option");
        option.value = theme.id;
        option.textContent = theme.name;
        select.appendChild(option);
      });
      select.value = value.id;
      checkboxes.forEach(function (entry) {
        var isShown = hiddenIds.indexOf(entry.id) === -1;
        entry.checkbox.checked = isShown;
        entry.checkbox.disabled = isShown && shown.length === 1;
      });
      radios.forEach(function (radio) {
        radio.checked = radio.value === value.mode;
      });
    }

    select.addEventListener("change", function () {
      set(select.value);
    });

    wrapper.appendChild(label);
    wrapper.appendChild(select);
    wrapper.appendChild(group);
    wrapper.appendChild(details);
    container.appendChild(wrapper);
    var unmountComfort = null;
    if (options.comfort && window.ThemeKit && window.ThemeKit.comfort) {
      unmountComfort = window.ThemeKit.comfort.mount(wrapper);
    }
    update(get());

    var rerenderSwitcher = function () { update(get()); };
    mountedSwitchers.push(rerenderSwitcher);

    var unsubscribe = onChange(update);
    var hiddenListener = function () { update(get()); };
    document.addEventListener("themekit:hidden", hiddenListener);
    return function () {
      var switcherIndex = mountedSwitchers.indexOf(rerenderSwitcher);
      if (switcherIndex !== -1) mountedSwitchers.splice(switcherIndex, 1);
      unsubscribe();
      document.removeEventListener("themekit:hidden", hiddenListener);
      if (unmountComfort) unmountComfort();
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

  loadCachedCatalog();

  // Start-up order: saved v2 choice, migrated legacy choice, the page's own attributes
  // (data-theme / data-mode, legacy data-oled, legacy theme ids), data-theme-auto, bag-end.
  var stored = readChoice();
  var legacy = stored ? null : readLegacyChoice();
  var initialId = root.getAttribute("data-theme");
  var initialMode = root.getAttribute("data-mode");
  if (stored) {
    apply(stored);
  } else if (legacy) {
    apply(legacy);
    saveChoice(choice);
  } else {
    if (Object.prototype.hasOwnProperty.call(legacyThemeModes, initialId)) {
      initialMode = legacyThemeModes[initialId];
      initialId = "bag-end";
    }
    if (!validId(initialId)) initialId = "bag-end";
    if (!validMode(initialMode)) {
      if (root.getAttribute("data-oled") === "true") initialMode = "oled";
      else if (root.getAttribute("data-theme-auto") === "true" && colorPreference) initialMode = "system";
      else initialMode = nativeMode(initialId);
    }
    apply(normalise(initialId, initialMode, initialMode));
  }

  if (colorPreference) {
    var systemChanged = function () {
      if (choice.mode === "system") apply(choice);
    };
    if (colorPreference.addEventListener) {
      colorPreference.addEventListener("change", systemChanged);
    } else if (colorPreference.addListener) {
      colorPreference.addListener(systemChanged);
    }
  }

  var comfort = window.ThemeKit && window.ThemeKit.comfort || window.ThemeKitComfort;
  window.ThemeKit = {
    comfort: comfort,
    themes: themes,
    modes: modes,
    hide: hide,
    unhide: unhide,
    hidden: hidden,
    visibleThemes: visibleThemes,
    loadCatalog: loadCatalog,
    get: get,
    set: set,
    setMode: setMode,
    toggleOled: toggleOled,
    mountSwitcher: mountSwitcher,
    onChange: onChange,
    animate: animate,
    burnInGuard: burnInGuard,
    ambient: window.ThemeKitAmbient
  };
}());
