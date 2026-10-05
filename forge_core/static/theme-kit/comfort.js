(function () {
  "use strict";

  var root = document.documentElement;
  var storageKey = "theme-kit-comfort";
  var levels = ["standard", "comfortable", "large", "max"];
  var state = { level: "standard", contrast: false, motion: true };

  function read() {
    try {
      var value = JSON.parse(window.localStorage.getItem(storageKey));
      if (!value || typeof value !== "object") return null;
      return value;
    } catch (error) {
      return null;
    }
  }

  function save() {
    try {
      window.localStorage.setItem(storageKey, JSON.stringify(state));
    } catch (error) {
      // The preference still applies to this page.
    }
  }

  function get() {
    return {
      level: state.level,
      contrast: state.contrast,
      motion: state.motion
    };
  }

  function apply() {
    root.setAttribute("data-comfort", state.level);
    root.setAttribute("data-comfort-contrast", state.contrast ? "on" : "off");
    root.setAttribute("data-comfort-motion", state.motion ? "on" : "off");
    save();
    document.dispatchEvent(new CustomEvent("themekit:comfort", { detail: get() }));
  }

  function set(level) {
    if (levels.indexOf(level) === -1) {
      throw new RangeError("Unknown comfort level: " + level);
    }
    state.level = level;
    apply();
    return get();
  }

  function toggleContrast() {
    state.contrast = !state.contrast;
    apply();
    return get();
  }

  function mount(container) {
    if (!container || typeof container.appendChild !== "function") {
      throw new TypeError("Expected a container element");
    }

    var wrapper = document.createElement("div");
    var title = document.createElement("span");
    var levelLabel = document.createElement("label");
    var select = document.createElement("select");
    var contrastLabel = document.createElement("label");
    var contrast = document.createElement("input");
    var motionLabel = document.createElement("label");
    var motion = document.createElement("input");
    var labels = {
      standard: "Standard",
      comfortable: "Comfortable",
      large: "Large",
      max: "Maximum"
    };
    var id = "theme-kit-comfort-" + (++mount.count);

    wrapper.className = "tk-comfort";
    title.textContent = "Easier to read";
    levelLabel.setAttribute("for", id);
    levelLabel.textContent = "Bigger text";
    select.id = id;
    select.className = "tk-tap";
    levels.forEach(function (level) {
      var option = document.createElement("option");
      option.value = level;
      option.textContent = labels[level];
      select.appendChild(option);
    });

    contrast.type = "checkbox";
    contrastLabel.appendChild(contrast);
    contrastLabel.appendChild(document.createTextNode(" Stronger contrast"));
    motion.type = "checkbox";
    motionLabel.appendChild(motion);
    motionLabel.appendChild(document.createTextNode(" Reduce motion"));

    function update() {
      var current = get();
      select.value = current.level;
      contrast.checked = current.contrast;
      motion.checked = !current.motion;
    }

    function onLevel() { set(select.value); }
    function onContrast() { toggleContrast(); }
    function onMotion() {
      state.motion = !motion.checked;
      apply();
    }

    select.addEventListener("change", onLevel);
    contrast.addEventListener("change", onContrast);
    motion.addEventListener("change", onMotion);
    document.addEventListener("themekit:comfort", update);

    wrapper.appendChild(title);
    wrapper.appendChild(levelLabel);
    wrapper.appendChild(select);
    wrapper.appendChild(contrastLabel);
    wrapper.appendChild(motionLabel);
    container.appendChild(wrapper);
    update();

    return function () {
      document.removeEventListener("themekit:comfort", update);
      select.removeEventListener("change", onLevel);
      contrast.removeEventListener("change", onContrast);
      motion.removeEventListener("change", onMotion);
      wrapper.remove();
    };
  }
  mount.count = 0;

  var saved = read();
  var initialLevel = root.getAttribute("data-comfort");
  state.level = saved && levels.indexOf(saved.level) !== -1
    ? saved.level
    : (levels.indexOf(initialLevel) !== -1 ? initialLevel : "standard");
  state.contrast = saved && typeof saved.contrast === "boolean"
    ? saved.contrast
    : root.getAttribute("data-comfort-contrast") === "on";
  state.motion = saved && typeof saved.motion === "boolean"
    ? saved.motion
    : root.getAttribute("data-comfort-motion") !== "off";
  apply();

  // Works whether comfort.js or theme.js is loaded first.
  var comfort = {
    set: set,
    get: get,
    toggleContrast: toggleContrast,
    mount: mount
  };
  window.ThemeKit = window.ThemeKit || {};
  window.ThemeKit.comfort = comfort;
  window.ThemeKitComfort = comfort;
}());
