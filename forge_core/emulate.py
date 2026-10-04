"""Shared fakes for the three projects' ffmpeg, mpv, JACK, OBS, MediaMTX, and delegation API tests."""

from __future__ import annotations

import json
import subprocess
import threading
import time
from collections import defaultdict, deque, namedtuple
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


class Emulator:
    """A scriptable, recording fake for project integrations."""

    def __init__(self, name):
        self.name = name
        self.calls = []
        self._scripts = defaultdict(deque)
        self._latency = 0

    def script(self, method, *responses):
        """Queue values to return, or exception instances to raise, for a method."""
        self._scripts[method].extend(responses)
        return self

    def fail(self, method, exc, times=1):
        """Queue an exception for the next ``times`` calls to a method."""
        if not isinstance(exc, Exception):
            raise TypeError("exc must be an Exception instance")
        if not isinstance(times, int) or times < 0:
            raise ValueError("times must be a non-negative integer")
        return self.script(method, *(exc for _ in range(times)))

    def latency(self, seconds):
        """Set the delay, in seconds, applied to each scripted call."""
        if seconds < 0:
            raise ValueError("latency cannot be negative")
        self._latency = seconds
        return self

    def __getattr__(self, method):
        if method not in self._scripts:
            raise AttributeError(f"{self.name!r} has no scripted method {method!r}")

        def recorded(*args, **kwargs):
            self.calls.append((method, args, kwargs))
            if self._latency:
                time.sleep(self._latency)
            if not self._scripts[method]:
                raise RuntimeError(f"no responses left for {self.name}.{method}")
            response = self._scripts[method].popleft()
            if isinstance(response, Exception):
                raise response
            return response

        return recorded


class FakeProcess:
    """A configured subprocess result for ffmpeg, mpv, JACK, or OBS tests."""

    def __init__(self, stdout='', stderr='', returncode=0, delay=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.delay = delay


def _output(value, text):
    if value is None:
        return None
    if text and isinstance(value, bytes):
        return value.decode()
    if not text and isinstance(value, str):
        return value.encode()
    return value


def fake_run(table):
    """Return a recording subprocess.run fake; the longest argv prefix wins.

    ``table`` maps argv prefixes (tuples, lists, or single executable strings)
    to :class:`FakeProcess` instances. The returned function exposes ``calls``
    as a list of ``(args, kwargs)`` pairs.
    """
    prefixes = []
    for prefix, result in table.items():
        if isinstance(prefix, (str, bytes)):
            prefix = (prefix,)
        else:
            prefix = tuple(prefix)
        if not isinstance(result, FakeProcess):
            raise TypeError("table results must be FakeProcess instances")
        prefixes.append((prefix, result))
    prefixes.sort(key=lambda entry: len(entry[0]), reverse=True)

    def run(*popenargs, input=None, capture_output=False, timeout=None, check=False, **kwargs):
        recorded_kwargs = dict(kwargs)
        recorded_kwargs.update(input=input, capture_output=capture_output,
                               timeout=timeout, check=check)
        run.calls.append((popenargs, recorded_kwargs))
        if len(popenargs) > 1:
            raise TypeError("run() accepts at most one positional argument")
        if popenargs and 'args' in kwargs:
            raise TypeError("args supplied twice")
        argv = popenargs[0] if popenargs else kwargs.get('args')
        if argv is None:
            raise TypeError("missing required argument: args")
        command = argv if isinstance(argv, (str, bytes)) else list(argv)
        words = (argv,) if isinstance(argv, (str, bytes)) else tuple(command)
        for prefix, result in prefixes:
            if words[:len(prefix)] == prefix:
                break
        else:
            raise KeyError(f"no fake process matches {command!r}")

        if timeout is not None and result.delay > timeout:
            raise subprocess.TimeoutExpired(command, timeout)
        if capture_output and ('stdout' in kwargs or 'stderr' in kwargs):
            raise ValueError('stdout and stderr arguments may not be used with capture_output')
        text = bool(kwargs.get('text', False) or kwargs.get('universal_newlines', False)
                    or kwargs.get('encoding') is not None)
        stdout_requested = capture_output or kwargs.get('stdout') == subprocess.PIPE
        stderr_requested = capture_output or kwargs.get('stderr') == subprocess.PIPE
        stdout = _output(result.stdout, text) if stdout_requested else None
        stderr = _output(result.stderr, text) if stderr_requested else None
        completed = subprocess.CompletedProcess(command, result.returncode, stdout, stderr)
        if check:
            completed.check_returncode()
        return completed

    run.calls = []
    return run


HTTPRequest = namedtuple('HTTPRequest', 'method path headers body')


class FakeFileBrowser:
    """Local contract fake for the pinned File Browser API.

    Issues dummy JWT tokens on login, validates authorization tokens on
    protected endpoints, and records request methods and paths without
    retaining credentials, tokens, or uploaded content.
    """

    def __init__(self, username="admin", password="password", token="dummy-jwt-token"):
        self.username = username
        self.password = password
        self.token = token
        self.records = []
        self._server = None

    @property
    def url(self):
        return self._server.url if self._server else None

    def _login(self, request):
        try:
            creds = json.loads(request.body.decode("utf-8")) if request.body else {}
        except Exception:
            creds = {}
        if creds.get("username") == self.username and creds.get("password") == self.password:
            return 200, self.token, {"Content-Type": "text/plain; charset=utf-8"}
        return 403, "Forbidden", {}

    def _handle_request(self, request):
        path = urlsplit(request.path).path
        self.records.append((request.method, path))

        if (request.method, path) == ("POST", "/api/login"):
            return self._login(request)

        auth = request.headers.get("X-Auth") or request.headers.get("Authorization")
        expected_bearer = f"Bearer {self.token}"
        if auth != self.token and auth != expected_bearer:
            return 401, "Unauthorized", {}

        if (request.method, path) == ("GET", "/api/resources"):
            return 200, {"items": []}, {}
        return 200, "OK", {}

    def __enter__(self):
        owner = self

        class RouteDispatcher(dict):
            def get(self, key, default=None):
                return owner._handle_request

        self._server = FakeHTTPServer(RouteDispatcher())
        self._server.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._server:
            result = self._server.__exit__(exc_type, exc_val, exc_tb)
            self._server = None
            return result
        return False


class FakePlexServer:
    """Local HTTP fake for Plex Media Server XML endpoints.

    Routes map request path strings (e.g. ``'/library/sections'``) to XML
    strings, bytes, status-and-body tuples ``(status, xml)``, or callables
    accepting an :class:`HTTPRequest` and returning one of those forms.
    """

    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.requests = []
        self.url = None
        self._server = None
        self._thread = None

    def set_xml(self, path, xml, status=200):
        """Configure the XML response returned for GET requests to ``path``."""
        self.routes[path] = (status, xml)
        return self

    def start(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                length = int(self.headers.get('Content-Length', '0'))
                body = self.rfile.read(length) if length else b''
                request = HTTPRequest(self.command, self.path, dict(self.headers), body)
                owner.requests.append(request)

                request_path = urlsplit(self.path).path
                route = owner.routes.get(request_path)
                if route is None:
                    status = 404
                    payload = b'<MediaContainer message="Not Found"/>'
                else:
                    result = route(request) if callable(route) else route
                    if isinstance(result, tuple):
                        status, payload = result
                    else:
                        status, payload = 200, result

                if isinstance(payload, str):
                    data = payload.encode('utf-8')
                else:
                    data = bytes(payload)

                self.send_response(status)
                self.send_header('Content-Type', 'application/xml; charset=utf-8')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format, *args):
                pass

        self._server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self._server.daemon_threads = True
        self.url = f'http://127.0.0.1:{self._server.server_port}'
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._thread.join()
            self._server = None
            self._thread = None

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc_value, traceback):
        self.stop()
        return False


