import pathlib
import subprocess
import unittest


SW_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "forge_core/static/theme-kit/pwa/sw.js"
)


class ServiceWorkerPrecacheTest(unittest.TestCase):
    def test_precached_static_requests_use_cache(self):
        script = r"""
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const listeners = {};
const cached = new Map(
  ["/theme-kit/theme.js", "/theme-kit/themes.css", "/theme-kit/gamepad.js"]
    .map(path => [path, { source: "cache", path }])
);
const lookups = [];
let opens = 0;

const cache = {
  match(request) {
    const path = new URL(request.url).pathname;
    lookups.push(path);
    return Promise.resolve(cached.get(path));
  },
  put() {
    throw new Error("The cached response should not be replaced");
  }
};
const context = {
  URL,
  self: {
    location: { origin: "http://localhost" },
    addEventListener(type, listener) {
      listeners[type] = listener;
    },
    clients: { claim() { return Promise.resolve(); } },
    skipWaiting() {}
  },
  caches: {
    open() {
      opens += 1;
      return Promise.resolve(cache);
    }
  },
  fetch() {
    // No network: return a non-OK response for background refreshes.
    return Promise.resolve({ ok: false });
  },
  Response: { error() { throw new Error("Unexpected offline response"); } }
};

vm.runInNewContext(fs.readFileSync(process.argv[1], "utf8"), context);

async function dispatch(path) {
  const event = {
    request: {
      method: "GET",
      mode: "no-cors",
      url: "http://localhost" + path
    },
    responded: false,
    respondWith(promise) {
      this.responded = true;
      this.response = promise;
    },
    waitUntil(promise) {
      this.background = promise;
    }
  };
  listeners.fetch(event);
  return event;
}

(async () => {
  for (const path of cached.keys()) {
    const event = await dispatch(path);
    assert.equal(event.responded, true, path + " must be intercepted");
    assert.strictEqual(await event.response, cached.get(path));
    if (event.background) await event.background;
  }

  assert.deepEqual(lookups, Array.from(cached.keys()));
  assert.equal(opens, 3);

  const other = await dispatch("/other.js");
  assert.equal(other.responded, false);
  assert.equal(opens, 3);
  assert.deepEqual(lookups, Array.from(cached.keys()));
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
"""
        result = subprocess.run(
            ["node", "-e", script, str(SW_PATH)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
