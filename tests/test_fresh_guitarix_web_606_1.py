import json
import shutil
import subprocess
import unittest
from pathlib import Path


class BroadcastReconnectTest(unittest.TestCase):
    def test_reconnect_status_messages(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is required to run the broadcast client")

        script = Path(__file__).resolve().parents[1] / "static" / "broadcast.js"
        runner = r"""
const fs = require('fs');
const vm = require('vm');
const handlers = {};
const elements = {};
const document = {
  getElementById(id) {
    if (!elements[id]) {
      elements[id] = {
        textContent: '',
        hidden: false,
        classList: { toggle() {} }
      };
    }
    return elements[id];
  }
};
const context = {
  document,
  io() {
    return { on(event, handler) { handlers[event] = handler; } };
  },
  window: {}
};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), context);
handlers.reconnect_attempt(3);
const attempting = elements.status.textContent;
handlers.reconnect_failed();
const failed = elements.status.textContent;
console.log(JSON.stringify({ attempting, failed }));
"""
        result = subprocess.run(
            [node, "-e", runner, str(script)],
            capture_output=True,
            text=True,
            check=True,
        )
        statuses = json.loads(result.stdout)
        self.assertEqual(statuses["attempting"], "Reconnecting… (attempt 3)")
        self.assertEqual(statuses["failed"], "Connection lost — pull to refresh")


if __name__ == "__main__":
    unittest.main()
