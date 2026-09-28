#!/usr/bin/env python3
"""delegate.py - hand a scoped coding task to a local model, with a paper trail.

Standard library only, so this file can be copied into any project and just run.

    py -3 delegate.py health
    py -3 delegate.py run --target coder --instruction "..." --expect files
    py -3 delegate.py ledger --last 10
    py -3 delegate.py show dlg_20260928T0412Z_7f3a
    py -3 delegate.py apply dlg_20260928T0412Z_7f3a

Two rules shape the whole design:

1. Delegated output NEVER lands straight in the working tree. It is written to
   .delegate/staging/<run-id>/ and only copied in by an explicit `apply`, so
   there is always a review step between a small model and your repo.

2. Every run is recorded twice: a one-line summary in .delegate/ledger.jsonl
   for cheap grep/jq, and the complete request + response beside it for when
   you need to know exactly what was asked and what came back.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import socket
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Windows consoles default to cp1252, and model output routinely contains
# emoji and box-drawing characters. Without this, printing a summary raises
# UnicodeEncodeError and takes the whole run down after the work succeeded.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


SCHEMA = "delegation/v1"
HERE = Path(__file__).resolve().parent

EFFORT_LEVELS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
_PROVIDER_CACHE = {}

# Two roots, and conflating them was a bug waiting to happen:
#   TOOL_ROOT   - where this tool keeps its own state (ledger, runs, staging).
#                 Stays with the tool, so there is one history across projects.
#   TARGET_ROOT - the repo you are pointing it at right now, i.e. your cwd.
#                 Context files resolve here, and staged files land here.
TOOL_ROOT = Path(os.getenv("DELEGATE_HOME", HERE)).resolve()
TARGET_ROOT = Path.cwd()
PROJECT_ROOT = TARGET_ROOT  # alias: everything below that touches your repo
STORE = TOOL_ROOT / ".delegate"
LEDGER = STORE / "ledger.jsonl"
RUNS = STORE / "runs"
STAGING = STORE / "staging"


# ==========================================================================
# Output contracts
#
# Local models are sloppy about free-form structure but quite good when handed
# an explicit JSON schema, so every non-text task declares one and we enforce
# it on the way back.
# ==========================================================================

FILES_SCHEMA = {
    "type": "object",
    "properties": {
        "files": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "action": {"type": "string", "enum": ["create", "replace"]},
                    "content": {"type": "string"},
                },
                "required": ["path", "action", "content"],
            },
        },
        "notes": {"type": "string"},
    },
    "required": ["files"],
}

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "line": {"type": "integer"},
                    "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                    "summary": {"type": "string"},
                    "suggestion": {"type": "string"},
                },
                "required": ["file", "severity", "summary"],
            },
        },
        "verdict": {"type": "string"},
    },
    "required": ["findings"],
}

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "label": {"type": "string"},
        "confidence": {"type": "number"},
        "rationale": {"type": "string"},
    },
    "required": ["label"],
}

EXPECTATIONS = {
    "files": (FILES_SCHEMA, "a set of complete files"),
    "review": (REVIEW_SCHEMA, "a list of findings"),
    "classify": (CLASSIFY_SCHEMA, "a single label"),
    "json": (None, "free-form JSON"),
    "text": (None, "plain prose"),
}

SYSTEM_PROMPT = """You are a delegated worker model inside an automated pipeline.
Another engineer has scoped this task for you and will review everything you \
produce before it is used.

