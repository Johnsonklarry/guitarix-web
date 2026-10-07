import unittest

from diagnose import get_period_ms, period_ms
from spike_latency import parse_jack_iodelay


class TestLatencyBudget(unittest.TestCase):
    def test_parser(self):
        output = "320.000 frames    6.667 ms total roundtrip latency"
        record = parse_jack_iodelay(output)
        self.assertEqual(record["roundtrip_ms"], 6.667)

    def test_parser_reports_nothing_when_there_is_nothing(self):
        # a parse that found no roundtrip line is not a 0 ms measurement
        self.assertIsNone(parse_jack_iodelay("jack_iodelay: could not connect to JACK"))
        self.assertIsNone(parse_jack_iodelay(""))
        self.assertIsNone(parse_jack_iodelay(None))

    def test_parser_rounds_nothing_and_invents_nothing(self):
        record = parse_jack_iodelay("320.000 frames    6.667 ms total roundtrip latency")
        # only what the output carried: no period size, no xrun count claimed
        self.assertEqual(set(record), {"roundtrip_ms"})
        self.assertEqual(record["roundtrip_ms"], 6.667)

    def test_lookup(self):
        self.assertEqual(get_period_ms(64, 48000), 1.33)
        self.assertEqual(get_period_ms(128, 48000), 2.67)
        self.assertEqual(get_period_ms(256, 48000), 5.33)

    def test_lookup_matches_the_reporting_helper(self):
        for frames in (16, 32, 64, 128, 256, 512, 1024):
            self.assertEqual(get_period_ms(frames, 48000), period_ms(frames, 48000))
        # a bad rate is the ValueError period_ms documents, not a ZeroDivisionError
        self.assertRaises(ValueError, get_period_ms, 128, 0)
        self.assertRaises(ValueError, get_period_ms, 0, 48000)


if __name__ == "__main__":
    unittest.main()
