"""Regression tests for issue #11701: read-only Joplin setlist/practice adapter.

The tests drive the adapter entry point with an in-memory fake source: no
network, no Joplin server, and no files are written anywhere.
"""

import importlib.util
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SEARCH_DIRS = (
    os.path.join(_ROOT, "guitarix-web"),
    os.path.join(_ROOT, "guitarix_web"),
    _ROOT,
)


def _import_adapter():
    """Import ``joplin`` wherever the ticket placed it (repo root or subdir)."""
    for directory in reversed(_SEARCH_DIRS):
        if os.path.isdir(directory) and directory not in sys.path:
            sys.path.insert(0, directory)
    try:
        import joplin  # noqa: F401
    except ImportError:
        pass
    else:
        return sys.modules["joplin"]
    for directory in _SEARCH_DIRS:
        path = os.path.join(directory, "joplin.py")
        if not os.path.isfile(path):
            continue
        spec = importlib.util.spec_from_file_location("joplin", path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        sys.modules["joplin"] = module
        spec.loader.exec_module(module)
        return module
    raise ImportError("joplin.py not found in %s" % (list(_SEARCH_DIRS),))


joplin = _import_adapter()

SETLIST = "Set List"
PRACTICE = "Practice"

SAMPLE_NOTES = (
    {
        "id": "s1",
        "notebook": SETLIST,
        "title": "Warm up",
        "body": "A minor scale at 60 bpm.",
        "tags": ["setlist", "warmup"],
    },
    {
        "id": "s2",
        "notebook": SETLIST,
        "title": "Sound check",
        "body": "Check the levels.",
        "tags": ["setlist"],
    },
    {
        "id": "p1",
        "notebook": PRACTICE,
        "title": "Barre chords",
        "body": "Slow changes, steady pulse.",
        "tags": ["practice"],
    },
    {
        "id": "x1",
        "notebook": "Personal",
        "title": "Groceries",
        "body": "Milk and bread.",
        "tags": ["private"],
    },
    {
        "id": "x2",
        "notebook": "Archive",
        "title": "Old set",
        "body": "Retired songs.",
        "tags": ["old"],
    },
)


class FakeSource:
    """In-memory stand-in for the Joplin backend."""

    def __init__(self, notes=(), error=None):
        self.notes = [dict(note) for note in notes]
        self.error = error
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return [dict(note) for note in self.notes]


class JoplinAdapterTests(unittest.TestCase):
    """Behaviour asserted through the adapter entry point."""

    def adapter(self, source, **kwargs):
        return joplin.JoplinAdapter(source, SETLIST, PRACTICE, **kwargs)

    # -- notebook restriction -------------------------------------------------
    def test_notes_come_only_from_configured_notebooks(self):
        view = self.adapter(FakeSource(SAMPLE_NOTES)).load()
        self.assertTrue(view.available)
        self.assertEqual([book.name for book in view.notebooks], [SETLIST, PRACTICE])
        self.assertEqual(
            [book.role for book in view.notebooks], ["setlist", "practice"]
        )
        self.assertEqual(
            [note.title for note in view.notes()],
            ["Warm up", "Sound check", "Barre chords"],
        )
        self.assertEqual(
            [note.notebook for note in view.notes()], [SETLIST, SETLIST, PRACTICE]
        )
        titles = [note.title for note in view.notes()]
        self.assertNotIn("Groceries", titles)
        self.assertNotIn("Old set", titles)

    def test_tags_come_only_from_configured_notebooks(self):
        view = self.adapter(FakeSource(SAMPLE_NOTES)).load()
        self.assertEqual(view.notebooks[0].tags, ("setlist", "warmup"))
        self.assertEqual(view.notebooks[1].tags, ("practice",))
        self.assertEqual(view.tags(), ("setlist", "warmup", "practice"))
        self.assertNotIn("private", view.tags())
        self.assertNotIn("old", view.tags())

    # -- escaping -------------------------------------------------------------
    def test_note_content_is_escaped_for_html(self):
        hostile = {
            "id": "s1",
            "notebook": SETLIST,
            "title": "<b>Bold</b>",
            "body": "<script>alert('x')</script><img src=x onerror=\"alert(1)\">",
            "tags": ["<i>tag</i>"],
        }
        view = self.adapter(FakeSource([hostile])).load()
        note = view.notes()[0]
        self.assertEqual(note.title, "&lt;b&gt;Bold&lt;/b&gt;")
        self.assertNotIn("<", note.body)
        self.assertIn("&lt;script&gt;", note.body)
        self.assertIn("onerror=&quot;alert(1)&quot;", note.body)
        self.assertEqual(note.tags, ("&lt;i&gt;tag&lt;/i&gt;",))

        rendered = joplin.render_view(view)
        self.assertIn("&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertNotIn("<img", rendered)
        self.assertNotIn("<i>", rendered)

    # -- unavailable service --------------------------------------------------
    def test_unavailable_service_degrades_gracefully(self):
        source = FakeSource(error=OSError("Connection refused"))
        view = self.adapter(source).load()
        self.assertFalse(view.available)
        self.assertEqual(view.notebooks, ())
        self.assertEqual(view.notes(), ())
        self.assertEqual(view.tags(), ())
        self.assertIn("Connection refused", view.reason)

        rendered = joplin.render_view(view)
        self.assertIn("joplin-unavailable", rendered)
        self.assertIn("Joplin is unavailable", rendered)
        self.assertIn("<section", rendered)

    def test_every_failure_mode_stays_usable(self):
        errors = (
            joplin.JoplinUnavailable("socket closed"),
            RuntimeError("boom"),
            ValueError("bad payload"),
        )
        for error in errors:
            with self.subTest(error=type(error).__name__):
                view = self.adapter(FakeSource(error=error)).load()
                self.assertFalse(view.available)
                self.assertIn(type(error).__name__, view.reason)
                self.assertTrue(joplin.render_view(view))

    # -- bounded-interval caching --------------------------------------------
    def test_repeated_reads_within_interval_call_joplin_once(self):
        source = FakeSource(SAMPLE_NOTES)
        now = [1000.0]
        adapter = self.adapter(source, cache_ttl=30.0, clock=lambda: now[0])

        first = adapter.load()
        second = adapter.load()
        now[0] += 29.0
        third = adapter.load()

        self.assertEqual(source.calls, 1)
        self.assertIs(second, first)
        self.assertIs(third, first)
        self.assertEqual(
            [note.title for note in first.notes()],
            ["Warm up", "Sound check", "Barre chords"],
        )

        now[0] += 2.0  # past the bounded interval
        fourth = adapter.load()
        self.assertEqual(source.calls, 2)
        self.assertIsNot(fourth, first)
        self.assertEqual(
            [note.title for note in fourth.notes()],
            [note.title for note in first.notes()],
        )

    def test_outage_is_cached_too(self):
        failing = FakeSource(error=OSError("down"))
        now = [2000.0]
        adapter = self.adapter(failing, cache_ttl=30.0, clock=lambda: now[0])
        self.assertFalse(adapter.load().available)
        self.assertFalse(adapter.load().available)
        self.assertEqual(failing.calls, 1)

    def test_invalidate_forces_a_fresh_read(self):
        source = FakeSource(SAMPLE_NOTES)
        adapter = self.adapter(source, cache_ttl=60.0)
        adapter.load()
        adapter.invalidate()
        adapter.load()
        self.assertEqual(source.calls, 2)

    # -- rendered fragment ----------------------------------------------------
    def test_rendered_view_has_no_edit_or_playback_controls(self):
        views = (
            self.adapter(FakeSource(SAMPLE_NOTES)).load(),
            self.adapter(FakeSource(error=OSError("down"))).load(),
        )
        forbidden = (
            "<button",
            "<form",
            "<input",
            "<textarea",
            "<select",
            "<audio",
            "<video",
            "<iframe",
            "contenteditable",
            "onclick",
            "edit",
            "play",
            "delete",
            "record",
        )
        for view in views:
            rendered = joplin.render_view(view)
            lowered = rendered.lower()
            for token in forbidden:
                self.assertNotIn(token, lowered)
            self.assertIn("<section", lowered)
        available = joplin.render_view(views[0])
        self.assertIn("Warm up", available)
        self.assertIn("A minor scale at 60 bpm.", available)


if __name__ == "__main__":
    unittest.main()
