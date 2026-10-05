(function () {
  "use strict";

  if (!("serviceWorker" in navigator)) return;

  var refreshing = false;
  var toast = null;

  function showUpdate(worker) {
    if (toast || !worker || !document.body) return;

    toast = document.createElement("button");
    toast.type = "button";
    toast.textContent = "Update available, tap to refresh";
    toast.setAttribute("aria-label", "Update available, tap to refresh");
    toast.style.position = "fixed";
    toast.style.insetInline = "var(--space-4, 1rem)";
    toast.style.bottom = "calc(var(--space-4, 1rem) + env(safe-area-inset-bottom, 0px))";
    toast.style.zIndex = "2147483647";
    toast.style.minHeight = "var(--tap-min, 44px)";
    toast.style.padding = "var(--space-3, .75rem) var(--space-4, 1rem)";
    toast.style.border = "1px solid var(--edge-strong)";
    toast.style.borderRadius = "var(--radius-md)";
    toast.style.background = "var(--surface)";
    toast.style.color = "var(--text)";
    toast.style.boxShadow = "var(--shadow-2)";
    toast.style.cursor = "pointer";
    toast.addEventListener("click", function () {
      worker.postMessage({ type: "SKIP_WAITING" });
      toast.disabled = true;
    });
    document.body.appendChild(toast);
  }

  function watch(registration) {
    if (registration.waiting && navigator.serviceWorker.controller) {
      showUpdate(registration.waiting);
    }
    registration.addEventListener("updatefound", function () {
      var worker = registration.installing;
      if (!worker) return;
      worker.addEventListener("statechange", function () {
        if (worker.state === "installed" && navigator.serviceWorker.controller) {
          showUpdate(registration.waiting || worker);
        }
      });
    });
  }

  navigator.serviceWorker.addEventListener("controllerchange", function () {
    if (refreshing) return;
    refreshing = true;
    window.location.reload();
  });

  navigator.serviceWorker.register("/sw.js", { scope: "/" }).then(watch).catch(function () {
    // The app remains usable when service workers are unavailable.
  });
}());