Rules:
- Do exactly the task described. Do not expand the scope.
- Output ONLY the requested structure. No preamble, no markdown fences, no \
commentary outside the structure.
- When returning files, return each file COMPLETE. Never abbreviate with \
"..." or "rest unchanged" - truncated output is discarded.
- If the task is impossible or underspecified, return the structure with an \
empty result set and explain why in the notes field. Guessing is worse than \
saying so."""


# ==========================================================================
# Small utilities
# ==========================================================================

def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_run_id():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"dlg_{stamp}_{uuid.uuid4().hex[:4]}"


def load_targets(path=None):
    cfg_path = Path(path) if path else HERE / "targets.json"
    with open(cfg_path, encoding="utf-8") as handle:
        cfg = json.load(handle)
    return cfg


def resolve_target(cfg, name):
    targets = cfg["targets"]
    name = name or cfg.get("default_target")
    if name not in targets:
        raise SystemExit(
            f"Unknown target '{name}'. Available: {', '.join(sorted(targets))}")
    return name, targets[name]


def http_json(url, payload=None, method=None, timeout=600):
    """POST/GET JSON. Returns (status, parsed_or_text)."""
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method=method or ("POST" if data else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:600]
        return exc.code, body
    except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
        return 0, str(exc)


def extract_json(text):
    """Pull a JSON object out of a model response.

    Handles the three things local models actually do: wrap it in ```json
    fences, prepend chatter, or emit a <think> block first.
    """
    if not text:
        raise ValueError("empty response")

    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()

    # Try the whole thing first. Doing fence-extraction before this was a real
    # bug: when the JSON's own string values contain ``` (say, tests whose
    # fixtures are fenced snippets), the fence regex matches *inside* the
    # payload and shreds a response that would have parsed perfectly.
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    fence = re.search(r"```(?:json)?\s*(.+?)```", cleaned, flags=re.S)
    if fence:
        try:
            return json.loads(fence.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Fall back to the outermost balanced {...}.
    start = cleaned.find("{")
    if start == -1:
        raise ValueError("no JSON object found in response")
    depth, in_str, escape = 0, False, False
    for idx in range(start, len(cleaned)):
        ch = cleaned[idx]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(cleaned[start:idx + 1])
    raise ValueError("unbalanced JSON object in response")


def looks_truncated(content):
    """Cheap guard against the classic 'rest of the file unchanged' failure."""
    tail = content.rstrip()[-200:]
    markers = ("# ...", "// ...", "...rest", "rest unchanged", "unchanged)",
               "<!-- ... -->", "# rest of")
    return any(m in tail.lower() for m in markers)


# ==========================================================================
# Prompt assembly
# ==========================================================================

def build_request(target_name, target, args, context_files):
    schema, description = EXPECTATIONS[args.expect]

    task_lines = [f"TASK KIND: {args.kind}", "", "INSTRUCTION:", args.instruction.strip()]

    if args.acceptance:
        task_lines += ["", "ACCEPTANCE CRITERIA (all must hold):"]
        task_lines += [f"- {item}" for item in args.acceptance]

    if context_files:
        task_lines += ["", "CONTEXT FILES:"]
        for item in context_files:
            task_lines += [
                "",
                f"--- BEGIN {item['path']} ---",
                item["content"],
                f"--- END {item['path']} ---",
            ]

    if context_files:
        locked = [f["path"] for f in context_files
                  if f["path"] not in set(args.allow_edit or [])]
        if locked:
            task_lines += ["", "The context files above are REFERENCE ONLY. Do not "
                           "return them as output; returning them will be discarded:"]
            task_lines += [f"- {p}" for p in locked]
    task_lines += ["", f"OUTPUT: return {description}."]
    if schema:
        task_lines += ["Match this JSON schema exactly:",
                       json.dumps(schema, indent=2)]
    elif args.expect == "json":
        task_lines.append("Return a single JSON object and nothing else.")

    return {
        "schema": SCHEMA,
        "id": args.run_id,
        "created_at": now_iso(),
        "target": {
            "name": target_name,
            "transport": target["transport"],
            "base_url": target["base_url"],
            "model": target.get("model"),
            "provider_id": target.get("provider_id"),
        },
        "task": {
            "kind": args.kind,
            "instruction": args.instruction.strip(),
            "acceptance": list(args.acceptance or []),
            "expect": args.expect,
            "schema": schema,
            "allow_edit": list(args.allow_edit or []),
        },
        "context": {
            "files": [{"path": f["path"], "bytes": len(f["content"])}
                      for f in context_files],
            "paths": [f["path"] for f in context_files],
            "total_bytes": sum(len(f["content"]) for f in context_files),
        },
        "constraints": {
            "temperature": args.temperature,
            "num_ctx": args.num_ctx,
            "timeout_s": args.timeout,
        },
        "_prompt": "\n".join(task_lines),
        "_system": SYSTEM_PROMPT,
    }


# ==========================================================================
# Transports
# ==========================================================================

def run_ollama(request, target, timeout):
    """Native Ollama /api/chat. One shot, no tools, schema-constrained."""
    options = dict(target.get("options") or {})
    if request["constraints"]["temperature"] is not None:
        options["temperature"] = request["constraints"]["temperature"]
    if request["constraints"]["num_ctx"]:
        options["num_ctx"] = request["constraints"]["num_ctx"]

    body = {
        "model": target["model"],
        "stream": False,
        "options": options,
        "messages": [
            {"role": "system", "content": request["_system"]},
            {"role": "user", "content": request["_prompt"]},
        ],
    }
    # Thinking models (qwen3) reason by default. Ollama returns that in a
    # separate message.thinking field, which is dropped here, but its tokens
    # still cost time. A target can set "think": false to turn it off.
    if "think" in target:
        body["think"] = bool(target["think"])
    if request["target"]["name"] == "small" and request.get("effort") in ("none", "low"):
        body["think"] = False

    expect = request["task"]["expect"]
    if request["task"]["schema"]:
        # Ollama constrains generation to the schema - far more reliable than
        # asking nicely in the prompt.
        body["format"] = request["task"]["schema"]
    elif expect == "json":
        body["format"] = "json"

    started = time.monotonic()
    status, data = http_json(f"{target['base_url'].rstrip('/')}/api/chat",
                             body, timeout=timeout)
    elapsed_ms = int((time.monotonic() - started) * 1000)

    if status != 200:
        return {"ok": False, "error": f"HTTP {status}: {str(data)[:400]}",
                "duration_ms": elapsed_ms, "raw": None, "usage": {}}

    content = (data.get("message") or {}).get("content", "") if isinstance(data, dict) else ""
    usage = {
        "prompt_tokens": (data or {}).get("prompt_eval_count"),
        "completion_tokens": (data or {}).get("eval_count"),
        "total_duration_ms": int(((data or {}).get("total_duration") or 0) / 1e6) or None,
    }
    return {"ok": True, "error": None, "duration_ms": elapsed_ms,
            "raw": content, "usage": usage}


def _unwrap(payload):
    """OpenCode wraps every response body in {"data": ...}."""
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def _model_variants(target):
    """Fetch the OpenCode provider registry once per server, per process."""
    base = target["base_url"].rstrip("/")
    if base not in _PROVIDER_CACHE:
        status, body = http_json(f"{base}/provider", timeout=25)
        data = _unwrap(body)
        if status != 200 or not isinstance(data, dict):
            _PROVIDER_CACHE[base] = None
        else:
            providers = data.get("all")
            if isinstance(providers, dict):
                providers = list(providers.values())
            _PROVIDER_CACHE[base] = providers if isinstance(providers, list) else None

    providers = _PROVIDER_CACHE[base]
    if providers is None:
        return None
    for provider in providers:
        if not isinstance(provider, dict) or provider.get("id") != target["provider_id"]:
            continue
        models = provider.get("models")
        if not isinstance(models, dict):
            break
        model = models.get(target["model"])
        if not isinstance(model, dict):
            break
        variants = model.get("variants")
        return variants if isinstance(variants, dict) else {}
    return None


def set_effort(request, cfg, target, args):
    """Resolve requested effort to a setting the selected transport can use."""
    chosen = getattr(args, "effort", "auto")
    if chosen == "auto":
        chosen = target.get("default_effort",
                            cfg.get("effort_defaults", {}).get(args.kind))
    request["effort"] = None
    if chosen is None:
        return

    if target["transport"] == "ollama":
        if request["target"]["name"] == "small" and chosen in ("none", "low"):
            request["effort"] = chosen
        return
    if target["transport"] != "opencode":
        return

    variants = _model_variants(target)
    if variants is None:
        print(f"effort: variants unavailable for {target['model']}; omitting",
              file=sys.stderr)
        return
    supported = [level for level in EFFORT_LEVELS if level in variants]
    if not supported:
        return
    if chosen in supported:
        effective = chosen
    else:
        index = EFFORT_LEVELS.index(chosen)
        effective = min(supported, key=lambda level: (
            abs(EFFORT_LEVELS.index(level) - index),
            EFFORT_LEVELS.index(level) > index))
        print(f"effort: {chosen} unsupported for {target['model']}; using {effective}",
              file=sys.stderr)
    request["effort"] = effective
    request["_variant"] = effective


_OPENCODE_TOOLS = ("bash", "edit", "write", "read", "grep", "glob", "list", "patch",
                   "webfetch", "task", "todowrite", "todoread", "skill", "question")


def run_opencode(request, target, timeout):
    """OpenCode session API. The model gets tools and works in ITS workspace.

    Uses the classic synchronous endpoint POST /session/{id}/message, which
    blocks until the reply is complete and returns text parts and token
    counts directly. The newer /api/session/{id}/prompt route is accepted but
    never starts generation on the current server build (verified 2026-09-28:
    the session stays at one user message forever), so every run hung until
    its timeout. Do not switch back without re-testing with a tiny prompt.
    """
    base = target["base_url"].rstrip("/")

    status, body = http_json(f"{base}/session", {
        "title": f"delegated {request['id']}",
    }, timeout=60)
    session = _unwrap(body)
    if status not in (200, 201) or not isinstance(session, dict) or not session.get("id"):
        return {"ok": False, "error": f"session create failed: HTTP {status} {str(body)[:300]}",
                "duration_ms": 0, "raw": None, "usage": {}}

    session_id = session["id"]
    started = time.monotonic()

    payload = {
        "model": {"providerID": target["provider_id"], "modelID": target["model"]},
        "parts": [{"type": "text",
                   "text": request["_system"] + "\n\n" + request["_prompt"]}],
    }
    # Workers return text/JSON contracts and never need the agent's tools.
    # Disabling them cuts the per-call scaffold from ~5,100 to ~1,050 input
    # tokens (measured 2026-09-28). Set target["tools"]=true to opt back in.
    if not (target.get("tools") or os.getenv("DELEGATE_TOOLS")):
        payload["tools"] = {name: False for name in _OPENCODE_TOOLS}
    if request.get("_variant"):
        payload["variant"] = request["_variant"]
    status, body = http_json(f"{base}/session/{session_id}/message", payload,
                             timeout=timeout)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if status == 0:
        return {"ok": False, "error": f"no reply within {timeout}s or connection lost: {str(body)[:200]}",
                "duration_ms": elapsed_ms, "raw": None, "usage": {},
                "session_id": session_id}
    if status != 200 or not isinstance(body, dict):
        return {"ok": False, "error": f"message failed: HTTP {status} {str(body)[:300]}",
                "duration_ms": elapsed_ms, "raw": None, "usage": {},
                "session_id": session_id}

    info = body.get("info") or {}
    tokens = info.get("tokens") or {}
    usage = {
        "prompt_tokens": tokens.get("input"),
        "completion_tokens": tokens.get("output"),
        "reasoning_tokens": tokens.get("reasoning"),
    }
    if info.get("error"):
        return {"ok": False, "error": f"provider error: {str(info['error'])[:300]}",
                "duration_ms": elapsed_ms, "raw": None, "usage": usage,
                "session_id": session_id}
    text = "\n".join(p["text"] for p in (body.get("parts") or [])
                     if p.get("type") == "text" and p.get("text"))
    if not text:
        return {"ok": False, "error": "assistant replied with no text content",
                "duration_ms": elapsed_ms, "raw": None, "usage": usage,
                "session_id": session_id}
    return {"ok": True, "error": None, "duration_ms": elapsed_ms,
            "raw": text, "usage": usage, "session_id": session_id}


TRANSPORTS = {"ollama": run_ollama, "opencode": run_opencode}


# ==========================================================================
# Paper trail
# ==========================================================================

def record(request, result):
    RUNS.mkdir(parents=True, exist_ok=True)
    full_path = RUNS / f"{request['id']}.json"

    # Drop the rendered prompt from the summary but keep it in the full record;
    # it is the only way to reproduce a run exactly.
    full = {"request": request, "result": result}
    full_path.write_text(json.dumps(full, indent=2), encoding="utf-8")

    summary = {
        "id": request["id"],
        "at": request["created_at"],
        "target": request["target"]["name"],
        "model": request["target"].get("model"),
        "transport": request["target"]["transport"],
        "kind": request["task"]["kind"],
        "expect": request["task"]["expect"],
        "instruction": request["task"]["instruction"][:160],
        "status": result["status"],
        "duration_ms": result.get("duration_ms"),
        "usage": result.get("usage") or {},
        "files": [f["path"] for f in (result.get("output") or {}).get("files", [])],
        "warnings": result.get("warnings") or [],
        "error": result.get("error"),
        "accepted": None,
    }
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(summary) + "\n")
    return full_path


def read_ledger():
    if not LEDGER.exists():
        return []
    rows = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


# ==========================================================================
# Commands
# ==========================================================================

def cmd_health(args):
    cfg = load_targets(args.config)
    print(f"{'TARGET':<12} {'TRANSPORT':<10} {'STATUS':<10} DETAIL")
    print("-" * 78)
    exit_code = 0
    for name, target in cfg["targets"].items():
        base = target["base_url"].rstrip("/")
        if target["transport"] == "ollama":
            status, data = http_json(f"{base}/api/tags", timeout=6)
            if status == 200 and isinstance(data, dict):
                names = [m["name"] for m in data.get("models", [])]
                have = target["model"] in names
                state = "ok" if have else "NO MODEL"
                detail = target["model"] if have else f"missing; has {names}"
                exit_code |= 0 if have else 1
            else:
                state, detail = "DOWN", str(data)[:50]
                exit_code |= 1
        else:
            status, data = http_json(f"{base}/config/providers", timeout=25)
            if status == 200 and isinstance(data, dict):
                providers = {p.get("id"): p for p in data.get("providers", [])}
                prov = providers.get(target["provider_id"])
                if not prov:
                    state = "NO PROVIDER"
                    detail = f"{target['provider_id']} not registered; has {list(providers)}"
                    exit_code |= 1
                elif target["model"] not in (prov.get("models") or {}):
                    state = "NO MODEL"
                    detail = f"{target['model']} not in {list(prov.get('models') or {})}"
                    exit_code |= 1
                else:
                    state = "ok"
                    detail = f"{target['provider_id']}/{target['model']}"
            else:
                state, detail = "DOWN", str(data)[:50]
                exit_code |= 1
        print(f"{name:<12} {target['transport']:<10} {state:<10} {detail}")
    return exit_code


def cmd_targets(args):
    cfg = load_targets(args.config)
    for name, target in cfg["targets"].items():
        marker = " (default)" if name == cfg.get("default_target") else ""
        print(f"\n{name}{marker}")
        print(f"  {target['transport']} -> {target['base_url']}  [{target.get('model')}]")
        if target.get("notes"):
            print(f"  {target['notes']}")
        if target.get("good_for"):
            print(f"  good for: {', '.join(target['good_for'])}")
        if target.get("bad_for"):
            print(f"  bad for:  {', '.join(target['bad_for'])}")
    return 0


def cmd_run(args):
    cfg = load_targets(args.config)
    target_name, target = resolve_target(cfg, args.target)
    args.run_id = new_run_id()
    context_files = _load_context(args.file)

    if args.dry_run:
        request = build_request(target_name, target, args, context_files)
        set_effort(request, cfg, target, args)
        print(json.dumps({k: v for k, v in request.items() if k != "_system"},
                         indent=2))
        return 0

    request, result, path = _execute(args, target_name, target, context_files, cfg)
    _print_result(request, result, path)
    return 0 if result["status"] == "ok" else 1


def _execute(args, target_name, target, context_files, cfg):
    """One delegation: build, send, check, stage, record. Shared by run/steps."""
    request = build_request(target_name, target, args, context_files)
    set_effort(request, cfg, target, args)
    print(f"[{request['id']}] {target_name} ({target.get('model')}) "
          f"<- {len(context_files)} file(s), "
          f"{request['context']['total_bytes']:,} bytes", file=sys.stderr)

    transport = TRANSPORTS[target["transport"]]
    raw_result = transport(request, target, args.timeout)

    result = {
        "schema": SCHEMA,
        "request_id": request["id"],
        "completed_at": now_iso(),
        "status": "ok" if raw_result["ok"] else "error",
        "error": raw_result.get("error"),
        "duration_ms": raw_result.get("duration_ms"),
        "usage": raw_result.get("usage") or {},
        "warnings": [],
        "output": {},
        "raw_excerpt": (raw_result.get("raw") or "")[:2000],
    }
    if raw_result.get("session_id"):
        result["opencode_session"] = raw_result["session_id"]

    # Ollama silently truncates a prompt longer than num_ctx and still answers
    # 200, confidently, from whatever fragment survived. Its signature is a
    # prompt_eval_count far below what the prompt's size implies (prose and
    # code both run ~2.5-4 chars/token, so under chars/6 is not plausible).
    # Two past "coder rewrote the context file" incidents were this.
    prompt_chars = len(request["_system"]) + len(request["_prompt"])
    read = result["usage"].get("prompt_tokens")
    if raw_result["ok"] and read and prompt_chars > 4000 and read < prompt_chars / 6:
        raw_result["ok"] = False
        result["status"] = "truncated"
        result["error"] = (f"model read only {read} tokens of a {prompt_chars:,}-char "
                           f"prompt; it was truncated. Raise num_ctx or pass less context.")

    if raw_result["ok"]:
        _parse_output(request, raw_result["raw"], result)

    if result["status"] == "ok" and result["output"].get("files"):
        _stage_files(request["id"], result)

    path = record(request, result)
    return request, result, path


def _load_context(paths):
    context_files = []
    for raw in paths or []:
        path = Path(raw)
        if not path.is_absolute():
            path = PROJECT_ROOT / raw
        if not path.is_file():
            raise SystemExit(f"Context file not found: {raw}")
        context_files.append({
            "path": Path(raw).as_posix(),
            "content": path.read_text(encoding="utf-8", errors="replace"),
        })
    return context_files


def parse_plan(text):
    """Split a plan into (facts, [steps]). Sections are separated by a line
    that is exactly '---'. A first section starting with 'FACTS' is sent with
    every step instead of being a step itself."""
    sections = [s.strip() for s in re.split(r"^---\s*$", text, flags=re.M)]
    sections = [s for s in sections if s]
    facts = ""
    if sections and sections[0].upper().startswith("FACTS"):
        facts = sections.pop(0)
    return facts, sections


def cmd_steps(args):
    """Run a plan one small step at a time, each step editing the previous
    step's output. The 30B reliably drops requirements from a brief with more
    than ~5 of them, but does one or two at a time well; see OPERATING-NOTES."""
    cfg = load_targets(args.config)
    target_name, target = resolve_target(cfg, args.target)
    facts, steps = parse_plan(Path(args.plan).read_text(encoding="utf-8"))
    if not steps:
        raise SystemExit(f"{args.plan}: no steps found (separate them with '---')")

    if args.dry_run:
        if facts:
            print(f"FACTS (sent with every step):\n{facts}\n")
        for i, step in enumerate(steps, 1):
            print(f"STEP {i}/{len(steps)}:\n{step}\n")
        return 0

    given = _load_context(args.file)
    produced = {}  # path -> content, carried forward between steps
    run_ids = []
    for i, step in enumerate(steps, 1):
        instruction = f"STEP {i} of {len(steps)}: {step}"
        if produced:
            instruction += ("\n\nThe editable files below are the work so far. Apply ONLY "
                            "this step and return each of them COMPLETE, keeping "
                            "everything earlier steps added.")
        if facts:
            instruction = f"{facts}\n\n{instruction}"
        step_args = argparse.Namespace(**vars(args))
        step_args.instruction = instruction
        step_args.expect, step_args.kind = "files", "generate"
        step_args.allow_edit = list(args.allow_edit or []) + list(produced)
        step_args.run_id = new_run_id()
        context = given + [{"path": p, "content": c} for p, c in produced.items()]

        request, result, _ = _execute(step_args, target_name, target, context, cfg)
        run_ids.append(request["id"])
        files = result["output"].get("files") or []
        print(f"step {i}/{len(steps)}  {result['status']:10s} "
              f"{(result.get('duration_ms') or 0) / 1000:5.1f}s  "
              f"{', '.join(f['path'] for f in files) or '-'}")
        for warning in result["warnings"]:
            print(f"          warn {warning}")
        if result["status"] != "ok":
            print(f"\nstopped at step {i}: {result.get('error')}")
            print(f"record   {request['id']}")
            return 1
        for f in files:
            produced[f["path"]] = f["content"]

    # diff/apply read the file list from the run record, and a later step may
    # omit a file an earlier one produced. Put the accumulated result on the
    # last run (record and staging both) so they see the whole chain's output.
    files = [{"path": p, "action": "create", "content": c, "bytes": len(c)}
             for p, c in produced.items()]
    run_path = RUNS / f"{run_ids[-1]}.json"
    record_data = json.loads(run_path.read_text(encoding="utf-8"))
    record_data["result"]["output"]["files"] = files
    record_data["result"]["chain"] = run_ids
    run_path.write_text(json.dumps(record_data, indent=2), encoding="utf-8")
    _stage_files(run_ids[-1], {"output": {"files": files}})
    print(f"\n{len(steps)} steps ok: {' -> '.join(run_ids)}")
    for p, c in produced.items():
        print(f"staged   {p}  ({len(c):,} bytes)")
    print(f"\nReview:  py -3 delegate.py diff {run_ids[-1]}")
    print(f"Accept:  py -3 delegate.py apply {run_ids[-1]}")
    return 0


def _parse_output(request, raw, result):
    expect = request["task"]["expect"]
    if expect == "text":
        result["output"] = {"text": raw}
        return
    try:
        parsed = extract_json(raw)
    except ValueError as exc:
        result["status"] = "unparseable"
        result["error"] = f"could not parse {expect} output: {exc}"
        return

    if expect == "files":
        files = parsed.get("files") or []
        clean = []
        # Context files are READ-ONLY unless explicitly unlocked with
        # --allow-edit. This is not paranoia: qwen3-coder:30b was observed
        # twice in a row rewriting a 31KB context file into a shorter, worse
        # version instead of producing the new file it was asked for - the
        # second time under an instruction that explicitly forbade it. Telling
        # the model not to touch a file does not reliably work, so the guard
        # lives here, where it does.
        writable = set(request["task"].get("allow_edit") or [])
        locked = set(request["context"].get("paths") or []) - writable
        for item in files:
            if not isinstance(item, dict) or "path" not in item or "content" not in item:
                result["warnings"].append(f"dropped malformed file entry: {str(item)[:80]}")
                continue
            # A path escaping the project is never legitimate here.
            rel = Path(item["path"].replace("\\", "/"))
            if rel.is_absolute() or ".." in rel.parts:
                result["warnings"].append(f"rejected unsafe path: {item['path']}")
                continue
            if looks_truncated(item["content"]):
                result["warnings"].append(f"{item['path']} looks truncated")
            if rel.as_posix() in locked:
                result["warnings"].append(
                    f"BLOCKED {rel.as_posix()}: it was supplied as read-only context. "
                    f"The model tried to rewrite it instead of doing the task. "
                    f"Pass --allow-edit {rel.as_posix()} if that was genuinely intended.")
                continue
            clean.append({
                "path": rel.as_posix(),
                "action": item.get("action", "create"),
                "content": item["content"],
                "bytes": len(item["content"]),
            })
        result["output"] = {"files": clean, "notes": parsed.get("notes", "")}
        if not clean:
            result["status"] = "empty"
            result["error"] = parsed.get("notes") or "model returned no usable files"
    else:
        result["output"] = parsed


def _stage_files(run_id, result):
    stage_dir = STAGING / run_id
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    for item in result["output"]["files"]:
        dest = stage_dir / item["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(item["content"], encoding="utf-8")
    result["staged_at"] = str(stage_dir.relative_to(PROJECT_ROOT))


def _print_result(request, result, path):
    print(f"\nstatus   {result['status']}")
    print(f"took     {result.get('duration_ms', 0) / 1000:.1f}s")
    usage = result.get("usage") or {}
    if usage.get("completion_tokens"):
        print(f"tokens   {usage.get('prompt_tokens')} in / "
              f"{usage.get('completion_tokens')} out")
    if result.get("error"):
        print(f"error    {result['error']}")
    for warning in result["warnings"]:
        print(f"warn     {warning}")
    for item in result["output"].get("files", []):
        print(f"staged   {item['path']}  ({item['bytes']:,} bytes)")
    if result.get("staged_at"):
        print(f"\nReview:  py -3 tools/delegate/delegate.py diff {request['id']}")
        print(f"Accept:  py -3 tools/delegate/delegate.py apply {request['id']}")
    if result["output"].get("text"):
        print("\n" + result["output"]["text"])
    elif result["output"] and not result["output"].get("files"):
        print("\n" + json.dumps(result["output"], indent=2))
    print(f"\nrecord   {path.relative_to(PROJECT_ROOT)}")


def _load_run(run_id):
    path = RUNS / f"{run_id}.json"
    if not path.is_file():
        raise SystemExit(f"No such run: {run_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def cmd_diff(args):
    run = _load_run(args.run_id)
    files = (run["result"].get("output") or {}).get("files") or []
    if not files:
        print("This run staged no files.")
        return 1
    for item in files:
        target_path = PROJECT_ROOT / item["path"]
        current = (target_path.read_text(encoding="utf-8", errors="replace").splitlines(True)
                   if target_path.is_file() else [])
        proposed = item["content"].splitlines(True)
        diff = list(difflib.unified_diff(
            current, proposed,
            fromfile=f"a/{item['path']}" + ("" if current else " (new)"),
            tofile=f"b/{item['path']}"))
        if diff:
            sys.stdout.writelines(diff)
        else:
            print(f"{item['path']}: identical")
        print()
    return 0


def cmd_apply(args):
    run = _load_run(args.run_id)
    files = (run["result"].get("output") or {}).get("files") or []
    if not files:
        print("This run staged no files.")
        return 1

    written = []
    for item in files:
        dest = PROJECT_ROOT / item["path"]
        if dest.exists() and not args.overwrite:
            print(f"skip  {item['path']} (exists; pass --overwrite)")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(item["content"], encoding="utf-8")
        written.append(item["path"])
        print(f"write {item['path']}")

    # Mark the ledger entry as accepted so the trail reflects what actually
    # landed, not just what was generated.
    rows = read_ledger()
    for row in rows:
        if row["id"] == args.run_id:
            row["accepted"] = True
            row["applied_files"] = written
    LEDGER.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    print(f"\n{len(written)} file(s) applied.")
    return 0


def cmd_ledger(args):
    rows = read_ledger()[-args.last:]
    if not rows:
        print("No delegations recorded yet.")
        return 0
    print(f"{'RUN':<26} {'TARGET':<10} {'STATUS':<12} {'TIME':>7}  TASK")
    print("-" * 100)
    for row in rows:
        mark = {True: "+", False: "-", None: " "}[row.get("accepted")]
        secs = (row.get("duration_ms") or 0) / 1000
        print(f"{mark}{row['id']:<25} {row['target']:<10} {row['status']:<12} "
              f"{secs:6.1f}s  {row['instruction'][:44]}")
    print("\n  + applied   - rejected   (blank) not yet reviewed")
    return 0


def cmd_show(args):
    run = _load_run(args.run_id)
    if args.prompt:
        print(run["request"]["_system"])
        print("\n" + "=" * 70 + "\n")
        print(run["request"]["_prompt"])
        return 0
    print(json.dumps(run, indent=2)[:args.max_chars])
    return 0


# ==========================================================================
# CLI
# ==========================================================================

def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="delegate",
        description="Delegate a scoped coding task to a local model, with a paper trail.")
    parser.add_argument("--config", help="path to targets.json")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("health", help="check every target is reachable and loaded")
    sub.add_parser("targets", help="describe configured targets")

    run = sub.add_parser("run", help="delegate a task")
    run.add_argument("--target", "-t", help="target name (see `targets`)")
    run.add_argument("--instruction", "-i", required=True, help="what to do")
    run.add_argument("--kind", "-k", default="generate",
                     choices=["generate", "edit", "review", "explain", "classify"])
    run.add_argument("--expect", "-e", default="files", choices=sorted(EXPECTATIONS))
    run.add_argument("--file", "-f", action="append",
                     help="context file, repeatable (repo-relative)")
    run.add_argument("--acceptance", "-a", action="append",
                     help="acceptance criterion, repeatable")
    run.add_argument("--allow-edit", action="append", metavar="PATH",
                     help="permit rewriting this context file (default: all "
                          "context is read-only), repeatable")
    run.add_argument("--temperature", type=float, default=None)
    run.add_argument("--num-ctx", type=int, default=None,
                     help="override context window for this run")
    run.add_argument("--timeout", type=int, default=900)
    run.add_argument("--effort", default="auto",
                     choices=(*EFFORT_LEVELS, "auto"))
    run.add_argument("--dry-run", action="store_true",
                     help="print the assembled request and stop")

    steps = sub.add_parser("steps", help="run a plan one small step at a time, "
                                         "each step editing the last one's output")
    steps.add_argument("plan", help="text file: optional FACTS section, then steps, "
                                    "separated by lines of '---'")
    steps.add_argument("--target", "-t")
    steps.add_argument("--file", "-f", action="append", help="read-only context, repeatable")
    steps.add_argument("--acceptance", "-a", action="append",
                       help="criterion applied to every step, repeatable")
    steps.add_argument("--allow-edit", action="append", metavar="PATH")
    steps.add_argument("--temperature", type=float, default=None)
    steps.add_argument("--num-ctx", type=int, default=None)
    steps.add_argument("--timeout", type=int, default=900)
    steps.add_argument("--dry-run", action="store_true", help="show the parsed steps and stop")

    diff = sub.add_parser("diff", help="diff a run's staged files against the tree")
    diff.add_argument("run_id")

    apply_cmd = sub.add_parser("apply", help="copy a run's staged files into the tree")
    apply_cmd.add_argument("run_id")
    apply_cmd.add_argument("--overwrite", action="store_true")

    ledger = sub.add_parser("ledger", help="recent delegations")
    ledger.add_argument("--last", type=int, default=20)

    show = sub.add_parser("show", help="full record for one run")
    show.add_argument("run_id")
    show.add_argument("--prompt", action="store_true", help="show the sent prompt")
    show.add_argument("--max-chars", type=int, default=20000)

    args = parser.parse_args(argv)
    handlers = {
        "health": cmd_health, "targets": cmd_targets, "run": cmd_run, "steps": cmd_steps,
        "diff": cmd_diff, "apply": cmd_apply, "ledger": cmd_ledger, "show": cmd_show,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