class FakeHTTPServer:
    """Local HTTP fake for MediaMTX and the delegation API.

    Routes map ``(method, path)`` to ``(status, body, headers)`` or to a
    callable accepting a recorded :class:`HTTPRequest` and returning that
    tuple. Bodies may be dictionaries (JSON) or strings. ``requests`` records
    ``(method, path, headers, body)`` tuples, with byte-string request bodies.
    """

    def __init__(self, routes, expected_auth=None):
        self.routes = routes
        self.expected_auth = expected_auth
        self.requests = []
        self.request_metadata = []
        self.url = None
        self._server = None
        self._thread = None

    def __enter__(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self):
                length = int(self.headers.get('Content-Length', '0'))
                body = self.rfile.read(length) if length else b''
                auth_header = self.headers.get('Authorization')
                redacted_auth = '[REDACTED]' if auth_header is not None else None
                owner.request_metadata.append({
                    'method': self.command,
                    'path': self.path,
                    'authorization': redacted_auth,
                    'body': body if body else None,
                })
                if owner.expected_auth is not None and auth_header != owner.expected_auth:
                    self.send_response(401)
                    self.send_header('Content-Type', 'text/plain; charset=utf-8')
                    self.send_header('Content-Length', '12')
                    self.end_headers()
                    if self.command != 'HEAD':
                        self.wfile.write(b'Unauthorized')
                    return
                request = HTTPRequest(self.command, self.path, dict(self.headers), body)
                owner.requests.append(request)
                route = owner.routes.get((self.command, urlsplit(self.path).path))
                if route is None:
                    status, response, headers = 404, 'Not Found', {}
                else:
                    status, response, headers = route(request) if callable(route) else route
                if isinstance(response, dict):
                    data = json.dumps(response).encode('utf-8')
                    content_type = 'application/json; charset=utf-8'
                elif isinstance(response, str):
                    data = response.encode('utf-8')
                    content_type = 'text/plain; charset=utf-8'
                else:
                    data = bytes(response)
                    content_type = 'application/octet-stream'
                self.send_response(status)
                header_names = {key.lower() for key in headers}
                if 'content-type' not in header_names:
                    self.send_header('Content-Type', content_type)
                if 'content-length' not in header_names:
                    self.send_header('Content-Length', str(len(data)))
                for key, value in headers.items():
                    self.send_header(key, str(value))
                self.end_headers()
                if self.command != 'HEAD':
                    self.wfile.write(data)

            def do_GET(self):
                self._handle()

            def do_POST(self):
                self._handle()

            def do_PUT(self):
                self._handle()

            def do_PATCH(self):
                self._handle()

            def do_DELETE(self):
                self._handle()

            def do_HEAD(self):
                self._handle()

            def log_message(self, format, *args):
                pass

        self._server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self._server.daemon_threads = True
        self.url = f'http://127.0.0.1:{self._server.server_port}'
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._thread.join()
            self._server = None
            self._thread = None
        return False

    class FakeSMTPServer:
        """A minimal fake SMTP server for testing email sending."""
        def __init__(self, host='localhost', port=25):
            self.host = host
            self.port = port
            self.sent_messages = []

        def sendmail(self, from_addr, to_addrs, msg):
            self.sent_messages.append((from_addr, to_addrs, msg))

        def quit(self):
            pass


