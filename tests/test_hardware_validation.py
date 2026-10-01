import json
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from benchmarks.hardware_validation import (
    compare_static,
    inventory,
    main,
    memory_window,
    summarize_sample,
)
from gpuroster.monitoring.collectors import CollectionError
from gpuroster.monitoring.models import GPU, GPUProcess


class HardwareValidationTests(unittest.TestCase):
    def setUp(self):
        self.device = GPU(0, "PRIVATE_DEVICE", "PRIVATE_NAME", 50, 12, 100, 40, 30)
        self.process = GPUProcess(
            "PRIVATE_DEVICE", 0, 912345, "PRIVATE_USER", "PRIVATE_COMMAND", 12
        )

    def test_report_omits_all_device_and_process_identities(self):
        report = summarize_sample((self.device,), (self.process,))
        self.assertTrue(report["valid"])
        self.assertEqual(report["processes"], 1)
        serialized = json.dumps(report)
        self.assertNotIn("PRIVATE", serialized)
        self.assertNotIn("912345", serialized)

    def test_invalid_metrics_and_process_mapping_fail(self):
        for device in (
            replace(self.device, utilization=101),
            replace(self.device, power=float("nan")),
            replace(self.device, memory_used=101),
        ):
            with self.subTest(device=device):
                self.assertFalse(summarize_sample((device,), ())["valid"])
        self.assertFalse(summarize_sample((self.device, self.device), ())["valid"])
        self.assertFalse(
            summarize_sample((self.device,), (replace(self.process, gpu=3),))["valid"]
        )
        self.assertFalse(summarize_sample((), ())["valid"])

    def test_unavailable_metrics_and_permissions_are_counted_without_zero_fill(self):
        report = summarize_sample(
            (replace(self.device, utilization=None, power=None),),
            (replace(self.process, user="unknown", mem_mb=None),),
        )
        self.assertTrue(report["valid"])
        self.assertEqual(report["null_metrics"]["utilization"], 1)
        self.assertEqual(report["null_metrics"]["power"], 1)
        self.assertEqual(report["unknown_process_users"], 1)

    def test_static_comparison_uses_uuid_and_tolerates_only_mib_rounding(self):
        right = replace(self.device, index=7, utilization=90, memory_total=100.9)
        self.assertTrue(compare_static((self.device,), (right,))["memory_totals_match"])
        self.assertFalse(
            compare_static((self.device,), (replace(right, memory_total=102),))[
                "memory_totals_match"
            ]
        )
        self.assertFalse(
            compare_static((self.device,), (replace(right, uuid="another"),))[
                "identities_match"
            ]
        )
        missing = compare_static((self.device,), (replace(right, memory_total=None),))
        self.assertEqual(missing["memory_totals_compared"], 0)

    def test_inventory_aggregates_modes_and_rejects_unexpected_identifiers(self):
        result = inventory(
            Mock(return_value="590.48.01, Disabled, Enabled\n590.48.01, [N/A], [N/A]")
        )
        self.assertEqual(result["mig_current"], {"disabled": 1, "unavailable": 1})
        self.assertEqual(result["mig_pending"], {"enabled": 1, "unavailable": 1})
        with self.assertRaisesRegex(CollectionError, "invalid_output"):
            inventory(Mock(return_value="PRIVATE_HOST, Disabled, Disabled"))

    def test_memory_window_excludes_warmup_and_detects_growth(self):
        samples = [
            {
                "seconds": second,
                "parent": {"uss_bytes": size},
                "worker": {"uss_bytes": size},
            }
            for second, size in ((0, 0), (60, 20 * 1048576), (120, 20 * 1048576))
        ]
        self.assertTrue(memory_window(samples)["worker"]["within_memory_threshold"])
        samples[-1]["worker"]["uss_bytes"] += 2 * 1048576
        self.assertFalse(memory_window(samples)["worker"]["within_memory_threshold"])
        self.assertFalse(
            memory_window(samples[:1])["worker"]["within_memory_threshold"]
        )

    def test_cli_hides_driver_exception_text_and_exits_nonzero(self):
        with (
            patch("sys.argv", ["hardware_validation"]),
            patch(
                "benchmarks.hardware_validation.run",
                side_effect=RuntimeError("PRIVATE_DRIVER_DATA"),
            ),
            patch("builtins.print") as output,
        ):
            with self.assertRaises(SystemExit) as raised:
                main()
        self.assertEqual(raised.exception.code, 1)
        self.assertNotIn("PRIVATE", str(output.call_args))
        self.assertEqual(
            json.loads(output.call_args.args[0])["error"], "hardware_validation_failed"
        )


if __name__ == "__main__":
    unittest.main()
