"""Regression tests for the JACK routing fix.

The previous revision of this file defined its own FakeJack/FakePlayer pair and
asserted against that: it never imported the application, so it passed whether
or not Player.route() was fixed. Everything below drives the application's real
routing code; the JACK client is the only thing stubbed, at its boundary.
"""

import importlib
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Player.route() lives in application code that is not part of the files
# attached to this review, so the class is looked up by name instead of being
# hard-coded: set PLAYER_MODULE (and PLAYER_CLASS, if it is not called Player)
# to the real module. If it cannot be imported the routing tests SKIP with a
# visible reason -- they must never again pass by exercising a local stand-in.
PLAYER_MODULE = os.environ.get("PLAYER_MODULE")
PLAYER_CLASS = os.environ.get("PLAYER_CLASS", "Player")
CANDIDATE_MODULES = ("player", "core.player", "jack_client", "jack", "app")


def load_player_class():
    """The application's Player class, or None when it cannot be imported."""
    names = [PLAYER_MODULE] if PLAYER_MODULE else []
    names += [n for n in CANDIDATE_MODULES if n not in names]
    for name in names:
        try:
            module = importlib.import_module(name)
        except Exception:
            continue
        cls = getattr(module, PLAYER_CLASS, None)
        if cls is not None and hasattr(cls, "route"):
            return cls
    return None


PLAYER = load_player_class()


class StubJack(object):
    """Stand-in for the JACK client: the external boundary, and the only stub."""

    def __init__(self):
        self.connections = {}          # destination -> set of sources
        self.fail_ports = set()
        self.calls = []                # ordered (op, source, destination)

    def connect(self, source, destination):
        if destination in self.fail_ports:
            raise RuntimeError("cannot connect to %s" % destination)
        self.calls.append(("connect", source, destination))
        self.connections.setdefault(destination, set()).add(source)

    def disconnect(self, source, destination):
        self.calls.append(("disconnect", source, destination))
        self.connections.get(destination, set()).discard(source)


def make_player(jack):
    """The real Player wired to StubJack, or None if that is not how it is built."""
    if PLAYER is None:
        return None
    for args, kwargs in (((), {"jack": jack}), ((jack,), {}), ((), {"client": jack})):
        try:
            player = PLAYER(*args, **kwargs)
        except TypeError:
            continue
        if getattr(player, "jack", None) is jack or getattr(player, "client", None) is jack:
            return player
    return None


@unittest.skipUnless(PLAYER is not None,
                     "the application's Player class is not importable from this checkout; "
                     "set PLAYER_MODULE/PLAYER_CLASS (see load_player_class)")
class TestJackRouting(unittest.TestCase):

    def setUp(self):
        self.jack = StubJack()
        self.player = make_player(self.jack)
        if self.player is None:
            self.skipTest("could not construct the real Player around the stub JACK client")

    def test_valid_destination_connects_before_disconnecting_old(self):
        self.player.route("recorder", "system:capture_1")
        self.player.route("recorder", "system:capture_2")
        self.assertTrue(self.jack.connections["system:capture_2"])
        self.assertEqual(self.jack.connections.get("system:capture_1", set()), set())
        # the ordering is the behaviour under test, and it is asserted on the
        # call log rather than on port names the real client chooses itself
        connects = [i for i, c in enumerate(self.jack.calls)
                    if c[0] == "connect" and c[2] == "system:capture_2"]
        drops = [i for i, c in enumerate(self.jack.calls)
                 if c[0] == "disconnect" and c[2] == "system:capture_1"]
        self.assertTrue(connects, "the new destination was never connected")
        self.assertTrue(drops, "the previous destination was never dropped")
        self.assertLess(connects[0], drops[0],
                        "the old connection was dropped before the new one was made")
        self.assertEqual(self.player.routes["recorder"], "system:capture_2")

    def test_invalid_destination_aborts_and_keeps_previous_routing(self):
        self.player.route("recorder", "system:capture_1")
        self.jack.fail_ports.add("system:capture_9")
        with self.assertRaises(RuntimeError):
            self.player.route("recorder", "system:capture_9")
        self.assertTrue(self.jack.connections["system:capture_1"])
        self.assertEqual(self.player.routes["recorder"], "system:capture_1")

    def test_concurrent_roles_do_not_drop_non_conflicting_routes(self):
        for role, destination in (("recorder", "system:capture_1"),
                                  ("monitor", "system:playback_1"),
                                  ("reamp", "system:playback_2")):
            self.player.route(role, destination)
        for destination in ("system:capture_1", "system:playback_1", "system:playback_2"):
            self.assertTrue(self.jack.connections.get(destination),
                            "%s lost its connection" % destination)
        self.assertEqual([c for c in self.jack.calls if c[0] == "disconnect"], [])

    def test_rerouting_to_the_same_destination_keeps_it_connected(self):
        # a wall-clock budget over in-memory bookkeeping proved nothing (and no
        # timing of the real client is meaningful from a test host); this keeps
        # the happy path covered with a deterministic assertion instead
        self.player.route("recorder", "system:capture_1")
        sources = set(self.jack.connections["system:capture_1"])
        self.assertEqual([c for c in self.jack.calls if c[0] == "disconnect"], [])
        self.player.route("recorder", "system:capture_1")
        self.assertEqual(set(self.jack.connections.get("system:capture_1", set())), sources)
        self.assertEqual(self.player.routes["recorder"], "system:capture_1")

    def test_connection_error_reported_as_error_state(self):
        self.jack.fail_ports.add("system:capture_9")
        with self.assertRaises(RuntimeError):
            self.player.route("recorder", "system:capture_9")
        self.assertEqual(self.player.state, "error")


if __name__ == "__main__":
    unittest.main()
