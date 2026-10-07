"""Regression tests for issue #26403: the latency report and the mpv flag schema.

Run with:  python3 -m pytest tests/test_bash_26403.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import diagnose
import player


class PeriodMsTest(unittest.TestCase):
    """diagnose.py's period-ms lookup."""

    def test_lookup_matches_the_jack_periods_the_report_quotes(self):
        self.assertEqual(diagnose.period_ms(64), 1.33)
        self.assertEqual(diagnose.period_ms(128), 2.67)
        self.assertEqual(diagnose.period_ms(256), 5.33)

    def test_the_period_table_holds_the_same_numbers(self):
        self.assertEqual(diagnose.PERIOD_MS[64], 1.33)
        self.assertEqual(diagnose.PERIOD_MS[128], 2.67)
        self.assertEqual(diagnose.PERIOD_MS[256], 5.33)

    def test_another_sample_rate_is_honoured(self):
        self.assertEqual(diagnose.period_ms(64, 44100), 1.45)

    def test_nonsense_periods_are_refused(self):
        for args in ((0,), (-64,), (64, 0), (64, -48000)):
            with self.assertRaises(ValueError):
                diagnose.period_ms(*args)


class ReportTest(unittest.TestCase):
    """diagnose.py's report builder."""

    def sample_records(self):
        return {
            "guitar": {"measured_ms": 8.5, "budget_ms": 10.0, "xruns": 0},
            "backing/reamp": {"measured_ms": 24.0, "budget_ms": 20.0, "xruns": 2,
                              "detail": "mpv command to first sound"},
            "browser/listening": {"measured_ms": 35.0, "budget_ms": 40.0, "xruns": 1},
        }

    def test_report_covers_all_three_budgets(self):
        report = diagnose.build_report(self.sample_records())
        # The whole section heading, not a substring a measurement line could
        # contain by accident.
        for title in diagnose.BUDGET_TITLES.values():
            self.assertIn("### " + title, report)

    def test_report_shows_measurements_and_budgets(self):
        report = diagnose.build_report(self.sample_records())
        self.assertIn("measured 8.50 ms", report)
        self.assertIn("budget 10.00 ms", report)
        self.assertIn("OVER BUDGET", report)
        self.assertIn("mpv command to first sound", report)

    def test_report_totals_the_xruns_it_was_given(self):
        report = diagnose.build_report(self.sample_records())
        self.assertIn("Total xruns: 3", report)
        self.assertIn("xruns: 2", report)

    def test_report_honours_an_explicit_xrun_total(self):
        self.assertIn("Total xruns: 9", diagnose.build_report({}, xruns=9))

    def test_report_carries_rollback_instructions(self):
        report = diagnose.build_report(self.sample_records())
        self.assertIn("Rollback instructions", report)
        self.assertIn("128 frames", report)
        self.assertIn("2.67 ms", report)
        # the over-budget budget is named in the rollback note, by its full
        # title and with the numbers it went over by
        rollback = report.split("Rollback instructions")[1]
        self.assertIn("Over budget:", rollback)
        self.assertIn("backing/reamp (command to sound) is 24.00 ms "
                      "against a 20.00 ms budget", rollback)

    def test_unmeasured_budgets_still_get_a_section(self):
        report = diagnose.build_report({"guitar": 8.0})
        self.assertIn("guitar path", report)
        self.assertIn("backing/reamp", report)
        self.assertIn("browser listening", report)
        self.assertIn("not measured", report)

    def test_loose_budget_names_land_in_the_right_section(self):
        report = diagnose.build_report([{"name": "backing track", "measured_ms": 12.0}])
        self.assertIn("backing/reamp", report)
        self.assertIn("measured 12.00 ms", report)

    def test_a_budget_can_be_passed_by_keyword(self):
        report = diagnose.build_report(guitar={"measured_ms": 7.25, "budget_ms": 10.0})
        self.assertIn("measured 7.25 ms", report)
        self.assertIn("within budget", report)

    def test_the_report_is_reproducible(self):
        first = diagnose.build_report(self.sample_records())
        second = diagnose.build_report(self.sample_records())
        self.assertEqual(first, second)
        self.assertTrue(first.endswith("\n"))

    def test_a_malformed_xrun_count_cannot_take_down_the_report(self):
        report = diagnose.build_report({
            "guitar": {"measured_ms": 8.0, "xruns": "n/a"},
            "backing/reamp": {"measured_ms": 9.0, "xruns": None},
            "browser/listening": {"measured_ms": 10.0, "xruns": "3"},
        })
        self.assertIn("Total xruns: 3", report)
        self.assertIn("xruns: 0", report)

    def test_a_second_record_for_one_budget_is_kept_not_dropped(self):
        report = diagnose.build_report([
            {"name": "guitar", "measured_ms": 8.0},
            {"name": "guitar path", "measured_ms": 9.0},
        ])
        # the first record holds the section ...
        self.assertIn("measured 8.00 ms", report.split("## Other measurements")[0])
        # ... and the second is printed rather than swallowed
        other = report.split("## Other measurements")[1]
        self.assertIn("measured 9.00 ms", other)
        self.assertIn("guitar path (second guitar record)", other)

    def test_a_name_that_only_contains_a_budget_name_is_left_alone(self):
        report = diagnose.build_report([{"name": "backingtrack latency",
                                         "measured_ms": 3.0}])
        self.assertIn("## Other measurements", report)
        self.assertIn("backingtrack latency",
                      report.split("## Other measurements")[1])

    def test_xruns_from_other_measurements_count_towards_the_total(self):
        report = diagnose.build_report([
            {"name": "guitar", "measured_ms": 8.0, "xruns": 1},
            {"name": "usb interface", "measured_ms": 2.0, "xruns": 4},
        ])
        self.assertIn("Total xruns: 5", report)


