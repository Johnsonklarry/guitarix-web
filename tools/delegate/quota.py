"""Track quota usage and cooldowns for delegate targets (Python standard library only)."""

import calendar
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest import mock


DEFAULT_PATH = Path(__file__).resolve().parent / ".delegate" / "usage.json"
UTC = timezone.utc


def _path(path):
    return Path(path) if path is not None else DEFAULT_PATH


def _datetime(value):
    if value is None:
        return datetime.now(UTC)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, UTC)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return value.astimezone(UTC)


def _stamp(value):
    return _datetime(value).isoformat().replace("+00:00", "Z")


def _read_usage(path):
    try:
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)
    except FileNotFoundError:
        return {"events": {}, "cooldowns": {}}


def load_usage(path=None):
    """Return usage data with events grouped by target and UTC cooldown timestamps."""
    return _read_usage(_path(path))


@contextmanager
def _file_lock(path):
    """Lock a stable sidecar file, independent of replacement of the data file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock:
        lock.seek(0)
        if lock.read(1) == b"":
            lock.seek(0)
            lock.write(b"\0")
            lock.flush()
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _write_usage_atomic(path, data):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=path.name + ".", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _update_usage(path, change):
    path = _path(path)
    with _file_lock(path.with_name(path.name + ".lock")):
        data = _read_usage(path)
        change(data)
        _write_usage_atomic(path, data)


def record_use(target, requests=1, tokens=0, now=None, path=None):
    """Append one usage event under a cross-process lock."""
    if requests < 0 or tokens < 0:
        raise ValueError("usage amounts must be nonnegative")
    event = {"ts": _stamp(now), "requests": requests, "tokens": tokens}
    _update_usage(path, lambda data: data.setdefault("events", {}).setdefault(target, []).append(event))


def _fixed_bounds(window, now):
    kind = window["type"]
    hour, minute = map(int, window.get("reset_utc", "00:00").split(":"))
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError("reset_utc must be HH:MM")
    today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if kind == "daily":
        start = today if today <= now else today - timedelta(days=1)
        return start, start + timedelta(days=1)
    if kind == "weekly":
        weekday = window.get("weekday", 0)
        if not 0 <= weekday <= 6:
            raise ValueError("weekday must be 0 through 6")
        start = today - timedelta(days=(today.weekday() - weekday) % 7)
        if start > now:
            start -= timedelta(days=7)
        return start, start + timedelta(days=7)
    if kind == "monthly":
        day = window.get("day", 1)
        if not 1 <= day <= 28:
            raise ValueError("day must be 1 through 28")
        start = today.replace(day=day)
        if start > now:
            year, month = (start.year - 1, 12) if start.month == 1 else (start.year, start.month - 1)
            start = start.replace(year=year, month=month)
        year, month = (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
        return start, start.replace(year=year, month=month)
    raise ValueError("unknown window type: " + str(kind))


def window_state(entry, events, now):
    """Calculate usage and reset timing for one quota entry.

    Events have keys 'ts', 'requests', and 'tokens'. Timestamps and
    window_start are ISO 8601 UTC strings; reset intervals are seconds.
    """
    now = _datetime(now)
    kind = entry["kind"]
    if kind == "unmetered":
        return {"used": 0, "limit": math.inf, "remaining": math.inf,
                "seconds_to_reset": 0, "window_start": None}
    if kind not in ("requests", "tokens"):
        raise ValueError("unknown quota kind: " + str(kind))
    window = entry["window"]
    if window["type"] == "rolling":
        duration = timedelta(hours=window["hours"])
        if duration <= timedelta(0):
            raise ValueError("rolling hours must be positive")
        start = now - duration
        end = None
    else:
        start, end = _fixed_bounds(window, now)
    relevant = [event for event in events
                if event.get(kind, 0) > 0 and start < _datetime(event["ts"]) <= now]
    if end is not None:
        relevant += [event for event in events
                     if event.get(kind, 0) > 0 and _datetime(event["ts"]) == start]
    used = sum(event[kind] for event in relevant)
    if end is None:
        reset = min(((_datetime(event["ts"]) + duration - now).total_seconds()
                     for event in relevant), default=0)
    else:
        reset = (end - now).total_seconds()
    limit = entry["limit"]
    return {"used": used, "limit": limit, "remaining": limit - used,
            "seconds_to_reset": reset, "window_start": _stamp(start)}


def headroom(target_cfg, events, now):
    """Return capacity across all quota windows for a target.

    'binding' identifies the entry with the lowest remaining fraction as
    'kind:window_type'; it is 'unmetered' when no metered entry exists.
    """
    entries = [entry for entry in target_cfg.get("quota", [])
               if entry["kind"] != "unmetered"]
    if not entries:
        return {"usable": True, "worst_remaining_frac": 1.0,
                "soonest_reset_s": 0, "binding": "unmetered"}
    states = [window_state(entry, events, now) for entry in entries]
    if any(state["limit"] <= 0 for state in states):
        raise ValueError("metered quota limits must be positive")
    fractions = [state["remaining"] / state["limit"] for state in states]
    tightest = min(range(len(entries)), key=fractions.__getitem__)
    entry = entries[tightest]
    return {
        "usable": all(state["remaining"] > state["limit"] *
                      entry.get("reserve_pct", 10) / 100
                      for entry, state in zip(entries, states)),
        "worst_remaining_frac": fractions[tightest],
        "soonest_reset_s": min(state["seconds_to_reset"] for state in states),
        "binding": entry["kind"] + ":" + entry["window"]["type"],
    }


def mark_cooldown(target, until_ts, path=None):
    """Set a target's cooldown deadline to an ISO 8601 UTC timestamp."""
    stamp = _stamp(until_ts)
    _update_usage(path, lambda data: data.setdefault("cooldowns", {}).__setitem__(target, stamp))