def _default_snapcast_server():
    return {'host': '127.0.0.1', 'version': '0.27.0', 'name': 'Snapserver'}


def _default_snapcast_groups():
    return [
        {
            'id': 'group-0',
            'name': 'Default',
            'muted': False,
            'stream_id': 'stream-0',
            'clients': [
                {
                    'id': 'client-0',
                    'host': {'name': 'living-room', 'ip': '127.0.0.1'},
                    'connected': True,
                    'config': {'volume': {'muted': False, 'percent': 50}, 'latency': 0},
                },
            ],
        },
        {
            'id': 'group-1',
            'name': 'Kitchen',
            'muted': True,
            'stream_id': 'stream-0',
            'clients': [
                {
                    'id': 'client-1',
                    'host': {'name': 'kitchen', 'ip': '127.0.0.2'},
                    'connected': True,
                    'config': {'volume': {'muted': True, 'percent': 20}, 'latency': 5},
                },
                {
                    'id': 'client-2',
                    'host': {'name': 'patio', 'ip': '127.0.0.3'},
                    'connected': False,
                    'config': {'volume': {'muted': False, 'percent': 80}, 'latency': 10},
                },
            ],
        },
    ]


class FakeSnapcastTransport:
    """In-process loopback transport for :class:`FakeSnapcastServer`.

    ``request`` mirrors the Snapcast TCP wire protocol: it accepts one
    newline-terminated JSON-RPC message as bytes and returns the server's
    newline-terminated response as bytes, without ever opening a socket.
    """

    def __init__(self, server):
        self.server = server

    def request(self, payload):
        return self.server.handle_message(payload)


class FakeSnapcastServer:
    """In-process fake Snapcast JSON-RPC server with canned status data.

    By default :meth:`connect` returns a :class:`FakeSnapcastTransport` ready
    for :class:`forge_core.services.snapcast.SnapcastClient`. ``messages``
    records every decoded request. ``groups`` and ``clients`` expose the canned
    Server.GetStatus data (``clients`` flattens the clients nested in groups).
    """

    def __init__(self, groups=None, server=None):
        self.server = dict(server) if server is not None else _default_snapcast_server()
        self.groups = list(groups) if groups is not None else _default_snapcast_groups()
        self.messages = []

    @property
    def clients(self):
        clients = []
        for group in self.groups:
            clients.extend(group.get('clients', []))
        return clients

    def connect(self):
        return FakeSnapcastTransport(self)

    def handle_message(self, payload):
        if isinstance(payload, str):
            payload = payload.encode('utf-8')
        responses = []
        for line in payload.split(b'\n'):
            line = line.strip()
            if not line:
                continue
            message = json.loads(line.decode('utf-8'))
            self.messages.append(message)
            responses.append(self._respond(message))
        return b''.join(
            json.dumps(response).encode('utf-8') + b'\n' for response in responses
        )

    def _respond(self, message):
        method = message.get('method')
        message_id = message.get('id')
        if method == 'Server.GetRPCVersion':
            result = {'major': 2, 'minor': 0, 'patch': 0}
        elif method == 'Server.GetStatus':
            result = {'server': self.server, 'groups': self.groups}
        else:
            return {
                'id': message_id,
                'jsonrpc': '2.0',
                'error': {'code': -32601, 'message': f'Method not found: {method}'},
            }
        return {'id': message_id, 'jsonrpc': '2.0', 'result': result}
