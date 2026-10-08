#!/usr/bin/env python3
"""
Does this Pi have everything the app needs, and are the names in the config
the names your rig actually uses?

    python3 app.py --check

Read-only and safe to run while you're playing: it looks at JACK's port list
and asks guitarix what it has, but changes nothing, connects nothing, and
writes no files.

Most of the port names in recorder.py and reamp.py are inferences about how a
typical JACK setup is named. This is the thing that replaces the guess with
your actual answer, and prints the exact line to change when they differ.
"""

import os
import shutil
import subprocess
import sys

OK, WARN, FAIL = "ok", "warn", "fail"
MARK = {OK: "  ok  ", WARN: " warn ", FAIL: " FAIL "}


class Report:
    def __init__(self):
        self.rows = []

    def add(self, status, name, detail="", fix=""):
        self.rows.append((status, name, detail, fix))
        return status

    def worst(self):
        for s in (FAIL, WARN):
            if any(r[0] == s for r in self.rows):
                return s
        return OK


def have(tool):
    return shutil.which(tool) is not None


def run(args, timeout=5):
    """Output of a command, or None if it isn't there or fails."""
    try:
        p = subprocess.run(args, capture_output=True, timeout=timeout)
        return (p.stdout + p.stderr).decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return None


# ---------------------------------------------------------------- checks

def check_python(r):
    for mod, why, needed in [("flask", "the web app", True),
                             ("flask_socketio", "live updates to the browser", True),
                             ("simple_websocket", "real websockets; without it Socket.IO polls", False)]:
        try:
            __import__(mod)
            r.add(OK, "python: " + mod)
        except ImportError:
            r.add(FAIL if needed else WARN, "python: " + mod, "missing -- " + why,
                  "pip install -r requirements.txt")


def check_tools(r):
    if not have("ffmpeg"):
        r.add(FAIL, "ffmpeg", "not installed", "sudo apt install ffmpeg")
    else:
        encoders = run(["ffmpeg", "-hide_banner", "-encoders"]) or ""
        devices = run(["ffmpeg", "-hide_banner", "-devices"]) or ""
        r.add(OK, "ffmpeg", "installed")
        r.add(OK if "libmp3lame" in encoders else FAIL, "ffmpeg: mp3",
              "libmp3lame " + ("present" if "libmp3lame" in encoders else "MISSING"),
              "" if "libmp3lame" in encoders else "Listen and mp3 exports need it; install a fuller ffmpeg build")
        jack_in = any(line.strip().startswith(("D", "DE")) and " jack " in line
                      for line in devices.splitlines())
        r.add(OK if jack_in else FAIL, "ffmpeg: jack input",
              "present" if jack_in else "MISSING -- recording reads from JACK through this",
              "" if jack_in else "install an ffmpeg built with --enable-libjack")

    r.add(OK if have("ffprobe") else WARN, "ffprobe",
          "installed" if have("ffprobe") else "missing -- take lengths will be blank",
          "" if have("ffprobe") else "sudo apt install ffmpeg")

    if not have("mpv"):
        r.add(FAIL, "mpv", "not installed -- reamping and backing tracks need it",
              "sudo apt install mpv")
    else:
        aos = run(["mpv", "--ao=help"]) or ""
        jack_out = any(l.strip().startswith("jack") for l in aos.splitlines())
        r.add(OK, "mpv", "installed")
        r.add(OK if jack_out else FAIL, "mpv: jack output",
              "present" if jack_out else "MISSING -- nothing can play into JACK without it",
              "" if jack_out else "install an mpv build with JACK support")

    missing = [t for t in ("jack_lsp", "jack_connect", "jack_disconnect") if not have(t)]
    r.add(OK if not missing else FAIL, "jack tools",
          "all present" if not missing else "missing: " + ", ".join(missing),
          "" if not missing else "sudo apt install jackd2")


