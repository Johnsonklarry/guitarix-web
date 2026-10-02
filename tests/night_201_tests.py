import unittest
from unittest.mock import MagicMock
import spike_latency


class TestSpikeLatencyErrorHandling(unittest.TestCase):
    def test_send_error_callback_on_timeout(self):
        # Verify that send handles socketio.emit error/acknowledgement callback on timeout
        error_callback_called = []

        def fake_emit(event, data, callback=None, **kwargs):
            # simulate timeout / no ack calling error callback or callback with error
            if "timeout" in kwargs and "error_callback" in kwargs:
                kwargs["error_callback"]()
                error_callback_called.append(True)

        original_emit = spike_latency.socketio.emit
        spike_latency.socketio.emit = MagicMock(side_effect=fake_emit)
        spike_latency.state["listeners"] = 1

        try:
            spike_latency.send(1, b"test_buffer")
            self.assertTrue(error_callback_called, "Error callback was not called on timeout")
        finally:
            spike_latency.socketio.emit = original_emit
            spike_latency.state["listeners"] = 0


if __name__ == "__main__":
    unittest.main()
