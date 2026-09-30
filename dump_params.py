#!/usr/bin/env python3
"""
Print every parameter the running guitarix engine exposes, so you can see
the real ids and ranges before editing controls.py.

    python3 dump_params.py                 # everything
    python3 dump_params.py freeverb        # only ids containing "freeverb"
    python3 dump_params.py --raw select    # exactly what the engine sent

--raw prints each matching descriptor as the engine sent it, before this app
interprets it. That's the thing to look at when a control renders wrong --
an empty dropdown, say -- because it shows the real field names.
"""

import sys
import threading

from gx_rpc import GuitarixRPC

ready = threading.Event()
rpc = GuitarixRPC(on_ready=ready.set)
rpc.start()

if not ready.wait(timeout=10):
    rpc.stop()
    sys.exit("could not reach guitarix on 127.0.0.1:7000 "
             "(is it running with -p 7000 ?)")

import json

args = [a for a in sys.argv[1:] if a != "--raw"]
raw_mode = "--raw" in sys.argv[1:]
needle = args[0] if args else ""

if raw_mode:
    raw = rpc.raw_parameters()
    for pid in sorted(raw):
        if needle and needle not in pid:
            continue
        type_name, desc = raw[pid]
        print("%s  (%s)" % (pid, type_name))
        print(json.dumps(desc, indent=2, sort_keys=True))
        try:
            print("  current value:", json.dumps(rpc.get([pid]).get(pid)))
        except Exception as exc:
            print("  current value: unreadable (%s)" % exc)
        print()
    rpc.stop()
    sys.exit(0)

params = rpc.parameter_list()

for pid in sorted(params):
    if needle and needle not in pid:
        continue
    p = params[pid]
    print("{id:38} {type:16} {min:>10} .. {max:<10} step={step:<8} = {value}".format(**p))
    extra = []
    if p.get("name") and p["name"] != pid.split(".")[-1]:
        extra.append("name: %s" % p["name"])
    if p.get("desc"):
        extra.append("desc: %s" % p["desc"])
    if p.get("options"):
        extra.append("options: %s" % ", ".join(o["label"] for o in p["options"]))
    for line in extra:
        print("    " + line)

print("\nNothing under a parameter means the engine sent no name or description")
print("beyond its id. Give it a label in LABELS in controls.py.")

print("\n%d parameters" % len(params))
rpc.stop()
