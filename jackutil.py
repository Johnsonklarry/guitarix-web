"""
Small helpers around the JACK command-line tools: finding a client's ports,
seeing what's connected to what, and wiring one set of ports to another.

Everything here shells out to jack_lsp / jack_connect / jack_disconnect
rather than linking libjack, so a failure is a failed command, never a hung
realtime thread inside this process.
"""

import shutil
import subprocess

TOOLS = ("jack_lsp", "jack_connect", "jack_disconnect")


def available():
    return all(shutil.which(t) for t in TOOLS)


def ports(client, direction, fallback=()):
    """
    `client`'s ports of one direction, "input" or "output", via `jack_lsp -p`,
    which prints each port followed by an indented properties line. Asking
    JACK beats trusting fixed names: a client's ports are named differently
    depending on how it was started and what hardware is attached.
    """
    if not shutil.which("jack_lsp"):
        return list(fallback)
    try:
        out = subprocess.run(["jack_lsp", "-p"], capture_output=True,
                             timeout=3).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.TimeoutExpired):
        return list(fallback)

    found, current = [], None
    for line in out.splitlines():
        if line.startswith((" ", "\t")):
            if current and direction in line and current.startswith(client + ":"):
                found.append(current)
        else:
            current = line.strip()
    return found or list(fallback)


def connections(port):
    """Ports connected to `port`, in either direction, via `jack_lsp -c`."""
    if not shutil.which("jack_lsp"):
        return []
    try:
        out = subprocess.run(["jack_lsp", "-c", port], capture_output=True,
                             timeout=3).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.TimeoutExpired):
        return []
    # the first line echoes the port itself; its connections follow, indented
    return [line.strip() for line in out.splitlines()[1:] if line.strip()]


def connect(src, dst):
    return subprocess.run(["jack_connect", src, dst], capture_output=True).returncode == 0


def disconnect(src, dst):
    return subprocess.run(["jack_disconnect", src, dst], capture_output=True).returncode == 0


def pairs(outs, ins):
    """
    Which output feeds which input. One source feeds both sides, so a mono
    signal isn't silent on the right; one destination takes both sources, so
    stereo into a mono input isn't half-lost; otherwise left to left, right
    to right.
    """
    if not outs or not ins:
        return []
    if len(outs) == 1:
        return [(outs[0], i) for i in ins[:2]]
    if len(ins) == 1:
        return [(o, ins[0]) for o in outs[:2]]
    return list(zip(outs, ins))
