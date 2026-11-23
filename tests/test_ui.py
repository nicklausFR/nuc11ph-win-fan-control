# SPDX-License-Identifier: GPL-3.0-only
"""Read-only presentation, engine ownership and Qt lifecycle regression tests."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ui"))
from PySide6.QtCore import QPoint, QRect, QSettings, QSize, QThread, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app import CoolingWindow, anchored_position
from backend import EngineConnection, PollWorker
from nuc_client import NucError
from telemetry import cooling_mode, format_rpm, storage_rows, temperature


def sample():
    return {"status": {
        "engine_pid": 123, "version": "0.3.0",
        "temperatures": [{"id": "cpu", "celsius": 54, "available": True},
                         {"id": "nvidia_gpu", "celsius": 48, "available": True},
                         {"id": "integrated_gpu", "celsius": 51, "available": True}],
        "fans": [{"id": "cpu", "rpm": 1780}, {"id": "nvidia_gpu", "rpm": 1620}],
        "thresholds": {"primary_min_encoded": 50, "secondary_min_encoded": 42,
                       "primary_min_duty_percent": 30, "secondary_min_duty_percent": 35},
        "manual_channels": [], "control": {"mode": "automatic"},
        "ssds": [{"id": "ssd_0", "model": "CT1000P3PSSD8", "available": True,
                  "sensors": [{"index": 0, "celsius": 39}, {"index": 1, "celsius": 46}]},
                 {"id": "ssd_1", "model": "CT2000P3PSSD8", "available": True,
                  "sensors": [{"index": 0, "celsius": 42}]}]},
        "capabilities": {"model": "NUC11PHi7", "simulated": False},
        "profiles": [{"id": "balanced", "label": "Équilibré",
                      "thresholds": {"primary_min_encoded": 50, "secondary_min_encoded": 42},
                      "minimum_duty_percent": {"cpu": 30, "nvidia_gpu": 35}}]}


class PresentationTests(unittest.TestCase):
    def test_profile_requires_all_four_values(self):
        payload = sample()
        self.assertEqual(cooling_mode(payload["status"], payload["profiles"]).title, "Automatique · Équilibré")
        for field in payload["status"]["thresholds"]:
            status = copy.deepcopy(payload["status"])
            status["thresholds"][field] += 1
            with self.subTest(field=field):
                self.assertEqual(cooling_mode(status, payload["profiles"]).title, "Automatique")

    def test_missing_or_ambiguous_profile_not_claimed(self):
        payload = sample()
        self.assertEqual(cooling_mode(payload["status"], payload["profiles"] * 2).title, "Automatique")
        payload["status"]["thresholds"] = {}
        self.assertEqual(cooling_mode(payload["status"], payload["profiles"]).title, "Automatique")

    def test_manual_restoration_unknown_and_threshold_modes(self):
        status = sample()["status"]
        for mode, expected in (("external_manual", "Manuel · 60 %"), ("duty", "Manuel · 60 %"),
                               ("recovering", "Restauration en cours"), ("thresholds", "Seuils personnalisés"),
                               ("future_mode", "Mode inconnu")):
            status["control"]["mode"] = mode
            status["manual_channels"] = [{"duty_percent": 60}]
            with self.subTest(mode=mode):
                self.assertEqual(cooling_mode(status, []).title, expected)

    def test_missing_and_invalid_temperatures(self):
        for value in (None, True, "50", float("nan"), float("inf"), -51, 126):
            with self.subTest(value=value):
                self.assertIsNone(temperature({"celsius": value}))
        self.assertIsNone(temperature({"celsius": 50, "available": False}))
        self.assertEqual(temperature({"celsius": 0}), 0)
        self.assertEqual(format_rpm(0), "0 tr/min")

    def test_storage_uses_composite_and_respects_availability(self):
        status = sample()["status"]
        rows = storage_rows(status)
        self.assertEqual(rows[0][2], 39)
        self.assertIn("Sonde 1 : 46 °C", rows[0][3])
        status["ssds"][0]["available"] = False
        status["ssds"][1]["sensors"] = [{"index": 1, "celsius": 51}]
        self.assertEqual([r[2] for r in storage_rows(status)], [None, None])

    def test_anchor_stays_on_each_screen(self):
        for area in (QRect(0, 0, 1920, 1040), QRect(-1920, -1080, 1920, 1040)):
            for anchor in (QRect(area.left(), area.top(), 30, 30),
                           QRect(area.right() - 30, area.bottom(), 30, 30)):
                pos = anchored_position(area, QSize(414, 510), anchor)
                self.assertTrue(area.contains(QRect(pos, QSize(414, 510))))


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        source = Mock()
        source.read.return_value = {}
        patcher = patch("backend.SystemLoad", return_value=source)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_reads_only_and_never_closes_external_session(self):
        client = Mock()
        client.telemetry.return_value = sample()["status"]
        client.capabilities.return_value = sample()["capabilities"]
        client.profiles.return_value = sample()["profiles"]
        connection = EngineConnection(client_factory=lambda **_: client)
        connection.read()
        connection.read()
        connection.close()
        self.assertEqual([call[0] for call in client.method_calls],
                         ["telemetry", "capabilities", "profiles", "telemetry"])

    def test_no_autostart_for_access_denied(self):
        client = Mock()
        client.telemetry.side_effect = NucError("Accès refusé", win32=5)
        connection = EngineConnection(client_factory=lambda **_: client)
        with patch("backend.subprocess.Popen") as start, self.assertRaises(NucError):
            connection.read()
        start.assert_not_called()

    def test_disabled_autostart_reports_missing_engine(self):
        client = Mock()
        client.telemetry.side_effect = NucError("Absent", win32=2)
        connection = EngineConnection(autostart=False, client_factory=lambda **_: client)
        with patch("backend.subprocess.Popen") as start, self.assertRaisesRegex(RuntimeError, "Moteur arrêté"):
            connection.read()
        start.assert_not_called()

    def test_autostart_is_hidden_and_read_only(self):
        payload = sample()
        client = Mock()
        client.telemetry.side_effect = [NucError("Absent", win32=2), payload["status"]]
        client.capabilities.return_value = payload["capabilities"]
        client.profiles.return_value = payload["profiles"]
        with tempfile.TemporaryDirectory() as temporary:
            engine = Path(temporary) / "nuc-engine.exe"
            engine.touch()
            process = Mock()
            process.poll.return_value = None
            connection = EngineConnection(engine, client_factory=lambda **_: client)
            with patch("backend.subprocess.Popen", return_value=process) as start:
                self.assertEqual(connection.read()["status"], payload["status"])
                self.assertEqual(start.call_args.args[0], [str(engine.resolve()), "serve"])
                self.assertEqual(start.call_args.kwargs["creationflags"], 0x08000000 if os.name == "nt" else 0)
                connection.close()
            process.terminate.assert_called_once()

    def test_engine_restart_refreshes_catalog(self):
        client = Mock()
        status = sample()["status"]
        client.telemetry.side_effect = [status, dict(status, engine_pid=456)]
        connection = EngineConnection(client_factory=lambda **_: client)
        connection.read()
        connection.read()
        self.assertEqual(client.capabilities.call_count, 2)
        self.assertEqual(client.profiles.call_count, 2)


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        settings = QSettings(str(Path(self.temporary.name) / "ui.ini"), QSettings.IniFormat)
        settings.setValue("language", "fr")
        self.window = CoolingWindow(settings, create_tray=False)
        self.window.pinned = True

    def tearDown(self):
        self.window.quitting = True
        self.window.close()
        self.window.deleteLater()
        self.application.processEvents()
        self.temporary.cleanup()

    def test_real_units_and_readings_render(self):
        self.window.update_telemetry(sample())
        self.assertEqual(self.window.cpu.value.text(), "54 °C")
        self.assertEqual(self.window.fan_speed.text(), "1\u202f700 tr/min")
        self.assertIn("GPU : 1\u202f620 tr/min", self.window.fan_speed.toolTip())
        self.assertEqual(self.window.igpu.value.text(), "51 °C")
        self.assertEqual(self.window.disk_cards["ssd_1"].value.text(), "42 °C")
        self.assertIn("Équilibré", self.window.mode_choice.toolTip())

    def test_disconnect_clears_values_and_reconnect_recovers(self):
        self.window.update_telemetry(sample())
        self.window.show_error("Moteur déconnecté")
        self.assertEqual(self.window.cpu.value.text(), "—")
        self.assertEqual(self.window.fan_speed.text(), "—")
        self.assertEqual(self.window.fan_bar.value(), 0)
        self.assertFalse(self.window.fan_bar.isEnabled())
        self.assertTrue(all(card.value.text() == "—" for card in self.window.disk_cards.values()))
        self.assertEqual(self.window.mode_choice.toolTip(), "Indisponible")
        self.window.update_telemetry(sample())
        self.assertTrue(self.window.error.isHidden())
        self.assertEqual(self.window.cpu.value.text(), "54 °C")

    def test_sensor_tiles_and_common_fan_controls_are_separate(self):
        self.window.update_telemetry(sample())
        self.assertEqual(set(self.window.sensor_cards),
                         {"cpu", "nvidia_gpu", "integrated_gpu", "storage:ssd_0", "storage:ssd_1"})
        self.assertTrue(self.window.mode_card.isAncestorOf(self.window.fan_speed))
        self.assertTrue(self.window.mode_card.isAncestorOf(self.window.fan_bar))
        self.assertTrue(self.window.mode_card.isAncestorOf(self.window.duty_slider))
        for card in self.window.sensor_cards.values():
            self.assertTrue(self.window.sensors_panel.isAncestorOf(card))
            self.assertFalse(card.isAncestorOf(self.window.fan_speed))
            self.assertFalse(card.isAncestorOf(self.window.fan_bar))
            self.assertFalse(hasattr(card, "rpm"))
        self.assertFalse(self.window.cpu.load.isHidden())
        self.assertFalse(self.window.gpu.load.isHidden())
        self.assertTrue(self.window.igpu.load.isHidden())
        self.assertTrue(all(card.load.isHidden() for card in self.window.disk_cards.values()))

    def test_one_shared_bar_uses_mean_rpm_and_keeps_exact_readings(self):
        from PySide6.QtWidgets import QProgressBar
        self.window.update_telemetry(sample())
        self.assertEqual(self.window.fan_readings.findChildren(QProgressBar), [self.window.fan_bar])
        self.assertEqual(self.window.fan_bar.value(), 1700)
        self.assertEqual(self.window.fan_bar.maximum(), 3000)
        self.assertIn("CPU : 1\u202f780 tr/min", self.window.fan_bar.toolTip())
        payload = sample()
        payload["status"]["fans"] = [{"id": "cpu", "rpm": 4000}, {"id": "nvidia_gpu", "rpm": 4200}]
        self.window.update_telemetry(payload)
        self.assertEqual(self.window.fan_bar.value(), 4100)
        self.assertEqual(self.window.fan_bar.maximum(), 5000)
        self.window.update_telemetry(sample())
        self.assertEqual(self.window.fan_bar.maximum(), 5000)

    def test_missing_fan_readings_are_not_treated_as_zero(self):
        payload = sample()
        for invalid in (None, -1, float("nan")):
            payload["status"]["fans"] = [{"id": "cpu", "rpm": invalid}, {"id": "nvidia_gpu", "rpm": 1800}]
            self.window.update_telemetry(payload)
            self.assertEqual(self.window.fan_bar.value(), 1800)
            self.assertEqual(self.window.fan_speed.text(), "1\u202f800 tr/min")
        payload["status"]["fans"] = []
        self.window.update_telemetry(payload)
        self.assertEqual(self.window.fan_speed.text(), "—")
        self.assertFalse(self.window.fan_bar.isEnabled())

    def test_disconnection_keeps_sensor_tiles_without_stale_readings(self):
        self.window.update_telemetry(sample())
        cards = dict(self.window.sensor_cards)
        self.window.show_error("Moteur déconnecté")
        self.assertEqual(cards, self.window.sensor_cards)
        self.assertTrue(all(card.value.text() == "—" for card in cards.values()))

    def test_simulation_is_explicit(self):
        payload = sample()
        payload["capabilities"]["simulated"] = True
        self.window.update_telemetry(payload)
        self.assertIn("SIMULATION", self.window.title.text())
        self.assertIn("SIMULATION", self.window.windowTitle())

    def test_escape_and_close_hide_without_quitting(self):
        self.window.show()
        self.application.processEvents()
        QTest.keyClick(self.window, Qt.Key_Escape)
        self.assertFalse(self.window.isVisible())
        self.window.show()
        self.window.close()
        self.assertFalse(self.window.isVisible())
        self.assertFalse(self.window.quitting)

    def test_theme_and_pin_persist(self):
        self.window._set_theme("dark")
        self.window._set_pinned(True)
        self.assertEqual(self.window.settings.value("theme"), "dark")
        self.assertTrue(self.window.settings.value("pinned", type=bool))
        self.assertTrue(self.window.pin_action.isChecked())

    def test_slow_poll_runs_outside_ui_thread_and_stops(self):
        connection = Mock()
        def slow_read(**_):
            time.sleep(0.15)
            return sample()
        connection.read.side_effect = slow_read
        worker = PollWorker(connection=connection, interval=1000)
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.start)
        worker.updated.connect(self.window.update_telemetry)
        worker.stopped.connect(thread.quit, Qt.DirectConnection)
        thread.finished.connect(worker.deleteLater)
        ticks = []
        heartbeat = QTimer()
        heartbeat.setInterval(10)
        heartbeat.timeout.connect(lambda: ticks.append(True))
        heartbeat.start()
        try:
            thread.start()
            QTest.qWait(300)
            self.assertGreater(len(ticks), 5)
            self.assertEqual(self.window.cpu.value.text(), "54 °C")
        finally:
            heartbeat.stop()
            from PySide6.QtCore import QMetaObject
            thread.requestInterruption()
            QMetaObject.invokeMethod(worker, "stop", Qt.QueuedConnection)
            self.assertTrue(thread.wait(3000))
        connection.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
