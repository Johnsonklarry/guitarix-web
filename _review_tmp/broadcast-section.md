## Broadcast mode

A read-only public view of the rig, served by app.py with GX_BROADCAST=1 on its own port, for strangers at amp.larebear.org. It shows what is playing right now and lets a visitor listen; it can change nothing.

The server refuses every control event and every route but the page, its static files, the manifest and /monitor.mp3. Over the socket the client receives exactly four events: 'snapshot' with {broadcast, connected, bank, preset, recording, takes}, 'preset' with {bank, preset}, 'status' with {connected}, and 'rec' with {recording, count}. Nothing else arrives, ever. The client must never emit anything.

Visitor sees the same controls as in Live mode, but with no interaction: the amp, effects and EQ are all greyed out, and the Record, Loop last, Backing and Listen pads are disabled. The preset name is shown large, and the bank's presets are tiles. The current take is shown below the controls.

Broadcast mode is different from Demo mode in that the demo is made-up data and never contacts the rig; broadcast is real data and read-only. The demo runs entirely in the browser, while broadcast talks to guitarix but can change nothing.

To run it: GX_BROADCAST=1 GX_WEB_PORT=5090 python3 app.py, or guitarix-broadcast.service. Forward 5090.
