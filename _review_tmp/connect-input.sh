#!/bin/sh

# Poll for JACK server readiness and connect guitar input to amplifier.
# This script waits for both system:capture_1 and gx_head_amp:in_0 to appear
# in jack_lsp output, then connects them using jack_connect.
# It exits 0 if successful or already connected, non-zero on failure.

MAX_ATTEMPTS=20
DELAY=0.5

for i in $(seq 1 $MAX_ATTEMPTS); do
    # Check if both ports exist
    if jack_lsp | grep -q "^system:capture_1$" && jack_lsp | grep -q "^gx_head_amp:in_0$"; then
        # Try to connect them
        if jack_connect system:capture_1 gx_head_amp:in_0; then
            exit 0
        else
            echo "Error: failed to connect ports" >&2
            exit 1
        fi
    fi
    sleep $DELAY
done

echo "Error: ports system:capture_1 and gx_head_amp:in_0 did not appear within ${MAX_ATTEMPTS} attempts" >&2
exit 1
