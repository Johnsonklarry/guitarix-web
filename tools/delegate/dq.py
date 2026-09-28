#!/usr/bin/env python3
"""Quiet, standard-library front-end for delegate.py."""
from __future__ import annotations

import argparse
import concurrent.futures
import difflib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
STORE = Path(os.environ.get("DELEGATE_HOME", HERE)).resolve() / ".delegate"
RUN_ID = re.compile(r"\[(dlg_[A-Za-z0-9_]+)\]")
TOOLS = ("bash", "edit", "write", "read", "grep", "glob", "list", "patch",
         "webfetch", "task", "todowrite", "todoread", "skill", "question")

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def short(value, limit):
    return " ".join(str(value or "").split())[:limit]


def emit(data, lines, json_mode):
    if json_mode:
        print(json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str))
    else:
        for line in lines:
            print(line)


def record(run_id, store=STORE):
    if not re.fullmatch(r"dlg_[A-Za-z0-9_]+", run_id):
        raise ValueError("invalid run id")
    return json.loads((store / "runs" / (run_id + ".json")).read_text(encoding="utf-8"))


def staged(run_id, store=STORE):
    root = store / "staging" / run_id
    return sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: p.as_posix()) if root.is_dir() else []


def summary(run_id, store=STORE):
    run = record(run_id, store)
    result = run["result"]
    usage = result.get("usage") or {}
    return {"id": run_id, "status": result.get("status", "error"),
            "target": run.get("request", {}).get("target", {}).get("name", "-"),
            "secs": round((result.get("duration_ms") or 0) / 1000, 1),
            "in": usage.get("prompt_tokens"), "out": usage.get("completion_tokens"),
            "files": len(staged(run_id, store)), "error": result.get("error")}


def summary_line(item):
    line = (f"{item['id']} {item['status']} {item['target']} {item['secs']:.1f}s "
            f"in={item['in'] if item['in'] is not None else '-'} "
            f"out={item['out'] if item['out'] is not None else '-'} files={item['files']}")
    return line + (" err: " + short(item["error"], 100) if item.get("error") else "")


def delegate(*args):
    return subprocess.run([sys.executable, str(HERE / "delegate.py"), *args],
                          cwd=Path.cwd(), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def run_flags(spec):
    flags = []
    names = {"target": "--target", "instruction": "--instruction", "kind": "--kind",
             "expect": "--expect", "effort": "--effort", "timeout": "--timeout",
             "temperature": "--temperature", "num_ctx": "--num-ctx"}
    for key, flag in names.items():
        if key in spec and spec[key] is not None:
            # delegate.py has no --effort flag.
            if key == "effort":
                raise ValueError("delegate.py does not support effort")
            flags.extend((flag, str(spec[key])))
    for key, flag in (("files", "--file"), ("acceptance", "--acceptance"),
                      ("allow_edit", "--allow-edit")):
        values = spec.get(key, [])
        if not isinstance(values, list):
            raise ValueError(key + " must be a list")
        for value in values:
            flags.extend((flag, str(value)))
    return flags


def execute(flags):
    proc = delegate("run", *flags)
    match = RUN_ID.search(proc.stderr)
    if not match:
        raise RuntimeError(short(proc.stderr or proc.stdout or "delegate.py produced no run id", 160))
    item = summary(match.group(1))
    return item


def show_data(run_id, full=False, store=STORE):
    run = record(run_id, store)
    result = run["result"]
    output = result.get("output") or {}
    kind = run.get("request", {}).get("task", {}).get("expect", "unknown")
    files = [{"path": p.relative_to(store / "staging" / run_id).as_posix(),
              "bytes": p.stat().st_size} for p in staged(run_id, store)]
    findings = []
    if kind == "review" and isinstance(output, dict):
        for finding in output.get("findings") or []:
            if isinstance(finding, dict):
                findings.append(f"{short(finding.get('severity'), 30)} "
                                f"{short(finding.get('file'), 100)}:"
                                f"{finding.get('line', '-')} "
                                f"{short(finding.get('summary'), 120)}")
    data = {"id": run_id, "status": result.get("status"), "error": result.get("error"),
            "warnings": result.get("warnings") or [], "usage": result.get("usage") or {},
            "kind": kind, "files": files, "findings": findings}
    if full:
        data["output"] = output
    return data


def show_lines(data, full=False):
    lines = [f"{data['id']} {data['status']} kind={data['kind']} "
             f"usage={json.dumps(data['usage'], separators=(',', ':'))}"]
    if data["error"]:
        lines.append("error " + short(data["error"], 200))
    lines.extend("warning " + short(w, 200) for w in data["warnings"])
    lines.extend(f"{f['path']} {f['bytes']} bytes" for f in data["files"])
    lines.extend(data["findings"])
    if full:
        lines.append(json.dumps(data["output"], ensure_ascii=False))
    return lines


def digest_data(run_id, cwd=None, store=STORE):
    root = Path.cwd() if cwd is None else Path(cwd)
    entries = []
    totals = {"new": 0, "modified": 0, "same": 0, "added": 0, "removed": 0}
    for path in staged(run_id, store):
        rel = path.relative_to(store / "staging" / run_id).as_posix()
        current = root / rel
        old = current.read_text(encoding="utf-8", errors="replace").splitlines() if current.is_file() else []
        new = path.read_text(encoding="utf-8", errors="replace").splitlines()
        added = removed = 0
        for tag, a, b, c, d in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
            if tag != "equal":
                removed += b - a
                added += d - c
        state = "new" if not current.is_file() else ("same" if old == new else "modified")
        totals[state] += 1
        totals["added"] += added
        totals["removed"] += removed
        entries.append({"state": state, "path": rel, "added": added, "removed": removed})
    return {"id": run_id, "files": entries, "totals": totals}


def digest_lines(data):
    lines = [f"{f['state']} {f['path']} +{f['added']} -{f['removed']}" for f in data["files"]]
    t = data["totals"]
    lines.append(f"totals new={t['new']} modified={t['modified']} same={t['same']} "
                 f"+{t['added']} -{t['removed']}")
    return lines


def ledger_data(last, run_id):
    path = STORE / "ledger.jsonl"
    rows = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                if not run_id or row.get("id") == run_id:
                    rows.append(row)
            except json.JSONDecodeError:
                continue
    return rows[-last:] if last else []


def request_json(url, payload=None, timeout=8):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Accept": "application/json",
                                  "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def unwrap(data):
    return data.get("data", data) if isinstance(data, dict) else data


def probe(name, target):
    transport = target.get("transport")
    if transport not in ("ollama", "opencode"):
        return {"name": name, "status": "unsupported", "reason": transport or "unknown"}
    base = target["base_url"].rstrip("/")
    try:
        if transport == "ollama":
            data = request_json(base + "/api/tags")
            models = [m.get("name") for m in data.get("models", [])]
            if target.get("model") not in models:
                raise ValueError("model missing: " + str(target.get("model")))
        else:
            session = unwrap(request_json(base + "/session", {"title": "dq health"}))
            session_id = session["id"]
            payload = {"model": {"providerID": target["provider_id"],
                                 "modelID": target["model"]},
                       "tools": {tool: False for tool in TOOLS},
                       "parts": [{"type": "text", "text": "Reply with the single word: ok"}]}
            reply = unwrap(request_json(base + "/session/" + session_id + "/message",
                                        payload, timeout=30))
            if reply.get("info", {}).get("error"):
                raise ValueError(str(reply["info"]["error"]))
            text = " ".join(p.get("text", "") for p in reply.get("parts", [])
                            if p.get("type") == "text")
            if text.strip().lower() != "ok":
                raise ValueError("unexpected reply: " + short(text, 45))
        return {"name": name, "status": "ok", "reason": ""}
    except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError) as exc:
        return {"name": name, "status": "DOWN", "reason": short(exc, 60)}


