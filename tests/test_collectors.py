import unittest
from unittest.mock import Mock, patch

from monitoring.collectors import CollectionError, NvidiaSMI, SystemCollector


class CollectorTests(unittest.TestCase):
    def test_uuid_survives_index_changes_and_unsupported_values(self):
        runner = Mock(
            return_value='2, GPU-example, "Example, GPU", N/A, 10, 100, 40, N/A'
        )
        device = NvidiaSMI(runner).gpus()[0]
        self.assertEqual(device.uuid, "GPU-example")
        self.assertEqual(device.index, 2)
        self.assertEqual(device.name, "Example, GPU")
        self.assertIsNone(device.utilization)
        self.assertIsNone(device.power)

    def test_processes_use_sampled_identity_without_an_extra_query(self):
        runner = Mock(
            side_effect=[
                "2, GPU-example, Example, 42, 10, 100, 40, 50",
                "123, GPU-example, N/A\n124, GPU-unmapped, 20",
            ]
        )
        provider = NvidiaSMI(runner)
        process = Mock()
        process.username.return_value = "example-user"
        process.name.return_value = "python"
        with patch("monitoring.collectors.psutil.Process", return_value=process):
            processes = provider.processes(provider.gpus())
        self.assertEqual(runner.call_count, 2)
        self.assertEqual(processes[0].gpu, 2)
        self.assertIsNone(processes[0].mem_mb)
        self.assertIsNone(processes[1].gpu)
        self.assertEqual(processes[1].gpu_uuid, "GPU-unmapped")
        process.cmdline.assert_not_called()

    def test_invalid_metrics_and_duplicate_identity_are_rejected(self):
        for output in (
            "0, GPU-example, Example, 101, 10, 100, 40, 50",
            "0, GPU-example, Example, NaN, 10, 100, 40, 50",
            "-1, GPU-example, Example, 20, 10, 100, 40, 50",
            "0, GPU-example, Example, 20, 10, 100, 40, 50\n" * 2,
        ):
            with self.subTest(output=output), self.assertRaises(CollectionError):
                NvidiaSMI(Mock(return_value=output)).gpus()

    def test_cpu_warms_up_and_too_short_intervals_are_unknown(self):
        collector = SystemCollector()
        with (
            patch("monitoring.collectors.time.monotonic", side_effect=[1, 4, 4.01]),
            patch("monitoring.collectors.psutil.cpu_percent", side_effect=[0, 25, 0]),
            patch(
                "monitoring.collectors.psutil.virtual_memory",
                return_value=Mock(used=100, total=1000, percent=10),
            ),
        ):
            self.assertIsNone(collector.collect().cpu_percent)
            self.assertEqual(collector.collect().cpu_percent, 25)
            self.assertIsNone(collector.collect().cpu_percent)


if __name__ == "__main__":
    unittest.main()
