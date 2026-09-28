#!/usr/bin/env python3
"""
Work out which preset-management RPC methods your guitarix build answers to.

Guitarix has no published list of RPC method names and they have changed
between releases, so gx_rpc.py ships with best guesses. This walks the
candidate names and tells you which ones exist, so you can correct
PRESET_METHODS in gx_rpc.py once and be done.

    python3 probe_rpc.py

How it works: each candidate is sent with NO arguments. A method the engine
doesn't know comes back as JSON-RPC error -32601; one it does know comes back
complaining about the arguments instead. That difference is the answer.

Back up ~/.config/guitarix/banks before running this. An argument-less call
should never be acted on, but these are save and delete methods and this is
a probe, not a guarantee.
"""

import sys
import threading

import gx_rpc
from gx_rpc import GuitarixRPC

ready = threading.Event()
rpc = GuitarixRPC(on_ready=ready.set)
rpc.start()

if not ready.wait(timeout=10):
    sys.exit("could not reach guitarix on 127.0.0.1:7000 "
             "(is it running with -p 7000 ?)")

print("probing %s:%s\n" % (rpc.host, rpc.port))

found = {}
for action, names in gx_rpc.CANDIDATES.items():
    hit = None
    for name in names:
        state = rpc.probe(name)
        print("  %-22s %s" % (name, state))
        if state != "missing" and hit is None:
            hit = name
    found[action] = hit
    print("%-14s -> %s\n" % (action, hit or "NOTHING FOUND"))

print("Put this in gx_rpc.py:\n")
print("PRESET_METHODS = {")
for action, name in found.items():
    print('    %-16s %s,' % ('"%s":' % action, '"%s"' % name if name else "None"))
print("}")

missing = [a for a, n in found.items() if not n]
if missing:
    print("\nNo candidate matched for: %s" % ", ".join(missing))
    print("save_current is the least important one: with save_as present, the")
    print("Save button overwrites the loaded preset by name instead.")
    print("Run  grep -rn 'method_name' src/gx_head/engine/jsonrpc_methods.gperf_tmpl")
    print("in a guitarix source checkout to see the real list, and add the")
    print("names to CANDIDATES in gx_rpc.py.")

rpc.stop()
