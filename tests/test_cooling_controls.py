# SPDX-License-Identifier: GPL-3.0-only
"""Leased control, guard handling, utilization and deliberate GUI actions."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ui"))
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication
from app import CoolingWindow
from backend import EngineConnection, LEASE_SECONDS, PollWorker, RENEW_INTERVAL_MS
from nuc_client import NucError, integer
from system_load import SystemLoad, cpu_busy
from test_ui import sample


class FakeClient:
    def __init__(self):
        self.status = sample()["status"]
        self.status["control"] = {"mode": "automatic", "active_lease": None, "owner_pid": None}
        self.caps = dict(sample()["capabilities"], write_enabled=True, hardware_verified=True)
        self.calls = []
        self.renew_error = None

    def telemetry(self):
        return copy.deepcopy(self.status)

    def capabilities(self):
        return copy.deepcopy(self.caps)

    def profiles(self):
        return sample()["profiles"]

    def _own(self, mode):
        self.status["control"] = dict(mode=mode, active_lease="ab" * 16, owner_pid=os.getpid(), remaining_ms=10000)

    def set_profile(self, name, *, lease_seconds):
        self.calls.append(("profile", name, lease_seconds))
        self._own("profile")
        return self.telemetry()

    def set_duty(self, value, *, lease_seconds):
        integer(value, "percent", 50, 99)
        self.calls.append(("duty", value, lease_seconds))
        self._own("duty")
        self.status["manual_channels"] = [{"channel": 1, "duty_percent": value}]
        return self.telemetry()

    def renew(self, *, lease_seconds):
        self.calls.append(("renew", lease_seconds))
        if self.renew_error:
            self.status["control"] = dict(mode="automatic", active_lease=None, owner_pid=None,
                                          last_restoration={"reason": self.renew_error})
            raise NucError("Limite thermique", code=self.renew_error)
        return self.telemetry()

    def restore(self):
        self.calls.append(("restore",))
        self.status["control"] = dict(mode="automatic", active_lease=None, owner_pid=None)
        self.status["manual_channels"] = []
        return self.telemetry()


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        load = Mock()
        load.read.return_value = {"cpu_percent": 20.0, "gpu_percent": 30.0}
        self.connection = EngineConnection(client_factory=lambda **_: self.client, load=load)

    def tearDown(self):
        self.connection.close()

    def test_command_renew_and_explicit_restore(self):
        result = self.connection.command("profile", "balanced")
        self.assertTrue(result["control_owned"])
        self.connection.renew()
        restored = self.connection.restore()
        self.assertFalse(restored["control_owned"])
        self.assertEqual(self.client.calls, [("profile", "balanced", LEASE_SECONDS), ("renew", LEASE_SECONDS), ("restore",)])

    def test_forced_speed_is_percent_and_sdk_bounds_apply(self):
        for invalid in (0, 49, 100, True):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.connection.command("duty", invalid)
        result = self.connection.command("duty", 75)
        self.assertEqual(result["status"]["manual_channels"][0]["duty_percent"], 75)
        self.assertEqual(self.client.calls, [("duty", 75, LEASE_SECONDS)])

    def test_read_only_and_monitor_only_reject_before_write(self):
        self.client.caps["write_enabled"] = False
        with self.assertRaises(NucError):
            self.connection.command("profile", "balanced")
        self.client.caps["write_enabled"] = True
        self.connection.control_allowed = False
        with self.assertRaisesRegex(RuntimeError, "Surveillance seule"):
            self.connection.command("duty", 75)
        self.assertEqual(self.client.calls, [])

    def test_foreign_session_is_not_replaced_or_restored(self):
        self.client.status["control"] = dict(mode="duty", active_lease="ff" * 16, owner_pid=os.getpid() + 1)
        with self.assertRaisesRegex(RuntimeError, "autre application"):
            self.connection.command("duty", 75)
        with self.assertRaisesRegex(RuntimeError, "autre application"):
            self.connection.restore()
        self.connection.close()
        self.assertEqual(self.client.calls, [])

    def test_guard_trip_stops_renewal_and_never_reapplies(self):
        self.connection.command("duty", 75)
        self.client.renew_error = "temperature_limit_or_unavailable"
        with self.assertRaises(NucError):
            self.connection.renew()
        self.connection.renew()
        result = self.connection.read()
        self.assertFalse(result["control_owned"])
        self.assertIn("85", result["control_notice"])
        self.assertEqual(self.client.calls, [("duty", 75, LEASE_SECONDS), ("renew", LEASE_SECONDS)])

    def test_engine_restart_does_not_reapply_last_command(self):
        self.connection.command("profile", "balanced")
        self.client.status["engine_pid"] = 456
        self.client.status["control"] = dict(mode="automatic", active_lease=None, owner_pid=None)
        self.connection.read()
        self.connection.renew()
        self.assertIsNone(self.connection.lease_token)
        self.assertEqual(self.client.calls, [("profile", "balanced", LEASE_SECONDS)])

    def test_close_restores_only_own_active_session(self):
        self.connection.command("duty", 75)
        self.connection.close()
        self.assertEqual(self.client.calls[-1], ("restore",))
        self.connection.close()
        self.assertEqual(sum(c[0] == "restore" for c in self.client.calls), 1)

    def test_owned_engine_is_preserved_when_another_client_controls_it(self):
        self.connection.read()
        self.connection.allow_write = True
        process = Mock()
        process.poll.return_value = None
        self.connection.process = process
        self.client.status["control"] = dict(mode="duty", active_lease="ff" * 16, owner_pid=os.getpid() + 1)
        self.connection.close()
        process.terminate.assert_not_called()

    def test_controlled_autostart_passes_allow_write_only_when_requested(self):
        self.connection.allow_write = True
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "nuc-engine.exe"
            executable.touch()
            self.connection.engine = executable
            self.connection.client = self.client
            process = Mock()
            process.poll.return_value = None
            with patch("backend.subprocess.Popen", return_value=process) as start:
                self.connection._start_engine()
            self.assertEqual(start.call_args.args[0], [str(executable), "serve", "--allow-write"])
            self.connection.close()
            process.terminate.assert_called_once()


class UtilizationTests(unittest.TestCase):
    def test_cpu_delta_includes_kernel_idle_and_uses_no_wall_clock(self):
        self.assertEqual(cpu_busy((100, 200, 100), (140, 280, 120)), 60)
        self.assertIsNone(cpu_busy(None, (140, 280, 120)))
        self.assertIsNone(cpu_busy((100, 200, 100), (100, 200, 100)))
        self.assertIsNone(cpu_busy((100, 200, 100), (10, 20, 10)))

    def test_gpu_failure_keeps_cpu_available_and_retries_later(self):
        cpu = Mock()
        cpu.read.return_value = 25.0
        with patch("system_load.CpuLoad", return_value=cpu), patch("system_load.NvidiaLoad", side_effect=OSError("Pas de NVML")) as gpu:
            source = SystemLoad()
            first = source.read()
            second = source.read()
            self.assertEqual(first["cpu_percent"], 25.0)
            self.assertIsNone(first["gpu_percent"])
            self.assertEqual(second["cpu_percent"], 25.0)
            self.assertEqual(gpu.call_count, 1)
            source.close()


class ControlWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        settings = QSettings(str(Path(self.temporary.name) / "ui.ini"), QSettings.IniFormat)
        settings.setValue("language", "fr")
        self.window = CoolingWindow(settings, create_tray=False)
        self.payload = sample()
        self.payload["capabilities"].update(write_enabled=True, hardware_verified=True)
        self.payload["load"] = {"cpu_percent": 24.2, "gpu_percent": 0.0}
        self.payload["control_owned"] = False
        self.window.update_telemetry(self.payload)

    def tearDown(self):
        self.window.quitting = True
        self.window.close()
        self.window.deleteLater()
        self.application.processEvents()
        self.temporary.cleanup()

    def test_loads_distinguish_idle_from_unavailable(self):
        self.assertEqual(self.window.cpu.load.text(), "Charge : 24 %")
        self.assertEqual(self.window.gpu.load.text(), "Charge : 0 %")
        self.payload["load"]["gpu_percent"] = None
        self.window.update_telemetry(self.payload)
        self.assertEqual(self.window.gpu.load.text(), "Charge : —")
        self.window.show_error("Déconnecté")
        self.assertEqual(self.window.cpu.load.text(), "Charge : —")

    def test_refresh_and_selection_do_not_send_commands(self):
        profile = QSignalSpy(self.window.profile_requested)
        duty = QSignalSpy(self.window.duty_requested)
        self.window.mode_choice.setCurrentIndex(self.window.mode_choice.findData("duty"))
        self.window.duty_percent.setValue(99)
        self.window.update_telemetry(self.payload)
        self.assertEqual(profile.count(), 0)
        self.assertEqual(duty.count(), 0)

    def test_apply_profile_and_forced_speed_require_click(self):
        profile = QSignalSpy(self.window.profile_requested)
        duty = QSignalSpy(self.window.duty_requested)
        self.window.mode_choice.setCurrentIndex(self.window.mode_choice.findData("balanced"))
        QTest.mouseClick(self.window.apply_button, Qt.LeftButton)
        self.assertEqual(profile.at(0), ["balanced"])
        self.assertFalse(self.window.apply_button.isEnabled())
        self.window.set_control_busy(False)
        self.window.mode_choice.setCurrentIndex(self.window.mode_choice.findData("duty"))
        self.window.duty_percent.setValue(99)
        QTest.mouseClick(self.window.apply_button, Qt.LeftButton)
        self.assertEqual(duty.at(0), [99])
        self.assertEqual(self.window.duty_slider.value(), 99)

    def test_foreign_and_read_only_controls_are_disabled(self):
        self.payload["capabilities"]["write_enabled"] = False
        self.window.update_telemetry(self.payload)
        self.assertFalse(self.window.apply_button.isEnabled())
        self.payload["capabilities"]["write_enabled"] = True
        self.payload["status"]["control"].update(active_lease="ff" * 16, owner_pid=1234567)
        self.window.update_telemetry(self.payload)
        self.assertFalse(self.window.apply_button.isEnabled())
        self.assertFalse(self.window.restore_button.isEnabled())

    def test_restore_is_enabled_only_for_owned_control(self):
        self.assertFalse(self.window.restore_button.isEnabled())
        self.payload["control_owned"] = True
        self.window.update_telemetry(self.payload)
        restore = QSignalSpy(self.window.restore_requested)
        QTest.mouseClick(self.window.restore_button, Qt.LeftButton)
        self.assertEqual(restore.count(), 1)

    def test_renewal_clock_is_independent_of_display_refresh(self):
        connection = Mock()
        connection.read.return_value = self.payload
        worker = PollWorker(connection=connection, interval=5000)
        worker.start()
        try:
            self.assertEqual(worker.timer.interval(), 5000)
            self.assertEqual(worker.lease_timer.interval(), RENEW_INTERVAL_MS)
            self.assertLess(RENEW_INTERVAL_MS, LEASE_SECONDS * 1000)
        finally:
            worker.stop()

    def test_engine_switch_waits_for_successful_release(self):
        connection = Mock()
        connection.engine = Path("original.exe")
        connection.read.return_value = self.payload
        connection.close.side_effect = RuntimeError("Restauration non confirmée")
        worker = PollWorker(connection=connection)
        failures = QSignalSpy(worker.control_failed)
        worker.set_engine("another.exe")
        self.assertEqual(connection.engine, Path("original.exe"))
        self.assertEqual(failures.count(), 1)


if __name__ == "__main__":
    unittest.main()
