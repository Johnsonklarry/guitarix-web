#!/usr/bin/env python3
"""
Vendor the Socket.IO browser client into static/socket.io.min.js.

    python3 tools/vendor_socketio.py            # fetch, verify, write
    python3 tools/vendor_socketio.py --check    # verify the file already in static/ (offline)

The owner runs the fetch ONCE on a machine with internet, then commits the
file. The Pi then serves the client itself and works with no internet.

Safety: the download goes to memory first; nothing is written unless the
SHA-384 matches EXPECTED_SHA384, the size is sane and the banner names the
pinned version. To move to a new version change VERSION and EXPECTED_SHA384
together (the SRI hash is published at
https://socket.io/docs/v4/client-installation/ ).
"""

import argparse
import base64
import hashlib
import os
import sys
import urllib.request

VERSION = "4.7.5"
URL = "https://cdn.socket.io/%s/socket.io.min.js" % VERSION
# SRI hash for cdn.socket.io 4.7.5. UNVERIFIED: recorded without network access.
# Cross-check with the socket.io docs page above before trusting it.
EXPECTED_SHA384 = "sha384-2huaZvOR9iDzHqslqwpR87isEmrfxqyWOF7hr7BY6KG0+hVKLoEXMPUJw3ynWuhO"
MIN_BYTES = 30 * 1024
MAX_BYTES = 200 * 1024

HERE = os.path.dirname(os.path.abspath(__file__))
DEST = os.path.join(os.path.dirname(HERE), "static", "socket.io.min.js")


def sri384(data):
    return "sha384-" + base64.b64encode(hashlib.sha384(data).digest()).decode("ascii")


def problems(data, expected=EXPECTED_SHA384, version=VERSION):
    """Everything wrong with `data` as the pinned client; [] means good."""
    out = []
    if not MIN_BYTES <= len(data) <= MAX_BYTES:
        out.append("size %d bytes is outside %d..%d" % (len(data), MIN_BYTES, MAX_BYTES))
    if ("v" + version).encode() not in data[:400]:
        out.append("banner does not mention v%s" % version)
    got = sri384(data)
    if got != expected:
        out.append("hash mismatch: got %s, expected %s" % (got, expected))
    return out


def fetch(url=URL):
    req = urllib.request.Request(url, headers={"User-Agent": "guitarix-web-vendor/1"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true",
                    help="verify the existing static/socket.io.min.js, no network")
    ap.add_argument("--accept-hash", metavar="SHA384",
                    help="use this sha384-... value instead of EXPECTED_SHA384 "
                         "(only after cross-checking it against the socket.io docs)")
    args = ap.parse_args(argv)
    expected = args.accept_hash or EXPECTED_SHA384

    if args.check:
        try:
            with open(DEST, "rb") as f:
                data = f.read()
        except OSError as e:
            print("missing: %s (%s)" % (DEST, e))
            return 1
    else:
        print("fetching %s" % URL)
        try:
            data = fetch()
        except Exception as e:                      # noqa: BLE001 - report and stop
            print("download failed: %s" % e)
            return 1

    bad = problems(data, expected)
    print("size %d bytes, %s" % (len(data), sri384(data)))
    if bad:
        for b in bad:
            print("REFUSED: " + b)
        return 2
    if args.check:
        print("OK: %s matches the pin" % DEST)
        return 0

    os.makedirs(os.path.dirname(DEST), exist_ok=True)
    tmp = DEST + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, DEST)
    print("wrote %s -- now commit it" % DEST)
    return 0


if __name__ == "__main__":
    sys.exit(main())
