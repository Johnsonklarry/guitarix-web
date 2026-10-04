import os
import shutil
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JS = os.path.join(ROOT, 'static', 'app.js')


def read_source():
    with open(APP_JS, 'r', encoding='utf-8') as fh:
        return fh.read()


class TestFlashReducedMotion(unittest.TestCase):
    def test_node_check_passes(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('node is not installed')
        proc = subprocess.run(
            [node, '--check', APP_JS],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(
            proc.returncode, 0,
            'node --check failed:\n' + proc.stderr.decode('utf-8', 'replace'),
        )

    def test_flash_guards_on_reduced_motion(self):
        src = read_source()
        start = src.index('function flash(')
        end = src.index('/* ----', start)
        body = src[start:end]
        self.assertIn('prefers-reduced-motion', body)
        self.assertIn('matchMedia', body)


class TestTakeControlsAria(unittest.TestCase):
    def test_audio_player_is_labelled(self):
        self.assertIn('Playback for ', read_source())

    def test_more_button_is_expandable(self):
        self.assertIn('aria-expanded', read_source())


if __name__ == '__main__':
    unittest.main()
