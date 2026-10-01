import unittest
import subprocess
import sys
import time
from unittest.mock import Mock, patch

from monitoring.collectors import (
    CollectionError,
    CommandRunner,
    NvidiaSMI,
    SystemCollector,
)


class CollectorTests(unittest.TestCase):
    def test_real_subprocess_timeout_is_bounded(self):
        started = time.monotonic()
        with self.assertRaisesRegex(CollectionError, "command_timeout"):
            CommandRunner(0.05)([sys.executable, "-c", "import time; time.sleep(5)"])
        self.assertLess(time.monotonic() - started, 2)

    def test_command_errors_are_safe_and_distinct(self):
        for error, code in [
            (FileNotFoundError("PRIVATE"), "command_missing"),
            (
                subprocess.CalledProcessError(1, ["tool"], stderr="PRIVATE"),
                "command_failed",
            ),
        ]:
            with (
                patch("monitoring.collectors.subprocess.run", side_effect=error),
                self.assertRaisesRegex(CollectionError, code),
            ):
                CommandRunner()(["tool"])

    def test_credentials_are_not_inherited_and_output_locale_is_fixed(self):
        with (
            patch.dict("os.environ", {"GPUROSTER_AUTH_PASSWORD": "TEST_ONLY"}),
            patch(
                "monitoring.collectors.subprocess.run", return_value=Mock(stdout="ok")
            ) as run,
        ):
            self.assertEqual(CommandRunner()(["tool"]), "ok")
        self.assertNotIn("GPUROSTER_AUTH_PASSWORD", run.call_args.kwargs["env"])
        self.assertEqual(run.call_args.kwargs["env"]["TZ"], "UTC")
        self.assertEqual(run.call_args.kwargs["env"]["LC_ALL"], "C")

    def test_opt_in_process_arguments_are_truncated(self):
        process = Mock()
        process.username.return_value = "example-user"
        process.cmdline.return_value = ["python", "x" * 200]
        with patch("monitoring.collectors.psutil.Process", return_value=process):
            rows = NvidiaSMI(Mock(return_value="123, GPU-example, 10"), True).processes(
                ()
            )
        self.assertTrue(rows[0].command.startswith("python "))
        self.assertEqual(len(rows[0].command), 121)

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
