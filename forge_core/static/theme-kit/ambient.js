(function () {
  "use strict";

  var names = ["embers", "rain", "stars", "snow", "fireflies"];
  var root = document.documentElement;
  var reduced = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
  var slow = window.matchMedia ? window.matchMedia("(update: slow)") : null;
  var canvas = null;
  var context = null;
  var particles = [];
  var frame = null;
  var previous = null;

  function list() {
    return names.slice();
  }

  function set(name) {
    if (names.indexOf(name) === -1) throw new RangeError("Unknown ambient background: " + name);
    root.setAttribute("data-ambient", name);
    sync();
    return name;
  }

  function off() {
    root.removeAttribute("data-ambient");
    sync();
    return null;
  }

  function stopFrame() {
    if (frame !== null) window.cancelAnimationFrame(frame);
    frame = null;
    previous = null;
  }

  function removeCanvas() {
    stopFrame();
    if (canvas) canvas.remove();
    canvas = null;
    context = null;
    particles = [];
    root.removeAttribute("data-ambient-render");
  }

  function blocked() {
    return document.hidden || root.getAttribute("data-fx") === "off" ||
      (reduced && reduced.matches) || (slow && slow.matches);
  }

  function opacityCap() {
    var styles = window.getComputedStyle(root);
    var requested = parseFloat(styles.getPropertyValue("--ambient-max-opacity"));
    if (!Number.isFinite(requested)) requested = .35;
    var oled = root.getAttribute("data-mode") === "oled" ||
      root.getAttribute("data-oled") === "true";
    return Math.max(0, Math.min(requested, oled ? .12 : .35));
  }

  function resize() {
    if (!canvas) return;
    var ratio = Math.min(window.devicePixelRatio || 1, 2);
    var width = window.innerWidth;
    var height = window.innerHeight;
    canvas.width = Math.ceil(width * ratio);
    canvas.height = Math.ceil(height * ratio);
    canvas.style.width = width + "px";
    canvas.style.height = height + "px";
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    particles = [];
    var count = Math.min(120, Math.ceil(width * height / 12000));
    for (var i = 0; i < count; i++) {
      particles.push({
        x: Math.random() * width,
        y: Math.random() * height,
        speed: .35 + Math.random() * .8,
        size: 1 + Math.random() * 2
      });
    }
  }

  function tick(timestamp) {
    frame = null;
    if (blocked() || !canvas) return;
    var delta = previous === null ? 0 : Math.min(50, Math.max(0, timestamp - previous));
    previous = timestamp;
    var width = window.innerWidth;
    var height = window.innerHeight;
    var rain = root.getAttribute("data-ambient") === "rain";
    context.clearRect(0, 0, width, height);
    context.fillStyle = window.getComputedStyle(root).getPropertyValue(
      rain ? "--info" : "--accent"
    ).trim();
    context.strokeStyle = context.fillStyle;
    context.lineWidth = 1.5;
    for (var i = 0; i < particles.length; i++) {
      var particle = particles[i];
      particle.y += (rain ? 3 : -.7) * particle.speed * delta / 16;
      particle.x += (rain ? -.5 : .2) * particle.speed * delta / 16;
      if (particle.y > height + 20) particle.y = -20;
      if (particle.y < -20) particle.y = height + 20;
      if (particle.x < -20) particle.x = width + 20;
      if (particle.x > width + 20) particle.x = -20;
      context.save();
      context.translate(particle.x, particle.y);
      context.globalAlpha = .4 + particle.speed * .5;
      if (rain) {
        context.beginPath();
        context.moveTo(0, 0);
        context.lineTo(-3, 13);
        context.stroke();
      } else {
        context.beginPath();
        context.arc(0, 0, particle.size, 0, Math.PI * 2);
        context.fill();
      }
      context.restore();
    }
    frame = window.requestAnimationFrame(tick);
  }

  function sync() {
    var name = root.getAttribute("data-ambient");
    var wantsCanvas = root.getAttribute("data-ambient-canvas") === "on" &&
      (name === "rain" || name === "embers") && !blocked() && document.body;

    if (!wantsCanvas) {
      removeCanvas();
      return;
    }
    if (!canvas) {
      canvas = document.createElement("canvas");
      canvas.className = "tk-ambient-canvas";
      context = canvas.getContext("2d");
      if (!context) {
        canvas = null;
        return; // Keep the CSS layers as the fallback.
      }
      document.body.appendChild(canvas);
      resize();
    }
    canvas.style.opacity = String(opacityCap());
    root.setAttribute("data-ambient-render", "canvas");
    if (frame === null) frame = window.requestAnimationFrame(tick);
  }

  function mediaChanged() {
    sync();
  }

  [reduced, slow].forEach(function (query) {
    if (!query) return;
    if (query.addEventListener) query.addEventListener("change", mediaChanged);
    else if (query.addListener) query.addListener(mediaChanged);
  });

  document.addEventListener("visibilitychange", sync);
  window.addEventListener("resize", function () {
    if (canvas) resize();
  });
  new MutationObserver(sync).observe(root, {
    attributes: true,
    attributeFilter: [
      "data-ambient", "data-ambient-canvas", "data-fx",
      "data-theme", "data-mode", "data-oled"
    ]
  });
  if (document.body) sync();
  else document.addEventListener("DOMContentLoaded", sync, { once: true });

  var api = { set: set, off: off, list: list };
  window.ThemeKitAmbient = api;
  if (window.ThemeKit) window.ThemeKit.ambient = api;
}());
