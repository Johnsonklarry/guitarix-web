import unittest
from spike_latency import parse_jack_iodelay
from diagnose import get_period_ms

class TestLatencyBudget(unittest.TestCase):
    def test_parser(self):
        output = "320.000 frames    6.667 ms total roundtrip latency"
        record = parse_jack_iodelay(output, 48000)
        self.assertEqual(record["roundtrip_ms"], 6.667)

    def test_lookup(self):
        self.assertEqual(get_period_ms(64, 48000), 1.33)
        self.assertEqual(get_period_ms(128, 48000), 2.67)
        self.assertEqual(get_period_ms(256, 48000), 5.33)

if __name__ == "__main__":
    unittest.main()
