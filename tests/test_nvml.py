import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from gpuroster.monitoring.collectors import CollectionError, CommandRunner
from gpuroster.monitoring.models import GPU
from gpuroster.monitoring.nvml import AutoGPU, NVMLWorker, nvml_worker, read_nvml


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


class PermissionDenied(NVMLError):
    pass


def two_device_nvml():
    nvml = Mock(
        NVMLError=NVMLError,
        NVMLError_NotSupported=Unsupported,
        NVMLError_NoPermission=PermissionDenied,
        NVML_TEMPERATURE_GPU=0,
    )
    nvml.nvmlDeviceGetCount.return_value = 2
    nvml.nvmlDeviceGetHandleByIndex.side_effect = [7, 3]
    nvml.nvmlDeviceGetUUID.side_effect = lambda handle: f"GPU-example-{handle}"
    nvml.nvmlDeviceGetName.return_value = "Example GPU"
    nvml.nvmlDeviceGetMemoryInfo.return_value = SimpleNamespace(
        used=1048576, total=2097152
    )
    nvml.nvmlDeviceGetUtilizationRates.return_value = SimpleNamespace(gpu=50)
    nvml.nvmlDeviceGetPowerUsage.return_value = 50000
    nvml.nvmlDeviceGetTemperature.return_value = 40
    nvml.nvmlDeviceGetComputeRunningProcesses.side_effect = lambda handle: [
        SimpleNamespace(pid=100 + handle, usedGpuMemory=1048576)
    ]
    return nvml


class NVMLTests(unittest.TestCase):
    def test_multiple_devices_map_processes_by_uuid_after_index_reordering(self):
        devices, rows, error = read_nvml(two_device_nvml())
        self.assertIsNone(error)
        self.assertEqual([d.uuid for d in devices], ["GPU-example-7", "GPU-example-3"])
        provider = NVMLWorker()
        provider.rows = rows
        with patch(
            "gpuroster.monitoring.nvml.process_description",
            return_value=("example-user", "example"),
        ):
            processes = provider.processes(devices)
        self.assertEqual(
            [(p.gpu_uuid, p.gpu) for p in processes],
            [("GPU-example-7", 0), ("GPU-example-3", 1)],
        )

    def test_mig_like_unsupported_utilization_and_denied_metrics_remain_null(self):
        nvml = two_device_nvml()
        nvml.nvmlDeviceGetUtilizationRates.side_effect = Unsupported
        nvml.nvmlDeviceGetMemoryInfo.side_effect = PermissionDenied
        nvml.nvmlDeviceGetPowerUsage.side_effect = PermissionDenied
        nvml.nvmlDeviceGetTemperature.side_effect = Unsupported
        devices, rows, error = read_nvml(nvml)
        self.assertIsNone(error)
        self.assertEqual(len(rows), 2)
        for device in devices:
            self.assertEqual(
                (
                    device.utilization,
                    device.memory_used,
                    device.memory_total,
                    device.temperature,
                    device.power,
                ),
                (None,) * 5,
            )

    def test_partial_process_permission_failure_does_not_discard_gpu_metrics(self):
        nvml = two_device_nvml()
        nvml.nvmlDeviceGetComputeRunningProcesses.side_effect = [
            PermissionDenied(),
            [SimpleNamespace(pid=103, usedGpuMemory=None)],
        ]
        devices, rows, error = read_nvml(nvml)
        self.assertEqual(len(devices), 2)
        self.assertEqual(error, "process_metrics_unavailable")
        self.assertEqual(rows, (("GPU-example-3", 103, None),))
        provider = NVMLWorker()
        provider.rows, provider.process_error = rows, error
        with self.assertRaisesRegex(CollectionError, "process_metrics_unavailable"):
            provider.processes(devices)

    def test_driver_failure_is_reported_without_exception_text_and_shutdown_runs(self):
        nvml = two_device_nvml()
        nvml.nvmlDeviceGetMemoryInfo.side_effect = NVMLError("PRIVATE_DRIVER_DATA")
        connection = Mock()
        connection.recv.side_effect = ["sample", "stop"]
        with (
            patch.dict("sys.modules", pynvml=nvml),
            patch.dict("os.environ", {}, clear=False),
        ):
            nvml_worker(connection)
        connection.send.assert_called_once_with({"error": "gpu_collection_failed"})
        nvml.nvmlShutdown.assert_called_once()
        connection.close.assert_called_once()

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
            "gpuroster.monitoring.nvml.process_description",
            return_value=("example-user", "python"),
        ) as describe:
            rows = provider.processes((GPU(2, "GPU-example", "GPU", 0, 0, 1, 20, 0),))
        describe.assert_called_once_with(123, False)
        self.assertEqual(rows[0].gpu, 2)


if __name__ == "__main__":
    unittest.main()
