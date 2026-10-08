"""Regression test for issue #19301 (#193.1).

``app.py`` owns a single ``StateCoordinator`` for the whole process. Every
subsystem state change goes through it and bumps a monotonic generation
counter, which is how the browser notices that something moved.

The test drives the real class defined in ``app.py``. ``app.py`` builds a
flask/socket.io server at import time, so permissive stand-ins are installed
in ``sys.modules`` for the duration of the import, which itself runs in a
worker thread with a timeout. No network access, no files are written.
"""

import ast
import importlib
import importlib.machinery
import os
import sys
import threading
import types
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_APP_PATH = os.path.join(_REPO_ROOT, "app.py")
_IMPORT_TIMEOUT = 20.0
_MISSING = object()

# Packages app.py needs at import time; they are stubbed for this test only.
_STUBBED_MODULES = (
    "flask",
    "flask_socketio",
    "simple_websocket",
    "werkzeug",
    "engineio",
    "socketio",
    "eventlet",
    "gevent",
)


class _Stub(object):
    """Permissive stand-in for whatever a third-party module hands out.

    It answers every attribute, call and item access, so importing ``app.py``
    cannot fail because of the surrounding flask/socket.io machinery.
    """

    def __init__(self, name="stub"):
        self._stub_name = name

    def __call__(self, *args, **kwargs):
        return _Stub(self._stub_name)

    def __getattr__(self, item):
        if item.startswith("__") and item.endswith("__"):
            raise AttributeError(item)
        return _Stub(item)

    def __getitem__(self, item):
        return _Stub(item)

    def __setitem__(self, item, value):
        return None

    def __delitem__(self, item):
        return None

    def __contains__(self, item):
        return False

    def __iter__(self):
        return iter(())

    def __len__(self):
        return 0

    def __bool__(self):
        return True

    def __repr__(self):
        return "<stub>"


def _stub_module(name):
    module = types.ModuleType(name)
    module.__spec__ = importlib.machinery.ModuleSpec(name, None)
    module.__package__ = name

    def _module_getattr(item, _name=name):
        if item.startswith("__") and item.endswith("__"):
            raise AttributeError(item)
        return _Stub(item)

    module.__getattr__ = _module_getattr
    return module


def _import_application():
    """Import ``app.py`` with its third-party imports stubbed out.

    Returns the module object, or ``None`` when the module cannot be imported
    at all (an import time side effect that blocks, for instance).
    """
    saved = {name: sys.modules.get(name, _MISSING) for name in _STUBBED_MODULES}
    saved_app = sys.modules.pop("app", None)
    if _REPO_ROOT not in sys.path:
        sys.path.insert(0, _REPO_ROOT)
    for name in _STUBBED_MODULES:
        sys.modules[name] = _stub_module(name)

    outcome = {}

    def _worker():
        try:
            outcome["module"] = importlib.import_module("app")
        except BaseException as exc:      # pragma: no cover - defensive
            outcome["error"] = exc

    worker = threading.Thread(target=_worker, name="test-import-app.py")
    worker.daemon = True
    worker.start()
    worker.join(_IMPORT_TIMEOUT)

    if not worker.is_alive():
        # Only take the sandbox down once nobody is importing any more.
        for name, previous in saved.items():
            if previous is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
        if saved_app is None:
            sys.modules.pop("app", None)
        else:
            sys.modules["app"] = saved_app

    return outcome.get("module")


def _coordinator_definitions():
    """Every top-level ``class StateCoordinator`` in ``app.py``.

    Two definitions with the same name mean the later one silently shadows
    the earlier: the module-level name resolves to the last class, and
    anything written against the first one's API breaks. The suite asserts
    there is exactly one, and this is how it looks.
    """
    try:
        with open(_APP_PATH, "r", encoding="utf-8") as handle:
            source = handle.read()
        tree = ast.parse(source, filename=_APP_PATH)
    except (OSError, SyntaxError):        # pragma: no cover - defensive
        return []
    return [node for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "StateCoordinator"]


