"""Issue #16701: verify demo.py content was in app.py before deletion.

The ticket asks for a check that every function and import defined in the
deleted demo.py also exists in app.py. That check is underspecified here:
neither app.py nor demo.py is present in the attached files (app.py is shown
only as an EXCERPT, and demo.py is absent entirely), and no git history or
fixture containing demo.py is available to this test. The tests below
therefore assert the parts of the plan that can be checked without inventing
source text, and record the missing inputs explicitly rather than guessing.
"""

import ast
import os
import subprocess
import tempfile
import unittest

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


def _from_git_history(name):
    """Contents of a deleted file from git history, or None if unavailable."""
    try:
        out = subprocess.run(
            ["git", "log", "--all", "--diff-filter=D", "--format=%H", "--", name],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    commit = out.stdout.split()[0]
    try:
        show = subprocess.run(
            ["git", "show", "%s^:%s" % (commit, name)],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if show.returncode != 0:
        return None
    return show.stdout


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

    def test_demo_content_in_app(self):
        demo_path = _find(DEMO_CANDIDATES)
        demo_source = _read(demo_path) if demo_path else _from_git_history("demo.py")

        if demo_source is None:
            # Underspecified: demo.py is neither on disk nor recoverable from
            # git history, so the plan's first step cannot be performed.
            self.assertIsNone(demo_path)
            self.assertIsNone(_from_git_history("demo.py"))
            return

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

    def test_attached_files_do_not_include_demo_py(self):
        """Records why the verification is underspecified in this checkout."""
        self.assertIsNone(
            _find(DEMO_CANDIDATES),
            "demo.py unexpectedly present; the ticket's premise no longer holds",
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


if __name__ == "__main__":
    unittest.main()
