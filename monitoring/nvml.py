"""Read-only NVML in a disposable worker with a parent-enforced deadline."""

import multiprocessing
import os

from monitoring.collectors import CollectionError, NvidiaSMI, process_description
from monitoring.models import GPU, GPUProcess


def read_nvml(nvml):
    devices, processes = [], []
    process_error = None

    def optional(call):
        try:
            return call()
        except (nvml.NVMLError_NotSupported, nvml.NVMLError_NoPermission):
            return None

    def text(value):
        return value.decode() if isinstance(value, bytes) else value

    for index in range(nvml.nvmlDeviceGetCount()):
        handle = nvml.nvmlDeviceGetHandleByIndex(index)
        uuid = text(nvml.nvmlDeviceGetUUID(handle))
        memory = optional(lambda: nvml.nvmlDeviceGetMemoryInfo(handle))
        utilization = optional(lambda: nvml.nvmlDeviceGetUtilizationRates(handle))
        power = optional(lambda: nvml.nvmlDeviceGetPowerUsage(handle))
        devices.append(
            GPU(
                index,
                uuid,
                text(nvml.nvmlDeviceGetName(handle)),
                utilization.gpu if utilization is not None else None,
                memory.used / 1048576 if memory is not None else None,
                memory.total / 1048576 if memory is not None else None,
                optional(
                    lambda: nvml.nvmlDeviceGetTemperature(
                        handle, nvml.NVML_TEMPERATURE_GPU
                    )
                ),
                power / 1000 if power is not None else None,
            )
        )
        try:
            for process in nvml.nvmlDeviceGetComputeRunningProcesses(handle):
                used = process.usedGpuMemory
                processes.append(
                    (
                        uuid,
                        process.pid,
                        None if used is None or used >= 2**64 - 1 else used / 1048576,
                    )
                )
        except nvml.NVMLError:
            process_error = "process_metrics_unavailable"
    return tuple(devices), tuple(processes), process_error


def nvml_worker(connection):
    # The spawn environment is inherited; discard application credentials before
    # loading the driver. They are never included in worker messages.
    os.environ.pop("GPUROSTER_AUTH_USER", None)
    os.environ.pop("GPUROSTER_AUTH_PASSWORD", None)
    try:
        import pynvml

        pynvml.nvmlInit()
    except Exception:
        connection.send({"error": "nvml_unavailable"})
        connection.close()
        return
    try:
        while connection.recv() == "sample":
            try:
                connection.send({"sample": read_nvml(pynvml)})
            except Exception:
                connection.send({"error": "gpu_collection_failed"})
    except (EOFError, BrokenPipeError):
        pass
    finally:
        pynvml.nvmlShutdown()
        connection.close()


class NVMLWorker:
    name = "nvml"

    def __init__(self, timeout=3, show_commands=False, target=nvml_worker):
        self.timeout = timeout
        self.show_commands = show_commands
        self.target = target
        self.worker = None
        self.connection = None
        self.rows = None
        self.process_error = None

    def gpus(self):
        self.rows = None
        if self.worker is None:
            context = multiprocessing.get_context("spawn")
            self.connection, child = context.Pipe()
            self.worker = context.Process(
                target=self.target, args=(child,), daemon=True
            )
            self.worker.start()
            child.close()
        try:
            self.connection.send("sample")
            if not self.connection.poll(self.timeout):
                raise CollectionError("command_timeout")
            message = self.connection.recv()
            if "error" in message:
                raise CollectionError(message["error"])
            devices, self.rows, self.process_error = message["sample"]
            return devices
        except (EOFError, BrokenPipeError, OSError):
            self.close()
            raise CollectionError("gpu_worker_failed") from None
        except CollectionError:
            self.close()
            raise

    def processes(self, devices):
        if self.rows is None or self.process_error:
            raise CollectionError(self.process_error or "gpu_sample_unavailable")
        indices = {device.uuid: device.index for device in devices}
        result = []
        for uuid, pid, memory in self.rows:
            user, command = process_description(pid, self.show_commands)
            result.append(
                GPUProcess(uuid, indices.get(uuid), pid, user, command, memory)
            )
        return tuple(result)

    def close(self):
        if self.worker is not None:
            try:
                self.connection.send("stop")
            except (BrokenPipeError, OSError):
                pass
            self.worker.join(timeout=0.1)
            if self.worker.is_alive():
                self.worker.terminate()
                self.worker.join(timeout=0.2)
            if self.worker.is_alive():
                self.worker.kill()
                self.worker.join(timeout=0.2)
            self.connection.close()
            self.worker = self.connection = None


class AutoGPU:
    """Fallback only when NVML cannot initialize, never hide a runtime failure."""

    def __init__(self, runner, show_commands=False):
        self.runner = runner
        self.show_commands = show_commands
        self.provider = NVMLWorker(runner.timeout, show_commands)
        self.initialized = False

    @property
    def name(self):
        return self.provider.name

    def gpus(self):
        try:
            result = self.provider.gpus()
        except CollectionError as error:
            if self.initialized or error.code != "nvml_unavailable":
                raise
            self.provider.close()
            self.provider = NvidiaSMI(self.runner, self.show_commands)
            result = self.provider.gpus()
        self.initialized = True
        return result

    def processes(self, devices):
        return self.provider.processes(devices)

    def close(self):
        self.provider.close()
