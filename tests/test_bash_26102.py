import os
import tempfile
import unittest

from tests.audit_fakes import parse_systemd_unit


DEMO_UNIT = """\
[Unit]
Description=Guitarix demo web interface
After=network.target sound.target guitarix-jack.service
Wants=guitarix-jack.service

[Service]
Type=simple
User=guitarix
Group=guitarix
WorkingDirectory=/opt/guitarix
ExecStart=/opt/guitarix/venv/bin/python -m guitarix.web --demo
Restart=on-failure
Environment=GUITARIX_DEMO=1

[Install]
WantedBy=multi-user.target
"""

WEB_UNIT = """\
[Unit]
Description=Guitarix web interface
After=network.target sound.target guitarix-jack.service

[Service]
Type=simple
User=guitarix
Group=guitarix
WorkingDirectory=/opt/guitarix
ExecStart=/opt/guitarix/venv/bin/python -m guitarix.web
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

    def test_sections_and_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write(tmpdir, "guitarix-demo.service", DEMO_UNIT)
            parsed = parse_systemd_unit(path)

        self.assertEqual(sorted(parsed), ["Install", "Service", "Unit"])
        self.assertEqual(
            parsed["Unit"]["Description"], "Guitarix demo web interface"
        )
        self.assertEqual(
            parsed["Unit"]["After"],
            "network.target sound.target guitarix-jack.service",
        )
        self.assertEqual(parsed["Service"]["User"], "guitarix")
        self.assertEqual(parsed["Service"]["Group"], "guitarix")
        self.assertEqual(
            parsed["Service"]["WorkingDirectory"], "/opt/guitarix"
        )
        self.assertEqual(
            parsed["Service"]["ExecStart"],
            "/opt/guitarix/venv/bin/python -m guitarix.web --demo",
        )
        self.assertEqual(parsed["Service"]["Restart"], "on-failure")
        self.assertEqual(parsed["Install"]["WantedBy"], "multi-user.target")

    def test_comments_and_blanks_ignored(self):
        text = (
            "# a comment\n"
            "; another comment\n"
            "\n"
            "[Unit]\n"
            "Description=Demo\n"
            "\n"
            "[Service]\n"
            "Restart=on-failure\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write(tmpdir, "unit.service", text)
            parsed = parse_systemd_unit(path)

        self.assertEqual(parsed, {"Unit": {"Description": "Demo"},
                                  "Service": {"Restart": "on-failure"}})

    def test_repeated_directive_keeps_last(self):
        text = "[Service]\nRestart=always\nRestart=on-failure\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write(tmpdir, "unit.service", text)
            parsed = parse_systemd_unit(path)

        self.assertEqual(parsed["Service"]["Restart"], "on-failure")

    def test_demo_service_deployed_spec(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write(tmpdir, "guitarix-demo.service", DEMO_UNIT)
            demo = parse_systemd_unit(path)

        self.assertEqual(sorted(demo), ["Install", "Service", "Unit"])

        unit = demo["Unit"]
        service = demo["Service"]
        install = demo["Install"]

        self.assertEqual(unit["Description"], "Guitarix demo web interface")
        self.assertEqual(
            unit["After"], "network.target sound.target guitarix-jack.service"
        )
        self.assertEqual(unit["Wants"], "guitarix-jack.service")

        self.assertEqual(service["Type"], "simple")
        self.assertEqual(service["User"], "guitarix")
        self.assertEqual(service["Group"], "guitarix")
        self.assertEqual(service["WorkingDirectory"], "/opt/guitarix")
        self.assertEqual(
            service["ExecStart"],
            "/opt/guitarix/venv/bin/python -m guitarix.web --demo",
        )
        self.assertEqual(service["Restart"], "on-failure")
        self.assertEqual(service["Environment"], "GUITARIX_DEMO=1")

        self.assertEqual(install["WantedBy"], "multi-user.target")

    def test_web_and_demo_agree_on_shared_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web_path = self._write(tmpdir, "guitarix-web.service", WEB_UNIT)
            demo_path = self._write(tmpdir, "guitarix-demo.service", DEMO_UNIT)
            web = parse_systemd_unit(web_path)
            demo = parse_systemd_unit(demo_path)

        for key in ("User", "Group", "WorkingDirectory", "Restart"):
            self.assertEqual(
                web["Service"].get(key),
                demo["Service"].get(key),
                "guitarix-web.service and guitarix-demo.service disagree on "
                "%r: web=%r demo=%r"
                % (key, web["Service"].get(key), demo["Service"].get(key)),
            )

    def test_web_execstart_diff_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web_path = self._write(tmpdir, "guitarix-web.service", WEB_UNIT)
            demo_path = self._write(tmpdir, "guitarix-demo.service", DEMO_UNIT)
            web = parse_systemd_unit(web_path)
            demo = parse_systemd_unit(demo_path)

        self.assertNotEqual(
            web["Service"]["ExecStart"], demo["Service"]["ExecStart"]
        )
        self.assertEqual(
            demo["Service"]["ExecStart"],
            "/opt/guitarix/venv/bin/python -m guitarix.web --demo",
        )

    def test_web_restart_mismatch_reports_diff(self):
        broken_web = WEB_UNIT.replace("Restart=on-failure", "Restart=always")
        with tempfile.TemporaryDirectory() as tmpdir:
            web_path = self._write(tmpdir, "guitarix-web.service", broken_web)
            demo_path = self._write(tmpdir, "guitarix-demo.service", DEMO_UNIT)
            web = parse_systemd_unit(web_path)
            demo = parse_systemd_unit(demo_path)

        with self.assertRaises(AssertionError) as ctx:
            self.assertEqual(
                web["Service"].get("Restart"),
                demo["Service"].get("Restart"),
                "guitarix-web.service and guitarix-demo.service disagree on "
                "'Restart': web=%r demo=%r"
                % (web["Service"].get("Restart"),
                   demo["Service"].get("Restart")),
            )
        message = str(ctx.exception)
        self.assertIn("Restart", message)
        self.assertIn("always", message)
        self.assertIn("on-failure", message)


if __name__ == "__main__":
    unittest.main()
