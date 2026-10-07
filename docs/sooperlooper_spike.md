# SooperLooper feasibility spike (#2604)

Status: findings recorded, recommendation below. No implementation ticket
should be cut until this document is reviewed.

## Scope

Determine whether SooperLooper can run alongside guitarix on the Pi's JACK
graph, whether `app.py` can drive it over OSC, and what it costs in CPU and
memory. This is research only; nothing in this document is implemented.

## Install result

SooperLooper was installed from the distribution package and started as a
JACK client on the same graph guitarix already occupies. It registers its
own ports and connects to the same JACK server; guitarix keeps running and
keeps its existing connections. No exclusive-device conflict was observed,
and the two clients coexist without either being restarted.

Result: PASS - SooperLooper installs and joins the existing JACK graph
alongside guitarix.

## OSC control result

SooperLooper exposes an OSC surface. Driving it from `app.py` means adding
a second outbound transport next to the existing JSON-RPC socket to
guitarix: the browser talks socket.io to Flask as it does today, and Flask
sends OSC datagrams to SooperLooper's port. The control surface is
address-based (record, overdub, undo, loop select), which maps cleanly onto
the existing per-operation request/response pattern, but it is fire-and-
forget: there is no reply to correlate, so state has to be read back
separately rather than inferred from a response.

Result: PASS - `app.py` can control SooperLooper over OSC, with the caveat
that OSC gives no acknowledgement and state must be polled back.

## Measured CPU/memory cost

Measured on the Pi with guitarix running and a single loop armed, sampled
over a steady-state window after startup settled:

- SooperLooper process: roughly 2-4% of one core while looping, spiking
  briefly on loop boundary transitions.
- Resident memory: on the order of tens of megabytes, stable over the
  sampling window with no observed growth.
- guitarix's own CPU and memory were unchanged within measurement noise
  when SooperLooper was added to the graph.

These are order-of-magnitude figures from a short spike, not a benchmark.

## Go/no-go

GO - with conditions. SooperLooper installs cleanly alongside guitarix on
the same JACK graph, `app.py` can drive it over OSC, and the measured
CPU/memory cost is small enough to be acceptable on the Pi. The conditions
are that the OSC layer must be treated as fire-and-forget with explicit
state polling, and that the CPU/memory numbers above must be re-measured
under a real workload before the feature is considered done.
