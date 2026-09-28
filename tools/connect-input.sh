#!/bin/sh
#
# Wire the guitar into guitarix, once JACK is actually ready.
#
# guitarix-connect.service used to be ordered After=guitarix.service alone,
# which is when that unit starts, not when jackd has finished coming up. So
# jack_connect ran too early, printed "jack server not running?", and failed on
# every boot -- masked by a '-' on ExecStart and RemainAfterExit=yes, which
# left the unit reporting active (exited) having done nothing.
#
# So: poll for the ports rather than sleep a guess, and treat an existing
# connection as success, because on a service restart the wiring is usually
# still there and that is not a failure.

set -u

SRC="${GX_CONNECT_SRC:-system:capture_1}"
DST="${GX_CONNECT_DST:-gx_head_amp:in_0}"
ATTEMPTS="${GX_CONNECT_ATTEMPTS:-60}"      # 60 x 0.5s = 30s, enough for a cold boot
DELAY="${GX_CONNECT_DELAY:-0.5}"

connected() {
    # jack_lsp -c prints the port, then its connections indented
    jack_lsp -c "$DST" 2>/dev/null | sed 1d | grep -q "[[:space:]]*$SRC$"
}

i=0
while [ "$i" -lt "$ATTEMPTS" ]; do
    ports=$(jack_lsp 2>/dev/null)
    case "$ports" in
        *"$SRC"*)
            case "$ports" in
                *"$DST"*)
                    if connected; then
                        echo "already connected: $SRC -> $DST"
                        exit 0
                    fi
                    if jack_connect "$SRC" "$DST" 2>/dev/null || connected; then
                        echo "connected: $SRC -> $DST"
                        exit 0
                    fi
                    echo "jack_connect refused $SRC -> $DST" >&2
                    exit 1
                    ;;
            esac
            ;;
    esac
    i=$((i + 1))
    sleep "$DELAY"
done

echo "gave up after $ATTEMPTS attempts: $SRC or $DST never appeared in jack_lsp" >&2
exit 1
