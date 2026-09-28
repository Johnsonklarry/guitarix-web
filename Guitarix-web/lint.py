#!/usr/bin/env python3
"""
A small static check, since pyflakes may not be installed on the Pi.

  * names used in a module that nothing in that module defines -- typos,
    missing imports
  * module.attribute references between this project's own files that point
    at something the other module doesn't have -- stale references after a
    rename or a move

Deliberately coarse (one namespace per file), so it can miss a name used
outside its scope, but it doesn't cry wolf.

    python3 tests/lint.py
"""
import ast
import builtins
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULES = sorted(f[:-3] for f in os.listdir(ROOT) if f.endswith(".py"))


def bound_names(tree):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                names.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
    return names


def top_level(tree):
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            for t in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        names.add(n.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                names.add((a.asname or a.name).split(".")[0])
    return names


def main():
    trees = {m: ast.parse(open(os.path.join(ROOT, m + ".py")).read(), m + ".py") for m in MODULES}
    tops = {m: top_level(t) for m, t in trees.items()}
    problems = []
    for mod, tree in trees.items():
        known = bound_names(tree) | set(dir(builtins)) | {"__file__", "__name__"}
        project_imports = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name in tops:
                        project_imports[a.asname or a.name] = a.name
            elif isinstance(node, ast.ImportFrom) and node.module in tops:
                for a in node.names:
                    if a.name not in tops[node.module]:
                        problems.append("%s.py:%d  imports %s from %s, which doesn't define it"
                                        % (mod, node.lineno, a.name, node.module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id not in known:
                problems.append("%s.py:%d  '%s' is never defined" % (mod, node.lineno, node.id))
            if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                    and node.value.id in project_imports):
                target = project_imports[node.value.id]
                if node.attr not in tops[target]:
                    problems.append("%s.py:%d  %s.%s doesn't exist in %s.py"
                                    % (mod, node.lineno, node.value.id, node.attr, target))
    for p in sorted(set(problems)):
        print(p)
    print("%d problem%s in %d modules" % (len(set(problems)), "" if len(set(problems)) == 1 else "s", len(MODULES)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
