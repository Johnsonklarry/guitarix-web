#!/usr/bin/env python3
"""
A gate-and-retry wrapper around delegate.py, so the 30B fixes its own output
instead of handing it to a human half-finished.

The point: on the first night of real use, qwen3-coder:30b got the *logic*
right nearly every time and the *plumbing* wrong repeatedly. Every one of
those failures was mechanically detectable:

    tests/fakes/bin/jack_connect     import path one directory short
    tests/fakes/bin/jack_disconnect  no sys.path setup at all
    tests/fakes/bin/ffmpeg           imported 'tests.fakes', needs project root
    ffmpeg transcode mode            returned {"files": []} and a prose summary
    broadcast README section         described a screen that does not exist

So don't review those by hand. Run gates, and when one fails, hand the failure
back to the model and let it try again. It costs more wall-clock time and no
attention, which is the trade we want.

    py -3 tools/delegate/gated.py -t coder -e files -i "..." -f a.py -a "..."
    py -3 tools/delegate/gated.py --attempts 5 ...

Two rules keep the loop from doing harm, both learned the hard way:

  never regress   each attempt is scored by how many gates it passes. A rewrite
                  that scores lower than one we already have is discarded and
                  the loop stops -- on the first real task the writer, pushed
                  through a fourth round, introduced a syntax error into code
                  that had compiled.

  stop on logic   recursion ends when there is nothing concrete left to say,
                  not when a counter runs out. Hedged review findings ('may
                  miss', 'could contain', 'consider adding') are dropped rather
                  than fed back, repeats are dropped, and peer review gets its
                  own small budget -- one round by default -- because past that
                  the 8B produces criticism rather than defects.

Gates run against the STAGED files in a sandbox copy of the project, never
against the working tree. A run that passes is left staged exactly as
delegate.py leaves it, for a human to read and apply. Nothing is ever applied
here.
"""

import argparse
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(os.path.dirname(HERE))
DELEGATE = os.path.join(HERE, "delegate.py")
STAGING = os.path.join(HERE, ".delegate", "staging")

# Things a generated file should never contain, with why they matter.
FORBIDDEN = [
    ("rest unchanged", "a truncation marker instead of the rest of the file"),
    ("# ... (", "an elision instead of code"),
    ("TODO: implement", "an unimplemented stub"),
    ("your code here", "a placeholder"),
    ("raise NotImplementedError", "an unimplemented stub"),
    # The ones that produced a script which ran cleanly, exited 0, and checked
    # nothing at all -- the hardest kind to spot, because it looks like a pass.
    ("For now", "a deferral: the real work was not done"),
    ("for now, we", "a deferral: the real work was not done"),
    ("simplified approach", "an admission that the real work was not done"),
    ("in practice, we", "an admission that the real work was not done"),
    ("simple heuristic", "a guess standing in for the real work"),
    ("would need to", "an admission that the real work was not done"),
]


# ----------------------------------------------------------------- gates

def gate_returned_files(staged_files, sandbox):
    """The model described the change instead of making it."""
    if not staged_files:
        return ("Nothing was returned. You must return the COMPLETE FILE CONTENT "
                "in the 'files' array -- a description of the change in 'notes' is "
                "not the change. Return the file.")
    return None