def _coordinator_class_from_source():
    """Fallback: execute only the StateCoordinator class from ``app.py``.

    Used when the module cannot be imported in the test environment. Only the
    class definition of the module under test is executed; nothing else.
    """
    wanted = _coordinator_definitions()
    if not wanted:
        return None

    chunk = ast.Module(body=wanted, type_ignores=[])
    ast.fix_missing_locations(chunk)
    namespace = {"threading": threading}
    try:
        exec(compile(chunk, _APP_PATH, "exec"), namespace)
    except Exception:                     # pragma: no cover - defensive
        return None
    candidate = namespace.get("StateCoordinator")
    return candidate if isinstance(candidate, type) else None


def _load_coordinator_class(module):
    if module is not None:
        candidate = getattr(module, "StateCoordinator", None)
        if isinstance(candidate, type):
            return candidate
    return _coordinator_class_from_source()


class StateCoordinatorTestCase(unittest.TestCase):
    """Ticket test plan for issue #19301."""

    @classmethod
    def setUpClass(cls):
        cls.app_module = _import_application()
        cls.coordinator_cls = _load_coordinator_class(cls.app_module)
        if cls.coordinator_cls is None:
            raise AssertionError(
                "StateCoordinator not found in %s" % _APP_PATH)

    def test_base_generation_is_zero(self):
        coordinator = self.coordinator_cls()
        self.assertEqual(0, coordinator.get_generation())
        generation, states = coordinator.snapshot()
        self.assertEqual(0, generation)
        self.assertEqual({}, states)
        self.assertIsNone(coordinator.get_state("nothing-here"))
        self.assertEqual("fallback",
                         coordinator.get_state("nothing-here", "fallback"))

    def test_single_update_increments_generation_by_one(self):
        coordinator = self.coordinator_cls()
        coordinator.update("amp", {"gain": 1.0})
        self.assertEqual(1, coordinator.get_generation())
        self.assertEqual({"gain": 1.0}, coordinator.get_state("amp"))
        coordinator.update("amp", {"gain": 2.0})
        self.assertEqual(2, coordinator.get_generation())
        self.assertEqual({"gain": 2.0}, coordinator.get_state("amp"))

    def test_concurrent_updates_advance_generation_exactly_once_per_update(self):
        coordinator = self.coordinator_cls()
        workers = 8
        updates_per_worker = 64
        failures = []

        def _update(worker_index):
            previous = 0
            try:
                for step in range(updates_per_worker):
                    generation = coordinator.update(
                        "worker-%d" % worker_index,
                        {"worker": worker_index, "step": step})
                    if generation <= previous:
                        raise AssertionError(
                            "generation went backwards: %r <= %r"
                            % (generation, previous))
                    previous = generation
            except Exception as exc:      # pragma: no cover - defensive
                failures.append(exc)

        threads = [threading.Thread(target=_update, args=(index,))
                   for index in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)

        self.assertEqual([], failures)
        self.assertEqual(workers * updates_per_worker,
                         coordinator.get_generation())

    def test_snapshot_stays_consistent_while_updates_run(self):
        coordinator = self.coordinator_cls()
        workers = 4
        updates_per_worker = 200
        snapshots_per_reader = 300
        failures = []
        start = threading.Event()

        def _update(worker_index):
            start.wait(5)
            try:
                for step in range(updates_per_worker):
                    coordinator.update("worker-%d" % worker_index,
                                       {"worker": worker_index, "step": step})
            except Exception as exc:      # pragma: no cover - defensive
                failures.append(exc)

        def _read():
            start.wait(5)
            try:
                previous_generation = -1
                for _ in range(snapshots_per_reader):
                    generation, states = coordinator.snapshot()
                    if generation < previous_generation:
                        raise AssertionError(
                            "snapshot generation went backwards")
                    previous_generation = generation
                    if not isinstance(states, dict):
                        raise AssertionError("snapshot states is not a dict")
                    for name, value in states.items():
                        if not isinstance(value, dict):
                            raise AssertionError(
                                "torn state stored under %r: %r" % (name, value))
                        if name != "worker-%d" % value.get("worker"):
                            raise AssertionError(
                                "state %r stored under %r" % (value, name))
            except Exception as exc:      # pragma: no cover - defensive
                failures.append(exc)

        threads = [threading.Thread(target=_update, args=(index,))
                   for index in range(workers)]
        threads += [threading.Thread(target=_read) for _ in range(2)]
        for thread in threads:
            thread.start()
        start.set()
        for thread in threads:
            thread.join(60)

        self.assertEqual([], failures)
        self.assertEqual(workers * updates_per_worker,
                         coordinator.get_generation())
        generation, states = coordinator.snapshot()
        self.assertEqual(workers * updates_per_worker, generation)
        self.assertEqual({"worker-%d" % index for index in range(workers)},
                         set(states))

    def _swap_coordinator(self):
        """Install a fresh coordinator; put the original back afterwards.

        The module-level coordinator is process-wide state, so every test
        that writes to it restores the singleton rather than leaving a
        generation count behind for whatever runs next.
        """
        original = getattr(self.app_module, "state_coordinator", None)
        self.assertIsInstance(original, self.coordinator_cls)
        fresh = self.coordinator_cls()
        self.app_module.state_coordinator = fresh
        self.addCleanup(setattr, self.app_module, "state_coordinator", original)
        return fresh

    def test_app_defines_exactly_one_state_coordinator(self):
        definitions = _coordinator_definitions()
        self.assertEqual(
            1, len(definitions),
            "app.py defines StateCoordinator %d times; the later definition "
            "shadows the earlier one and silently drops the first one's API"
            % len(definitions))

    def test_state_changes_reach_the_coordinator(self):
        """The app must feed the coordinator, not merely declare it.

        A generation counter that nothing ever bumps stays at zero, which is
        the whole point of it. This drives two of the app's own state-push
        paths -- a connection change and a preset change -- and checks that
        each one lands in the coordinator.
        """
        if self.app_module is None:       # pragma: no cover - defensive
            self.skipTest("app.py could not be imported for this test")
        coordinator = self._swap_coordinator()

        # Assert that each path moves the generation, not by how much: the
        # number of coordinator updates behind one app path is an internal
        # detail, and pinning it would fail the day a path legitimately
        # records one more thing.
        baseline = coordinator.get_generation()
        self.app_module.on_status(True)
        self.assertGreater(coordinator.get_generation(), baseline)
        self.assertEqual({"connected": True}, coordinator.get_state("status"))

        baseline = coordinator.get_generation()
        self.app_module.on_params({"system.current_bank": "Bank",
                                   "system.current_preset": "Preset"})
        self.assertGreater(coordinator.get_generation(), baseline)
        self.assertEqual({"bank": "Bank", "preset": "Preset"},
                         coordinator.get_state("preset"))

    def test_app_entry_point_exposes_state_coordinator(self):
        if self.app_module is None:       # pragma: no cover - defensive
            self.skipTest("app.py could not be imported for this test")
        original = getattr(self.app_module, "state_coordinator", None)
        self.assertIsInstance(original, self.coordinator_cls)
        # a fresh instance, so the process-wide singleton's generation is
        # left exactly as it was found
        coordinator = self._swap_coordinator()
        initial = coordinator.get_generation()
        self.assertIsInstance(initial, int)
        coordinator.update("test-subsystem-19301", {"ok": True})
        self.assertEqual(initial + 1, coordinator.get_generation())
        self.assertEqual({"ok": True},
                         coordinator.get_state("test-subsystem-19301"))
        generation, states = coordinator.snapshot()
        self.assertEqual(initial + 1, generation)
        self.assertEqual({"ok": True}, states["test-subsystem-19301"])


if __name__ == "__main__":                # pragma: no cover
    unittest.main()
