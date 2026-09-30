#!/usr/bin/env python3
"""
Regression for gx-43: under GX_BROADCAST=1 the root must serve broadcast.html
(and broadcast.js / broadcast.css), not the Studio index.html.

    python3 tests/day_gx_43_tests.py

GX_BROADCAST and GX_DEMO_ONLY are read at import time, so each mode runs in a
child process that sets the environment BEFORE importing app. The parent
starts the three children (broadcast, normal, demo) and reports.

Limit: the tests stub of flask_socketio has no test client, so the real
Socket.IO client is not driven here. What is checked instead is the server
half of the initial-snapshot / reconnect contract: every (re)connect runs the
connect handler, which must emit the reduced broadcast snapshot.
"""

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

STUDIO_MARKERS = ('id="btn-save"', 'id="btn-live"', 'id="btn-listen"',
                  "/static/app.js", "/static/style.css", "/static/demo.js")


def check(cond, message):
    if not cond:
        raise AssertionError(message)


def child(mode):
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    for k in ("GX_BROADCAST", "GX_DEMO_ONLY"):
        os.environ.pop(k, None)
    if mode == "broadcast":
        os.environ["GX_BROADCAST"] = "1"
    elif mode == "demo":
        os.environ["GX_DEMO_ONLY"] = "1"
    os.environ["GX_PORT"] = str(port)
    os.environ["FAKE_JACK_DIR"] = tempfile.mkdtemp(prefix="gxweb-jack-")
    os.environ["PATH"] = os.path.join(HERE, "fakes", "bin") + os.pathsep + os.environ["PATH"]
    sys.path[:0] = [os.path.join(HERE, "stubs"), ROOT]
    import logging
    logging.disable(logging.WARNING)
    import recorder
    import backing as backing_mod
    recorder.RECORDINGS_DIR = tempfile.mkdtemp(prefix="gxweb-rec-")
    backing_mod.BACKING_DIR = tempfile.mkdtemp(prefix="gxweb-backing-")
    import app as A
    A.rec.dir, A.backing.dir = recorder.RECORDINGS_DIR, backing_mod.BACKING_DIR

    templates = []
    from flask import template_rendered

    def note(sender, template, context, **extra):
        templates.append(template.name)

    template_rendered.connect(note, A.app)
    with A.app.test_client() as c:
        r = c.get("/")
        html = r.get_data(as_text=True)
        check(r.status_code == 200, "root returned %s" % r.status_code)

        if mode == "broadcast":
            check(templates == ["broadcast.html"],
                  "rendered %s, wanted broadcast.html" % templates)
            check("/static/broadcast.js" in html, "broadcast.js not referenced")
            check("/static/broadcast.css" in html, "broadcast.css not referenced")
            for marker in STUDIO_MARKERS:
                check(marker not in html, "Studio control leaked: %s" % marker)
            check("<button" not in html and "<input" not in html
                  and "<select" not in html, "the page carries a control")
            # Initial snapshot, then a reconnect: each connect emits the
            # reduced snapshot and nothing from the full state.
            before = len(A.socketio.emitted)
            for _ in range(2):
                A.client_connected()
            sent = A.socketio.emitted[before:]
            check([e for e, _ in sent] == ["snapshot", "snapshot"],
                  "connect did not emit a snapshot each time: %s" % sent)
            for _, payload in sent:
                check("values" not in payload and "banks" not in payload,
                      "full snapshot leaked on connect: %s" % sorted(payload))
            # The routes it answers are unchanged.
            for path in ("/static/broadcast.js", "/static/broadcast.css"):
                check(c.get(path).status_code == 200, "%s blocked" % path)
            check(c.get("/api/state").status_code == 403, "/api/state allowed")
        else:
            check(templates == ["index.html"],
                  "rendered %s, wanted index.html" % templates)
            check("/static/broadcast.js" not in html,
                  "broadcast client served outside broadcast mode")
            check("/static/app.js" in html or mode == "demo",
                  "Studio app.js missing in normal mode")
            if mode == "normal":
                d = c.get("/?demo=1").get_data(as_text=True)
                check("demo" in d, "?demo=1 lost the demo flag")


def main():
    failed = []
    for mode in ("broadcast", "normal", "demo"):
        p = subprocess.run([sys.executable, os.path.abspath(__file__), "--child", mode],
                           capture_output=True, text=True, timeout=120)
        ok = p.returncode == 0
        print("  %-5s %s" % ("ok" if ok else "FAIL", mode))
        if not ok:
            print((p.stdout + p.stderr).strip())
            failed.append(mode)
    print("FAILED: " + ", ".join(failed) if failed else "all good")
    sys.stdout.flush()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--child":
        try:
            child(sys.argv[2])
        except Exception as exc:
            print("%s: %s" % (type(exc).__name__, exc))
            sys.stdout.flush()
            os._exit(1)
        sys.stdout.flush()
        os._exit(0)
    main()
