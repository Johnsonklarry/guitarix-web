import json
import os
import tempfile

FAULTS_KEY = 'faults'
FAULT_LOG_KEY = 'fault_log'


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
    return load()['ports']


def set_faults(**faults):
    """
    Opt-in fault injection. Each keyword is a fault name and its value the
    number of times it should fire (None/negative means "every time"). The
    settings are persisted in graph.json so a fault armed by one process is
    seen by the next, which is the whole point: the recorder and the fake
    engine are separate processes.
    """
    state = load()
    state[FAULTS_KEY] = dict(faults)
    save(state)


def clear_faults():
    """Remove all fault settings and the fault log."""
    state = load()
    state[FAULTS_KEY] = {}
    state[FAULT_LOG_KEY] = []
    save(state)


def faults():
    """Return the current fault settings."""
    return dict(load().get(FAULTS_KEY) or {})


def fault_log():
    """Return the list of faults that have fired, oldest first."""
    return list(load().get(FAULT_LOG_KEY) or [])


def _trigger_fault(state, name, detail):
    """
    Record one firing of `name` and consume one of its remaining uses.

    Returns True if the fault fired. The log entry is appended before the
    counter is decremented so a fault armed for one use still leaves a trace
    of that single use behind for the test to assert on.
    """
    settings = state.get(FAULTS_KEY) or {}
    if name not in settings:
        return False

    remaining = settings[name]
    if remaining is not None and remaining <= 0:
        return False

    log = state.setdefault(FAULT_LOG_KEY, [])
    log.append({'fault': name, 'detail': detail})

    if remaining is not None:
        settings[name] = remaining - 1

    return True


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

    if _trigger_fault(state, 'drop_connect', '%s -> %s' % (src, dst)):
        save(state)
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

    if _trigger_fault(state, 'drop_disconnect', '%s -> %s' % (src, dst)):
        save(state)
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
    
    found = []
    for src, dst in state['connections']:
        if src == port:
            found.append(dst)
        elif dst == port:
            found.append(src)
    return found