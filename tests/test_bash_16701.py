"""Issue #16701: verify demo.py content was in app.py before deletion.

The ticket asks for a check that every function and import defined in the
deleted demo.py also exists in app.py. That check is underspecified here:
neither app.py nor demo.py is present in the attached files (app.py is shown
only as an EXCERPT, and demo.py is absent entirely), and no git history or
fixture containing demo.py is available to this test. So that a missing
input can never be mistaken for a passing check, test_demo_content_in_app
skips when demo.py or app.py cannot be read, and the comparison itself
(_missing_from_app) is covered directly against sources this test owns.
"""

import ast
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEMO_CANDIDATES = ("demo.py",)
APP_CANDIDATES = ("app.py",)


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _find(names):
    """First existing file among names, relative to the repo root."""
    for name in names:
        path = os.path.join(ROOT, name)
        if os.path.isfile(path):
            return path
    return None


NO_HISTORY = object()   # the name was never tracked: nothing to recover


class GitError(RuntimeError):
    """git exists but could not answer: not a repository, timeout, bad rev."""


def _git(*args):
    """Run git in the repo root, raising GitError if it cannot be run at all."""
    try:
        return subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitError("git %s could not run: %s" % (" ".join(args), exc))


def _from_git_history(name):
    """Contents of a tracked, deleted ``name``; NO_HISTORY if never tracked.

    Raises GitError rather than returning None when git cannot answer, so a
    failed lookup is never mistaken for "the file never existed".
    """
    log = _git("log", "--all", "--diff-filter=D", "--format=%H", "--", name)
    if log.returncode != 0:
        raise GitError(log.stderr.strip() or "git log failed")
    commits = log.stdout.split()
    if not commits:
        return NO_HISTORY
    for commit in commits:
        # The delete commit no longer holds the blob; its parent does. A root
        # commit or a shallow clone has no parent, so try both revisions and
        # give up only once every deletion commit has been examined.
        for rev in ("%s^" % commit, commit):
            if _git("rev-parse", "--verify", "--quiet", rev).returncode != 0:
                continue
            show = _git("show", "%s:%s" % (rev, name))
            if show.returncode == 0:
                return show.stdout
    raise GitError(
        "%s has %d deletion commit(s) but no resolvable revision" % (name, len(commits))
    )


def _defs_and_imports(source):
    """Top-level and nested function names plus imported module names."""
    tree = ast.parse(source)
    funcs = set()
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.add(node.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module.split(".")[0])
    return funcs, imports


def _missing_from_app(demo_source, app_source):
    """Functions and imports the demo had that app.py does not define.

    Returns (missing_functions, missing_imports), both sorted, so the check
    the ticket asks for is testable without the deleted demo.py on hand.
    """
    demo_funcs, demo_imports = _defs_and_imports(demo_source)
    app_funcs, app_imports = _defs_and_imports(app_source)
    return sorted(demo_funcs - app_funcs), sorted(demo_imports - app_imports)


class DemoContentInAppTest(unittest.TestCase):
    """Ticket test plan, driven through test_demo_content_in_app."""

    def test_demo_content_in_app(self):
        demo_path = _find(DEMO_CANDIDATES)
        if demo_path is not None:
            demo_source = _read(demo_path)
        else:
            try:
                demo_source = _from_git_history("demo.py")
            except GitError as exc:
                self.skipTest("cannot read demo.py's history: %s" % exc)
            if demo_source is NO_HISTORY:
                # Underspecified: demo.py is neither on disk nor recoverable
                # from git history. Skip -- do not return -- so a missing
                # input can never be mistaken for a passing regression test.
                self.skipTest("demo.py is not on disk and was never tracked")

        app_path = _find(APP_CANDIDATES)
        self.assertIsNotNone(app_path, "app.py not found; cannot compare")
        app_source = _read(app_path)

        missing_funcs, missing_imports = _missing_from_app(demo_source, app_source)

        self.assertEqual(
            missing_funcs, [],
            "functions defined in demo.py but absent from app.py: %s" % missing_funcs,
        )
        self.assertEqual(
            missing_imports, [],
            "imports used by demo.py but absent from app.py: %s" % missing_imports,
        )

    def test_missing_inputs_skip_rather_than_pass(self):
        """A missing demo.py must surface as a skip, never a silent pass."""
        module = sys.modules[__name__]
        with mock.patch.object(module, "_find", return_value=None), \
                mock.patch.object(module, "_from_git_history", return_value=NO_HISTORY):
            case = DemoContentInAppTest("test_demo_content_in_app")
            with self.assertRaises(unittest.SkipTest):
                case.test_demo_content_in_app()

    def test_unreadable_history_skips_with_the_reason(self):
        """A failed git lookup must not masquerade as 'never existed'."""
        module = sys.modules[__name__]
        with mock.patch.object(module, "_find", return_value=None), \
                mock.patch.object(module, "_from_git_history",
                                  side_effect=GitError("shallow clone")):
            case = DemoContentInAppTest("test_demo_content_in_app")
            with self.assertRaises(unittest.SkipTest) as caught:
                case.test_demo_content_in_app()
        self.assertIn("shallow clone", str(caught.exception))

    def test_ast_extraction_round_trip(self):
        """The extractor itself works, on a temp file rather than the repo."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sample.py")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("import os\nfrom json import dumps\n\n"
                         "def alpha():\n    def inner():\n        pass\n    return inner\n")
            funcs, imports = _defs_and_imports(_read(path))
        self.assertEqual(funcs, {"alpha", "inner"})
        self.assertEqual(imports, {"os", "json"})


class MissingFromAppTest(unittest.TestCase):
    """The comparison the ticket asks for, on sources the test owns."""

    DEMO = (
        "import os\n"
        "from json import dumps\n"
        "\n"
        "def alpha():\n"
        "    return dumps(os.getcwd())\n"
        "\n"
        "def beta():\n"
        "    return alpha()\n"
    )

    def test_missing_function_is_reported(self):
        app = "import os\nfrom json import dumps\n\ndef alpha():\n    return 1\n"
        self.assertEqual(_missing_from_app(self.DEMO, app), (["beta"], []))

    def test_missing_import_is_reported(self):
        app = "def alpha():\n    return 1\n\ndef beta():\n    return alpha()\n"
        self.assertEqual(_missing_from_app(self.DEMO, app), ([], ["json", "os"]))

    def test_demo_fully_folded_into_app_is_clean(self):
        app = self.DEMO + "\ndef gamma():\n    return beta()\n"
        self.assertEqual(_missing_from_app(self.DEMO, app), ([], []))


if __name__ == "__main__":
    unittest.main()
