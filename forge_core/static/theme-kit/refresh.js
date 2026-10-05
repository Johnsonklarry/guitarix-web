(function () {
  'use strict';

  try {
    var root = document.documentElement;
    var rates = [60, 75, 90, 100, 120, 144, 165, 240, 360];
    var raf = window.requestAnimationFrame;
    var cancelRaf = window.cancelAnimationFrame;
    var clock = window.performance && typeof window.performance.now === 'function'
      ? function () { return window.performance.now(); }
      : function () { return Date.now(); };
    var listeners = [];
    var stopped = false;
    var frameId = null;
    var resizeTimer = null;
    var idleTimer = null;
    var idleObserver = null;
    var rate = 60;
    var target = 1000 / rate;
    var previous = null;
    var detectionCount = 0;
    var detectionTimes = [];
    var recent = [];
    var history = [];
    var badSince = null;
    var goodSince = null;
    var ownsLite = false;
    var hudElement = null;
    var hudText = null;
    var hudCanvas = null;
    var lastText = -Infinity;
    var lastDraw = -Infinity;

    function safe(fn) {
      return function () {
        try { return fn.apply(null, arguments); } catch (error) { return undefined; }
      };
    }

    function listen(targetElement, name, fn, options) {
      if (targetElement && typeof targetElement.addEventListener === 'function') {
        var handler = safe(fn);
        targetElement.addEventListener(name, handler, options);
        listeners.push([targetElement, name, handler, options]);
      }
    }

    function visible() {
      return document.visibilityState !== 'hidden';
    }

    function percentile(values, fraction) {
      var sorted = values.slice().sort(function (a, b) { return a - b; });
      return sorted.length ? sorted[Math.min(sorted.length - 1, Math.ceil(sorted.length * fraction) - 1)] : 0;
    }

    function startDetection() {
      detectionCount = 0;
      detectionTimes = [];
      previous = null;
      recent = [];
      history = [];
      badSince = null;
      goodSince = null;
    }

    function finishDetection() {
      if (!detectionTimes.length) return;
      var median = percentile(detectionTimes, 0.5);
      var best = rates[0];
      rates.forEach(function (candidate) {
        if (Math.abs(candidate - 1000 / median) < Math.abs(best - 1000 / median)) best = candidate;
      });
      rate = best;
      target = 1000 / best;
      root.dataset.refresh = String(best);
      if (root.style && typeof root.style.setProperty === 'function') {
        root.style.setProperty('--frame-ms', target.toFixed(2) + 'ms');
      }
      detectionTimes = [];
    }

    function oled() {
      return root.dataset.mode === 'oled' || root.dataset.oled === 'true';
    }

    function resetIdle() {
      if (idleTimer !== null && typeof window.clearTimeout === 'function') window.clearTimeout(idleTimer);
      idleTimer = null;
      if (root.dataset.screensaver === 'off' ||
          (!oled() && root.dataset.screensaver !== 'on')) {
        delete root.dataset.idle;
        return;
      }
      root.dataset.idle = 'false';
      var minutes = Number(root.dataset.idleMinutes);
      if (!isFinite(minutes) || minutes <= 0) minutes = 10;
      if (typeof window.setTimeout === 'function') {
        idleTimer = window.setTimeout(safe(function () {
          idleTimer = null;
          if (!stopped && oled()) root.dataset.idle = 'true';
        }), minutes * 60000);
      }
    }

    function gamepadInput() {
      var nav = window.navigator;
      if (!nav || typeof nav.getGamepads !== 'function') return;
      var pads = nav.getGamepads();
      if (!pads) return;
      for (var i = 0; i < pads.length; i++) {
        var pad = pads[i];
        if (!pad) continue;
        var buttons = pad.buttons || [];
        var axes = pad.axes || [];
        for (var j = 0; j < buttons.length; j++) {
          if (buttons[j] && buttons[j].pressed) { resetIdle(); return; }
        }
        for (var k = 0; k < axes.length; k++) {
          if (Math.abs(axes[k]) > 0.25) { resetIdle(); return; }
        }
      }
    }

    function govern(t) {
      if (!recent.length) return;
      var times = recent.map(function (sample) { return sample.dt; });
      var bad = percentile(times, 0.99) > 1.6 * target;
      if (root.dataset.fx === 'off') ownsLite = false;
      if (bad) {
        goodSince = null;
        if (badSince === null) badSince = t;
        if (t - badSince >= 5000 && root.dataset.fx !== 'off') {
          root.dataset.fx = 'lite';
          ownsLite = true;
        }
      } else {
        badSince = null;
        if (goodSince === null) goodSince = t;
        if (t - goodSince >= 30000 && ownsLite) {
          if (root.dataset.fx === 'lite') delete root.dataset.fx;
          ownsLite = false;
        }
      }
    }

    function removeHud() {
      if (hudElement && hudElement.parentNode) hudElement.parentNode.removeChild(hudElement);
      hudElement = null;
      hudText = null;
      hudCanvas = null;
    }

    function makeHud() {
      if (hudElement || !document.createElement || !root.appendChild) return;
      var box = document.createElement('div');
      var text = document.createElement('div');
      var canvas = document.createElement('canvas');
      box.setAttribute('aria-hidden', 'true');
      box.style.cssText = 'position:fixed;top:8px;right:8px;z-index:2147483647;pointer-events:none;padding:8px;background:var(--surface,#111);color:var(--text,#fff);font:12px/1.4 monospace;white-space:pre;border:1px solid var(--accent,#6cf)';
      canvas.width = 120;
      canvas.height = 32;
      canvas.style.display = 'block';
      box.appendChild(text);
      box.appendChild(canvas);
      root.appendChild(box);
      hudElement = box;
      hudText = text;
      hudCanvas = canvas;
      lastText = -Infinity;
      lastDraw = -Infinity;
    }

    function hud(on) {
      if (stopped) return false;
      if (typeof on === 'undefined') on = !hudElement;
      if (on) makeHud();
      else removeHud();
      return !!hudElement;
    }

    function updateHud(t) {
      if (!hudElement) return;
      if (t - lastText >= 250) {
        var times = recent.map(function (sample) { return sample.dt; });
        var worst = times.length ? Math.max.apply(null, times) : 0;
        var low = percentile(times, 0.99);
        var dropped = times.filter(function (dt) { return dt > 1.5 * target; }).length;
        hudText.textContent = rate + ' Hz | ' + times.length + ' fps | 1% low ' +
          (low ? (1000 / low).toFixed(0) : '0') + ' fps\nworst ' +
          worst.toFixed(1) + ' ms | dropped ' + dropped + ' | fx ' +
          (root.dataset.fx || 'full');
        lastText = t;
      }
      var reduced = false;
      if (typeof window.matchMedia === 'function') {
        reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      }
      if (t - lastDraw < (reduced ? 250 : 1000 / 30)) return;
      lastDraw = t;
      var ctx = hudCanvas.getContext && hudCanvas.getContext('2d');
      if (!ctx) return;
      ctx.clearRect(0, 0, 120, 32);
      var scale = 32 / (target * 3);
      var line = Math.max(0, 32 - target * scale);
      ctx.strokeStyle = '#888';
      ctx.beginPath();
      ctx.moveTo(0, line);
      ctx.lineTo(120, line);
      ctx.stroke();
      ctx.strokeStyle = '#6cf';
      ctx.beginPath();
      history.forEach(function (dt, i) {
        var y = Math.max(0, 32 - dt * scale);
        if (i === 0) ctx.moveTo(i, y);
        else ctx.lineTo(i, y);
      });
      ctx.stroke();
    }

    function schedule() {
      if (!stopped && visible() && typeof raf === 'function') frameId = raf.call(window, safe(frame));
    }

    function frame(timestamp) {
      frameId = null;
      if (stopped || !visible()) return;
      var t = typeof timestamp === 'number' && isFinite(timestamp) ? timestamp : clock();
      if (previous !== null) {
        var dt = t - previous;
        if (dt > 0 && dt <= 100) {
          if (detectionCount < 100) {
            detectionCount++;
            if (detectionCount > 10) detectionTimes.push(dt);
            if (detectionCount === 100) finishDetection();
          }
          recent.push({ t: t, dt: dt });
          history.push(dt);
          if (history.length > 120) history.shift();
        }
      }
      previous = t;
      while (recent.length && t - recent[0].t > 1000) recent.shift();
      govern(t);
      gamepadInput();
      updateHud(t);
      schedule();
    }

    function visibilityChanged() {
      var show = visible();
      root.dataset.visible = String(show);
      if (!show) {
        if (frameId !== null && typeof cancelRaf === 'function') cancelRaf.call(window, frameId);
        frameId = null;
        previous = null;
        badSince = null;
        goodSince = null;
      } else {
        startDetection();
        if (frameId === null) schedule();
      }
    }

    function stop() {
      if (stopped) return;
      stopped = true;
      listeners.forEach(function (entry) {
        if (typeof entry[0].removeEventListener === 'function') {
          entry[0].removeEventListener(entry[1], entry[2], entry[3]);
        }
      });
      listeners = [];
      if (frameId !== null && typeof cancelRaf === 'function') cancelRaf.call(window, frameId);
      frameId = null;
      if (resizeTimer !== null && typeof window.clearTimeout === 'function') window.clearTimeout(resizeTimer);
      if (idleTimer !== null && typeof window.clearTimeout === 'function') window.clearTimeout(idleTimer);
      resizeTimer = null;
      idleTimer = null;
      if (idleObserver) idleObserver.disconnect();
      removeHud();
    }

    if (!root || !root.dataset) throw new Error('No document element');
    window.ForgeRefresh = {
      get rate() { return rate; },
      get quality() { return root.dataset.fx || 'full'; },
      hud: safe(hud),
      stop: safe(stop)
    };

    listen(document, 'visibilitychange', visibilityChanged);
    listen(window, 'resize', function () {
      if (resizeTimer !== null && typeof window.clearTimeout === 'function') window.clearTimeout(resizeTimer);
      if (typeof window.setTimeout === 'function') {
        resizeTimer = window.setTimeout(safe(function () {
          resizeTimer = null;
          startDetection();
          if (visible() && frameId === null) schedule();
        }), 1000);
      }
    });
    ['pointerdown', 'pointermove', 'keydown', 'wheel', 'touchstart', 'gamepadconnected'].forEach(function (eventName) {
      listen(window, eventName, resetIdle);
    });
    // Shift+Alt+F toggles the frame-pacing HUD.
    listen(window, 'keydown', function (event) {
      if (event.shiftKey && event.altKey && String(event.key).toLowerCase() === 'f') {
        if (typeof event.preventDefault === 'function') event.preventDefault();
        hud();
      }
    });
    root.dataset.visible = String(visible());
    if (typeof MutationObserver === 'function') {
      idleObserver = new MutationObserver(safe(resetIdle));
      idleObserver.observe(root, {
        attributes: true,
        attributeFilter: ['data-mode', 'data-oled', 'data-screensaver', 'data-idle-minutes']
      });
    }
    resetIdle();
    if (window.location && /(?:^|[?&])fps=1(?:&|$)/.test(window.location.search || '')) hud(true);
    if (visible()) schedule();
  } catch (error) {
    // A missing or restricted browser API must not break the host page.
  }
}());
