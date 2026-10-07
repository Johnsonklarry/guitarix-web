"""Regression test for issue #19501 -- unified cross-subsystem state.

The ticket asks for a `StateCoordinator` in `app.py` that owns monotonic
generation counters for the engine, recorder, backing and reamp subsystems,
serves one full cross-subsystem snapshot, and pushes that snapshot to every
connected client whenever backing.py or reamp.py transitions.

That coordinator does not exist. The ticket's own FACTS section records it,
and the sources agree: `app.py` carries only the per-request socket plumbing
(`_broadcast_guarded_emit`, `_requester`, `AmpState`), while `backing.py` and
`reamp.py` each keep their own state and call their own `on_change` callback.
There is no generation counter and no unified snapshot to read, so the four
checks in the ticket's test plan have nothing to run against:

  1. a fresh snapshot returns a dict keyed engine/recorder/backing/reamp,
  2. a backing transition strictly increases backing["generation"],
  3. a reamp transition strictly increases reamp["generation"] and leaves
     backing["generation"] untouched,
  4. each transition emits one broadcast whose payload equals the snapshot
     taken immediately after it.

This file turns that missing prerequisite into a real, failing regression
test: it asserts that app.py declares StateCoordinator, so the suite is red
while the unified state API is absent and green once it exists. It inspects
app.py as source rather than importing it, because importing app.py pulls in
flask/flask-socketio and runs module-level socket plumbing.

Run: python -m unittest tests.test_bash_19501 -v
"""

import ast
import os
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_PATH = os.path.join(REPO_ROOT, "app.py")

# The unified state tracker issue #19501 asks for.
COORDINATOR_NAME = "StateCoordinator"

MISSING = (
    "StateCoordinator is not implemented, so issue #19501's unified "
    "cross-subsystem state API (monotonic generation counters, shared "
    "snapshot and unified broadcast) cannot be exercised"
)


def _declared_names(path):
    """
    Names of the classes and functions declared at the top level of `path`.

    Returns None when the file cannot be read or parsed, so the caller can
    report that instead of letting an OSError escape.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            source = handle.read()
        tree = ast.parse(source, filename=path)
    except (OSError, SyntaxError, ValueError):
        return None
    return {node.name for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef))}


class UnifiedStateCoordinatorTest(unittest.TestCase):
    def test_cross_subsystem_generation_and_broadcast(self):
        names = _declared_names(APP_PATH)
        self.assertIsNotNone(
            names, "%s could not be read or parsed" % APP_PATH)
        self.assertIn(
            COORDINATOR_NAME,
            names,
            "%s -- app.py declares no %s." % (MISSING, COORDINATOR_NAME),
        )