def jack_ports():
    """{client: {"output": [...], "input": [...]}} from jack_lsp -p."""
    out = run(["jack_lsp", "-p"]) if have("jack_lsp") else None
    if not out:
        return None
    clients, current = {}, None
    for line in out.splitlines():
        if line.startswith((" ", "\t")):
            if not current or ":" not in current:
                continue
            client = current.split(":", 1)[0]
            slot = clients.setdefault(client, {"output": [], "input": []})
            if "output" in line:
                slot["output"].append(current)
            elif "input" in line:
                slot["input"].append(current)
        else:
            current = line.strip()
    return clients


def check_jack(r, clients):
    if clients is None:
        r.add(FAIL, "jack server", "no ports listed -- is JACK running?",
              "start JACK (qjackctl, or your usual jackd command), then run this again")
        return
    r.add(OK, "jack server", "%d clients, %d ports"
          % (len(clients), sum(len(v["output"]) + len(v["input"]) for v in clients.values())))

    import recorder
    import reamp

    def port_check(label, client, direction, setting, where, why):
        ports = clients.get(client, {}).get(direction, [])
        if ports:
            r.add(OK, label, "%s -> %s" % (client, ", ".join(ports[:4])))
            return
        # name it wrong and everything downstream fails mysteriously; offer the real options
        candidates = [c for c, v in clients.items() if v[direction]]
        r.add(FAIL, label,
              'no %s ports on "%s" -- %s' % (direction, client, why),
              'set %s in %s to one of: %s' % (setting, where, ", ".join(candidates) or "(none found)"))

    port_check("jack: amp output", recorder.SOURCE_CLIENT, "output",
               "SOURCE_CLIENT", "recorder.py", "this is what recording and Listen capture")
    port_check("jack: amp input", reamp.AMP_CLIENT, "input",
               "AMP_CLIENT", "reamp.py", "this is where reamped takes are played in")
    port_check("jack: dry source", recorder.DRY_CLIENT, "output",
               "DRY_CLIENT", "recorder.py", "this is the unprocessed signal for + dry")
    port_check("jack: speakers", reamp.OUTPUT_CLIENT, "input",
               "OUTPUT_CLIENT", "reamp.py", "backing tracks and amp-off playback go here")

    for name in ("gxweb", "gxweb-dry", "gxweb-mon", "gxweb-reamp", "gxweb-backing"):
        if name in clients:
            r.add(WARN, "jack: leftover client", '"%s" is still registered' % name,
                  "something didn't shut down cleanly; harmless, but restart the app to clear it")


def check_engine(r):
    host = os.environ.get("GX_HOST", "127.0.0.1")
    port = int(os.environ.get("GX_PORT", "7000"))
    import threading

    import gx_rpc
    ready = threading.Event()
    rpc = gx_rpc.GuitarixRPC(host, port, on_ready=ready.set)
    rpc.start()
    if not ready.wait(timeout=6):
        rpc.stop()
        r.add(FAIL, "guitarix", "no answer at %s:%d" % (host, port),
              "start it with the rpc port open: guitarix -N -p %d" % port)
        return
    try:
        # diagnostics are bulk work: they must not sit in front of a knob move
        params = rpc.parameter_list(lane=gx_rpc.LOW)
        banks = rpc.banks(lane=gx_rpc.LOW)
        bank, preset = rpc.current_preset()
        r.add(OK, "guitarix", "%s:%d -- %d parameters, %d banks, playing %s/%s"
              % (host, port, len(params), len(banks), bank, preset))

        import controls
        eq = controls.build_groups(controls.EQ_GROUPS, params)
        fx = controls.build_groups(controls.FX_GROUPS, params)
        shown = sum(len(g["controls"]) for g in eq + fx)
        r.add(OK if shown else WARN, "controls on screen",
              "%d controls in %d groups" % (shown, len(eq) + len(fx)),
              "" if shown else "none of the plugin prefixes in controls.py matched; "
                               "run: python3 dump_params.py")

        missing = [k for k, v in gx_rpc.PRESET_METHODS.items()
                   if not v and k not in ("save_current", "move")]
        r.add(OK if not missing else WARN, "preset methods",
              "configured" if not missing else "not set: " + ", ".join(missing),
              "" if not missing else "python3 probe_rpc.py, then paste the block into gx_rpc.py")
    except Exception as exc:                      # a diagnostic must never crash
        r.add(WARN, "guitarix", "connected, but asking it things failed: %s" % exc)
    finally:
        rpc.stop()