def selftest():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = root / ".delegate"
        run_id = "dlg_20000101T000000Z_test"
        (store / "runs").mkdir(parents=True)
        path = store / "staging" / run_id / "sample.txt"
        path.parent.mkdir(parents=True)
        # Write bytes directly to avoid text mode newline translation issues
        path.write_bytes(b"new\nline\n")
        (root / "sample.txt").write_text("old\n", encoding="utf-8")
        (store / "runs" / (run_id + ".json")).write_text(json.dumps({
            "request": {"_prompt": "SECRET", "task": {"expect": "review"}},
            "result": {"status": "ok", "warnings": [], "usage": {},
                       "output": {"findings": [{"severity": "low", "file": "sample.txt",
                                                 "line": 1, "summary": "test"}]}}}), encoding="utf-8")
        shown = show_data(run_id, store=store)
        diff = digest_data(run_id, cwd=root, store=store)
        assert shown["files"][0]["bytes"] == 9
        assert shown["findings"] == ["low sample.txt:1 test"]
        assert "SECRET" not in str(shown)
        assert diff["files"][0] == {"state": "modified", "path": "sample.txt",
                                    "added": 2, "removed": 1}
    return {"status": "ok"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--target", "-t")
    run.add_argument("--instruction", "-i", required=True)
    run.add_argument("--kind", "-k")
    run.add_argument("--expect", "-e")
    run.add_argument("--file", "-f", action="append")
    run.add_argument("--acceptance", "-a", action="append")
    run.add_argument("--allow-edit", action="append")
    run.add_argument("--temperature", type=float)
    run.add_argument("--num-ctx", type=int)
    run.add_argument("--timeout", type=int)
    batch = sub.add_parser("batch")
    batch.add_argument("specs")
    batch.add_argument("--workers", type=int, default=4)
    batch.add_argument("--per-target", type=int, default=1)
    show = sub.add_parser("show")
    show.add_argument("run_id")
    show.add_argument("--full", action="store_true")
    digest = sub.add_parser("digest")
    digest.add_argument("run_id")
    ledger = sub.add_parser("ledger")
    ledger.add_argument("--last", type=int, default=5)
    ledger.add_argument("--run-id")
    health = sub.add_parser("health")
    health.add_argument("--target", action="append")
    health.add_argument("--all", action="store_true")
    health.add_argument("--config")
    apply = sub.add_parser("apply")
    apply.add_argument("run_id")
    apply.add_argument("--overwrite", action="store_true")
    sub.add_parser("selftest")
    # Accept --json before or after the subcommand.
    for command in sub.choices.values():
        command.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    mode = args.json
    try:
        if args.command == "run":
            flags = []
            for key, flag in (("target", "--target"), ("instruction", "--instruction"),
                              ("kind", "--kind"), ("expect", "--expect"),
                              ("temperature", "--temperature"), ("num_ctx", "--num-ctx"),
                              ("timeout", "--timeout")):
                value = getattr(args, key)
                if value is not None:
                    flags.extend((flag, str(value)))
            for key, flag in (("file", "--file"), ("acceptance", "--acceptance"),
                              ("allow_edit", "--allow-edit")):
                for value in getattr(args, key) or []:
                    flags.extend((flag, value))
            item = execute(flags)
            emit(item, [summary_line(item)], mode)
            return 0 if item["status"] == "ok" else 1
        if args.command == "batch":
            specs = json.loads(Path(args.specs).read_text(encoding="utf-8"))
            if not isinstance(specs, list) or any(not isinstance(s, dict) for s in specs):
                raise ValueError("batch specs must be a JSON list of objects")
            if args.workers < 1 or args.per_target < 1:
                raise ValueError("workers and per-target must be positive")
            config = json.loads((HERE / "targets.json").read_text(encoding="utf-8"))
            locks = {}
            guard = threading.Lock()
            def worker(spec):
                target = spec.get("target") or config.get("default_target")
                with guard:
                    semaphore = locks.setdefault(target, threading.Semaphore(args.per_target))
                try:
                    flags = run_flags(spec)
                    with semaphore:
                        return execute(flags)
                except Exception as exc:
                    return {"id": "-", "status": "error", "target": target or "-",
                            "secs": 0.0, "in": None, "out": None, "files": 0,
                            "error": short(exc, 100)}
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
                items = list(pool.map(worker, specs))
            totals = {"tasks": len(items), "ok": sum(i["status"] == "ok" for i in items),
                      "failed": sum(i["status"] != "ok" for i in items),
                      "secs": round(sum(i["secs"] for i in items), 1),
                      "files": sum(i["files"] for i in items)}
            emit({"tasks": items, "totals": totals},
                 [summary_line(i) for i in items] +
                 [f"totals tasks={totals['tasks']} ok={totals['ok']} "
                  f"failed={totals['failed']} {totals['secs']:.1f}s files={totals['files']}"], mode)
            return 1 if totals["failed"] else 0
        if args.command == "show":
            data = show_data(args.run_id, args.full)
            emit(data, show_lines(data, args.full), mode)
        elif args.command == "digest":
            data = digest_data(args.run_id)
            emit(data, digest_lines(data), mode)
        elif args.command == "ledger":
            rows = ledger_data(args.last, args.run_id)
            emit(rows, [f"{r.get('id', '-')} {r.get('status', '-')} {r.get('target', '-')} "
                        f"{(r.get('duration_ms') or 0) / 1000:.1f}s "
                        f"{short(r.get('instruction'), 50)}" for r in rows], mode)
        elif args.command == "health":
            cfg = json.loads((Path(args.config) if args.config else HERE / "targets.json")
                             .read_text(encoding="utf-8"))
            targets = cfg["targets"]
            names = args.target or list(targets)
            if any(name not in targets for name in names):
                raise ValueError("unknown target: " + ", ".join(n for n in names if n not in targets))
            results = [None] * len(names)
            with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(names))) as pool:
                pending = {}
                for index, name in enumerate(names):
                    target = targets[name]
                    if "disabled" in target and not args.all:
                        continue
                    if target.get("disabled"):
                        results[index] = {"name": name, "status": "disabled",
                                          "reason": short(target["disabled"], 60)}
                    else:
                        pending[pool.submit(probe, name, target)] = index
                for future in concurrent.futures.as_completed(pending):
                    results[pending[future]] = future.result()
            results = [r for r in results if r is not None]
            emit(results, [f"{r['name']} {r['status']} {short(r['reason'], 60)}".rstrip()
                           for r in results], mode)
            return 1 if any(r["status"] == "DOWN" for r in results) else 0
        elif args.command == "apply":
            before = record(args.run_id)
            proc = delegate("apply", args.run_id, *(['--overwrite'] if args.overwrite else []))
            count = len(re.findall(r'^write ', proc.stdout, re.MULTILINE))
            data = {"id": args.run_id, "applied": count,
                    "status": "ok" if proc.returncode == 0 else "error"}
            emit(data, [f"{args.run_id} applied={count}"], mode)
            return 0 if proc.returncode == 0 else 1
        elif args.command == "selftest":
            data = selftest()
            emit(data, ["selftest ok"], mode)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, RuntimeError) as exc:
        emit({"status": "error", "error": short(exc, 160)},
             ["error " + short(exc, 160)], mode)
        return 1


if __name__ == "__main__":
    sys.exit(main())
