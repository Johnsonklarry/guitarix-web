"""Regression tests for issue #168: validate service file changes match deployment.

The two service files must stay consistent with what the deploy script and the
PR description promise:

  * guitarix-web.service runs the web control surface and must be the one the
    deploy script restarts.
  * guitarix-demo.service is the demo-only copy: the SAME app.py with
    GX_DEMO_ONLY=1, on its own port, and it must never contact guitarix.

Run standalone:  python3 tests/night_168_tests.py
"""

import os
import re
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TESTS_DIR)

WEB_SERVICE = os.path.join(PROJECT_ROOT, "guitarix-web.service")
DEMO_SERVICE = os.path.join(PROJECT_ROOT, "guitarix-demo.service")
DEPLOY_SH = os.path.join(PROJECT_ROOT, "tools", "deploy.sh")


def read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def unit_section(text, name):
    """Return the body of the [name] section, or '' if absent."""
    match = re.search(
        r"^\[%s\]\s*$(.*?)(?=^\[|\Z)" % re.escape(name),
        text,
        re.MULTILINE | re.DOTALL,
    )
    return match.group(1) if match else ""


def env_value(text, key):
    match = re.search(r"^Environment=%s=(.*)$" % re.escape(key), text, re.MULTILINE)
    return match.group(1).strip() if match else None


def exec_start(text):
    match = re.search(r"^ExecStart=(.*)$", text, re.MULTILINE)
    return match.group(1).strip() if match else None


class ServiceFilesMatchDeploymentTest(unittest.TestCase):
    def setUp(self):
        for path in (WEB_SERVICE, DEMO_SERVICE, DEPLOY_SH):
            self.assertTrue(os.path.exists(path), "missing file: %s" % path)
        self.web = read(WEB_SERVICE)
        self.demo = read(DEMO_SERVICE)
        self.deploy = read(DEPLOY_SH)

    # --- guitarix-web.service -------------------------------------------

    def test_web_service_is_a_system_service(self):
        self.assertIn("[Unit]", self.web)
        self.assertIn("[Service]", self.web)
        self.assertIn("[Install]", self.web)
        self.assertIn("WantedBy=multi-user.target", unit_section(self.web, "Install"))

    def test_web_service_runs_app_py_from_the_venv(self):
        start = exec_start(self.web)
        self.assertIsNotNone(start, "guitarix-web.service has no ExecStart")
        self.assertIn("/home/ljohnson/guitarix-web/.venv/bin/python", start)
        self.assertIn("/home/ljohnson/guitarix-web/app.py", start)

    def test_web_service_serves_the_live_port(self):
        self.assertEqual(env_value(self.web, "GX_WEB_PORT"), "5000")
        self.assertIsNone(
            env_value(self.web, "GX_DEMO_ONLY"),
            "the live service must not set GX_DEMO_ONLY",
        )

    def test_web_service_orders_itself_after_guitarix(self):
        unit = unit_section(self.web, "Unit")
        self.assertIn("guitarix.service", unit)

    def test_deploy_script_restarts_the_web_service(self):
        self.assertIn("systemctl restart guitarix-web.service", self.deploy)
        self.assertIn("systemctl is-active --quiet guitarix-web.service", self.deploy)

    def test_deploy_script_installs_every_service_file(self):
        # The deploy loop must pick up both files, not a hardcoded one.
        self.assertIn("for service_file in *.service", self.deploy)

    # --- guitarix-demo.service ------------------------------------------

    def test_demo_service_is_the_same_app_py(self):
        self.assertEqual(
            exec_start(self.demo),
            exec_start(self.web),
            "the demo service must run the same app.py as the live service",
        )

    def test_demo_service_sets_demo_only(self):
        self.assertEqual(env_value(self.demo, "GX_DEMO_ONLY"), "1")

    def test_demo_service_uses_its_own_port(self):
        self.assertEqual(env_value(self.demo, "GX_WEB_PORT"), "5080")
        self.assertNotEqual(
            env_value(self.demo, "GX_WEB_PORT"),
            env_value(self.web, "GX_WEB_PORT"),
            "the demo must not share the live port",
        )

    def test_demo_service_never_contacts_guitarix(self):
        unit = unit_section(self.demo, "Unit")
        self.assertNotIn("guitarix.service", unit)
        self.assertNotIn("Wants=guitarix.service", unit)
        self.assertIsNone(
            env_value(self.demo, "GX_PORT"),
            "the demo service must not point at the guitarix port",
        )

    def test_demo_service_has_no_forked_script(self):
        start = exec_start(self.demo)
        self.assertNotIn("demo.py", start)
        self.assertNotIn("demo_app", start)


if __name__ == "__main__":
    unittest.main(verbosity=2)
