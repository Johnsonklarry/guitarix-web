/* forge_core/static/theme-kit/vu.js: Canvas 2D VU needle meter.
   mountMeter(container, options) appends <canvas class="tk-vu" role="meter"> and returns
   { setLevel, getLevel, destroy }. The gauge arc spans -50deg..+50deg, tinted from the live
   theme (--text-muted track, --accent below the red zone, --fault above 0.8). The needle eases
   toward the target through ThemeKit.animate when the kit is loaded (requestAnimationFrame
   otherwise) and the meter redraws on ThemeKit.onChange. Exposes window.ThemeKitVU. */
(function (global) {
  "use strict";

  var DEG = Math.PI / 180;
  var START_ANGLE = -50 * DEG;
  var END_ANGLE = 50 * DEG;
  var RED_ZONE = 0.8;
  var EASE_MS = 160;

  function clamp(value) {
    var number = Number(value);
    if (!isFinite(number)) return 0;
    if (number < 0) return 0;
    if (number > 1) return 1;
    return number;
  }

  function themeColors() {
    var styles = global.getComputedStyle && global.document
      ? global.getComputedStyle(global.document.documentElement)
      : null;
    function read(name, fallback) {
      if (!styles || typeof styles.getPropertyValue !== "function") return fallback;
      var value = styles.getPropertyValue(name);
      return value ? String(value).trim() : fallback;
    }
    return {
      muted: read("--text-muted", "#8a8a8a"),
      accent: read("--accent", "#e8a33c"),
      fault: read("--fault", "#c4643f")
    };
  }

  function mountMeter(container, options) {
    if (!container || typeof container.appendChild !== "function") {
      throw new TypeError("Expected a container element");
    }
    options = options || {};

    var canvas = global.document.createElement("canvas");
    canvas.className = "tk-vu";
    canvas.setAttribute("role", "meter");
    canvas.setAttribute("aria-valuemin", "0");
    canvas.setAttribute("aria-valuemax", "1");
    canvas.setAttribute("aria-valuenow", "0");

    var width = Number(options.width) || Number(canvas.width) || 180;
    var height = Number(options.height) || Number(canvas.height) || 120;
    canvas.width = width;
    canvas.height = height;

    container.appendChild(canvas);

    var ctx = typeof canvas.getContext === "function" ? canvas.getContext("2d") : null;
    var level = 0;
    var shown = 0;
    var stopAnim = null;
    var unsubscribe = null;

    function draw() {
      if (!ctx) return;
      var w = canvas.width;
      var h = canvas.height;
      var cx = w / 2;
      var cy = h * 0.86;
      var radius = Math.min(w * 0.42, h * 0.78);
      var colors = themeColors();
      var value = clamp(shown);

      ctx.clearRect(0, 0, w, h);
      ctx.lineCap = "round";
      ctx.lineWidth = Math.max(6, radius * 0.14);

      // Muted track under the whole sweep.
      ctx.strokeStyle = colors.muted;
      ctx.beginPath();
      ctx.arc(cx, cy, radius, START_ANGLE, END_ANGLE);
      ctx.stroke();

      var valueAngle = START_ANGLE + (END_ANGLE - START_ANGLE) * value;
      var redAngle = START_ANGLE + (END_ANGLE - START_ANGLE) * RED_ZONE;

      // Accent up to the red zone, fault above it.
      if (valueAngle > START_ANGLE) {
        ctx.strokeStyle = colors.accent;
        ctx.beginPath();
        ctx.arc(cx, cy, radius, START_ANGLE, Math.min(valueAngle, redAngle));
        ctx.stroke();
      }
      if (valueAngle > redAngle) {
        ctx.strokeStyle = colors.fault;
        ctx.beginPath();
        ctx.arc(cx, cy, radius, redAngle, valueAngle);
        ctx.stroke();
      }

      // Red-zone tick mark.
      ctx.strokeStyle = colors.fault;
      ctx.lineWidth = Math.max(2, radius * 0.03);
      ctx.beginPath();
      ctx.moveTo(cx + Math.cos(redAngle) * radius * 0.82, cy + Math.sin(redAngle) * radius * 0.82);
      ctx.lineTo(cx + Math.cos(redAngle) * radius, cy + Math.sin(redAngle) * radius);
      ctx.stroke();

      // Needle.
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(valueAngle);
      ctx.strokeStyle = colors.accent;
      ctx.lineWidth = Math.max(2, radius * 0.05);
      ctx.beginPath();
      ctx.moveTo(0, 0);
      ctx.lineTo(radius * 0.92, 0);
      ctx.stroke();
      ctx.restore();

      // Hub.
      ctx.fillStyle = colors.accent;
      ctx.beginPath();
      ctx.arc(cx, cy, Math.max(3, radius * 0.08), 0, Math.PI * 2);
      ctx.fill();
    }

    function tick(delta) {
      if (delta === Infinity) {
        shown = level;
      } else {
        var step = Math.max(0, Math.min(1, delta / EASE_MS));
        shown += (level - shown) * step;
        if (Math.abs(level - shown) < 0.001) shown = level;
      }
      draw();
    }

    function start() {
      if (stopAnim) return;
      if (global.ThemeKit && typeof global.ThemeKit.animate === "function") {
        stopAnim = global.ThemeKit.animate(tick) || null;
      } else if (typeof global.requestAnimationFrame === "function") {
        var stopped = false;
        var previous = null;
        var frame = null;
        var loop = function (timestamp) {
          frame = null;
          if (stopped) return;
          var delta = previous === null ? 0 : Math.max(0, timestamp - previous);
          previous = timestamp;
          tick(delta);
          if (!stopped) frame = global.requestAnimationFrame(loop);
        };
        frame = global.requestAnimationFrame(loop);
        stopAnim = function () {
          stopped = true;
          if (frame !== null && typeof global.cancelAnimationFrame === "function") {
            global.cancelAnimationFrame(frame);
          }
          frame = null;
        };
      }
    }

    if (global.ThemeKit && typeof global.ThemeKit.onChange === "function") {
      unsubscribe = global.ThemeKit.onChange(function () { draw(); });
    }

    start();
    draw();

    return {
      setLevel: function (value) {
        level = clamp(value);
        canvas.setAttribute("aria-valuenow", String(level));
        return level;
      },
      getLevel: function () { return level; },
      destroy: function () {
        if (typeof stopAnim === "function") { stopAnim(); stopAnim = null; }
        if (typeof unsubscribe === "function") { unsubscribe(); unsubscribe = null; }
        if (canvas.parentNode && typeof canvas.parentNode.removeChild === "function") {
          canvas.parentNode.removeChild(canvas);
        } else if (typeof canvas.remove === "function") {
          canvas.remove();
        }
      }
    };
  }

  function attachInput(meter, stream, options) {
    if (!meter || typeof meter.setLevel !== "function") {
      throw new TypeError("Expected a meter with a setLevel function");
    }
    if (!stream || typeof stream.getAudioTracks !== "function") {
      throw new TypeError("Expected a MediaStream-like object");
    }
    options = options || {};

    var context = options.context || null;
    var ownsContext = false;
    if (!context) {
      var AudioContextCtor = global.AudioContext || global.webkitAudioContext;
      if (typeof AudioContextCtor !== "function") {
        throw new TypeError("AudioContext is not available");
      }
      context = new AudioContextCtor();
      ownsContext = true;
    }

    var source = context.createMediaStreamSource(stream);
    var analyser = context.createAnalyser();
    analyser.fftSize = 1024;
    source.connect(analyser);

    var buffer = new Float32Array(analyser.fftSize);
    var stopAnim = null;

    function measure() {
      analyser.getFloatTimeDomainData(buffer);
      var sum = 0;
      for (var i = 0; i < buffer.length; i++) {
        sum += buffer[i] * buffer[i];
      }
      var rms = Math.sqrt(sum / buffer.length);
      if (!(rms > 0)) {
        meter.setLevel(0);
        return;
      }
      meter.setLevel(clamp((20 * Math.log10(rms) + 48) / 48));
    }

    function start() {
      if (stopAnim) return;
      if (global.ThemeKit && typeof global.ThemeKit.animate === "function") {
        stopAnim = global.ThemeKit.animate(function () { measure(); }) || null;
      } else if (typeof global.requestAnimationFrame === "function") {
        var stopped = false;
        var frame = null;
        var loop = function () {
          frame = null;
          if (stopped) return;
          measure();
          if (!stopped) frame = global.requestAnimationFrame(loop);
        };
        frame = global.requestAnimationFrame(loop);
        stopAnim = function () {
          stopped = true;
          if (frame !== null && typeof global.cancelAnimationFrame === "function") {
            global.cancelAnimationFrame(frame);
          }
          frame = null;
        };
      }
    }

    start();

    return function detach() {
      if (typeof stopAnim === "function") { stopAnim(); stopAnim = null; }
      if (source && typeof source.disconnect === "function") source.disconnect();
      if (analyser && typeof analyser.disconnect === "function") analyser.disconnect();
      if (ownsContext && context && typeof context.close === "function") {
        context.close();
      }
    };
  }

  global.ThemeKitVU = { mountMeter: mountMeter, attachInput: attachInput };
})(typeof window !== "undefined" ? window : globalThis);
