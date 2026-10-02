import ast
import os
import subprocess
import sys
import unittest


class TestMpvFakeFlags(unittest.TestCase):
    def test_audit_fakes_finds_all_mpv_flags(self):
        tests_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(tests_dir)
        fake_path = os.path.join(tests_dir, "fakes", "bin", "mpv")

        with open(fake_path, "r", encoding="utf-8") as f:
            fake_content = f.read()

        fake_flags = set()
        fake_tree = ast.parse(fake_content)
        for node in ast.walk(fake_tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                flag = node.value
                if flag.startswith("-"):
                    if "=" in flag:
                        fake_flags.add(flag.split("=")[0])
                    else:
                        fake_flags.add(flag)

        expected_flags = {
            "--ao",
            "--jack-autostart",
            "--jack-connect",
            "--no-config",
            "--no-terminal",
            "--no-video",
            "--really-quiet",
            "--jack-name",
            "--pause",
            "--loop-file",
            "--input-ipc-server",
            "--volume",
        }
        missing = expected_flags - fake_flags
        self.assertFalse(missing, f"Missing flags in fake mpv: {sorted(missing)}")


if __name__ == "__main__":
    unittest.main()
