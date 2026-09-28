import json
import os
import tempfile

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
            return json.load(f)
    
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


def connect(src, dst):
    """Connect two ports. Return True on success."""
    state = load()

    # Check if ports exist
    if src not in state['ports'] or dst not in state['ports']:
        return False

    # One end has to be an output and the other an input. Real jack_connect
    # refuses two inputs or two outputs, and a fake that shrugs at a wiring
    # bug is worse than no test at all.
    if sorted((state['ports'][src], state['ports'][dst])) != ['input', 'output']:
        return False

    # Check if already connected
    if [src, dst] in state['connections']:
        return False

    # Add connection
    state['connections'].append([src, dst])
    save(state)
    return True


def disconnect(src, dst):
    """Disconnect two ports. Return True on success."""
    state = load()
    
    # Check if connected
    try:
        state['connections'].remove([src, dst])
        save(state)
        return True
    except ValueError:
        return False


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