def cooldown_until(target, path=None):
    """Return a target's cooldown deadline, or None if unset."""
    return load_usage(path).get("cooldowns", {}).get(target)


def prune(path=None, keep_days=40):
    """Remove usage events older than keep_days, retaining cooldowns."""
    if keep_days < 0:
        raise ValueError("keep_days must be nonnegative")
    cutoff = datetime.now(UTC) - timedelta(days=keep_days)

    def change(data):
        events = data.setdefault("events", {})
        for target in list(events):
            events[target] = [event for event in events[target]
                              if _datetime(event["ts"]) >= cutoff]
            if not events[target]:
                del events[target]

    _update_usage(path, change)


def _selftest():
    base = datetime(2026, 9, 28, 0, 0, tzinfo=UTC)
    event = lambda when, count=1: {"ts": _stamp(when), "requests": count, "tokens": 0}
    rolling = {"kind": "requests", "limit": 10,
               "window": {"type": "rolling", "hours": 5}}
    state = window_state(rolling, [event(base - timedelta(hours=5)),
                                   event(base - timedelta(hours=4), 2)], base)
    assert state["used"] == 2 and state["seconds_to_reset"] == 3600
    assert window_state(rolling, [], base)["seconds_to_reset"] == 0
    daily = {"kind": "requests", "limit": 10,
             "window": {"type": "daily", "reset_utc": "03:30"}}
    before = base + timedelta(hours=3, minutes=29)
    after = before + timedelta(minutes=1)
    assert window_state(daily, [event(before)], before)["seconds_to_reset"] == 60
    assert window_state(daily, [event(before)], after)["used"] == 0
    weekly = {"kind": "requests", "limit": 10,
              "window": {"type": "weekly", "weekday": 0}}
    assert window_state(weekly, [event(base - timedelta(seconds=1))], base)["used"] == 0
    assert window_state(weekly, [], base)["seconds_to_reset"] == 7 * 86400
    monthly = {"kind": "requests", "limit": 10,
               "window": {"type": "monthly", "day": 28}}
    monthly_base = base + timedelta(hours=1)
    assert window_state(monthly, [event(monthly_base - timedelta(seconds=1))],
                        monthly_base)["used"] == 1
    assert window_state(monthly, [event(base - timedelta(seconds=1))], base)["used"] == 0
    assert window_state(monthly, [event(monthly_base - timedelta(seconds=1))],
                        datetime(2026, 10, 28, tzinfo=UTC))["used"] == 0
    tight = {"kind": "requests", "limit": 3,
             "window": {"type": "weekly"}}
    result = headroom({"quota": [rolling, tight]}, [event(base, 2)], base)
    assert result["binding"] == "requests:weekly" and result["usable"]
    assert not headroom({"quota": [rolling, tight]}, [event(base, 3)], base)["usable"]
    assert not headroom({"quota": [rolling]}, [event(base, 9)], base)["usable"]
    assert headroom({"quota": [{**rolling, "reserve_pct": 0}]},
                    [event(base, 9)], base)["usable"]
    assert headroom({"quota": [{"kind": "unmetered"}]}, [], base) == {
        "usable": True, "worst_remaining_frac": 1.0,
        "soonest_reset_s": 0, "binding": "unmetered"}

    # Exercise the public cooldown functions using an in-memory store.
    store = {"events": {}, "cooldowns": {}}
    def write(_path_arg, data):
        store.clear()
        store.update(json.loads(json.dumps(data)))
    module = sys.modules[__name__]
    with mock.patch.object(module, "_file_lock", return_value=__import__("contextlib").nullcontext()), \
         mock.patch.object(module, "_read_usage", side_effect=lambda path: store.copy()), \
         mock.patch.object(module, "_write_usage_atomic", side_effect=write):
        assert cooldown_until("example") is None
        mark_cooldown("example", base)
        assert cooldown_until("example") == _stamp(base)
    print("quota selftest passed")


if __name__ == "__main__":
    if sys.argv[1:] == ["selftest"]:
        _selftest()
    else:
        print("Usage: quota.py selftest", file=sys.stderr)
        sys.exit(2)