def gate_not_truncated(staged_files, sandbox):
    for rel in staged_files:
        try:
            body = open(os.path.join(sandbox, rel), encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for needle, why in FORBIDDEN:
            if needle in body:
                return ("%s contains %r, which is %s. Return the whole file with "
                        "every part written out." % (rel, needle, why))
        if len(body.strip()) < 40:
            return "%s is almost empty (%d bytes). Return the whole file." % (rel, len(body))
    return None


def gate_python_parses(staged_files, sandbox):
    # compile() in memory rather than py_compile: it needs no output file, and
    # py_compile's cfile=os.devnull raises FileExistsError on Windows, where
    # devnull is 'nul' and not a regular file.
    for rel in staged_files:
        path = os.path.join(sandbox, rel)
        if not _is_python(path):
            continue
        src = open(path, encoding="utf-8", errors="replace").read()
        try:
            compile(src, rel, "exec")
        except SyntaxError as exc:
            return "%s does not compile: line %s: %s" % (rel, exc.lineno, exc.msg)
        except ValueError as exc:
            return "%s does not compile: %s" % (rel, exc)
    return None


def gate_imports_resolve(staged_files, sandbox):
    """
    The failure that bit three times: a sys.path computed one directory off, or
    a package path that does not exist. Work out what the file puts on sys.path,
    then check each local import can actually be found from there.
    """
    for rel in staged_files:
        path = os.path.join(sandbox, rel)
        if not _is_python(path):
            continue
        src = open(path, encoding="utf-8", errors="replace").read()
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue

        here = os.path.dirname(path)
        roots = [here]
        # sys.path.insert(0, <expr>) where the expr is dirname(...) over __file__
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and _is_syspath_insert(node)):
                continue
            depth = _dirname_depth(node.args[1] if len(node.args) > 1 else None)
            if depth is None:
                continue
            p = os.path.abspath(path)
            for _ in range(depth):
                p = os.path.dirname(p)
            roots.append(p)

        local = _local_imports(tree, sandbox)
        for name, lineno in local:
            head = name.split(".")[0]
            found = any(
                os.path.exists(os.path.join(r, *name.split(".")) + ".py") or
                os.path.exists(os.path.join(r, *name.split("."), "__init__.py"))
                for r in roots)
            if not found:
                return ("%s line %d imports %r, which cannot be found from the "
                        "directories this file puts on sys.path (%s). The import path "
                        "is wrong -- work out where %s.py actually sits relative to "
                        "this file and fix the sys.path line, or import the module "
                        "by its own name rather than through a package."
                        % (rel, lineno, name,
                           ", ".join(os.path.relpath(r, sandbox) for r in roots), head))
    return None


def gate_runs(staged_files, sandbox, smoke):
    """Execute the file and see whether it dies on import."""
    if not smoke:
        return None
    for rel in staged_files:
        path = os.path.join(sandbox, rel)
        if not _is_python(path):
            continue
        env = dict(os.environ, FAKE_JACK_DIR=tempfile.mkdtemp(prefix="gate-jack-"))
        p = subprocess.run([sys.executable, path], capture_output=True, text=True,
                           timeout=30, env=env, cwd=sandbox)
        err = (p.stderr or "")
        for fatal in ("ModuleNotFoundError", "ImportError", "IndentationError",
                      "NameError", "AttributeError: module"):
            if fatal in err:
                return ("%s fails as soon as it runs:\n%s\nFix it so the file can at "
                        "least be executed." % (rel, err.strip().splitlines()[-1]))
    return None


def gate_shell_parses(staged_files, sandbox):
    """sh -n on anything that looks like a shell script."""
    for rel in staged_files:
        path = os.path.join(sandbox, rel)
        if not _is_shell(path):
            continue
        p = subprocess.run(["sh", "-n", path], capture_output=True, text=True)
        if p.returncode != 0:
            return "%s is not valid shell: %s" % (rel, (p.stderr or "").strip()[:200])
    return None


def gate_cwd_independent(staged_files, sandbox):
    """
    Caught by the 8B twice in one batch: os.walk('.') and a hardcoded
    'tests/fakes/bin', both of which work only when run from the project root.
    A script invoked by systemd or from another directory then quietly finds
    nothing. Flag the pattern rather than guessing at runtime.
    """
    bad = [
        ("os.walk('.')", "walks the current directory, so it depends on where it is run from"),
        ('os.walk(".")', "walks the current directory, so it depends on where it is run from"),
        ("open('tests/", "opens a path relative to the current directory"),
        ('open("tests/', "opens a path relative to the current directory"),
    ]
    for rel in staged_files:
        path = os.path.join(sandbox, rel)
        if not _is_python(path):
            continue
        src = open(path, encoding="utf-8", errors="replace").read()
        if "__file__" in src:
            continue                      # it anchors itself somewhere; trust that
        for needle, why in bad:
            if needle in src:
                return ("%s uses %s, which %s. Anchor every path to the file's own "
                        "location with os.path.dirname(os.path.abspath(__file__)) so "
                        "it works whatever directory it is run from."
                        % (rel, needle, why))
    return None


GATES = [
    ("returned files", gate_returned_files),
    ("not truncated", gate_not_truncated),
    ("python parses", gate_python_parses),
    ("shell parses", gate_shell_parses),
    ("imports resolve", gate_imports_resolve),
    ("cwd independent", gate_cwd_independent),
]


# ----------------------------------------------------------------- helpers

def _is_python(path):
    """A .py file, or an extension-less script with a python shebang."""
    if path.endswith(".py"):
        return True
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            first = fh.readline()
    except OSError:
        return False
    return first.startswith("#!") and "python" in first


