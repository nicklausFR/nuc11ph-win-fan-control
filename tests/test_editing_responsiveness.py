# SPDX-License-Identifier: GPL-3.0-only
"""Regressions for clipped controls, draft editing and delayed driver requests."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ui"))
from PySide6.QtCore import Q_ARG, QMetaObject, QRect, QSettings, QThread, QTimer, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication
from app import CoolingWindow
from backend import CONTROL_TIMEOUT_SECONDS, EngineConnection, NucError, PollWorker
from system_load import LoadWorker
from test_cooling_controls import FakeClient
from test_ui import sample


class EditingTests(unittest.TestCase):
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
        self.payload = sample()
        self.payload["capabilities"]["write_enabled"] = True
        self.payload["control_owned"] = True
        self.payload["status"]["control"]["mode"] = "profile"
        self.window.update_telemetry(self.payload)
        self.window.show()
        self.application.processEvents()
        self.threads = []

    def tearDown(self):
        for thread, worker in self.threads:
            thread.requestInterruption()
            QMetaObject.invokeMethod(worker, "stop", Qt.QueuedConnection)
        for thread, _ in self.threads:
            self.assertTrue(thread.wait(3000))
        self.window.quitting = True
        self.window.close()
        self.window.deleteLater()
        self.application.processEvents()
        self.temporary.cleanup()

    def start_worker(self, worker):
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.start)
        worker.stopped.connect(thread.quit, Qt.DirectConnection)
        thread.finished.connect(worker.deleteLater)
        self.threads.append((thread, worker))
        thread.start()
        return thread

    def choose_forced(self):
        self.window.mode_choice.setCurrentIndex(self.window.mode_choice.findData("duty"))
        self.application.processEvents()

    def assert_controls_fit(self):
        for widget in (self.window.mode_choice, self.window.duty_percent,
                       self.window.apply_button, self.window.restore_button):
            self.assertGreaterEqual(widget.height(), widget.sizeHint().height(), widget.accessibleName() or widget.text())

    def test_mode_switch_and_busy_updates_never_compress_controls(self):
        self.choose_forced()
        for busy in (True, False) * 5:
            self.window.update_telemetry(copy.deepcopy(self.payload))
            self.window.set_control_busy(busy)
            self.application.processEvents()
            self.assert_controls_fit()

    def test_small_screen_scrolls_instead_of_clipping_controls(self):
        self.choose_forced()
        screen = self.window.screen()
        with patch.object(type(screen), "availableGeometry", return_value=QRect(0, 0, 800, 550)):
            self.window.fit_to_contents()
            self.application.processEvents()
            self.assertLessEqual(self.window.height(), 534)
            self.assertGreater(self.window.panel_scroll.verticalScrollBar().maximum(), 0)
            self.assert_controls_fit()

    def test_draft_remains_editable_while_command_is_pending(self):
        self.choose_forced()
        changes = QSignalSpy(self.window.duty_requested)
        QTest.mouseClick(self.window.apply_button, Qt.LeftButton)
        self.assertEqual(changes.at(0), [75])
        self.assertTrue(self.window.duty_slider.isEnabled())
        self.assertTrue(self.window.duty_percent.isEnabled())
        self.window.duty_percent.setValue(88)
        self.window.update_telemetry(copy.deepcopy(self.payload))
        self.window.set_control_busy(False)
        self.assertEqual(self.window.duty_percent.value(), 88)
        self.assertIn("88", self.window.control_notice.text())
        QTest.mouseClick(self.window.apply_button, Qt.LeftButton)
        self.assertEqual(changes.at(1), [88])

    def test_partial_spinbox_edit_survives_telemetry_refresh(self):
        self.choose_forced()
        editor = self.window.duty_percent.lineEdit()
        editor.setFocus()
        QTest.keyClick(editor, Qt.Key_A, Qt.ControlModifier)
        QTest.keyClicks(editor, "88")
        geometry = self.window.geometry()
        self.window.update_telemetry(copy.deepcopy(self.payload))
        self.application.processEvents()
        self.assertIn("88", editor.text())
        self.assertEqual(self.window.geometry(), geometry)
        sent = QSignalSpy(self.window.duty_requested)
        QTest.mouseClick(self.window.apply_button, Qt.LeftButton)
        self.assertEqual(sent.at(0), [88])

    def test_catalog_refresh_preserves_forced_selection_and_draft(self):
        self.choose_forced()
        self.window.duty_percent.setValue(88)
        changed = copy.deepcopy(self.payload)
        changed["profiles"][0]["label"] = "Équilibré OEM"
        self.window.update_telemetry(changed)
        self.assertEqual(self.window.mode_choice.currentData(), "duty")
        self.assertEqual(self.window.duty_percent.value(), 88)

    def test_slow_command_does_not_freeze_editing_or_ui_heartbeat(self):
        self.choose_forced()
        connection = Mock()
        connection.read.return_value = copy.deepcopy(self.payload)
        def slow_command(*_):
            time.sleep(.25)
            return copy.deepcopy(self.payload)
        connection.command.side_effect = slow_command
        worker = PollWorker(connection=connection, interval=5000)
        worker.updated.connect(self.window.update_telemetry)
        worker.busy.connect(self.window.set_control_busy)
        self.window.duty_requested.connect(worker.set_duty)
        ticks = []
        heartbeat = QTimer()
        heartbeat.setInterval(10)
        heartbeat.timeout.connect(lambda: ticks.append(True))
        self.start_worker(worker)
        QTest.qWait(30)
        heartbeat.start()
        try:
            QTest.mouseClick(self.window.apply_button, Qt.LeftButton)
            QTest.qWait(30)
            self.window.duty_percent.setValue(88)
            QTest.qWait(300)
            self.assertGreater(len(ticks), 10)
            self.assertEqual(self.window.duty_percent.value(), 88)
            self.assertTrue(self.window.apply_button.isEnabled())
        finally:
            heartbeat.stop()

    def test_slow_gpu_metrics_do_not_block_cooling_commands(self):
        started, done, commanded, release = threading.Event(), threading.Event(), threading.Event(), threading.Event()
        source = Mock()
        def slow_metrics():
            started.set()
            release.wait(3)
            done.set()
            return {"cpu_percent": 20.0, "gpu_percent": 10.0}
        source.read.side_effect = slow_metrics
        metrics = LoadWorker(interval=5000, source=source)
        metrics.updated.connect(self.window.update_loads)
        connection = Mock()
        connection.read.return_value = copy.deepcopy(self.payload)
        def command(*_):
            commanded.set()
            return copy.deepcopy(self.payload)
        connection.command.side_effect = command
        worker = PollWorker(connection=connection, interval=5000)
        self.start_worker(metrics)
        self.start_worker(worker)
        try:
            deadline = time.monotonic() + 1
            while not started.is_set() and time.monotonic() < deadline:
                QTest.qWait(10)
            self.assertTrue(started.is_set())
            QMetaObject.invokeMethod(worker, "set_duty", Qt.QueuedConnection, Q_ARG(int, 88))
            deadline = time.monotonic() + 1
            while not commanded.is_set() and time.monotonic() < deadline:
                QTest.qWait(10)
            self.assertTrue(commanded.is_set())
            self.assertFalse(done.is_set())
        finally:
            release.set()

    def test_driver_metrics_are_not_sampled_inside_cooling_transactions(self):
        client = FakeClient()
        source = Mock()
        source.read.side_effect = AssertionError("Une lecture GPU bloque une commande")
        connection = EngineConnection(client_factory=lambda **_: client, load=source)
        try:
            connection.command("duty", 75)
            connection.command("duty", 88)
            connection.renew()
            connection.restore()
        finally:
            connection.close()
        source.read.assert_not_called()

    def test_commands_allow_the_engine_five_second_acknowledgement_budget(self):
        client = FakeClient()
        client.timeout = 1.5
        send = client.set_duty
        def delayed_ack(*args, **kwargs):
            if client.timeout <= 5:
                raise NucError("Le moteur attend encore son contrôleur", code="timeout")
            return send(*args, **kwargs)
        client.set_duty = delayed_ack
        connection = EngineConnection(client_factory=lambda **_: client, load=Mock())
        try:
            result = connection.command("duty", 88)
            self.assertTrue(result["control_owned"])
            self.assertGreater(CONTROL_TIMEOUT_SECONDS, 5)
            self.assertEqual(client.timeout, 1.5)
        finally:
            connection.close()

    def test_renew_and_restore_use_the_control_timeout(self):
        client = FakeClient()
        client.timeout = 1.5
        connection = EngineConnection(client_factory=lambda **_: client, load=Mock())
        connection.command("duty", 75)
        renew, restore = client.renew, client.restore
        observed = []
        def checked_renew(**kwargs):
            observed.append(client.timeout)
            return renew(**kwargs)
        def checked_restore():
            observed.append(client.timeout)
            return restore()
        client.renew, client.restore = checked_renew, checked_restore
        try:
            connection.renew()
            connection.restore()
            self.assertTrue(all(timeout > 5 for timeout in observed))
            self.assertEqual(client.timeout, 1.5)
        finally:
            connection.close()

    def test_lost_reply_restores_only_the_command_from_this_process(self):
        client = FakeClient()
        send = client.set_duty
        def lost_reply(*args, **kwargs):
            send(*args, **kwargs)
            raise NucError("Réponse perdue", code="timeout")
        client.set_duty = lost_reply
        connection = EngineConnection(client_factory=lambda **_: client, load=Mock())
        try:
            with self.assertRaises(NucError):
                connection.command("duty", 88)
            self.assertEqual(client.status["control"]["mode"], "automatic")
            self.assertEqual(client.calls[-1], ("restore",))
            connection.renew()
            self.assertFalse(any(c[0] == "renew" for c in client.calls))
        finally:
            connection.close()

    def test_lost_reply_never_restores_a_foreign_session(self):
        client = FakeClient()
        def lost_reply(*_, **__):
            client._own("duty")
            client.status["control"]["owner_pid"] = os.getpid() + 1
            raise NucError("Réponse perdue", code="timeout")
        client.set_duty = lost_reply
        connection = EngineConnection(client_factory=lambda **_: client, load=Mock())
        try:
            with self.assertRaises(NucError):
                connection.command("duty", 88)
            self.assertFalse(any(c[0] == "restore" for c in client.calls))
            self.assertEqual(client.status["control"]["owner_pid"], os.getpid() + 1)
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
