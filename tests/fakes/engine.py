import json
import socket
import threading
import signal
import sys
import os
import time
from collections import OrderedDict

class FakeEngine:
    def __init__(self):
        self.port = int(os.environ.get('GX_PORT', 7000))
        # Opt-in per-method fault injection. Maps a JSON-RPC method name to a
        # dict of fault settings, e.g. {'get': {'delay': 0.5}} or
        # {'set': {'drop': True}}. Empty by default so normal request handling
        # is completely unchanged when faults are disabled.
        self.faults = {}
        # Log of every fault that was actually triggered, in order.
        self.fault_log = []
        self.values = {
            'amp.fuzz': 0.0,
            'amp.out_master': 0.0,
            'amp.tonestack.select': 0,
            'freeverb.RoomSize': 0.0,
            'freeverb.on_off': 0,
            'echo.on_off': 0,
            'echo.time': 0.0,
            'cab.on_off': 0,
            'system.current_bank': '',
            'system.current_preset': '',
        }
        
        self.banks = OrderedDict([
            ('Warm', ['Clean Warm', 'Crunch']),
        ])
        
        # Seed presets
        self.presets = {
            'Warm/Clean Warm': {'amp.fuzz': 0.02, 'cab.on_off': 1},
            'Warm/Crunch': {'amp.fuzz': 0.5},
        }
        
        self.clients = set()
        self.lock = threading.Lock()
        self.running = True
        
        # Handle SIGTERM
        signal.signal(signal.SIGTERM, self._signal_handler)
        
    def _signal_handler(self, signum, frame):
        self.running = False
        for client in list(self.clients):
            try:
                client.close()
            except:
                pass
        sys.exit(0)
        
    def start(self):
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind(('127.0.0.1', self.port))
        server_socket.listen(5)
        
        print(f"Fake Guitarix engine listening on 127.0.0.1:{self.port}")
        
        while self.running:
            try:
                client_socket, address = server_socket.accept()
                client_thread = threading.Thread(target=self._handle_client, args=(client_socket,))
                client_thread.daemon = True
                client_thread.start()
            except OSError:
                break
        
        server_socket.close()
        
    def _handle_client(self, client_socket):
        self.clients.add(client_socket)
        
        try:
            buffer = b''
            while self.running:
                try:
                    data = client_socket.recv(4096)
                    if not data:
                        break
                    
                    buffer += data
                    while b'\n' in buffer:
                        line, buffer = buffer.split(b'\n', 1)
                        line = line.strip()
                        if line:
                            self._process_request(client_socket, line)
                except ConnectionResetError:
                    break
                except OSError:
                    break
        finally:
            self.clients.discard(client_socket)
            try:
                client_socket.close()
            except:
                pass
        
    def _record_fault(self, method, kind, detail=None):
        entry = {'method': method, 'fault': kind}
        if detail is not None:
            entry['detail'] = detail
        self.fault_log.append(entry)
        return entry

    def _apply_fault(self, method):
        """Apply any configured fault for `method`.

        Returns True when the request should be dropped (no response and no
        broadcast). Delays are applied in place. Every triggered fault is
        appended to self.fault_log.
        """
        settings = self.faults.get(method)
        if not settings:
            return False

        delay = settings.get('delay')
        if delay:
            self._record_fault(method, 'delay', delay)
            time.sleep(delay)

        if settings.get('drop'):
            self._record_fault(method, 'drop')
            return True

        return False

    def _process_request(self, client_socket, line):
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            return
        
        response = None
        broadcast_params = None
        
        if 'method' not in request:
            return
        
        method = request['method']
        params = request.get('params', [])
        call_id = request.get('id')
        
        if self._apply_fault(method):
            return
        
        if method == 'get':
            if call_id is not None:
                # One flat object, not a list of single-key ones: gx_rpc.get()
                # feeds the result straight into dict.update().
                result = {pid: self.values.get(pid, 0) for pid in params}
                response = {
                    'jsonrpc': '2.0',
                    'id': call_id,
                    'result': result
                }
        
        elif method == 'set':
            # This is a notification, no reply
            if call_id is None:
                changes = {}
                for i in range(0, len(params), 2):
                    if i + 1 < len(params):
                        pid = params[i]
                        value = params[i+1]
                        self.values[pid] = value
                        changes[pid] = value
                
                # Broadcast changes to other clients
                if changes:
                    broadcast_params = []
                    for pid, value in changes.items():
                        broadcast_params.extend([pid, value])
            else:
                # If it has an ID, it's a call, but we don't support that
                response = {
                    'jsonrpc': '2.0',
                    'id': call_id,
                    'error': {'code': -32601, 'message': 'Method not found'}
                }
        
        elif method == 'banks':
            if call_id is not None:
                result = []
                for bank_name, presets in self.banks.items():
                    result.append({'name': bank_name, 'presets': list(presets)})
                response = {
                    'jsonrpc': '2.0',
                    'id': call_id,
                    'result': result
                }
        
        elif method == 'setpreset':
            # This is a notification, no reply
            if call_id is None:
                bank_name = params[0]
                preset_name = params[1]
                preset_key = f'{bank_name}/{preset_name}'
                if preset_key in self.presets:
                    # Apply the preset values
                    for pid, value in self.presets[preset_key].items():
                        self.values[pid] = value
                    
                    # Update current preset. gx_rpc.current_preset() reads the
                    # bank alongside the name, so both have to move together.
                    self.values['system.current_bank'] = bank_name
                    self.values['system.current_preset'] = preset_name

                    # Broadcast changes to other clients
                    broadcast_params = []
                    for pid, value in self.presets[preset_key].items():
                        broadcast_params.extend([pid, value])
                    broadcast_params.extend(['system.current_bank', bank_name,
                                             'system.current_preset', preset_name])
            else:
                response = {
                    'jsonrpc': '2.0',
                    'id': call_id,
                    'error': {'code': -32601, 'message': 'Method not found'}
                }
        
        elif method == 'parameterlist':
            if call_id is not None:
                result = []
                for pid, value in self.values.items():
                    param_type = 'float' if isinstance(value, (int, float)) and not isinstance(value, bool) else ('bool' if isinstance(value, bool) else 'enum')
                    min_val = 0.0
                    max_val = 1.0
                    step_val = 0.01
                    
                    # Special handling for some parameters
                    if pid == 'amp.tonestack.select':
                        min_val = 0
                        max_val = 2
                        step_val = 1
                    elif pid == 'freeverb.on_off' or pid == 'echo.on_off' or pid == 'cab.on_off':
                        min_val = 0
                        max_val = 1
                        step_val = 1
                    
                    result.extend([
                        'FloatParameter' if param_type == 'float' else ('BoolParameter' if param_type == 'bool' else 'EnumParameter'),
                        {
                            'Parameter': {
                                'id': pid,
                                'name': pid.split('.')[-1],
                                'desc': '',
                                'group': ''
                            },
                            'value': value,
                            'lower_bound': min_val,
                            'upper_bound': max_val,
                            'step': step_val
                        }
                    ])
                response = {
                    'jsonrpc': '2.0',
                    'id': call_id,
                    'result': result
                }
        
        elif method == 'listen':
            # This is a notification, no reply
            pass
        
        elif method == 'save_preset':
            # This is a notification, no reply
            if call_id is None:
                bank_name = params[0]
                preset_name = params[1]
                preset_key = f'{bank_name}/{preset_name}'
                self.presets[preset_key] = self.values.copy()
                
                # Ensure bank exists
                if bank_name not in self.banks:
                    self.banks[bank_name] = []
                
                # Add preset to bank if not already there
                if preset_name not in self.banks[bank_name]:
                    self.banks[bank_name].append(preset_name)
            else:
                response = {
                    'jsonrpc': '2.0',
                    'id': call_id,
                    'error': {'code': -32601, 'message': 'Method not found'}
                }
        
        elif method == 'bank_insert_new':
            # This is a notification, no reply
            if call_id is None:
                bank_name = params[0]
                if bank_name not in self.banks:
                    self.banks[bank_name] = []
            else:
                response = {
                    'jsonrpc': '2.0',
                    'id': call_id,
                    'error': {'code': -32601, 'message': 'Method not found'}
                }
        
        elif method == 'bank_remove':
            # This is a notification, no reply
            if call_id is None:
                bank_name = params[0]
                if bank_name in self.banks:
                    del self.banks[bank_name]
                    # Remove all presets from this bank
                    for preset_key in list(self.presets.keys()):
                        if preset_key.startswith(f'{bank_name}/'):
                            del self.presets[preset_key]
            else:
                response = {
                    'jsonrpc': '2.0',
                    'id': call_id,
                    'error': {'code': -32601, 'message': 'Method not found'}
                }
        
        elif method == 'preset_remove':
            # This is a notification, no reply
            if call_id is None:
                bank_name = params[0]
                preset_name = params[1]
                preset_key = f'{bank_name}/{preset_name}'
                if preset_key in self.presets:
                    del self.presets[preset_key]
                    # Remove from bank
                    if bank_name in self.banks and preset_name in self.banks[bank_name]:
                        self.banks[bank_name].remove(preset_name)
            else:
                response = {
                    'jsonrpc': '2.0',
                    'id': call_id,
                    'error': {'code': -32601, 'message': 'Method not found'}
                }
        
        elif method == 'rename_preset':
            # This is a notification, no reply
            if call_id is None:
                bank_name = params[0]
                old_name = params[1]
                new_name = params[2]
                old_key = f'{bank_name}/{old_name}'
                new_key = f'{bank_name}/{new_name}'
                
                if old_key in self.presets:
                    # Rename the preset
                    self.presets[new_key] = self.presets.pop(old_key)
                    
                    # Update bank list
                    if bank_name in self.banks and old_name in self.banks[bank_name]:
                        idx = self.banks[bank_name].index(old_name)
                        self.banks[bank_name][idx] = new_name
            else:
                response = {
                    'jsonrpc': '2.0',
                    'id': call_id,
                    'error': {'code': -32601, 'message': 'Method not found'}
                }
        
        else:
            # Unknown method with ID
            if call_id is not None:
                response = {
                    'jsonrpc': '2.0',
                    'id': call_id,
                    'error': {'code': -32601, 'message': 'Method not found'}
                }
        
        # Send response if needed
        if response:
            try:
                client_socket.send((json.dumps(response) + '\n').encode('utf-8'))
            except:
                pass
        
        # Broadcast changes to other clients
        if broadcast_params and len(self.clients) > 1:
            broadcast_msg = {
                'jsonrpc': '2.0',
                'method': 'set',
                'params': broadcast_params
            }
            broadcast_json = json.dumps(broadcast_msg) + '\n'
            
            # Send to all other clients
            with self.lock:
                for client in list(self.clients):
                    if client != client_socket:
                        try:
                            client.send(broadcast_json.encode('utf-8'))
                        except:
                            pass

if __name__ == '__main__':
    engine = FakeEngine()
    engine.start()
