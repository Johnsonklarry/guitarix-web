/* forge_core/static/theme-kit/chime.js: optional Web Audio chime presets for the theme kit.
   Exposes window.ThemeKitChime = { play, presets, setMuted, isMuted }. The mute flag is
   persisted in localStorage under "theme-kit-chime-muted" and mirrored on document as the
   "themekit:chime-muted" CustomEvent. play() returns false (a no-op) while muted, and also
   when the browser exposes neither AudioContext nor webkitAudioContext. The shared
   AudioContext is created lazily on the first unmuted play. */
(function (global) {
  "use strict";

  var mutedKey = "theme-kit-chime-muted";

  // Each preset is a list of steps: { freq (Hz), start (s), duration (s), type (osc) }.
  var presets = {
    done: [
      { freq: 659.25, start: 0, duration: 0.12, type: "sine" },
      { freq: 987.77, start: 0.12, duration: 0.2, type: "sine" }
    ],
    notice: [
      { freq: 880, start: 0, duration: 0.16, type: "sine" }
    ],
    warn: [
      { freq: 392, start: 0, duration: 0.16, type: "triangle" },
      { freq: 261.63, start: 0.16, duration: 0.3, type: "triangle" }
    ]
  };

  var context = null;

  function contextConstructor() {
    return global.AudioContext || global.webkitAudioContext || null;
  }

  function sharedContext() {
    if (context) return context;
    var Ctor = contextConstructor();
    if (!Ctor) return null;
    context = new Ctor();
    return context;
  }

  function isMuted() {
    var value;
    try {
      value = global.localStorage ? global.localStorage.getItem(mutedKey) : null;
    } catch (error) {
      return false; // Storage can be unavailable or hold an obsolete value.
    }
    if (value === true) return true;
    return value === "true";
  }

  function setMuted(muted) {
    muted = !!muted;
    try {
      if (global.localStorage) {
        global.localStorage.setItem(mutedKey, muted ? "true" : "false");
      }
    } catch (error) {
      // Muting still applies to the current page when storage is unavailable.
    }
    var doc = global.document;
    if (doc && typeof doc.dispatchEvent === "function") {
      var event;
      try {
        event = new global.CustomEvent("themekit:chime-muted", { detail: muted });
      } catch (error) {
        event = null;
      }
      if (event) doc.dispatchEvent(event);
    }
    return muted;
  }

  function playStep(ctx, step, now) {
    var start = now + (step.start || 0);
    var duration = Math.max(0.02, step.duration || 0.1);
    var end = start + duration;
    var attack = Math.min(0.02, duration / 2);
    var oscillator = ctx.createOscillator();
    var gain = ctx.createGain();

    oscillator.type = step.type || "sine";
    if (oscillator.frequency && typeof oscillator.frequency.setValueAtTime === "function") {
      oscillator.frequency.setValueAtTime(step.freq, start);
    }
    var envelope = gain.gain;
    envelope.setValueAtTime(0.0001, start);
    envelope.exponentialRampToValueAtTime(0.25, start + attack);
    envelope.exponentialRampToValueAtTime(0.0001, end);

    oscillator.connect(gain);
    gain.connect(ctx.destination);
    oscillator.start(start);
    oscillator.stop(end);
  }

  function play(name) {
    var preset = presets[name];
    if (!preset) throw new RangeError("Unknown chime: " + name);
    if (isMuted()) return false;
    var ctx = sharedContext();
    if (!ctx) return false;
    if (ctx.state === "suspended" && typeof ctx.resume === "function") {
      ctx.resume();
    }
    var now = typeof ctx.currentTime === "number" ? ctx.currentTime : 0;
    for (var i = 0; i < preset.length; i++) {
      playStep(ctx, preset[i], now);
    }
    return true;
  }

  global.ThemeKitChime = {
    play: play,
    presets: presets,
    setMuted: setMuted,
    isMuted: isMuted
  };
})(typeof window !== "undefined" ? window : this);