class MpvFlagSchemaTest(unittest.TestCase):
    """player.py's mpv audio-buffer and JACK flag schema."""

    def test_the_flags_player_builds_are_all_documented(self):
        args = player.mpv_args("gxweb-bench", "/tmp/bench.sock", "/tmp/take.wav", 80, True)
        self.assertTrue(player.validate_mpv_flags(args))

    def test_the_benchmark_flags_are_accepted(self):
        accepted = [
            "--ao=jack",
            "--jack-name=gxweb-bench",
            "--jack-port=system:playback_1",
            "--audio-buffer=0.2",
            "--input-ipc-server=/tmp/bench.sock",
            "--jack-connect=no",
            "--jack-autostart=no",
            "--pause",
            "--volume=80",
            "--loop-file=inf",
        ]
        self.assertTrue(player.validate_mpv_flags(accepted))

    def test_an_audio_buffer_reaches_the_command_line(self):
        args = player.mpv_args("gxweb-bench", "/tmp/bench.sock", "/tmp/take.wav", 100, False,
                               audio_buffer=0.1)
        self.assertIn("--audio-buffer=0.1", args)
        self.assertEqual(args[-1], "/tmp/take.wav")
        self.assertTrue(player.validate_mpv_flags(args))

    def test_without_an_audio_buffer_nothing_changes(self):
        plain = player.mpv_args("gxweb-bench", "/tmp/bench.sock", "/tmp/take.wav", 100, False)
        self.assertFalse([a for a in plain if a.startswith("--audio-buffer")])

    def test_undocumented_flags_are_rejected(self):
        for flag in ("--jack-nam=x", "--turbo-latency", "--audio-buffer-seconds=1",
                     "--no-such-flag", "--jack-autostart=maybe"):
            with self.assertRaises(ValueError):
                player.validate_mpv_flags([flag])

    def test_a_documented_flag_with_the_wrong_value_is_rejected(self):
        with self.assertRaises(ValueError):
            player.validate_mpv_flags(["--ao=alsa"])
        with self.assertRaises(ValueError):
            player.validate_mpv_flags(["--audio-buffer=soon"])

    def test_the_file_to_play_is_not_treated_as_a_flag(self):
        self.assertTrue(player.validate_mpv_flags(["--pause", "/tmp/take.wav"]))

    def test_a_non_string_flag_is_rejected(self):
        with self.assertRaises(ValueError):
            player.validate_mpv_flags([42])


if __name__ == "__main__":
    unittest.main()
