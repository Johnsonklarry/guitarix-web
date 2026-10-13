import json
import os
import subprocess
import tempfile
import unittest


class TestDemoOpDoneDeduplication(unittest.TestCase):
    def test_op_done_emitted_at_most_once_per_op(self):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        demo_path = os.path.normpath(os.path.join(base_dir, "..", "static", "demo.js"))
        if not os.path.exists(demo_path):
            demo_path = os.path.normpath(os.path.join(os.getcwd(), "static", "demo.js"))

        with open(demo_path, "r", encoding="utf-8") as f:
            demo_code = f.read()

        node_script = f"""
const vm = require('vm');

global.window = {{ GX_DEMO_CLIP: '' }};
global.document = {{ getElementById: function () {{ return null; }} }};

const code = {json.dumps(demo_code)} + ';globalThis.__io = io;';
vm.runInThisContext(code);

const s = globalThis.__io();
const completions = [];

s.on('op_done', function (data) {{
    completions.push(data);
}});

s.emit('no_such_event', {{ op: 'opX' }});
s.emit('no_such_event', {{ op: 'opX' }});

s.emit('no_such_event', {{ op: 'opY' }});
s.emit('no_such_event', {{ op: 'opZ' }});

setTimeout(function () {{
    console.log(JSON.stringify(completions));
    process.exit(0);
}}, 500);
"""

        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as tf:
            tf.write(node_script)
            temp_script = tf.name

        try:
            proc = subprocess.run(["node", temp_script], capture_output=True, text=True, check=True)
            output = json.loads(proc.stdout.strip())

            op_x = [c for c in output if c.get("op") == "opX"]
            op_y = [c for c in output if c.get("op") == "opY"]
            op_z = [c for c in output if c.get("op") == "opZ"]

            self.assertEqual(len(op_x), 1, f"Expected exactly 1 completion for opX, got {len(op_x)}")
            self.assertEqual(op_x[0], {"op": "opX", "ok": True})

            self.assertEqual(len(op_y), 1, f"Expected exactly 1 completion for opY, got {len(op_y)}")
            self.assertEqual(op_y[0], {"op": "opY", "ok": True})

            self.assertEqual(len(op_z), 1, f"Expected exactly 1 completion for opZ, got {len(op_z)}")
            self.assertEqual(op_z[0], {"op": "opZ", "ok": True})

            self.assertEqual(len(output), 3, f"Expected 3 completions in total, got {len(output)}")
        finally:
            if os.path.exists(temp_script):
                os.remove(temp_script)


if __name__ == '__main__':
    unittest.main()