def check_storage(r):
    import backing
    import recorder
    for label, path in [("recordings", recorder.RECORDINGS_DIR), ("backing tracks", backing.BACKING_DIR)]:
        path = os.path.expanduser(path)
        try:
            os.makedirs(path, exist_ok=True)
            probe = os.path.join(path, ".gxweb-write-test")
            with open(probe, "w") as f:
                f.write("x")
            os.remove(probe)
        except OSError as exc:
            r.add(FAIL, "storage: " + label, "can't write to %s (%s)" % (path, exc))
            continue
        free = shutil.disk_usage(path).free
        gb = free / 1e9
        # about 11 MB a minute, doubled when + dry is on
        minutes = free / (11e6 * 2)
        r.add(OK if gb > 2 else WARN, "storage: " + label,
              "%s -- %.1f GB free (about %d min with + dry)" % (path, gb, minutes),
              "" if gb > 2 else "getting tight; delete some takes")


def get_period_ms(frames, rate):
    """Legacy name for period_ms: the same number, with the same validation.

    period_ms is the real implementation -- it rejects a zero or negative
    sample rate with a ValueError instead of a ZeroDivisionError, and it is
    what the report is built from -- so this defers to it rather than
    computing the same value a second way and drifting out of step. The name
    is resolved when this is called, so period_ms being defined further down
    the module is fine.
    """
    return period_ms(frames, rate)


def check_serving(r):
    here = os.path.dirname(os.path.abspath(__file__))
    vendored = os.path.exists(os.path.join(here, "static", "socket.io.min.js"))
    r.add(OK if vendored else WARN, "socket.io client",
          "served locally" if vendored else "loaded from a CDN",
          "" if vendored else "a device with no internet can't connect; download "
                              "socket.io.min.js into static/ with tools/vendor_socketio.py")
    r.add(OK if os.environ.get("GX_SECRET_KEY") else OK, "secret key",
          "set from the environment" if os.environ.get("GX_SECRET_KEY")
          else "random each start (fine -- nothing needs to outlive a restart)")


# ------------------------------------------------------------ latency report

SAMPLE_RATE = 48000
PERIOD_FRAMES = 128

# The three budgets, in the order a report lists them.
BUDGETS = ("guitar", "reamp", "listening")

BUDGET_TITLES = {
    "guitar": "guitar path (string to speaker)",
    "reamp": "backing/reamp (command to sound)",
    "listening": "browser listening (amp to headphones)",
}

# The names these budgets turn up under in benchmark output. Matched loosely,
# so a rig that reports "backing track" still lands in the right section
# instead of being dropped on the floor.
BUDGET_ALIASES = {
    "guitar": "guitar",
    "guitar path": "guitar",
    "guitar rig": "guitar",
    "reamp": "reamp",
    "backing": "reamp",
    "backing reamp": "reamp",
    "backing track": "reamp",
    "command to sound": "reamp",
    "listening": "listening",
    "browser": "listening",
    "browser listening": "listening",
    "monitor": "listening",
}


def period_ms(frames, sample_rate=SAMPLE_RATE):
    """How long one JACK period lasts, in milliseconds.

    64 frames at 48 kHz is 1.33 ms, 128 is 2.67 ms, 256 is 5.33 ms -- the
    numbers a rollback note has to quote to be any use.
    """
    frames, sample_rate = float(frames), float(sample_rate)
    if frames <= 0 or sample_rate <= 0:
        raise ValueError("frames and sample rate must be positive, got %g at %g Hz"
                         % (frames, sample_rate))
    return round(frames * 1000.0 / sample_rate, 2)


