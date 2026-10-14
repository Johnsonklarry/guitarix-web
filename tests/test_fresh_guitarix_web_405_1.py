import json
import shutil
import subprocess
import unittest
from pathlib import Path


class ChimeResumeTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_play_waits_for_resume_before_scheduling(self):
        chime_path = (
            Path(__file__).resolve().parents[1]
            / "forge_core"
            / "static"
            / "theme-kit"
            / "chime.js"
        )
        harness = r"""
const fs = require("fs");
const vm = require("vm");
const assert = require("assert");
const source = fs.readFileSync(process.argv[1], "utf8");

function makeHarness(shouldReject) {
  let resolveResume;
  let rejectResume;
  const events = [];
  let resumeSettled = false;

  class FakeAudioContext {
    constructor() {
      this.state = "suspended";
      this.currentTime = 1;
      this.destination = {};
      this.resume = () => new Promise((resolve, reject) => {
        resolveResume = () => {
          resumeSettled = true;
          this.state = "running";
          resolve();
        };
        rejectResume = () => {
          resumeSettled = true;
          reject(new Error("resume rejected"));
        };
      });
    }

    createOscillator() {
      events.push({ kind: "oscillator", afterResume: resumeSettled });
      return {
        type: "sine",
        frequency: {
          setValueAtTime: (frequency) => events.push({
            kind: "frequency",
            frequency,
            afterResume: resumeSettled
          })
        },
        connect: () => {},
        start: () => {},
        stop: () => {}
      };
    }

    createGain() {
      return {
        gain: {
          setValueAtTime: () => {},
          exponentialRampToValueAtTime: () => {}
        },
        connect: () => {}
      };
    }
  }

  const window = {
    AudioContext: FakeAudioContext,
    localStorage: {
      getItem: () => null,
      setItem: () => {}
    }
  };
  vm.runInNewContext(source, { window, Promise, RangeError });

  return {
    chime: window.ThemeKitChime,
    events,
    resolve: () => resolveResume(),
    reject: () => rejectResume(),
    shouldReject
  };
}

(async () => {
  const resolving = makeHarness(false);
  const playResult = resolving.chime.play("done");
  assert.strictEqual(typeof playResult.then, "function");
  assert.strictEqual(
    resolving.events.filter((event) => event.kind === "oscillator").length,
    0
  );

  resolving.resolve();
  assert.strictEqual(await playResult, true);
  const frequencies = resolving.events
    .filter((event) => event.kind === "frequency")
    .map((event) => event.frequency);
  assert.deepStrictEqual(frequencies, [659.25, 987.77]);
  assert.ok(
    resolving.events
      .filter((event) => event.kind === "oscillator")
      .every((event) => event.afterResume)
  );

  const rejecting = makeHarness(true);
  const rejectedResult = rejecting.chime.play("done");
  assert.strictEqual(typeof rejectedResult.then, "function");
  assert.strictEqual(rejecting.events.length, 0);
  rejecting.reject();
  assert.strictEqual(await rejectedResult, false);
  assert.strictEqual(rejecting.events.length, 0);
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
"""
        result = subprocess.run(
            ["node", "-e", harness, str(chime_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=json.dumps(
                {"stdout": result.stdout, "stderr": result.stderr},
                ensure_ascii=False,
            ),
        )


if __name__ == "__main__":
    unittest.main()
