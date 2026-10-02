import os
import unittest
from tests.audit_fakes import main

class TestFakeFlags(unittest.TestCase):
    def test_audit_fakes(self):
        tests_dir = os.path.dirname(os.path.abspath(__file__))
        fakes_bin = os.path.join(tests_dir, "fakes", "bin")
        ffmpeg = os.path.join(fakes_bin, "ffmpeg")
        ffprobe = os.path.join(fakes_bin, "ffprobe")
        with open(ffmpeg, "r", encoding="utf-8") as f:
            ffmpeg_content = f.read()
        for flag in ("-devices", "-encoders", "-flush_packets", "-hide_banner"):
            self.assertIn(flag, ffmpeg_content, f"ffmpeg missing {flag}")
        with open(ffprobe, "r", encoding="utf-8") as f:
            ffprobe_content = f.read()
        for flag in ("-show_format",):
            self.assertIn(flag, ffprobe_content, f"ffprobe missing {flag}")

if __name__ == "__main__":
    unittest.main()
