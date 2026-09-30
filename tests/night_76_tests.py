#!/usr/bin/env python3
"""Regression tests for a reamp ending while a recorded pass starts."""

import importlib.util
import os
import sys
import threading
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Recorder:
    def __init__(self):
        self.recording = False
        self.starts = 0
        self.stops = 0
        self.entered = None
        self.release = None

    def _resolve(self, name):
        return "/tmp/dry.wav"

    def start(self, name=None):
        self.starts += 1
        if self.entered:
            self.entered.set()
            if not self.release.wait(2):
                raise RuntimeError("test timed out waiting to release start")
        self.recording = True
        return {"file": "render.wav"}

    def stop(self):
        self.stops += 1
        self.recording = False


class Night76Tests(unittest.TestCase):
    def setUp(self):
        self.sources = {"guitar"}
        jack = types.ModuleType("jackutil")
        jack.available = lambda: True
        jack.ports = lambda client, direction, *args: ["amp:in"]
        jack.connections = lambda port: list(self.sources)

        def disconnect(source, port):
            self.sources.discard(source)

        def connect(source, port):
            self.sources.add(source)
            return True

        jack.disconnect = disconnect
        jack.connect = connect

        player = types.ModuleType("player")

        class PlayerError(Exception):
            pass

        class Player:
            def __init__(self, name, on_change, on_end):
                self.on_end = on_end
                self.end_on_play = False

            def play(self, path, targets, loop=False):
                if self.end_on_play:
                    self.on_end("finished")

            def stop(self):
                self.on_end("stopped")

        player.Player = Player
        player.PlayerError = PlayerError
        recorder = types.ModuleType("recorder")
        recorder.SOURCE_CLIENT = "amp"

        with mock.patch.dict(sys.modules, {
            "jackutil": jack, "player": player, "recorder": recorder,
        }):
            spec = importlib.util.spec_from_file_location(
                "night_76_reamp", os.path.join(ROOT, "reamp.py"))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        self.recorder = Recorder()
        self.reamp = module.Reamp(self.recorder)
        self.ReampError = module.ReampError

    def test_end_before_recording_starts(self):
        self.reamp.player.end_on_play = True
        with self.assertRaises(self.ReampError):
            self.reamp.start("take.wav", "dry.wav", record=True)
        self.assertEqual(self.recorder.starts, 0)
        self.assertFalse(self.recorder.recording)
        self.assertFalse(self.reamp.active)
        self.assertEqual(self.sources, {"guitar"})

    def test_end_during_recording_start(self):
        self.recorder.entered = threading.Event()
        self.recorder.release = threading.Event()
        finished = threading.Event()
        errors = []
        outputs = []

        def start():
            try:
                outputs.append(self.reamp.start("take.wav", "dry.wav", record=True))
            except Exception as exc:
                errors.append(exc)

        def end():
            self.reamp._ended("finished")
            finished.set()

        starter = threading.Thread(target=start)
        ending = threading.Thread(target=end)
        starter.start()
        try:
            self.assertTrue(self.recorder.entered.wait(2))
            ending.start()
            self.recorder.release.set()
            starter.join(2)
            ending.join(2)
            self.assertFalse(starter.is_alive())
            self.assertFalse(ending.is_alive())
            self.assertFalse(errors, errors)
            self.assertEqual(outputs, ["render.wav"])
            self.assertTrue(finished.is_set())
            self.assertEqual(self.recorder.stops, 1)
            self.assertFalse(self.recorder.recording)
            self.assertFalse(self.reamp.active)
            self.assertEqual(self.sources, {"guitar"})
        finally:
            self.recorder.release.set()
            starter.join(2)
            if ending.ident is not None:
                ending.join(2)


if __name__ == "__main__":
    unittest.main()
