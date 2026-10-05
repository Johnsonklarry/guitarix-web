"use strict";

const CACHE_VERSION = "theme-kit-pwa-v1";
const CACHE_NAME = CACHE_VERSION;
const PRECACHE = [
  "/theme-kit/theme.js",
  "/theme-kit/themes.css",
  "/theme-kit/gamepad.js"
];
const OFFLINE_URL = "/offline.html";

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(CACHE_NAME).then(function (cache) {
      return cache.addAll(PRECACHE);
    })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys().then(function (names) {
      return Promise.all(names.filter(function (name) {
        return name.startsWith("theme-kit-pwa-") && name !== CACHE_NAME;
      }).map(function (name) {
        return caches.delete(name);
      }));
    }).then(function () {
      return self.clients.claim();
    })
  );
});

self.addEventListener("message", function (event) {
  if (event.data && event.data.type === "SKIP_WAITING") {
    self.skipWaiting();
  }
});

function isApi(pathname) {
  return pathname === "/api" || pathname.startsWith("/api/") ||
    pathname === "/v1" || pathname.startsWith("/v1/") ||
    pathname === "/forge/api" || pathname.startsWith("/forge/api/");
}

function networkOnly(request, navigation) {
  return fetch(request).catch(function (error) {
    if (navigation) {
      return fetch(OFFLINE_URL).catch(function () {
        return Response.error();
      });
    }
    throw error;
  });
}

self.addEventListener("fetch", function (event) {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // API responses are never read from or written to a cache, even when
  // requested as a navigation.
  if (isApi(url.pathname)) {
    event.respondWith(networkOnly(request, false));
    return;
  }

  if (request.mode === "navigate") {
    event.respondWith(networkOnly(request, true));
    return;
  }

  if (!PRECACHE.includes(url.pathname) || url.search) return;

  event.respondWith(
    caches.open(CACHE_NAME).then(function (cache) {
      const fresh = fetch(request).then(function (response) {
        if (response.ok) {
          return cache.put(request, response.clone()).then(function () {
            return response;
          });
        }
        return response;
      });
      return cache.match(request).then(function (cached) {
        if (cached) {
          event.waitUntil(fresh.catch(function () {}));
          return cached;
        }
        return fresh;
      });
    })
  );
});
