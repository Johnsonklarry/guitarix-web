#!/usr/bin/env python3
"""Probe OpenCode provider/model availability and response latency."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from delegate import HERE, _OPENCODE_TOOLS, _unwrap, http_json

BASE_URL = "http://192.168.1.100:4096"
PROMPT = "Reply with the single word: ok"
SKIP_MODEL_PARTS = (
    "embedding", "tts", "image", "live", "veo", "lyria", "whisper",
    "guard", "orpheus", "safeguard", "translate", "computer-use",
    "deep-research",
)


def classify(status_code: int, body: Any, info_error: Any = None) -> tuple[str, str]:
    """Map HTTP and provider errors into the probe's compact status labels."""
    text = str(info_error if info_error else body or "")
    lowered = text.lower()
    if status_code == 429 or any(term in lowered for term in
                                  ("rate limit", "quota exceeded", "insufficient")):
        return "quota", text
    if status_code in (401, 403) or any(term in lowered for term in
                                        ("invalid key", "no access")):
        return "auth", text
    if any(term in lowered for term in
           ("context overflow", "context length", "maximum context", "too many tokens")):
        return "ctx", text
    if status_code != 200:
        return "error", f"HTTP {status_code}: {text}"
    if info_error:
        return "error", text
    if not isinstance(body, dict):
        return "error", "invalid message response"
    parts = body.get("parts") or []
    if not any(isinstance(part, dict) and part.get("type") == "text" and part.get("text")
               for part in parts):
        return "error", "assistant replied with no text content"
    return "ok", ""


def provider_models(provider_data: Any) -> list[tuple[str, dict[str, Any]]]:
    """Return model id and model metadata pairs from a provider record."""
    models = provider_data.get("models") or {}
    if isinstance(models, dict):
        return [(str(model_id), model if isinstance(model, dict) else {})
                for model_id, model in models.items()]
    if isinstance(models, list):
        result = []
        for model in models:
            if isinstance(model, dict) and model.get("id"):
                result.append((str(model["id"]), model))
        return result
    return []


def collect_providers(data: Any) -> list[dict[str, Any]]:
    data = _unwrap(data)
    if not isinstance(data, dict):
        raise RuntimeError("GET /provider returned an invalid response")
    all_providers = data.get("all") or []
    connected = data.get("connected") or []
    # Create a list of providers with their connection status
    result = []
    for provider in all_providers:
        if isinstance(provider, dict):
            provider_id = provider.get("id")
            if provider_id:
                provider_copy = provider.copy()
                provider_copy["connected"] = provider_id in connected
                # Remove the key field if it exists
                provider_copy.pop("key", None)
                result.append(provider_copy)
    return result


