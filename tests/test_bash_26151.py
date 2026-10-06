import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import audit_fakes


WEB_UNIT = """[Unit]
Description=Guitarix web
After=network.target

[Service]
User=guitarix
Group=guitarix
WorkingDirectory=/var/lib/guitarix
ExecStart=/usr/bin/guitarix --web
Restart=on-failure

[Install]
WantedBy=multi-user.target
"""

DEMO_UNIT = """[Unit]
Description=Guitarix demo
After=network.target

[Service]
User=guitarix
Group=guitarix
WorkingDirectory=/var/lib/guitarix
ExecStart=/usr/bin/guitarix --demo
Restart=on-failure

[Install]
WantedBy=multi-user.target
"""


class ParseSystemdUnitTests(unittest.TestCase):
    def _write(self, tmpdir, name, text):
        path = os.path.join(tmpdir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_parse_maps_directives_into_sections(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write(tmpdir, "guitarix-web.service", WEB_UNIT)
            parsed = audit_fakes.parse_systemd_unit(path)

        self.assertIn("Unit", parsed)
        self.assertIn("Service", parsed)
        self.assertIn("Install", parsed)
        self.assertEqual(parsed["Unit"]["Description"], "Guitarix web")
        self.assertEqual(parsed["Service"]["ExecStart"], "/usr/bin/guitarix --web")
        self.assertEqual(parsed["Service"]["WorkingDirectory"], "/var/lib/guitarix")
        self.assertEqual(parsed["Service"]["Restart"], "on-failure")
        self.assertEqual(parsed["Install"]["WantedBy"], "multi-user.target")

    def test_parse_ignores_comments_and_blank_lines(self):
        text = "# comment\n\n[Service]\n; another\nRestart=always\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write(tmpdir, "unit.service", text)
            parsed = audit_fakes.parse_systemd_unit(path)

        self.assertEqual(parsed["Service"], {"Restart": "always"})

    def test_parse_last_value_wins_for_repeated_key(self):
        text = "[Service]\nRestart=always\nRestart=on-failure\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write(tmpdir, "unit.service", text)
            parsed = audit_fakes.parse_systemd_unit(path)

        self.assertEqual(parsed["Service"]["Restart"], "on-failure")


class ServiceFilesMatchTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = self._tmp.name
        self._orig_unit_path = audit_fakes._unit_path

        def fake_unit_path(name):
            return os.path.join(self.tmpdir, name)

        audit_fakes._unit_path = fake_unit_path
        self.addCleanup(setattr, audit_fakes, "_unit_path", self._orig_unit_path)

    def _write(self, name, text):
        path = os.path.join(self.tmpdir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def _run(self):
        case = audit_fakes.ServiceFileTests("test_service_files_match")
        result = unittest.TestResult()
        case.run(result)
        return result

    def test_matching_units_pass(self):
        self._write("guitarix-web.service", WEB_UNIT)
        self._write("guitarix-demo.service", DEMO_UNIT)
        result = self._run()
        self.assertEqual(result.errors, [])
        self.assertEqual(result.failures, [])

    def test_missing_execstart_fails(self):
        broken = WEB_UNIT.replace("ExecStart=/usr/bin/guitarix --web\n", "")
        self._write("guitarix-web.service", broken)
        self._write("guitarix-demo.service", DEMO_UNIT)
        result = self._run()
        self.assertEqual(len(result.failures), 1)
        self.assertIn("ExecStart", result.failures[0][1])

    def test_wrong_restart_fails(self):
        broken = WEB_UNIT.replace("Restart=on-failure", "Restart=always")
        self._write("guitarix-web.service", broken)
        self._write("guitarix-demo.service", DEMO_UNIT)
        result = self._run()
        self.assertEqual(len(result.failures), 1)
        self.assertIn("Restart", result.failures[0][1])

    def test_demo_missing_required_key_fails(self):
        broken = DEMO_UNIT.replace("Group=guitarix\n", "")
        self._write("guitarix-web.service", WEB_UNIT)
        self._write("guitarix-demo.service", broken)
        result = self._run()
        self.assertEqual(len(result.failures), 1)
        self.assertIn("Group", result.failures[0][1])

    def test_user_mismatch_fails(self):
        broken = DEMO_UNIT.replace("User=guitarix", "User=other")
        self._write("guitarix-web.service", WEB_UNIT)
        self._write("guitarix-demo.service", broken)
        result = self._run()
        self.assertEqual(len(result.failures), 1)
        self.assertIn("User", result.failures[0][1])

    def test_working_directory_mismatch_fails(self):
        broken = DEMO_UNIT.replace(
            "WorkingDirectory=/var/lib/guitarix", "WorkingDirectory=/tmp"
        )
        self._write("guitarix-web.service", WEB_UNIT)
        self._write("guitarix-demo.service", broken)
        result = self._run()
        self.assertEqual(len(result.failures), 1)
        self.assertIn("WorkingDirectory", result.failures[0][1])

    def test_restart_mismatch_fails(self):
        broken = DEMO_UNIT.replace("Restart=on-failure", "Restart=always")
        self._write("guitarix-web.service", WEB_UNIT)
        self._write("guitarix-demo.service", broken)
        result = self._run()
        self.assertEqual(len(result.failures), 1)
        self.assertIn("Restart", result.failures[0][1])


if __name__ == "__main__":
    unittest.main()
