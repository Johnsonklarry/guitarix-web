"""Regression tests for the benchmark result formatter and its telemetry points.

Issue #26805 (#239): diagnose.py exposes a utility that formats benchmark
results as markdown, and folds the telemetry data points a test or benchmark
run measured into that report.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import diagnose


class TelemetryPointTests(unittest.TestCase):
    """A telemetry data point is plain data, and it invents no numbers."""

    def test_point_is_plain_json_shaped_data(self):
        point = diagnose.telemetry_point("guitar", measured_ms=8.5, budget_ms=10.0,
                                         xruns=2, detail="cold strings")
        self.assertEqual(point["name"], "guitar")
        self.assertEqual(point["measured_ms"], 8.5)
        self.assertEqual(point["budget_ms"], 10.0)
        self.assertEqual(point["xruns"], 2)
        self.assertEqual(point["detail"], "cold strings")
        # a point survives a round trip through JSON, so a benchmark in
        # another process can hand one over without importing this module
        self.assertEqual(json.loads(json.dumps(point)), point)

    def test_point_names_the_test_it_came_from(self):
        point = diagnose.telemetry_point("reamp", 12.0, 10.0, test="benchmark_reamp")
        self.assertEqual(point["detail"], "from benchmark_reamp")
        point = diagnose.telemetry_point("reamp", 12.0, detail="after warmup",
                                         test="benchmark_reamp")
        self.assertEqual(point["detail"], "after warmup (from benchmark_reamp)")

    def test_point_without_a_name_is_refused(self):
        for bad_name in (None, "", "   "):
            with self.assertRaises(ValueError):
                diagnose.telemetry_point(bad_name)

    def test_unreadable_numbers_are_dropped_not_crashed(self):
        point = diagnose.telemetry_point("listening", measured_ms="n/a",
                                         budget_ms=None, xruns="n/a")
        self.assertNotIn("measured_ms", point)
        self.assertNotIn("budget_ms", point)
        self.assertEqual(point["xruns"], 0)


class FormatBenchmarkResultTests(unittest.TestCase):
    """The formatter: measurements and telemetry in, markdown out."""

    def test_measurements_are_formatted_with_their_budget(self):
        report = diagnose.format_benchmark_result(
            {"guitar": {"measured_ms": 8.5, "budget_ms": 10.0}})
        self.assertIn("Sample rate 48000 Hz, period 128 frames (2.67 ms per period).",
                      report)
        self.assertIn("### guitar path (string to speaker)", report)
        self.assertIn("measured 8.50 ms, budget 10.00 ms -- within budget", report)
        self.assertIn("## Rollback instructions", report)
        self.assertIn("Nothing is over budget", report)

    def test_telemetry_point_lands_in_its_budget_section(self):
        report = diagnose.format_benchmark_result(
            telemetry=[diagnose.telemetry_point("reamp", 12.0, 10.0, xruns=2,
                                                test="benchmark_reamp")])
        self.assertIn("### backing/reamp (command to sound)", report)
        self.assertIn("measured 12.00 ms, budget 10.00 ms -- OVER BUDGET", report)
        self.assertIn("from benchmark_reamp", report)
        self.assertIn("xruns: 2", report)
        self.assertIn("Total xruns: 2", report)
        self.assertIn("- backing/reamp (command to sound): 2", report)

    def test_telemetry_that_names_no_budget_is_still_reported(self):
        report = diagnose.format_benchmark_result(
            telemetry=[diagnose.telemetry_point("cold start", 3.0,
                                                test="benchmark_cold")])
        self.assertIn("## Other measurements", report)
        self.assertIn("### cold start", report)
        self.assertIn("measured 3.00 ms", report)

    def test_a_second_telemetry_point_for_a_budget_does_not_clobber_it(self):
        report = diagnose.format_benchmark_result(
            {"guitar": {"measured_ms": 8.5, "budget_ms": 10.0}},
            telemetry=[diagnose.telemetry_point("guitar", 30.0, 10.0)])
        guitar_section = report.split("## Other measurements")[0]
        self.assertIn("measured 8.50 ms", guitar_section)
        self.assertNotIn("30.00", guitar_section)
        self.assertIn("### guitar (second guitar record)", report)

    def test_a_lone_point_is_accepted_without_a_list(self):
        report = diagnose.format_benchmark_result(
            telemetry=diagnose.telemetry_point("listening", 4.0, 5.0))
        self.assertIn("measured 4.00 ms, budget 5.00 ms -- within budget", report)

    def test_total_xruns_adds_records_and_telemetry(self):
        report = diagnose.format_benchmark_result(
            {"guitar": {"measured_ms": 8.0, "xruns": 1}},
            telemetry=[diagnose.telemetry_point("reamp", 12.0, 10.0, xruns=2),
                       diagnose.telemetry_point("listening", 4.0, 5.0, xruns=3)])
        self.assertIn("Total xruns: 6", report)
        self.assertIn("- guitar path (string to speaker): 1", report)
        self.assertIn("- backing/reamp (command to sound): 2", report)
        self.assertIn("- browser listening (amp to headphones): 3", report)

    def test_the_named_utility_and_its_aliases_agree(self):
        telemetry = [diagnose.telemetry_point("listening", 4.0, 5.0, xruns=1)]
        expected = diagnose.format_benchmark_result(telemetry=telemetry)
        self.assertEqual(diagnose.build_report(telemetry=telemetry), expected)
        self.assertEqual(diagnose.generate_report(telemetry=telemetry), expected)


if __name__ == "__main__":
    unittest.main()