# Period sizes worth naming in a rollback note, at the default sample rate.
PERIOD_MS = {frames: period_ms(frames) for frames in (16, 32, 64, 128, 256, 512, 1024)}


def _normalise_name(name):
    """A record name flattened to lowercase words: "backing/reamp" -> "backing reamp"."""
    key = str(name).lower()
    for ch in "/_-":
        key = key.replace(ch, " ")
    return " ".join(key.split())


def _budget_key(name):
    """Which of the three budgets a record belongs to, or None if it isn't one.

    An exact match wins. Failing that an alias has to turn up as whole words:
    "backing track" and "backing/reamp" still land in the reamp section, but a
    name that merely contains one -- "backingtrack", "guitarists" -- is left
    for Other measurements instead of being quietly claimed. When more than one
    alias fits, the longest one decides, so the answer never depends on the
    order of BUDGET_ALIASES.
    """
    key = _normalise_name(name)
    if key in BUDGET_ALIASES:
        return BUDGET_ALIASES[key]
    words = key.split()
    for alias in sorted(BUDGET_ALIASES, key=len, reverse=True):
        span = alias.split()
        if any(words[i:i + len(span)] == span
               for i in range(len(words) - len(span) + 1)):
            return BUDGET_ALIASES[alias]
    return None


def _number_or_none(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value, default=0):
    """An integer out of whatever a record supplied.

    A malformed xrun count ("n/a", a list, inf) is 0, not a crash: this is a
    diagnostic, and the count it couldn't read is one of the things it exists
    to show. _number_or_none refuses bools, so True doesn't turn into 1.
    """
    number = _number_or_none(value)
    if number is None:
        return default
    try:
        return int(number)
    except (OverflowError, ValueError):
        return default


def _iter_records(records):
    """(name, value) pairs out of whatever the caller handed us."""
    if not records:
        return []
    if isinstance(records, dict):
        return list(records.items())
    pairs = []
    for record in records:
        if isinstance(record, dict):
            name = record.get("name", record.get("budget", record.get("label", "")))
            pairs.append((name, record))
        elif isinstance(record, (list, tuple)) and len(record) == 2:
            pairs.append((record[0], record[1]))
        else:
            pairs.append((str(record), record))
    return pairs


def _record_parts(value):
    """(measured_ms, budget_ms, xruns, detail) from a record of any shape."""
    if isinstance(value, dict):
        measured = value.get("measured_ms", value.get("measured", value.get("ms")))
        budget = value.get("budget_ms", value.get("budget", value.get("limit_ms")))
        xruns = value.get("xruns", value.get("xruns_count", 0)) or 0
        detail = value.get("detail", value.get("note", "")) or ""
        return (_number_or_none(measured), _number_or_none(budget),
                _as_int(xruns), str(detail))
    number = _number_or_none(value)
    if number is not None:
        return number, None, 0, ""
    return None, None, 0, "" if value is None else str(value)


def telemetry_point(name, measured_ms=None, budget_ms=None, xruns=0, detail="", test=None):
    """A telemetry data point, in the shape build_report() reads.

    A point is a plain dict -- {"name": ..., "measured_ms": ..., ...} -- so it
    can be written to JSON and read back, and a benchmark in another process
    can hand one over without importing this module.

    measured_ms and budget_ms are kept when they are numbers and dropped when
    they are not; xruns is a count, and an unreadable one is 0 rather than a
    crash, exactly as in a record. `test` names the test or benchmark that
    produced the point; it is folded into the detail line, so a measurement in
    the report can be traced back to whatever measured it.
    """
    if name is None or not str(name).strip():
        raise ValueError("a telemetry point needs a name")
    point = {"name": str(name), "xruns": _as_int(xruns)}
    measured = _number_or_none(measured_ms)
    if measured is not None:
        point["measured_ms"] = measured
    budget = _number_or_none(budget_ms)
    if budget is not None:
        point["budget_ms"] = budget
    detail = "" if detail is None else str(detail)
    if test:
        detail = "%s (from %s)" % (detail, test) if detail else "from %s" % test
    if detail:
        point["detail"] = detail
    return point


