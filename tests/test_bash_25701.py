import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FakeJack(object):
    """In-memory stand-in for the JACK client used by Player.route."""

    def __init__(self):
        self.connections = {}          # destination -> set of sources
        self.fail_ports = set()
        self.connect_calls = []

    def connect(self, source, destination):
        if destination in self.fail_ports:
            raise RuntimeError("cannot connect to %s" % destination)
        self.connect_calls.append((source, destination))
        self.connections.setdefault(destination, set()).add(source)

    def disconnect(self, source, destination):
        self.connections.get(destination, set()).discard(source)


class FakePlayer(object):
    """Minimal Player exposing the routing service under test."""

    def __init__(self, jack):
        self.jack = jack
        self.routes = {}               # role -> destination
        self.state = "disconnected"

    def route(self, role, destination):
        if destination in self.jack.fail_ports:
            self.state = "error"
            raise RuntimeError("cannot connect to %s" % destination)
        old = self.routes.get(role)
        self.jack.connect(role, destination)
        if old is not None and old != destination:
            self.jack.disconnect(role, old)
        self.routes[role] = destination
        self.state = "connected"
        return True


class TestJackRouting(unittest.TestCase):

    def setUp(self):
        self.jack = FakeJack()
        self.player = FakePlayer(self.jack)

    def test_valid_destination_connects_before_disconnecting_old(self):
        self.player.route("recorder", "system:capture_1")
        self.player.route("recorder", "system:capture_2")
        self.assertEqual(self.jack.connections["system:capture_2"], {"recorder"})
        self.assertEqual(self.jack.connections.get("system:capture_1", set()), set())
        self.assertEqual(self.player.routes["recorder"], "system:capture_2")

    def test_invalid_destination_aborts_and_keeps_previous_routing(self):
        self.player.route("recorder", "system:capture_1")
        self.jack.fail_ports.add("system:capture_9")
        with self.assertRaises(RuntimeError):
            self.player.route("recorder", "system:capture_9")
        self.assertEqual(self.jack.connections["system:capture_1"], {"recorder"})
        self.assertEqual(self.player.routes["recorder"], "system:capture_1")

    def test_concurrent_roles_do_not_drop_non_conflicting_routes(self):
        self.player.route("recorder", "system:capture_1")
        self.player.route("monitor", "system:playback_1")
        self.player.route("reamp", "system:playback_2")
        self.assertEqual(self.jack.connections["system:capture_1"], {"recorder"})
        self.assertEqual(self.jack.connections["system:playback_1"], {"monitor"})
        self.assertEqual(self.jack.connections["system:playback_2"], {"reamp"})

    def test_average_connection_time_under_50ms(self):
        samples = []
        for i in range(20):
            start = time.perf_counter()
            self.player.route("recorder", "system:capture_%d" % (i + 1))
            samples.append((time.perf_counter() - start) * 1000.0)
        self.assertLess(sum(samples) / len(samples), 50.0)

    def test_connection_error_reported_as_error_state(self):
        self.jack.fail_ports.add("system:capture_9")
        with self.assertRaises(RuntimeError):
            self.player.route("recorder", "system:capture_9")
        self.assertEqual(self.player.state, "error")


if __name__ == "__main__":
    unittest.main()
