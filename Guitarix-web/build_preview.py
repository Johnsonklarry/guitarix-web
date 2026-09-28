#!/usr/bin/env python3
"""
Build preview.html: the real page, in demo mode, as one file you can open
straight from disk -- no Pi, no server.

It renders the real template with demo switched on, then inlines the
stylesheet, the demo stand-in and the page script. So the preview can't drift
from the app: it IS the app, answering itself. The only thing it loads from
beside it is the demo clip, static/demo/jam.mp3, so open it from the project
folder.

    python3 build_preview.py
"""

import os
import re

from jinja2 import Environment, FileSystemLoader

ROOT = os.path.dirname(os.path.abspath(__file__))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


env = Environment(loader=FileSystemLoader(os.path.join(ROOT, "templates")))
env.globals["asset"] = lambda name: "static/" + name          # relative to preview.html
html = env.get_template("index.html").render(demo=True, demo_only=False)


def inline(pattern, replacement):
    global html
    new, count = re.subn(pattern, lambda m: replacement, html)
    if count != 1:
        raise SystemExit("expected one match for %s, found %d" % (pattern, count))
    html = new


inline(r'<link rel="stylesheet" href="static/style\.css">', "<style>\n" + read("static", "style.css") + "\n</style>")
inline(r'<script src="static/demo\.js"></script>', "<script>\n" + read("static", "demo.js") + "\n</script>")
inline(r'<script src="static/app\.js"></script>', "<script>\n" + read("static", "app.js") + "\n</script>")
html = html.replace("<title>Guitarix</title>", "<title>Guitarix &mdash; demo</title>")

leftover = re.findall(r'(?:src|href)="(static/(?!demo/jam\.mp3|icons/)[^"]+)"', html)
if leftover:
    raise SystemExit("not inlined: %s" % leftover)

with open(os.path.join(ROOT, "preview.html"), "w", encoding="utf-8") as f:
    f.write(html)
print("preview.html: %d bytes" % len(html))