def _telemetry_records(telemetry):
    """(name, value) pairs out of telemetry data points, in the order given.

    A list of points, a single point on its own, or a {name: point} mapping all
    do the job. A point without a name is skipped: it can't be filed under a
    budget or an Other measurement, and the rest of the run's telemetry is
    still worth reporting.
    """
    if isinstance(telemetry, dict) and ("name" in telemetry or "budget" in telemetry):
        telemetry = [telemetry]
    return [(name, value) for name, value in _iter_records(telemetry)
            if str(name).strip()]


def format_benchmark_result(records=None, telemetry=None, **kwargs):
    """A benchmark run formatted as a markdown report.

    The utility a benchmark or a test calls when it has numbers: measurements
    go in through `records`, the telemetry data points it gathered go in
    through `telemetry`, and any other keyword is a budget of its own, so

        format_benchmark_result(
            {"guitar": {"measured_ms": 8.5, "budget_ms": 10.0}},
            telemetry=[telemetry_point("reamp", 12.0, 10.0, xruns=2, test="spike")])

    is the guitar measurement and the reamp telemetry point in one report.
    build_report() does the work; this is the same call, named for the job.
    """
    return build_report(records, telemetry=telemetry, **kwargs)


def _budget_lines(title, measured, budget, xruns, detail):
    lines = ["### " + title]
    if measured is None:
        lines.append("not measured")
    else:
        text = "measured %.2f ms" % measured
        if budget is not None:
            text += ", budget %.2f ms -- %s" % (
                budget, "within budget" if measured <= budget else "OVER BUDGET")
        lines.append(text)
    if detail:
        lines.append(detail)
    lines.append("xruns: %d" % xruns)
    return lines


