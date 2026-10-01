import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from monitoring.collectors import CollectionError, CommandRunner
from monitoring.models import GPU
from monitoring.nvml import AutoGPU, NVMLWorker, read_nvml


def stalled_worker(connection):
    connection.recv()
    time.sleep(10)


def fixture_worker(connection):
    while connection.recv() == "sample":
        gpu = GPU(0, "GPU-example", "Example GPU", 25, 10, 100, 40, 50)
        connection.send({"sample": ((gpu,), (), None)})
    connection.close()


def failed_worker(connection):
    connection.close()


class NVMLError(Exception):
    pass


class Unsupported(NVMLError):
    pass


class NVMLTests(unittest.TestCase):
    def test_worker_deadline_terminates_and_next_sample_can_restart(self):
        provider = NVMLWorker(timeout=0.1, target=stalled_worker)
        self.addCleanup(provider.close)
        started = time.monotonic()
        with self.assertRaisesRegex(CollectionError, "command_timeout"):
            provider.gpus()
        self.assertLess(time.monotonic() - started, 2)
        self.assertIsNone(provider.worker)
        provider.target = fixture_worker
        provider.timeout = 3
        self.assertEqual(provider.gpus()[0].uuid, "GPU-example")
        first_pid = provider.worker.pid
        self.assertEqual(provider.processes(provider.gpus()), ())
        self.assertEqual(provider.worker.pid, first_pid)

    def test_worker_exit_is_a_safe_error(self):
        provider = NVMLWorker(timeout=3, target=failed_worker)
        self.addCleanup(provider.close)
        with self.assertRaisesRegex(CollectionError, "gpu_worker_failed"):
            provider.gpus()

    def test_auto_falls_back_only_on_initialization_failure(self):
        runner = Mock(
            timeout=3, return_value="0, GPU-example, Example, 42, 10, 100, 40, 50"
        )
        provider = AutoGPU(runner)
        provider.provider = Mock()
        provider.provider.gpus.side_effect = CollectionError("nvml_unavailable")
        self.assertEqual(provider.gpus()[0].uuid, "GPU-example")
        self.assertEqual(provider.name, "nvidia-smi")
        runner.assert_called_once()

    def test_runtime_failure_does_not_silently_switch_backend(self):
        provider = AutoGPU(CommandRunner())
        provider.provider = Mock(name="native")
        provider.provider.gpus.side_effect = [(), CollectionError("command_timeout")]
        self.assertEqual(provider.gpus(), ())
        with self.assertRaisesRegex(CollectionError, "command_timeout"):
            provider.gpus()
        self.assertNotEqual(provider.name, "nvidia-smi")

    def test_normalization_preserves_unsupported_values_and_memory_sentinel(self):
        nvml = Mock(
            NVMLError=NVMLError,
            NVMLError_NotSupported=Unsupported,
            NVMLError_NoPermission=Unsupported,
        )
        nvml.nvmlDeviceGetCount.return_value = 1
        nvml.nvmlDeviceGetUUID.return_value = b"GPU-example"
        nvml.nvmlDeviceGetName.return_value = b"Example GPU"
        nvml.nvmlDeviceGetMemoryInfo.return_value = SimpleNamespace(
            used=1048576, total=2097152
        )
        nvml.nvmlDeviceGetUtilizationRates.side_effect = Unsupported
        nvml.nvmlDeviceGetTemperature.return_value = 40
        nvml.nvmlDeviceGetPowerUsage.return_value = 150000
        nvml.nvmlDeviceGetComputeRunningProcesses.return_value = [
            SimpleNamespace(pid=123, usedGpuMemory=2**64 - 1)
        ]
        gpus, rows, error = read_nvml(nvml)
        self.assertIsNone(gpus[0].utilization)
        self.assertEqual(gpus[0].memory_used, 1)
        self.assertEqual(gpus[0].power, 150)
        self.assertIsNone(rows[0][2])
        self.assertIsNone(error)
        nvml.nvmlDeviceGetComputeRunningProcesses.side_effect = Unsupported
        self.assertEqual(read_nvml(nvml)[2], "process_metrics_unavailable")

    def test_worker_process_arguments_are_private_by_default(self):
        provider = NVMLWorker()
        provider.rows = (("GPU-example", 123, 1),)
        with patch(
            "monitoring.nvml.process_description",
            return_value=("example-user", "python"),
        ) as describe:
            rows = provider.processes((GPU(2, "GPU-example", "GPU", 0, 0, 1, 20, 0),))
        describe.assert_called_once_with(123, False)
        self.assertEqual(rows[0].gpu, 2)


if __name__ == "__main__":
    unittest.main()