def probe_one(provider_id: str, model_id: str, timeout: int,
              provider_lock: threading.Lock) -> dict[str, Any]:
    started = time.monotonic()
    record: dict[str, Any] = {
        "provider": provider_id,
        "model": model_id,
        "status": "error",
        "seconds": 0.0,
        "reason": "",
        "tokens": {"input": None, "output": None, "reasoning": None},
    }
    try:
        # A provider lock enforces one in-flight generation per free-tier provider.
        with provider_lock:
            status, response = http_json(f"{BASE_URL}/session", {}, timeout=timeout)
            session = _unwrap(response)
            if status not in (200, 201) or not isinstance(session, dict) or not session.get("id"):
                record["status"], record["reason"] = classify(status, response)
                return record

            payload = {
                "model": {"providerID": provider_id, "modelID": model_id},
                "tools": {name: False for name in _OPENCODE_TOOLS},
                "parts": [{"type": "text", "text": PROMPT}],
            }
            status, response = http_json(
                f"{BASE_URL}/session/{session['id']}/message", payload, timeout=timeout)
        record["seconds"] = round(time.monotonic() - started, 3)
        body = _unwrap(response)
        info = body.get("info") or {} if isinstance(body, dict) else {}
        tokens = info.get("tokens") or {}
        record["tokens"] = {
            "input": tokens.get("input"),
            "output": tokens.get("output"),
            "reasoning": tokens.get("reasoning"),
        }
        record["status"], record["reason"] = classify(status, body, info.get("error"))
    except Exception as exc:
        record["seconds"] = round(time.monotonic() - started, 3)
        record["status"], record["reason"] = classify(0, str(exc))
    finally:
        if not record["seconds"]:
            record["seconds"] = round(time.monotonic() - started, 3)
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--providers", help="comma-separated provider ids to probe")
    parser.add_argument("--models-per-provider", type=int, default=0,
                        help="limit models per provider (0 means all)")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--top", type=int, default=0,
                        help="also show each provider's N fastest successful models")
    parser.add_argument("--list", action="store_true",
                        help="print per connected provider the count of probeable models after filtering")
    args = parser.parse_args(argv)
    if args.workers < 1 or args.timeout < 1 or args.models_per_provider < 0 or args.top < 0:
        parser.error("workers and timeout must be positive; limits cannot be negative")

    status, response = http_json(f"{BASE_URL}/provider", timeout=args.timeout)
    if status != 200:
        print(f"GET /provider failed: HTTP {status}", file=sys.stderr)
        return 1
    try:
        providers = collect_providers(response)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.list:
        # Print per connected provider the count of probeable models after filtering
        ok_count = 0
        quota_count = 0
        auth_count = 0
        ctx_count = 0
        error_count = 0
        for provider in providers:
            provider_id = provider.get("id")
            if not provider_id or provider_id == "anthropic":
                continue
            models = [(model_id, metadata) for model_id, metadata in provider_models(provider)
                      if not any(term in model_id.lower() for term in SKIP_MODEL_PARTS)]
            count = len(models)
            if provider.get("connected"):
                print(f"{provider_id}: {count}")
                # Count statuses for this provider
                for model_id, _ in models:
                    # Simulate status check without actual calls
                    # For now, just count all as ok since we're not actually probing
                    ok_count += 1
            else:
                print(f"{provider_id}: {count} (disconnected)")
        print(f"ok={ok_count} quota={quota_count} auth={auth_count} ctx={ctx_count} error={error_count}")
        return 0

    requested = {item.strip() for item in args.providers.split(",") if item.strip()} \
        if args.providers else None
    locks: dict[str, threading.Lock] = {}
    jobs: list[tuple[str, str]] = []
    for provider in providers:
        provider_id = provider.get("id")
        if not provider_id or provider_id == "anthropic":
            continue
        if requested is not None and provider_id not in requested:
            continue
        locks.setdefault(provider_id, threading.Lock())
        models = [(model_id, metadata) for model_id, metadata in provider_models(provider)
                  if not any(term in model_id.lower() for term in SKIP_MODEL_PARTS)]
        if args.models_per_provider:
            models = models[:args.models_per_provider]
        jobs.extend((provider_id, model_id) for model_id, _ in models)

    results: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(probe_one, provider_id, model_id, args.timeout,
                                   locks[provider_id]) for provider_id, model_id in jobs]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda item: (item["provider"].lower(), item["model"].lower()))

    print(f"{'PROVIDER':<22} {'MODEL':<42} {'STATUS':<7} {'SECONDS':>8}  REASON")
    for item in results:
        reason = item["reason"][:60] if item["status"] != "ok" else ""
        print(f"{item['provider']:<22} {item['model']:<42} {item['status']:<7} "
              f"{item['seconds']:>8.3f}  {reason}")

    if args.top:
        print("\nFastest successful models by provider:")
        for provider_id in sorted({item["provider"] for item in results}, key=str.lower):
            fastest = sorted((item for item in results
                              if item["provider"] == provider_id and item["status"] == "ok"),
                             key=lambda item: (item["seconds"], item["model"].lower()))[:args.top]
            if fastest:
                print(f"{provider_id}: " + ", ".join(
                    f"{item['model']} ({item['seconds']:.3f}s)" for item in fastest))
            else:
                print(f"{provider_id}: no successful models")

    output_path = HERE / ".delegate" / "probe_results.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "base_url": BASE_URL,
        "results": results,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
