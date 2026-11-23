# SPDX-License-Identifier: GPL-3.0-only
"""CPU busy time from Windows and GPU utilization from the installed NVIDIA NVML."""
from __future__ import annotations

import ctypes as C
import os
from pathlib import Path
import time

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot


def cpu_busy(previous, current):
    """GetSystemTimes kernel time includes idle time. All values are cumulative."""
    if previous is None:
        return None
    idle, kernel, user = (now - old for now, old in zip(current, previous))
    total = kernel + user
    if min(idle, kernel, user) < 0 or total <= 0:
        return None
    return max(0.0, min(100.0, 100.0 * (total - idle) / total))


class CpuLoad:
    def __init__(self):
        self.previous = None
        kernel = C.WinDLL("kernel32", use_last_error=True)
        self.get_times = kernel.GetSystemTimes
        self.get_times.argtypes = [C.POINTER(C.c_uint64)] * 3
        self.get_times.restype = C.c_int

    def read(self):
        idle, kernel, user = C.c_uint64(), C.c_uint64(), C.c_uint64()
        if not self.get_times(C.byref(idle), C.byref(kernel), C.byref(user)):
            self.previous = None
            raise OSError("Compteurs de charge CPU Windows indisponibles")
        current = (idle.value, kernel.value, user.value)
        result = cpu_busy(self.previous, current)
        self.previous = current
        return result


class Utilization(C.Structure):
    _fields_ = [("gpu", C.c_uint), ("memory", C.c_uint)]


class NvidiaLoad:
    def __init__(self):
        # Absolute trusted driver locations; never load a DLL from the app folder.
        system = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/nvml.dll"
        legacy = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "NVIDIA Corporation/NVSMI/nvml.dll"
        path = next((p for p in (system, legacy) if p.is_file()), None)
        if path is None:
            raise OSError("NVML NVIDIA indisponible : pilote NVIDIA requis")
        self.dll = C.CDLL(str(path))
        self.initialize = self._bind("nvmlInit_v2", [])
        self.shutdown = self._bind("nvmlShutdown", [])
        self.get_handle = self._bind("nvmlDeviceGetHandleByIndex_v2", [C.c_uint, C.POINTER(C.c_void_p)])
        self.get_utilization = self._bind("nvmlDeviceGetUtilizationRates", [C.c_void_p, C.POINTER(Utilization)])
        self.device = C.c_void_p()
        self.started = False
        self._check(self.initialize())
        self.started = True
        try:
            self._check(self.get_handle(0, C.byref(self.device)))
        except Exception:
            self.close()
            raise

    def _bind(self, name, arguments):
        function = getattr(self.dll, name)
        function.argtypes = arguments
        function.restype = C.c_int
        return function

    @staticmethod
    def _check(code):
        if code:
            raise OSError(f"Charge GPU indisponible (NVML {code})")

    def read(self):
        usage = Utilization()
        self._check(self.get_utilization(self.device, C.byref(usage)))
        return float(usage.gpu) if usage.gpu <= 100 else None

    def close(self):
        if self.started:
            self.shutdown()
            self.started = False


class SystemLoad:
    def __init__(self):
        self.sources = {}
        self.retry_at = {}

    def read(self):
        result = {}
        for key, factory in (("cpu", CpuLoad), ("gpu", NvidiaLoad)):
            try:
                if key not in self.sources:
                    if time.monotonic() < self.retry_at.get(key, 0):
                        result[key + "_percent"] = None
                        result[key + "_error"] = "Compteur indisponible ; nouvelle tentative automatique"
                        continue
                    self.sources[key] = factory()
                result[key + "_percent"] = self.sources[key].read()
            except (OSError, AttributeError) as exc:
                source = self.sources.pop(key, None)
                if source is not None and hasattr(source, "close"):
                    source.close()
                self.retry_at[key] = time.monotonic() + 10
                result[key + "_percent"] = None
                result[key + "_error"] = str(exc)
        return result

    def close(self):
        for source in self.sources.values():
            if hasattr(source, "close"):
                source.close()
        self.sources.clear()


class LoadWorker(QObject):
    """Driver metrics must never delay cooling commands or lease renewal."""
    updated = Signal(dict)
    stopped = Signal()

    def __init__(self, interval=2000, source=None):
        super().__init__()
        self.interval = interval
        self.source = source if source is not None else SystemLoad()
        self.timer = None

    @Slot()
    def start(self):
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.poll)
        self.poll()

    @Slot()
    def poll(self):
        if QThread.currentThread().isInterruptionRequested():
            return
        try:
            self.updated.emit(self.source.read())
        except Exception as exc:
            self.updated.emit({"cpu_percent": None, "gpu_percent": None,
                               "cpu_error": str(exc), "gpu_error": str(exc)})
        if self.timer is not None:
            self.timer.start(self.interval)

    @Slot(int)
    def set_interval(self, interval):
        self.interval = interval
        if self.timer is not None:
            self.timer.start(interval)

    @Slot()
    def stop(self):
        if self.timer is not None:
            self.timer.stop()
        try:
            self.source.close()
        finally:
            self.stopped.emit()
