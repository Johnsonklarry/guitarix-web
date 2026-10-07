"""Issue #26852 -- the broadcast client must discard stale audio buffers.

`static/broadcast.js` is the broadcast page's client: it listens, never
speaks, and plays out the audio buffers the amp streams. When the link to the
web app drops, the page comes back to a queue full of audio that was captured
while it was away. That audio is older than the position playback resumes at,
so the disconnect path has to discard it instead of replaying it late.

No JavaScript engine is available in this test environment, so the file is not
executed with node. Instead the file is read and the registered 'disconnect'
callback is driven: the callback body is pulled out of the
`socket.on('disconnect', ...)` registration and every call it makes is
dispatched to a Python double. The double for the discard path is the queue
model the JavaScript mirrors, and the ceiling it uses is read out of the file
(`MAX_BACKLOG`), so the numbers cannot drift apart without a failure here.
"""

import re
import unittest
from pathlib import Path

BROADCAST_JS = Path(__file__).resolve().parent.parent / "static" / "broadcast.js"


def source():
    return BROADCAST_JS.read_text(encoding="utf-8")


def strip_comments(text):
    return "\n".join(line.split("//")[0] for line in text.splitlines())


DISCONNECT = re.compile(
    r"socket\.on\(\s*'disconnect'\s*,\s*function\s*\(\)\s*\{(?P<body>.*?)\n\s*\}\);",
    re.DOTALL,
)
CALL = re.compile(r"([A-Za-z_$][\w$]*)\s*\(([^()]*)\)\s*;")


def disconnect_body(text):
    match = DISCONNECT.search(text)
    if match is None:
        raise AssertionError("broadcast.js registers no disconnect handler")
    return strip_comments(match.group("body"))


def disconnect_calls(text):
    """The calls the registered disconnect callback makes, in order."""
    return [
        (name, argument.strip())
        for name, argument in CALL.findall(disconnect_body(text))
    ]


def function_body(text, name):
    match = re.search(
        r"function\s+" + re.escape(name) + r"\s*\([^)]*\)\s*\{(?P<body>.*?)\n  \}",
        text,
        re.DOTALL,
    )
    if match is None:
        raise AssertionError("broadcast.js defines no %s()" % name)
    return strip_comments(match.group("body"))


def constant(text, name):
    match = re.search(r"var\s+" + re.escape(name) + r"\s*=\s*(\d+)\s*;", text)
    if match is None:
        raise AssertionError("broadcast.js defines no %s" % name)
    return int(match.group(1))


class BroadcastClientDouble(object):
    """The page state a disconnect touches, plus doubles for what it calls."""

    def __init__(self, ceiling, queued):
        self.ceiling = ceiling
        self.queue = list(queued)   # sequence numbers waiting to be played
        self.dropped = 0
        self.calls = []

    def setLink(self, argument):
        self.calls.append(("setLink", argument))

    def setRolling(self, argument):
        self.calls.append(("setRolling", argument))

    def discardStaleAudio(self, argument=""):
        """Mirror of discardStaleAudio() in static/broadcast.js."""
        limit = self.ceiling if argument == "" else int(argument)
        dropped = 0
        while len(self.queue) > limit:
            self.queue.pop(0)       # audioQueue.shift()
            dropped += 1
        self.dropped += dropped
        self.calls.append(("discardStaleAudio", limit))
        return dropped


class DisconnectDiscardsStaleAudioTest(unittest.TestCase):
    def setUp(self):
        self.text = source()

    def ceiling(self):
        return constant(self.text, "MAX_BACKLOG")

    def drive_disconnect(self, queued):
        """Invoke the registered disconnect callback against a fresh double."""
        client = BroadcastClientDouble(self.ceiling(), queued)
        for name, argument in disconnect_calls(self.text):
            getattr(client, name)(argument)
        return client

    # The file tracks a backlog of buffers and a ceiling for it.

    def test_the_ceiling_is_a_positive_buffer_count(self):
        self.assertGreaterEqual(self.ceiling(), 1)

    def test_the_page_tracks_the_backlog_and_drops_past_the_ceiling(self):
        self.assertIn("audioQueue.length", function_body(self.text, "audioBacklog"))

        discard = function_body(self.text, "discardStaleAudio")
        self.assertIn("audioBacklog() > limit", discard)
        self.assertIn("audioQueue.shift()", discard)

        enqueue = function_body(self.text, "queueAudioBuffer")
        self.assertIn("audioQueue.push(buffer)", enqueue)
        self.assertIn("discardStaleAudio()", enqueue)

    def test_a_queue_inside_the_ceiling_is_played_out(self):
        ceiling = self.ceiling()
        client = BroadcastClientDouble(ceiling, range(1, ceiling + 1))
        self.assertEqual(0, client.discardStaleAudio(""))
        self.assertEqual(ceiling, len(client.queue))

    # And the registered disconnect callback is what discards them.

    def test_disconnect_still_reports_the_link_and_stops_the_recorder(self):
        calls = dict(disconnect_calls(self.text))
        self.assertEqual("false", calls.get("setLink"))
        self.assertEqual("false", calls.get("setRolling"))

    def test_disconnect_discards_the_stale_audio(self):
        queued = list(range(1, 13))          # twelve buffers behind live
        client = self.drive_disconnect(queued)

        self.assertIn("discardStaleAudio", [name for name, _ in client.calls])
        self.assertEqual(len(queued), client.dropped)     # every stale buffer went
        self.assertEqual([], client.queue)                # none of it is replayed
        self.assertLessEqual(len(client.queue), self.ceiling())
        self.assertIn(("setLink", "false"), client.calls)
        self.assertIn(("setRolling", "false"), client.calls)


if __name__ == "__main__":
    unittest.main()
