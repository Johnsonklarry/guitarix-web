import json
import os
import tempfile
import time

# ---------------------------------------------------------------- fault injection
#
# Delayed ports, dropped connections, stalled subprocesses and crashes, at
# every reamp and export transition. The knobs live in the graph state file
# (FAKE_JACK_DIR/graph.json) under "faults" so a test can arm them with the
# same tools it already uses, and every injection is appended to
# FAKE_JACK_DIR/faults.log so a test can assert what was injected.
#
#   faults = {
#     "delay": 0.0,            # seconds every tool call sleeps first
#     "stall": 0.0,            # seconds a call sleeps *after* doing its work
#     "drop_connect": [...],   # [src, dst] pairs connect() refuses
#     "drop_disconnect": [...],# [src, dst] pairs disconnect() refuses
#     "drop_ports": [...],     # port names ports()/load() hides
#     "crash": [...],          # tool names that exit non-zero ("jack_lsp", ...)
#     "crash_after": 0,        # crash only from the Nth call onwards (0 = always)
#   }
#
# A "stall" is what a hung jack_lsp looks like from the caller: the call is
# still in flight when the transition it belongs to is cancelled.

FAULT_LOG = 'faults.log'


def _faults(state=None):
    if state is None:
        try:
            state = load()
        except SystemExit:
            return {}
    f = state.get('faults')
    return f if isinstance(f, dict) else {}


def _log_fault(kind, detail):
    """Append one injected failure to FAKE_JACK_DIR/faults.log."""
    try:
        path = os.path.join(_dir(), FAULT_LOG)
        with open(path, 'a') as f:
            f.write(json.dumps({'kind': kind, 'detail': detail,
                                'at': time.time()}) + '\n')
    except (OSError, SystemExit):
        pass


def read_faults():
    """Every injected failure so far, oldest first. [] when none were logged."""
    try:
        path = os.path.join(_dir(), FAULT_LOG)
        with open(path) as f:
            return [json.loads(line) for line in f if line.strip()]
    except (OSError, ValueError, SystemExit):
        return []


def clear_faults():
    """Forget the injected failures logged so far."""
    try:
        os.remove(os.path.join(_dir(), FAULT_LOG))
    except (OSError, SystemExit):
        pass


def set_faults(**kwargs):
    """Arm (or, with None, disarm) the fault injection knobs."""
    state = load()
    faults = dict(_faults(state))
    for key, value in kwargs.items():
        if value is None:
            faults.pop(key, None)
        else:
            faults[key] = value
    state['faults'] = faults
    save(state)
    return faults


def _matches(pairs, a, b):
    """True if [a, b] or [b, a] is one of the armed pairs."""
    for pair in pairs or ():
        if isinstance(pair, (list, tuple)) and len(pair) == 2 and set(pair) == {a, b}:
            return True
    return False


def _inject(tool, state, detail=None):
    """
    Apply the armed faults for one tool call. Returns True when the call must
    be treated as failed (dropped or crashed); sleeps for delay/stall either
    way. `detail` is what gets logged for a drop.
    """
    faults = _faults(state)
    delay = faults.get('delay') or 0
    if delay:
        time.sleep(delay)
    crash = tool in (faults.get('crash') or ())
    if crash:
        after = faults.get('crash_after') or 0
        if after:
            state['_calls'] = state.get('_calls', 0) + 1
            crash = state['_calls'] >= after
    if crash:
        _log_fault('crash', detail or tool)
        return True
    stall = faults.get('stall') or 0
    if stall:
        _log_fault('stall', detail or tool)
        time.sleep(stall)
    return False


def _dir():
    """
    Where the graph lives. Without FAKE_JACK_DIR these tools are being run
    outside the harness -- most often because tests/fakes/bin was left on
    PATH -- and saying so beats a KeyError traceback that jackutil quietly
    turns into a fallback port list.
    """
    try:
        return os.environ['FAKE_JACK_DIR']
    except KeyError:
        raise SystemExit("FAKE_JACK_DIR is not set: these are the test fakes, "
                         "not real JACK tools. Is tests/fakes/bin still on PATH?")


