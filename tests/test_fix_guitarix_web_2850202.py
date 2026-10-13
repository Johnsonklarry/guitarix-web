import json
import socket
import threading
import time
import unittest

from gx_rpc import GuitarixRPC


class FakeEngine:
    """Minimal line-delimited JSON-RPC server standing in for guitarix."""

    def __init__(self):
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(8)
        self.host, self.port = self._srv.getsockname()
        self._stop = threading.Event()
        self._conns = []
        self._lock = threading.Lock()
        self.received = []          # every method name the engine saw
        self.hold = threading.Event()  # when set, requests are not answered
        self.hold.set()
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def _accept_loop(self):
        self._srv.settimeout(0.2)
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with self._lock:
                self._conns.append(conn)
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        buf = b""
        conn.settimeout(0.2)
        while not self._stop.is_set():
            try:
                chunk = conn.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, _, buf = buf.partition(b"\n")
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line.decode("utf-8"))
                except ValueError:
                    continue
                method = msg.get("method")
                with self._lock:
                    self.received.append(method)
                if "id" not in msg:
                    continue
                # wait until the test releases the hold, then answer
                self.hold.wait(timeout=5.0)
                reply = {"jsonrpc": "2.0", "id": msg["id"],
                         "result": {"echo": method}}
                try:
                    conn.sendall((json.dumps(reply) + "\n").encode("utf-8"))
                except OSError:
                    break
        try:
            conn.close()
        except OSError:
            pass

    def drop_connections(self):
        """Close every live client socket, simulating an engine restart."""
        with self._lock:
            conns = list(self._conns)
            self._conns = []
        for conn in conns:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                conn.close()
            except OSError:
                pass

    def saw(self, method):
        with self._lock:
            return method in self.received

    def count(self, method):
        with self._lock:
            return self.received.count(method)

    def stop(self):
        self._stop.set()
        self.drop_connections()
        try:
            self._srv.close()
        except OSError:
            pass


class RpcReconnectTest(unittest.TestCase):
    def setUp(self):
        self.engine = FakeEngine()
        self.rpc = GuitarixRPC(host=self.engine.host, port=self.engine.port)
        self.rpc.start()
        self._wait_connected()

    def tearDown(self):
        try:
            self.rpc.stop()
        finally:
            self.engine.stop()

    def _wait_connected(self, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.rpc.connected:
                return
            time.sleep(0.01)
        self.fail("rpc never connected to the fake engine")

    def _wait_disconnected(self, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.rpc.connected:
                return
            time.sleep(0.01)
        self.fail("rpc never noticed the disconnect")

    def test_default_call_fails_on_disconnect(self):
        # Engine accepts the request but never answers, then the socket dies.
        self.engine.hold.clear()
        result = {}

        def do_call():
            try:
                result["value"] = self.rpc.call("get", ["system.current_bank"],
                                               timeout=5.0)
            except Exception as exc:  # noqa: BLE001 - recorded for assertion
                result["error"] = exc

        t = threading.Thread(target=do_call, daemon=True)
        t.start()
        time.sleep(0.2)
        self.engine.drop_connections()
        t.join(timeout=5.0)
        self.assertFalse(t.is_alive(), "default call hung after disconnect")
        self.assertIn("error", result,
                      "default call should fail when the connection drops")
        self.assertNotIn("value", result)

    def test_opted_in_call_completes_after_reconnect(self):
        self.engine.hold.clear()
        result = {}

        def do_call():
            try:
                result["value"] = self.rpc.call("get", ["system.current_bank"],
                                               timeout=10.0, reconnect=True)
            except Exception as exc:  # noqa: BLE001
                result["error"] = exc

        t = threading.Thread(target=do_call, daemon=True)
        t.start()
        time.sleep(0.2)
        self.engine.drop_connections()
        self._wait_disconnected()
        self._wait_connected()
        # let the retried request be answered
        self.engine.hold.set()
        t.join(timeout=10.0)
        self.assertFalse(t.is_alive(), "opted-in call never completed")
        self.assertNotIn("error", result,
                         "opted-in call should survive a reconnect: %r"
                         % (result.get("error"),))
        self.assertEqual(result.get("value"), {"echo": "get"})

    def test_expired_call_not_sent_later_nor_resolved_by_stale_reply(self):
        # Hold replies so the call times out; the engine must not see a
        # second send, and a late reply must not resolve anything.
        self.engine.hold.clear()
        with self.assertRaises(TimeoutError):
            self.rpc.call("get", ["system.current_bank"], timeout=0.3)
        before = self.engine.count("get")
        # Release the held reply: it is stale and must be discarded.
        self.engine.hold.set()
        time.sleep(0.5)
        self.assertEqual(self.engine.count("get"), before,
                         "expired call was re-sent after its deadline")
        # A fresh call must get its own answer, not the stale one.
        value = self.rpc.call("get", ["system.current_bank"], timeout=5.0)
        self.assertEqual(value, {"echo": "get"})


if __name__ == "__main__":
    unittest.main()
