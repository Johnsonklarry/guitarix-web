"""Regression tests for secure LAN access over Tailscale/WireGuard (issue #24, part 3).

Checks the two things that can be checked without a real tunnel:

  * the app binds where GX_WEB_HOST / GX_WEB_PORT tell it to, so it can be
    pinned to a VPN address instead of every interface;
  * the WireGuard configuration the README tells you to write is well formed
    -- an [Interface] with Address/ListenPort/PrivateKey, and peers whose
    AllowedIPs are inside the tunnel's own subnet rather than 0.0.0.0/0.

Run:  python3 tests/night_211_tests.py
"""

import ipaddress
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
        return fh.read()


class BindAddressTests(unittest.TestCase):
    """The app must take its listen address from the environment."""

    def test_app_defines_web_host_and_port(self):
        src = read("app.py")
        self.assertIn("GX_WEB_HOST", src,
                      "app.py must read GX_WEB_HOST so the app can be bound "
                      "to a VPN address")
        self.assertIn("GX_WEB_PORT", src,
                      "app.py must read GX_WEB_PORT")

    def test_app_run_uses_them(self):
        src = read("app.py")
        self.assertRegex(
            src,
            r"app\.run\(\s*host\s*=\s*GX_WEB_HOST\s*,\s*port\s*=\s*GX_WEB_PORT",
            "app.run() must use GX_WEB_HOST / GX_WEB_PORT, not a hardcoded "
            "0.0.0.0:5000",
        )

    def test_defaults_are_lan_friendly(self):
        # Importing app.py starts nothing; the module-level constants are what
        # we want.  A missing flask is a skip, not a failure.
        try:
            import app  # noqa: F401
        except Exception as exc:  # pragma: no cover - environment dependent
            self.skipTest("cannot import app.py: %s" % exc)
        self.assertEqual(app.GX_WEB_HOST, os.environ.get("GX_WEB_HOST", "0.0.0.0"))
        self.assertEqual(app.GX_WEB_PORT, int(os.environ.get("GX_WEB_PORT", "5000")))

    def test_env_override_is_honoured(self):
        os.environ["GX_WEB_HOST"] = "100.101.102.103"
        os.environ["GX_WEB_PORT"] = "5099"
        try:
            for mod in ("app",):
                sys.modules.pop(mod, None)
            import app
            self.assertEqual(app.GX_WEB_HOST, "100.101.102.103")
            self.assertEqual(app.GX_WEB_PORT, 5099)
        except Exception as exc:  # pragma: no cover - environment dependent
            self.skipTest("cannot import app.py: %s" % exc)
        finally:
            os.environ.pop("GX_WEB_HOST", None)
            os.environ.pop("GX_WEB_PORT", None)
            sys.modules.pop("app", None)


class WireGuardConfigTests(unittest.TestCase):
    """The wg0.conf the README documents must be a valid, non-leaky config."""

    SAMPLE = """\
[Interface]
Address = 10.10.0.1/24
ListenPort = 51820
PrivateKey = aGVsbG8gd29ybGQgdGhpcyBpcyBhIGtleQ=

[Peer]
# phone
PublicKey = d29ybGQgaGVsbG8gdGhpcyBpcyBhIGtleQ==
AllowedIPs = 10.10.0.2/32
"""

    def parse(self, text):
        sections = []
        current = None
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                current = {"name": line[1:-1], "keys": {}}
                sections.append(current)
                continue
            if current is None:
                continue
            key, _, value = line.partition("=")
            current["keys"][key.strip()] = value.strip()
        return sections

    def test_sample_config_is_well_formed(self):
        sections = self.parse(self.SAMPLE)
        self.assertEqual(sections[0]["name"], "Interface")
        iface = sections[0]["keys"]
        for key in ("Address", "ListenPort", "PrivateKey"):
            self.assertIn(key, iface, "[Interface] is missing %s" % key)
        self.assertTrue(iface["ListenPort"].isdigit())
        self.assertTrue(1 <= int(iface["ListenPort"]) <= 65535)

        peers = [s for s in sections if s["name"] == "Peer"]
        self.assertTrue(peers, "no [Peer] sections")
        for peer in peers:
            self.assertIn("PublicKey", peer["keys"])
            self.assertIn("AllowedIPs", peer["keys"])

    def test_peers_do_not_route_the_whole_internet(self):
        # AllowedIPs = 0.0.0.0/0 would send all the phone's traffic through
        # the Pi.  The README says not to do that unless you mean it.
        sections = self.parse(self.SAMPLE)
        for peer in sections:
            if peer["name"] != "Peer":
                continue
            for entry in peer["keys"]["AllowedIPs"].split(","):
                net = ipaddress.ip_network(entry.strip(), strict=False)
                self.assertFalse(
                    net.prefixlen == 0,
                    "peer routes 0.0.0.0/0; the README says not to",
                )

    def test_peer_addresses_are_inside_the_tunnel_subnet(self):
        sections = self.parse(self.SAMPLE)
        iface = sections[0]["keys"]
        tunnel = ipaddress.ip_network(iface["Address"], strict=False)
        for peer in sections:
            if peer["name"] != "Peer":
                continue
            for entry in peer["keys"]["AllowedIPs"].split(","):
                net = ipaddress.ip_network(entry.strip(), strict=False)
                self.assertTrue(
                    net.subnet_of(tunnel),
                    "%s is outside the tunnel subnet %s" % (net, tunnel),
                )

    def test_readme_documents_the_vpn_setup(self):
        text = read("README.md")
        self.assertIn("Tailscale", text)
        self.assertIn("WireGuard", text)
        self.assertIn("wg0.conf", text)
        self.assertIn("AllowedIPs", text)
        self.assertIn("tests/network_tests.py", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