def build_report(records=None, xruns=None, sample_rate=SAMPLE_RATE, frames=PERIOD_FRAMES,
                 telemetry=None, **budgets):
    """Compose latency budget records into a markdown report.

    `records` maps a budget name to what was measured for it. A record is a
    dict with any of:

        measured_ms   what the benchmark measured
        budget_ms     what it was measured against
        xruns         xruns counted during that measurement
        detail        anything else worth printing

    or just a number of milliseconds, or a string to print as-is. A list of
    record dicts, each carrying a "name", does the same job, and a budget can
    also be handed over as a keyword: build_report(guitar={"measured_ms": 8.5}).

    `telemetry` carries telemetry data points a test or benchmark run measured,
    as built by telemetry_point(). Each one is folded in as a record, so a
    point named after one of the budgets lands in that budget's section and
    everything else is listed under Other measurements. A point for a budget
    that already has a record keeps the earlier record and is printed under
    Other measurements, exactly like a duplicate record is.

    Every budget gets a section whether or not it was measured, so the report
    has the same shape every time and a measurement that is missing says so
    rather than quietly disappearing. If two records land on the same budget
    the first one keeps the section and the second is printed under Other
    measurements, so nothing is dropped on the floor.

    `xruns` sets the total; leave it out and the counts from every record --
    including the ones under Other measurements -- are added up. The result is
    markdown, with no timestamps in it: the same measurements always produce
    the same report.
    """
    if budgets:
        records = dict(records or {}, **budgets)

    telemetry = _telemetry_records(telemetry)
    if telemetry:
        # Telemetry points are records too: what a test run measured is
        # reported rather than measured and then thrown away.
        if isinstance(records, dict):
            records = list(records.items()) + telemetry
        else:
            records = list(records or []) + telemetry

    collected, other = {}, []
    for name, value in _iter_records(records):
        label = str(name) or "unnamed"
        key = _budget_key(name)
        if key is not None and key not in collected:
            collected[key] = _record_parts(value)
        else:
            # Not a budget at all, or a second record for a budget that already
            # has one: the first record keeps the section, and this one is
            # printed rather than discarded without a word.
            if key is not None:
                label = "%s (second %s record)" % (label, key)
            other.append((label, _record_parts(value)))

    per_period = period_ms(frames, sample_rate)
    lines = [
        "# Latency report",
        "",
        "Sample rate %g Hz, period %d frames (%.2f ms per period)."
        % (sample_rate, frames, per_period),
        "",
        "## Budgets",
    ]

    counted, over = 0, []
    # Records under Other measurements carry xruns too, and the total is the
    # total of the run, so they count as well.
    counted += sum(parts[2] for _, parts in other)
    for key in BUDGETS:
        measured, budget, xrun_count, detail = collected.get(key, (None, None, 0, ""))
        counted += xrun_count
        lines.append("")
        lines.extend(_budget_lines(BUDGET_TITLES[key], measured, budget, xrun_count, detail))
        if measured is not None and budget is not None and measured > budget:
            over.append("%s is %.2f ms against a %.2f ms budget"
                        % (BUDGET_TITLES[key], measured, budget))

    if other:
        lines.append("")
        lines.append("## Other measurements")
        for name, parts in other:
            lines.append("")
            lines.extend(_budget_lines(name, *parts))

    lines.append("")
    lines.append("## Xruns")
    lines.append("")
    lines.append("Total xruns: %d" % (counted if xruns is None else _as_int(xruns)))
    lines.append("")
    lines.append("Xrun counts by budget:")
    for key in BUDGETS:
        lines.append("- %s: %d"
                     % (BUDGET_TITLES[key], collected.get(key, (None, None, 0, ""))[2]))

    steps = ", ".join("%d frames = %.2f ms" % (f, period_ms(f, sample_rate))
                      for f in (64, 128, 256, 512))
    lines.append("")
    lines.append("## Rollback instructions")
    lines.append("")
    if over:
        lines.append("Over budget: " + "; ".join(over) + ".")
    else:
        lines.append("Nothing is over budget, so nothing needs rolling back. If that changes:")
    lines.append("")
    lines.append("1. Put the period size back first -- every number above was measured "
                 "against it. At %g Hz the steps are %s." % (sample_rate, steps))
    lines.append("2. Stop the extra JACK clients the benchmark left running (mpv, "
                 "gxweb-*), then reconnect guitarix's output to the interface's "
                 "playback ports by hand.")
    lines.append("3. If only the browser listening budget got worse, put the stream back "
                 "the way it was (Socket.IO polling rather than websockets) and re-measure.")
    lines.append("4. Re-run `python3 spike_latency.py` and `python3 app.py --check`; both "
                 "should be back inside their budgets before you record anything real.")
    return "\n".join(lines) + "\n"


# The name an earlier draft of this report used; kept working.
generate_report = build_report


# ---------------------------------------------------------------- output

def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    quick = "--quick" in argv            # skip anything that talks to guitarix

    r = Report()
    check_python(r)
    check_tools(r)
    clients = jack_ports()
    check_jack(r, clients)
    if not quick:
        check_engine(r)
    check_storage(r)
    check_serving(r)

    width = max(len(row[1]) for row in r.rows)
    print()
    for status, name, detail, fix in r.rows:
        print("[%s] %-*s  %s" % (MARK[status], width, name, detail))
        if fix and status != OK:
            print("        %s-> %s" % (" " * width, fix))

    counts = {s: sum(1 for row in r.rows if row[0] == s) for s in (OK, WARN, FAIL)}
    print("\n%d ok, %d warnings, %d failures" % (counts[OK], counts[WARN], counts[FAIL]))

    worst = r.worst()
    if worst == FAIL:
        print("Something needed is missing or misnamed. The -> lines say what to do.")
    elif worst == WARN:
        print("Everything needed is present. The warnings are worth a look, not urgent.")
    else:
        print("Everything checks out.")

    if clients:
        print("\nYour JACK clients, in case a port name above needs correcting:")
        for client in sorted(clients):
            slot = clients[client]
            print("  %-22s %d out, %d in   %s" % (client, len(slot["output"]), len(slot["input"]),
                  ", ".join((slot["output"] + slot["input"])[:3])))

    return 1 if worst == FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
