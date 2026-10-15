import json
import shutil
import subprocess
import unittest
from pathlib import Path


class DemoBackingVolumeTest(unittest.TestCase):
    def test_zero_volume_is_retained_when_next_message_omits_volume(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is required to run the browser demo")

        demo_path = Path(__file__).resolve().parents[1] / "static" / "demo.js"
        script = """
const fs = require('node:fs');
const vm = require('node:vm');

const context = {
  window: {},
  document: { getElementById() { return null; } },
  setTimeout() {},
  setInterval() {},
};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), context);

const socket = context.io();
const volumes = [];
socket.on('rec', state => volumes.push(state.backing.volume));
socket.emit('backing_play', { kind: 'backing', name: 'Slow Blues in A.mp3' });
socket.emit('backing_play', { kind: 'backing', name: 'Slow Blues in A.mp3', volume: 0 });
socket.emit('backing_play', { kind: 'backing', name: 'Slow Blues in A.mp3' });
process.stdout.write(JSON.stringify(volumes));
"""
        result = subprocess.run(
            [node, "-e", script, str(demo_path)],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(json.loads(result.stdout), [80, 0, 0])


if __name__ == "__main__":
    unittest.main()
