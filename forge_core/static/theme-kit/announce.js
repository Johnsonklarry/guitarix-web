(function () {
  "use strict";

  var kit = window.ThemeKit = window.ThemeKit || {};
  var POLITE_ID = "tk-announce-polite";
  var ASSERTIVE_ID = "tk-announce-assertive";
  var DEDUPE_MS = 1000;
  var lastMessage = "";
  var lastAt = 0;
  var regions = null;

  // Fixed allowlist of state words -> sentence fragments. Unknown states fall back to
  // "<name> status: <state>" so nothing is silently dropped.
  var STATES = {
    online: "is online",
    offline: "is offline",
    running: "is running",
    stopped: "has stopped",
    healthy: "is healthy",
    unhealthy: "needs attention",
    error: "hit a problem",
    idle: "is idle"
  };

  function region(id, live) {
    var node = document.getElementById(id);
    if (!node) {
      node = document.createElement("div");
      node.id = id;
      node.className = "tk-sr-only";
      node.setAttribute("aria-live", live);
      node.setAttribute("aria-atomic", "true");
      node.setAttribute("role", "status");
      (document.body || document.documentElement).appendChild(node);
    }
    return node;
  }

  function ensureRegions() {
    if (!regions) {
      regions = {
        polite: region(POLITE_ID, "polite"),
        assertive: region(ASSERTIVE_ID, "assertive")
      };
    }
    return regions;
  }

  function announce(message, options) {
    var text = message === null || message === undefined ? "" : String(message);
    if (!text) return false;
    var assertive = !!(options && options.assertive);
    var now = Date.now();
    // Debounce: the same message within 1 s is not repeated (screen readers would stutter).
    if (text === lastMessage && now - lastAt < DEDUPE_MS) return false;
    lastMessage = text;
    lastAt = now;
    var node = ensureRegions()[assertive ? "assertive" : "polite"];
    // textContent only: never innerHTML, so announced text can never inject markup.
    node.textContent = text;
    return true;
  }

  function describeState(key, value) {
    var name = key === null || key === undefined ? "" : String(key);
    var state = value === null || value === undefined ? "" : String(value);
    var phrase = STATES[state];
    if (phrase) return name ? name + " " + phrase : phrase;
    return name ? name + " status: " + state : "status: " + state;
  }

  kit.announce = announce;
  kit.describeState = describeState;
  kit.announceStates = STATES;
})();
