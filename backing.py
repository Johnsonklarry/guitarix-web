"""
Backing tracks: audio files you play along with.

A backing track goes straight to the interface outputs, alongside the amp,
so you hear both. It never goes near the amp's input.

"Include in takes" also feeds it to the recorder, so a take has the backing
in it rather than just your part. JACK mixes everything connected to an input,
so the recorder simply hears both. With it off, takes are your part alone,
which is what you want for anything you might reamp later.

Looping a take as a backing track is how the loop mode works: record a phrase,
then play it back on repeat and play over it.
"""

import json
import os
import re
import shutil
import subprocess

import jackutil
from player import Player, PlayerError
from reamp import OUTPUT_CLIENT, FALLBACK_OUTPUTS

BACKING_DIR = os.path.expanduser("~/backing")
EXTENSIONS = (".wav", ".mp3", ".flac", ".ogg", ".oga", ".m4a", ".aac", ".opus")
SAFE = re.compile(r"[^A-Za-z0-9 _.\-()&']")


class BackingError(Exception):
    pass


def safe_filename(name):
    base = os.path.basename(name or "").strip()
    stem, ext = os.path.splitext(base)
    stem = SAFE.sub("", stem)[:80].strip(" .")
    return (stem or "track") + ext.lower()


class Backing:
    def __init__(self, directory=BACKING_DIR, on_change=None):
        self.dir = os.path.abspath(os.path.expanduser(directory))
        os.makedirs(self.dir, exist_ok=True)
        self.on_change = on_change or (lambda: None)
        self.player = Player("gxweb-backing", on_change=self.on_change)
        self.include_in_takes = False
        self._source = None        # ("backing", name) or ("take", name)
        self._durations = {}

    # ------------------------------------------------------------ library

    def _resolve(self, name, directory=None):
        directory = directory or self.dir
        path = os.path.abspath(os.path.join(directory, os.path.basename(name or "")))
        if os.path.dirname(path) != directory or not os.path.isfile(path):
            return None
        return path

    def listing(self):
        items = []
        for entry in os.scandir(self.dir):
            if not entry.is_file() or not entry.name.lower().endswith(EXTENSIONS):
                continue
            stat = entry.stat()
            items.append({"name": entry.name, "size": stat.st_size,
                          "duration": self._duration(entry.path, stat.st_mtime)})
        items.sort(key=lambda i: i["name"].lower())
        return items

    def save_upload(self, filename, stream):
        name = safe_filename(filename)
        if not name.lower().endswith(EXTENSIONS):
            raise BackingError("That isn't an audio file this can play (%s)." % ", ".join(EXTENSIONS))
        stem, ext = os.path.splitext(name)
        path, n = os.path.join(self.dir, name), 2
        while os.path.exists(path):
            path = os.path.join(self.dir, "%s (%d)%s" % (stem, n, ext))
            n += 1
        with open(path, "wb") as f:
            shutil.copyfileobj(stream, f, 1024 * 1024)
        self.on_change()
        return os.path.basename(path)

    def delete(self, name):
        path = self._resolve(name)
        if not path:
            raise BackingError("No backing track called %s." % name)
        if self._source == ("backing", name) and self.player.playing:
            self.player.stop()
        os.remove(path)
        self.on_change()

    def _duration(self, path, mtime):
        cached = self._durations.get(path)
        if cached and cached[0] == mtime:
            return cached[1]
        seconds = None
        if shutil.which("ffprobe"):
            try:
                out = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json",
                                      "-show_format", path], capture_output=True, timeout=5)
                seconds = float(json.loads(out.stdout)["format"]["duration"])
            except (ValueError, KeyError, OSError, subprocess.TimeoutExpired):
                seconds = None
        self._durations[path] = (mtime, seconds)
        return seconds

    # ------------------------------------------------------------ playback

    def play(self, name, recorder, kind="backing", volume=80, loop=True):
        """
        Play a backing track -- or, with kind="take", one of your takes as a
        loop to play over. Goes to the outputs, and into a running recording
        too when "include in takes" is on.
        """
        directory = self.dir if kind == "backing" else recorder.dir
        path = self._resolve(name, directory)
        if not path:
            raise BackingError("Can't find %s." % name)
        targets = jackutil.ports(OUTPUT_CLIENT, "input", FALLBACK_OUTPUTS)
        if self.include_in_takes:
            targets += recorder.recorder_inputs()
        try:
            self.player.play(path, targets, volume=volume, loop=loop)
        except PlayerError as exc:
            raise BackingError(str(exc))
        self._source = (kind, name)
        self.on_change()

    def recording_started(self, recorder):
        """A take just started: feed it the backing too, if that's wanted."""
        if self.include_in_takes and self.player.playing:
            self.player.add_targets(recorder.recorder_inputs())

    def stop(self):
        self.player.stop()
        self._source = None

    def status(self):
        playback = self.player.status()
        return {
            "playing": bool(playback),
            "kind": self._source[0] if (playback and self._source) else None,
            "name": self._source[1] if (playback and self._source) else None,
            "position": (playback or {}).get("position"),
            "duration": (playback or {}).get("duration"),
            "paused": (playback or {}).get("paused", False),
            "volume": (playback or {}).get("volume", 80),
            "loop": (playback or {}).get("loop", False),
            "include": self.include_in_takes,
            "available": Player.installed(),
        }
