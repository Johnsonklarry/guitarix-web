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
        params = rpc.parameter_list()
        banks = rpc.banks()
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


def check_serving(r):
    here = os.path.dirname(os.path.abspath(__file__))
    vendored = os.path.exists(os.path.join(here, "static", "socket.io.min.js"))
    r.add(OK if vendored else WARN, "socket.io client",
          "served locally" if vendored else "loaded from a CDN",
          "" if vendored else "a device with no internet can't connect; download "
                              "socket.io.min.js into static/ and point the script tag at it")
    r.add(OK if os.environ.get("GX_SECRET_KEY") else OK, "secret key",
          "set from the environment" if os.environ.get("GX_SECRET_KEY")
          else "random each start (fine -- nothing needs to outlive a restart)")


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