def load():
    """Load the graph state from FAKE_JACK_DIR/graph.json, seeding if needed."""
    dir = _dir()
    path = os.path.join(dir, 'graph.json')
    
    # If file exists, load it as-is
    if os.path.exists(path):
        with open(path, 'r') as f:
            try:
                state = json.load(f)
            except ValueError as e:
                raise SystemExit("%s is not valid JSON (%s); delete it to "
                                 "reseed the default rig." % (path, e))
        # A delayed port: the port exists in the rig but the tool hasn't
        # reported it yet, so callers see it appear a call later.
        hidden = _faults(state).get('drop_ports') or ()
        if hidden:
            for port in hidden:
                state['ports'].pop(port, None)
            state['connections'][:] = [c for c in state['connections']
                                       if c[0] not in hidden and c[1] not in hidden]
        # Callers index state['ports'] and state['connections'] directly, so a
        # truncated or hand-edited file must fail here, with a message, rather
        # than as a KeyError/TypeError deep inside connect() or remove_client().
        if (not isinstance(state, dict)
                or not isinstance(state.get('ports'), dict)
                or not isinstance(state.get('connections'), list)):
            raise SystemExit("%s is malformed: expected an object with a "
                             "'ports' object and a 'connections' list; delete "
                             "it to reseed the default rig." % path)
        return state
    
    # Otherwise, seed with default rig
    ports = {
        'system:capture_1': 'output',
        'system:capture_2': 'output',
        'system:playback_1': 'input',
        'system:playback_2': 'input',
        'gx_head_amp:in_0': 'input',
        'gx_head_amp:out_0': 'output',
        'gx_head_amp:out_1': 'output',
        'gx_head_fx:in_0': 'input',
        'gx_head_fx:out_0': 'output',
        'gx_head_fx:out_1': 'output',
    }
    
    connections = [['system:capture_1', 'gx_head_amp:in_0']]
    
    state = {'ports': ports, 'connections': connections}
    save(state)
    return state


def save(state):
    """Save the graph state atomically to FAKE_JACK_DIR/graph.json."""
    dir = _dir()
    path = os.path.join(dir, 'graph.json')
    
    # Write to temp file and replace
    with tempfile.NamedTemporaryFile(mode='w', dir=dir, delete=False) as f:
        json.dump(state, f)
        tmp_path = f.name
    
    os.replace(tmp_path, path)


def ports():
    """Return the current port dictionary."""
    state = load()
    if _inject('jack_lsp', state):
        return {}
    return state['ports']


def add_ports(mapping):
    """Add new ports to the graph."""
    state = load()
    state['ports'].update(mapping)
    save(state)


def remove_client(client):
    """Remove all ports starting with client: and their connections."""
    state = load()
    
    # Remove ports
    ports = state['ports']
    to_remove = [k for k in ports if k.startswith(client + ':')]
    for port in to_remove:
        del ports[port]
    
    # Remove connections involving those ports
    connections = state['connections']
    connections[:] = [c for c in connections if not (
        c[0].startswith(client + ':') or c[1].startswith(client + ':'))]
    
    save(state)


def _edge(state, a, b):
    """
    One link, one representation: the output end first, whichever way round
    the caller named them. Without this, connecting a pair and then the same
    pair reversed stores two edges, and connections_of() reports the peer
    twice -- which is exactly the list reamp.py saves and restores the
    guitar's wiring from.
    """
    return [a, b] if state['ports'][a] == 'output' else [b, a]


def connect(src, dst):
    """Connect two ports. Return True on success."""
    state = load()

    if _inject('jack_connect', state, '%s -> %s' % (src, dst)):
        return False
    if _matches(_faults(state).get('drop_connect'), src, dst):
        _log_fault('drop_connect', '%s -> %s' % (src, dst))
        return False

    if src not in state['ports'] or dst not in state['ports']:
        return False

    # One end has to be an output and the other an input. Real jack_connect
    # refuses two inputs or two outputs, and a fake that shrugs at a wiring
    # bug is worse than no test at all.
    if sorted((state['ports'][src], state['ports'][dst])) != ['input', 'output']:
        return False

    edge = _edge(state, src, dst)
    if edge in state['connections']:
        return False

    state['connections'].append(edge)
    save(state)
    return True


def disconnect(src, dst):
    """Disconnect two ports. Return True on success."""
    state = load()

    if _inject('jack_disconnect', state, '%s -> %s' % (src, dst)):
        return False
    if _matches(_faults(state).get('drop_disconnect'), src, dst):
        _log_fault('drop_disconnect', '%s -> %s' % (src, dst))
        return False

    if src not in state['ports'] or dst not in state['ports']:
        return False

    edge = _edge(state, src, dst)
    try:
        state['connections'].remove(edge)
    except ValueError:
        return False
    save(state)
    return True


def connections_of(port):
    """Return all ports connected to this one in either direction."""
    state = load()

    if _inject('jack_lsp', state, port):
        return []

    found = []
    for src, dst in state['connections']:
        if src == port:
            found.append(dst)
        elif dst == port:
            found.append(src)
    return found