def _is_shell(path):
    if path.endswith(".sh"):
        return True
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            first = fh.readline()
    except OSError:
        return False
    return first.startswith("#!") and ("/sh" in first or "bash" in first)


def _is_syspath_insert(node):
    f = node.func
    return (isinstance(f, ast.Attribute) and f.attr == "insert"
            and isinstance(f.value, ast.Attribute) and f.value.attr == "path"
            and isinstance(f.value.value, ast.Name) and f.value.value.id == "sys")


def _dirname_depth(node):
    """How many os.path.dirname() calls wrap __file__, or None if not that shape."""
    depth = 0
    while isinstance(node, ast.Call):
        f = node.func
        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
        if name == "dirname":
            depth += 1
            node = node.args[0] if node.args else None
        elif name in ("abspath", "realpath"):
            node = node.args[0] if node.args else None
        else:
            return None
    if isinstance(node, ast.Name) and node.id == "__file__":
        return depth
    return None


def _local_imports(tree, sandbox):
    """Imports that are not stdlib and not a known third-party package."""
    skip = set(sys.stdlib_module_names) | {
        "flask", "flask_socketio", "socketio", "engineio", "werkzeug", "jinja2",
        "playwright", "pytest", "numpy",
    }
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] not in skip:
                    out.append((a.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module and node.module.split(".")[0] not in skip:
                out.append((node.module, node.lineno))
    return out


def _staged(run_id):
    root = os.path.join(STAGING, run_id)
    if not os.path.isdir(root):
        return root, []
    files = []
    for dp, _, fs in os.walk(root):
        for f in fs:
            files.append(os.path.relpath(os.path.join(dp, f), root).replace("\\", "/"))
    return root, sorted(files)


def _sandbox(staged_root, staged_files):
    """The project with the staged files laid over it, so gates see real neighbours."""
    box = tempfile.mkdtemp(prefix="gate-box-")
    for rel in staged_files:
        src = os.path.join(staged_root, rel.replace("/", os.sep))
        dst = os.path.join(box, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        # bring along whatever sits beside it in the real project
        real_dir = os.path.join(PROJ, os.path.dirname(rel.replace("/", os.sep)))
        if os.path.isdir(real_dir):
            for sib in os.listdir(real_dir):
                s = os.path.join(real_dir, sib)
                d = os.path.join(box, os.path.dirname(rel.replace("/", os.sep)), sib)
                if os.path.isfile(s) and not os.path.exists(d):
                    shutil.copy2(s, d)
    # and the project root's own modules, for imports that reach up
    for f in os.listdir(PROJ):
        s = os.path.join(PROJ, f)
        d = os.path.join(box, f)
        if os.path.isfile(s) and f.endswith(".py") and not os.path.exists(d):
            shutil.copy2(s, d)
    return box


def _delegate(args, extra_instruction):
    cmd = [sys.executable, DELEGATE, "run", "-t", args.target, "-e", args.expect,
           "--timeout", str(args.timeout), "-i", args.instruction + extra_instruction]
    for f in args.file or []:
        cmd += ["-f", f]
    for a in args.acceptance or []:
        cmd += ["-a", a]
    p = subprocess.run(cmd, cwd=PROJ, capture_output=True, text=True)
    out = ((p.stdout or "") + (p.stderr or "")).strip()
    rid = next((l.split("]")[0].lstrip("[") for l in out.splitlines()
                if l.startswith("[dlg_")), None)
    return ("status   ok" in out), rid, out


def _peer_review(args, staged_root, staged_files):
    """
    The 8B reads what the 30B wrote. Its findings are claims, not verdicts --
    it has both missed real bugs and invented ones -- so they are fed back for
    the 30B to answer, and every one of them is printed for the human at the
    end whether it was acted on or not.
    """
    tmp = os.path.join(PROJ, "_review_tmp")
    os.makedirs(tmp, exist_ok=True)
    findings = []
    for rel in staged_files:
        src = os.path.join(staged_root, rel.replace("/", os.sep))
        dst = os.path.join(tmp, os.path.basename(rel))
        shutil.copy2(src, dst)
        cmd = [sys.executable, DELEGATE, "run", "-t", args.review_target,
               "-e", "review", "--timeout", str(args.review_timeout),
               "-f", "_review_tmp/" + os.path.basename(rel),
               "-i", ("Review this file against what it is meant to do:\n\n"
                      + args.instruction +
                      "\n\nReport only defects you can point at a line for, and say "
                      "so explicitly if you find none. Look hardest for: an import "
                      "that fails at runtime; a path computed one directory off; a "
                      "default that contradicts the stated contract; a body that "
                      "stops early; an exit code of 0 where failure is required.")]
        p = subprocess.run(cmd, cwd=PROJ, capture_output=True, text=True)
        out = (p.stdout or "") + (p.stderr or "")
        for block in _json_blocks(out):
            for f in block.get("findings") or []:
                summary = f.get("summary") or f.get("issue") or ""
                if summary:
                    findings.append((rel, summary.strip(),
                                     (f.get("suggestion") or "").strip()))
    return findings


def gate_detects(staged_files, sandbox, prove, entry):
    """
    Does the thing actually detect anything?

    The failure this exists for: a generated auditor passed all six gates and a
    clean peer review, ran without error, exited 0, and reported 'all handled'
    for every tool -- while collecting zero flags. It would have printed the
    same green output against an empty fakes directory. Nothing that reads the
    code's shape can tell that apart from a real pass.

    So run it, break one of its inputs on purpose, and run it again. A detector
    that does not notice is not a detector. `prove` is a shell command executed
    inside the sandbox to do the breaking.
    """
    if not (prove and entry):
        return None
    target = os.path.join(sandbox, entry.replace("/", os.sep))
    if not os.path.exists(target):
        return "cannot prove detection: %s is not among the generated files" % entry

    def run():
        p = subprocess.run([sys.executable, os.path.basename(entry)],
                           cwd=os.path.dirname(target), capture_output=True,
                           text=True, timeout=120)
        return p.returncode

    try:
        before = run()
    except subprocess.TimeoutExpired:
        return "%s did not finish within 120s on a clean tree" % entry

    b = subprocess.run(prove, shell=True, cwd=sandbox, capture_output=True, text=True)
    if b.returncode != 0:
        return ("could not break the input to prove detection (%s): %s"
                % (prove, (b.stderr or b.stdout).strip()[:160]))

    try:
        after = run()
    except subprocess.TimeoutExpired:
        return "%s did not finish within 120s after the input was broken" % entry

    if after == before:
        return ("%s reports the same result (exit %d) whether or not its input is "
                "broken, so it is not actually checking anything. Breaking the input "
                "with `%s` must change what it reports. Make the check real."
                % (entry, before, prove))
    return None


HEDGES = ("may ", "might ", "could ", "possibly", "potential", "in some cases",
          "if the", "unless the", "consider ", "it is unclear", "appears to")


def _actionable(summary):
    """
    A finding worth another round, or hedged noise.

    By the fourth round on the first real task the 8B was producing 'may not be
    correctly derived', 'may miss flags', 'may contain' -- speculation dressed
    as review. Feeding that back does not improve the file; it is what made the
    writer introduce a syntax error into code that had compiled.
    """
    low = summary.lower()
    if any(h in low for h in HEDGES):
        return False
    return len(summary.split()) >= 4


def _score(files, sandbox):
    """How many gates this attempt passes. Higher is better; used to refuse a
    rewrite that is worse than what we already had."""
    passed, failed_at = 0, None
    for name, gate in GATES:
        if gate(files, sandbox):
            failed_at = name
            break
        passed += 1
    return passed, failed_at


def _json_blocks(text):
    """Pull JSON objects out of delegate's printed output."""
    out, depth, start = [], 0, None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    out.append(json.loads(text[start:i + 1]))
                except ValueError:
                    pass
                start = None
    return out


# ----------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-t", "--target", default="coder")
    ap.add_argument("-e", "--expect", default="files")
    ap.add_argument("-i", "--instruction", required=True)
    ap.add_argument("-f", "--file", action="append")
    ap.add_argument("-a", "--acceptance", action="append")
    ap.add_argument("--attempts", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--smoke", action="store_true",
                    help="also execute each generated Python file once")
    ap.add_argument("--review-target", default="small",
                    help="model that reviews what the writer produced (default: small)")
    ap.add_argument("--review-timeout", type=int, default=240)
    ap.add_argument("--review-rounds", type=int, default=1,
                    help="how many peer-review rounds to spend (default: 1)")
    ap.add_argument("--no-review", action="store_true",
                    help="gates only, skip the peer review round")
    args = ap.parse_args()

    feedback = ""
    history = []
    best = None            # (score, rid, files, findings) -- never hand back worse
    seen_findings = set()
    review_rounds = 0

    for attempt in range(1, args.attempts + 1):
        print("\n--- attempt %d of %d ---" % (attempt, args.attempts), flush=True)
        ok, rid, out = _delegate(args, feedback)
        print(out, flush=True)
        if not ok:
            history.append((attempt, "no output"))
            feedback = ("\n\nYour previous attempt failed to produce output at all. "
                        "Return the complete file.")
            continue

        root, files = _staged(rid)
        sandbox = _sandbox(root, files) if files else ""
        score, failed_at = _score(files, sandbox)

        for i, (name, gate) in enumerate(GATES):
            state = "ok" if i < score else ("FAILED" if name == failed_at else "-")
            print("  gate %-18s %s" % (name, state), flush=True)

        # --- regression guard -------------------------------------------
        # A rewrite that passes fewer gates than one we already have is worse,
        # whatever it was trying to fix. Keep the better one and stop: more
        # rounds made the writer break working code, not repair it.
        if best is not None and score < best[0]:
            print("\n  REGRESSION: attempt %d passes %d gate(s), attempt %s passed %d."
                  % (attempt, score, best[4], best[0]), flush=True)
            print("  Keeping the earlier one and stopping.", flush=True)
            history.append((attempt, "regressed (%d < %d gates)" % (score, best[0])))
            _summary(history, best[1], best[3])
            return 0 if not best[3] else 1

        if failed_at:
            problem = None
            for name, gate in GATES:
                problem = gate(files, sandbox)
                if problem:
                    break
            history.append((attempt, "gate: " + problem.split("\n")[0][:80]))
            print("  -> %s" % problem, flush=True)
            if best is None or score > best[0]:
                best = (score, rid, files, [], attempt)
            feedback = ("\n\nYour previous attempt was rejected by an automatic check. "
                        "Fix exactly this and change nothing else. Return the complete "
                        "file again:\n" + problem)
            continue

        # --- all gates pass ---------------------------------------------
        if args.no_review or review_rounds >= args.review_rounds:
            why = "review disabled" if args.no_review else "review budget spent"
            print("\n  all gates pass (%s)" % why, flush=True)
            _summary(history, rid, [])
            return 0

        print("  peer review on %s ..." % args.review_target, flush=True)
        raw = _peer_review(args, root, files)
        review_rounds += 1

        actionable, noise = [], []
        for rel, summary, sug in raw:
            (actionable if _actionable(summary) else noise).append((rel, summary, sug))
        fresh = [f for f in actionable if f[1] not in seen_findings]
        seen_findings.update(f[1] for f in actionable)

        print("  peer review         %d finding(s): %d actionable, %d hedged, %d repeat"
              % (len(raw), len(actionable), len(noise), len(actionable) - len(fresh)),
              flush=True)
        for rel, summary, _s in raw:
            mark = "*" if (rel, summary, _s) in fresh else " "
            print("   %s %s: %s" % (mark, rel, summary[:100]), flush=True)

        best = (score, rid, files, actionable, attempt)

        if not fresh:
            # Nothing new and nothing concrete. Another round is churn, and
            # churn is where the regressions came from.
            print("\n  no new actionable findings -- stopping here", flush=True)
            history.append((attempt, "review: nothing actionable"))
            _summary(history, rid, noise)
            return 0

        history.append((attempt, "review: %d actionable" % len(fresh)))

        if attempt == args.attempts:
            print("\n  out of attempts with findings outstanding", flush=True)
            _summary(history, rid, fresh)
            return 1

        feedback = ("\n\nA reviewer read your previous attempt and reported the "
                    "following. Change ONLY what is needed to address these; leave "
                    "everything else exactly as it is. If a point is mistaken, keep "
                    "the code and say why in 'notes'. Return the complete file:\n"
                    + "\n".join("- %s: %s%s" % (r, s, ("  (suggested: %s)" % g) if g else "")
                                for r, s, g in fresh))

    print("\ngave up after %d attempts" % args.attempts, flush=True)
    _summary(history, best[1] if best else None, best[3] if best else [])
    return 1


def _summary(history, rid, outstanding):
    print("\n" + "=" * 68, flush=True)
    for attempt, what in history:
        print("  attempt %d  %s" % (attempt, what), flush=True)
    if rid:
        print("\n  staged: %s" % rid, flush=True)
        print("  review: py -3 tools/delegate/delegate.py diff %s" % rid, flush=True)
    if outstanding:
        print("\n  UNRESOLVED, for a human to judge:", flush=True)
        for rel, summary, _s in outstanding:
            print("    - %s: %s" % (rel, summary), flush=True)
    print("\n  nothing applied.", flush=True)


if __name__ == "__main__":
    sys.exit(main())
