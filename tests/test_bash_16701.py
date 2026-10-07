"""Issue #16701: verify demo.py content was in app.py before deletion.

The ticket asks for a check that every function and import defined in the
deleted demo.py also exists in app.py. That check needs both files, and this
checkout supplies neither: app.py is shown only as an EXCERPT, and demo.py is
absent entirely. When demo.py can be found neither on disk nor in git history
the test now skips, so a missing input is reported as a skip instead of a pass
for a verification that never ran.
"""

import ast
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEMO_CANDIDATES = ("demo.py",)
APP_CANDIDATES = ("app.py",)


class HistoryLookupError(RuntimeError):
    """git could not answer whether the deleted file ever existed.

    Distinct from "the path was never tracked": a failed lookup must not be
    reported as an absent file, or the test silently degrades to a pass.
    """


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _write(directory, name, source):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(source)
    return path


def _find(names):
    """First existing file among names, relative to the repo root."""
    for name in names:
        path = os.path.join(ROOT, name)
        if os.path.isfile(path):
            return path
    return None


def _run_git(args):
    try:
        return subprocess.run(["git"] + args, cwd=ROOT, capture_output=True,
                              text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise HistoryLookupError("could not run git %s: %s" % (" ".join(args), exc))


def _in_repo():
    try:
        return _run_git(["rev-parse", "--git-dir"]).returncode == 0
    except HistoryLookupError:
        return False


def _from_git_history(name):
    """Last known contents of a deleted file, or None if it was never tracked.

    Raises HistoryLookupError when git is present but the lookup fails, so the
    caller can tell "the file is gone" from "I could not find out".
    """
    if not _in_repo():
        return None
    log = _run_git(["log", "--all", "--format=%H", "--", name])
    if log.returncode != 0:
        raise HistoryLookupError("git log failed for %s: %s" % (name, log.stderr.strip()))
    # `git log` lists every commit that touched the path, newest first. The
    # deletion commit's parent holds the last content; reading the commit's
    # own tree covers a rename, and a deletion with no usable parent (a root
    # commit) simply falls through to None below.
    for commit in log.stdout.split():
        for rev in ("%s^:%s" % (commit, name), "%s:%s" % (commit, name)):
            show = _run_git(["show", rev])
            if show.returncode == 0:
                return show.stdout
    return None


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


class DemoContentInAppTest(unittest.TestCase):
    """Ticket test plan, driven through test_demo_content_in_app."""

    def _demo_source(self):
        demo_path = _find(DEMO_CANDIDATES)
        if demo_path:
            return _read(demo_path)
        return _from_git_history("demo.py")

    def test_demo_content_in_app(self):
        demo_source = self._demo_source()
        if demo_source is None:
            # demo.py is neither on disk nor in git history, so there is no
            # source text to compare and every function and import of the
            # deleted file is unknown. Skipping says that out loud; returning
            # here would report a pass for a check that never ran.
            self.skipTest("demo.py is neither on disk nor in git history; "
                          "the demo-content check has no input to verify")

        app_path = _find(APP_CANDIDATES)
        self.assertIsNotNone(app_path, "app.py not found; cannot compare")
        app_source = _read(app_path)

        demo_funcs, demo_imports = _defs_and_imports(demo_source)
        app_funcs, app_imports = _defs_and_imports(app_source)

        missing_funcs = sorted(demo_funcs - app_funcs)
        missing_imports = sorted(demo_imports - app_imports)

        self.assertEqual(
            missing_funcs, [],
            "functions defined in demo.py but absent from app.py: %s" % missing_funcs,
        )
        self.assertEqual(
            missing_imports, [],
            "imports used by demo.py but absent from app.py: %s" % missing_imports,
        )

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


class MissingInputTest(unittest.TestCase):
    """Regression tests for the vacuous pass this change was reviewed for.

    demo.py is absent from this checkout, so the ticket's check cannot run
    here. These tests pin the behaviour that matters: a missing input is a
    skip, never a silent pass.
    """

    def _case(self):
        return DemoContentInAppTest("test_demo_content_in_app")

    def test_absent_demo_py_raises_skip(self):
        with mock.patch.dict(globals(), {"_find": lambda names: None,
                                         "_from_git_history": lambda name: None}):
            with self.assertRaises(unittest.SkipTest):
                self._case().test_demo_content_in_app()

    def test_absent_demo_py_is_recorded_as_a_skip_not_a_pass(self):
        result = unittest.TestResult()
        with mock.patch.dict(globals(), {"_find": lambda names: None,
                                         "_from_git_history": lambda name: None}):
            self._case().run(result)
        self.assertEqual(result.testsRun, 1)
        self.assertEqual(len(result.skips), 1)
        self.assertEqual(result.failures + result.errors, [])
        self.assertIn("demo.py", result.skips[0][1])

    def _patched_find(self, tmp, demo_body, app_body):
        demo = _write(tmp, "demo.py", demo_body)
        app = _write(tmp, "app.py", app_body)
        return mock.patch.dict(globals(), {
            "_find": lambda names: demo if names == DEMO_CANDIDATES else app})

    def test_function_missing_from_app_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self._patched_find(tmp,
                                    "def kept():\n    pass\n\n\ndef lost():\n    pass\n",
                                    "def kept():\n    pass\n"):
                with self.assertRaises(AssertionError) as caught:
                    self._case().test_demo_content_in_app()
        self.assertIn("lost", str(caught.exception))

    def test_import_missing_from_app_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self._patched_find(tmp,
                                    "import os\nfrom json import dumps\n",
                                    "import os\n"):
                with self.assertRaises(AssertionError) as caught:
                    self._case().test_demo_content_in_app()
        self.assertIn("json", str(caught.exception))

    def test_preserved_content_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = ("import os\n\n\ndef kept():\n    def inner():\n        pass\n"
                    "    return inner\n")
            with self._patched_find(tmp, body, body):
                self._case().test_demo_content_in_app()


class GitHistoryTest(unittest.TestCase):
    """_from_git_history must recover a deleted file, and never guess."""

    def setUp(self):
        if shutil.which("git") is None:
            self.skipTest("git is not installed")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = self._tmp.name
        patcher = mock.patch.dict(globals(), {"ROOT": self.repo})
        patcher.start()
        self.addCleanup(patcher.stop)
        self._git("init", "-q")
        self._git("config", "user.email", "test@example.invalid")
        self._git("config", "user.name", "Test")
        self._git("config", "commit.gpgsign", "false")

    def _git(self, *args):
        out = subprocess.run(["git"] + list(args), cwd=self.repo,
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr or out.stdout)
        return out.stdout

    def test_deleted_file_content_is_recovered(self):
        _write(self.repo, "demo.py", "def gone():\n    pass\n")
        self._git("add", "demo.py")
        self._git("commit", "-qm", "add demo")
        self._git("rm", "-q", "demo.py")
        self._git("commit", "-qm", "delete demo")
        self.assertIn("def gone():", _from_git_history("demo.py"))

    def test_renamed_then_deleted_file_is_recovered(self):
        _write(self.repo, "demo.py", "def gone():\n    pass\n")
        self._git("add", "demo.py")
        self._git("commit", "-qm", "add demo")
        self._git("mv", "demo.py", "renamed.py")
        self._git("commit", "-qm", "rename demo")
        self._git("rm", "-q", "renamed.py")
        self._git("commit", "-qm", "delete renamed")
        self.assertIn("def gone():", _from_git_history("demo.py"))

    def test_untracked_path_is_none_not_an_error(self):
        _write(self.repo, "demo.py", "x = 1\n")
        self._git("add", "demo.py")
        self._git("commit", "-qm", "add demo")
        self.assertIsNone(_from_git_history("never_existed.py"))

    def test_lookup_falls_back_when_the_parent_tree_lacks_the_path(self):
        def fake_run_git(args):
            if args[0] == "rev-parse":
                return subprocess.CompletedProcess(args, 0, "", "")
            if args[0] == "log":
                return subprocess.CompletedProcess(args, 0, "abc123\n", "")
            if args[1].startswith("abc123^:"):
                return subprocess.CompletedProcess(args, 128, "", "fatal: absent in parent")
            return subprocess.CompletedProcess(args, 0, "def gone():\n    pass\n", "")

        with mock.patch.dict(globals(), {"_run_git": fake_run_git}):
            self.assertEqual(_from_git_history("demo.py"), "def gone():\n    pass\n")

    def test_failed_lookup_raises_instead_of_returning_none(self):
        failed = subprocess.CompletedProcess(["git"], 128, "", "fatal: not a git repository")
        with mock.patch.dict(globals(), {"_in_repo": lambda: True,
                                         "_run_git": lambda args: failed}):
            with self.assertRaises(HistoryLookupError):
                _from_git_history("demo.py")

    def test_not_a_repository_yields_none(self):
        with mock.patch.dict(globals(), {"_in_repo": lambda: False}):
            self.assertIsNone(_from_git_history("demo.py"))


if __name__ == "__main__":
    unittest.main()
