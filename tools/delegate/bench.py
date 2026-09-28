#!/usr/bin/env python3
"""Benchmark output generation speed for OpenCode provider models and local Ollama targets."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import statistics
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import delegate
from delegate import _OPENCODE_TOOLS, http_json

BASE_URL = "http://192.168.1.100:4096"
PROMPT = "Write exactly 250 words about how a hash map works, no headings."
SKIP_MODEL_PARTS = (
    "embedding", "tts", "image", "live", "veo", "lyria", "whisper",
    "guard", "orpheus", "safeguard", "translate", "computer-use",
    "deep-research",
)
LOCAL_TARGETS = {
    "coder": {"url": "http://127.0.0.1:11434", "model": "qwen3-coder:30b"},
    "small": {"url": "http://192.168.1.110:11434", "model": "qwen3:8b"},
}


def _csv(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def _safe_error(value: object) -> str:
    """Bound and sanitize errors before they are persisted or printed."""
    text = str(value).replace("\r", " ").replace("\n", " ")[:400]
    # Provider errors may echo a credential-bearing request/header.
    for marker in ("api_key", "apikey", "authorization", "bearer"):
        lower = text.lower()
        pos = lower.find(marker)
        if pos >= 0:
            text = text[:pos] + "[redacted]"
    return text


def _skipped(model: str) -> bool:
    lowered = model.lower()
    return any(part in lowered for part in SKIP_MODEL_PARTS)


def _unwrap(payload):
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def discover_models(providers_filter: list[str] | None,
                    models_filter: list[str] | None) -> list[dict]:
    status, payload = http_json(f"{BASE_URL}/config/providers", timeout=25)
    data = _unwrap(payload)
    if status != 200 or not isinstance(data, dict):
        raise RuntimeError(f"provider discovery failed: HTTP {status}: {_safe_error(payload)}")

    provider_ids = set(providers_filter) if providers_filter is not None else None
    model_ids = set(models_filter) if models_filter is not None else None
    found = []
    for provider in data.get("providers", []):
        if not isinstance(provider, dict):
            continue
        provider_id = provider.get("id")
        if not isinstance(provider_id, str) or provider_id == "anthropic":
            continue
        if provider_ids is not None and provider_id not in provider_ids:
            continue
        models = provider.get("models") or {}
        if isinstance(models, dict):
            entries = models.items()
        elif isinstance(models, list):
            entries = ((item.get("id"), item) for item in models if isinstance(item, dict))
        else:
            continue
        for model_id, metadata in entries:
            if not isinstance(model_id, str) or _skipped(model_id):
                continue
            if model_ids is not None and model_id not in model_ids:
                continue
            # Keep only non-secret identifying model metadata; never persist provider objects.
            variants = metadata.get("variants") if isinstance(metadata, dict) else None
            found.append({"provider": provider_id, "model": model_id,
                          "variants": variants if isinstance(variants, dict) else {}})
    return found


def _opencode_call(target: dict, effort: str | None, timeout: int) -> dict:
    status, body = http_json(f"{BASE_URL}/session", {}, timeout=60)
    session = _unwrap(body)
    if status not in (200, 201) or not isinstance(session, dict) or not session.get("id"):
        return {"ok": False, "error": f"session create failed: HTTP {status}: {_safe_error(body)}"}

    payload = {
        "model": {"providerID": target["provider"], "modelID": target["model"]},
        "tools": {name: False for name in _OPENCODE_TOOLS},
        "parts": [{"type": "text", "text": PROMPT}],
    }
    if effort:
        payload["variant"] = effort
    started = time.monotonic()
    status, body = http_json(
        f"{BASE_URL}/session/{quote(str(session['id']), safe='')}/message",
        payload, timeout=timeout)
    elapsed = time.monotonic() - started
    if status != 200 or not isinstance(body, dict):
        return {"ok": False, "total_s": elapsed,
                "error": f"message failed: HTTP {status}: {_safe_error(body)}"}
    info = body.get("info") or {}
    tokens = info.get("tokens") or {}
    if info.get("error"):
        return {"ok": False, "total_s": elapsed, "error": _safe_error(info["error"]),
                "out_tok": tokens.get("output"), "reasoning_tok": tokens.get("reasoning")}
    text = "\n".join(part.get("text", "") for part in body.get("parts", [])
                     if isinstance(part, dict) and part.get("type") == "text")
    if not text:
        return {"ok": False, "total_s": elapsed, "error": "empty response",
                "out_tok": tokens.get("output"), "reasoning_tok": tokens.get("reasoning")}
    output = tokens.get("output")
    reasoning = tokens.get("reasoning") or 0
    return {"ok": True, "total_s": elapsed, "out_tok": output,
            "reasoning_tok": reasoning,
            "tok_per_s": output / elapsed if isinstance(output, (int, float)) and elapsed else None,
            "non_reasoning_tok_per_s": max(0, output - reasoning) / elapsed
            if isinstance(output, (int, float)) and elapsed else None}


def _ollama_call(target: dict, timeout: int) -> dict:
    started = time.monotonic()
    status, body = http_json(target["url"].rstrip("/") + "/api/chat", {
        "model": target["model"], "stream": False,
        "messages": [{"role": "user", "content": PROMPT}],
    }, timeout=timeout)
    elapsed = time.monotonic() - started
    if status != 200 or not isinstance(body, dict):
        return {"ok": False, "total_s": elapsed,
                "error": f"HTTP {status}: {_safe_error(body)}"}
    message = body.get("message") or {}
    if not message.get("content"):
        return {"ok": False, "total_s": elapsed, "error": "empty response"}
    output = body.get("eval_count")
    duration_ns = body.get("eval_duration")
    tok_per_s = output / (duration_ns / 1e9) if isinstance(output, (int, float)) and duration_ns else None
    return {"ok": True, "total_s": elapsed, "out_tok": output,
            "reasoning_tok": 0, "tok_per_s": tok_per_s,
            "non_reasoning_tok_per_s": tok_per_s,
            "eval_duration_ns": duration_ns}


def _median(values):
    values = [value for value in values if isinstance(value, (int, float))]
    return statistics.median(values) if values else None


def _bench_one(target: dict, effort: str | None, runs: int,
               timeout: int, lock: threading.Lock) -> dict:
    samples = []
    for _ in range(runs):
        with lock:
            result = (_ollama_call(target, timeout) if target.get("local")
                      else _opencode_call(target, effort, timeout))
        samples.append(result)
    successes = [sample for sample in samples if sample.get("ok")]
    return {
        "provider": target["provider"], "model": target["model"],
        "effort": effort or "", "runs": samples,
        "total_s": _median([sample.get("total_s") for sample in successes]),
        "out_tok": _median([sample.get("out_tok") for sample in successes]),
        "reasoning_tok": _median([sample.get("reasoning_tok") for sample in successes]),
        "tok_per_s": _median([sample.get("tok_per_s") for sample in successes]),
        "non_reasoning_tok_per_s": _median(
            [sample.get("non_reasoning_tok_per_s") for sample in successes]),
        "status": "ok" if len(successes) == runs else
                  ("partial" if successes else "error"),
        "error": "; ".join(_safe_error(sample.get("error")) for sample in samples
                            if sample.get("error")) or None,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--providers", help="comma-separated provider ids")
    parser.add_argument("--models", help="comma-separated model ids")
    parser.add_argument("--effort", help="optional OpenCode variant/effort")
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--include-local", action="store_true")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args(argv)
    if args.runs < 1 or args.workers < 1:
        parser.error("--runs and --workers must be at least 1")

    try:
        targets = discover_models(_csv(args.providers), _csv(args.models))
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    # Local targets can be selected by their target names through --models.
    if args.include_local:
        selected = set(_csv(args.models) or [])
        for name, local in LOCAL_TARGETS.items():
            if not selected or name in selected or local["model"] in selected:
                targets.append({"provider": name, "model": local["model"], "local": True})
    elif args.models:
        # A model filter must still allow local target names when locals are enabled.
        targets = [target for target in targets if target["model"] in set(_csv(args.models) or [])]

    # Enforce provider selection for local targets too, and deduplicate catalog entries.
    providers = set(_csv(args.providers) or [])
    if providers:
        targets = [target for target in targets if target["provider"] in providers]
    unique = {}
    for target in targets:
        unique[(target["provider"], target["model"])] = target
    targets = list(unique.values())

    locks: dict[str, threading.Lock] = {}
    for target in targets:
        locks.setdefault(target["provider"], threading.Lock())
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(_bench_one, target, args.effort, args.runs,
                               args.timeout, locks[target["provider"]])
                   for target in targets]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())

    results.sort(key=lambda row: (row["tok_per_s"] is not None,
                                  row["tok_per_s"] or 0), reverse=True)
    record = {"timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "prompt": PROMPT, "runs_per_target": args.runs, "results": results}
    output = Path(delegate.HERE) / ".delegate" / "bench_results.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, indent=2), encoding="utf-8")

    print("provider\tmodel\teffort\ttotal_s\tout_tok\ttok_per_s\tstatus")
    for row in results:
        total = f"{row['total_s']:.2f}" if row["total_s"] is not None else "-"
        output_tokens = f"{row['out_tok']:g}" if row["out_tok"] is not None else "-"
        speed = f"{row['tok_per_s']:.2f}" if row["tok_per_s"] is not None else "-"
        print(f"{row['provider']}\t{row['model']}\t{row['effort'] or '-'}\t"
              f"{total}\t{output_tokens}\t{speed}\t{row['status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
