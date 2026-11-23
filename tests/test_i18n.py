# SPDX-License-Identifier: GPL-3.0-only
"""Language switching must preserve drafts, ownership and disconnected state."""
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
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from app import CoolingWindow
from backend import EngineConnection, control_error
from i18n import ENGLISH, Translator, resolve_language
from nuc_client import NucError
from telemetry import cooling_mode, format_rpm, storage_rows
from test_ui import sample


class TranslationTests(unittest.TestCase):
    def test_windows_language_and_fallback(self):
        for locale, expected in (("fr_FR", "fr"), ("fr_CA", "fr"), ("fr-BE", "fr"),
                                 ("en_GB", "en"), ("de_DE", "en")):
            with self.subTest(locale=locale):
                self.assertEqual(resolve_language("auto", system_locale=locale), expected)
        self.assertEqual(resolve_language("en", system_locale="fr_FR"), "en")

    def test_worker_diagnostics_translate_parameters_and_preserve_unknown_details(self):
        tr = Translator("en")
        self.assertEqual(tr.message("Charge GPU indisponible (NVML 7)"), "GPU load unavailable (NVML 7)")
        self.assertEqual(tr.message("Moteur introuvable : C:\\a {b}\\nuc-engine.exe. Utilisez --engine pour indiquer nuc-engine.exe."),
                         "Engine not found: C:\\a {b}\\nuc-engine.exe. Use --engine to specify nuc-engine.exe.")
        self.assertEqual(tr.message("driver detail 0x80070005"), "driver detail 0x80070005")
        for code in ("read_only", "temperature_limit_or_unavailable", "fan_unavailable", "session_owner_mismatch", "lease_expired", "timeout"):
            message = control_error(NucError(code, code=code))
            self.assertNotEqual(tr.message(message), message)

    def test_profiles_modes_storage_and_rpm_are_localized(self):
        payload = sample()
        self.assertEqual(cooling_mode(payload["status"], payload["profiles"], "en").title, "Automatic · Balanced")
        self.assertEqual(format_rpm(1780, "en"), "1,780 RPM")
        self.assertIn("Sensor 1: 46 °C", storage_rows(payload["status"], "en")[0][3])
        for mode, expected in (("duty", "Manual"), ("recovering", "Restoring automatic control"),
                               ("thresholds", "Custom thresholds"), ("future", "Unknown mode")):
            payload["status"]["control"]["mode"] = mode
            self.assertEqual(cooling_mode(payload["status"], [], "en").title, expected)

    def test_translation_placeholders_match(self):
        from string import Formatter
        fields = lambda text: sorted((key, spec) for _, key, spec, _ in Formatter().parse(text) if key is not None)
        for source, target in ENGLISH.items():
            with self.subTest(source=source):
                self.assertEqual(fields(source), fields(target))


class WindowLanguageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.settings = QSettings(str(Path(self.temporary.name) / "ui.ini"), QSettings.IniFormat)
        self.settings.setValue("language", "fr")
        self.window = CoolingWindow(self.settings, create_tray=False)
        self.payload = sample()
        self.payload["capabilities"]["write_enabled"] = True
        self.payload["load"] = {"cpu_percent": 25, "gpu_percent": 10}
        self.window.update_telemetry(self.payload)

    def tearDown(self):
        self.window.quitting = True
        self.window.close()
        self.window.deleteLater()
        self.application.processEvents()
        self.temporary.cleanup()

    def test_switch_preserves_draft_busy_state_and_sends_no_command(self):
        window = self.window
        sent = Mock()
        window.duty_requested.connect(sent)
        window.profile_requested.connect(sent)
        window.restore_requested.connect(sent)
        window.mode_choice.setCurrentIndex(window.mode_choice.findData("duty"))
        window.duty_percent.setValue(83)
        window.set_control_busy(True)
        timestamp = window.last_update
        status = copy.deepcopy(window.last_payload["status"])
        window.language_actions["en"].trigger()
        self.assertEqual(window.mode_choice.currentData(), "duty")
        self.assertEqual(window.duty_percent.value(), 83)
        self.assertTrue(window.control_busy)
        self.assertFalse(window.apply_button.isEnabled())
        self.assertEqual(window.apply_button.text(), "Applying…")
        self.assertEqual(window.last_update, timestamp)
        self.assertEqual(window.last_payload["status"], status)
        self.assertEqual(window.cpu.subtitle.text(), "Processor")
        self.assertEqual(window.cpu.load.text(), "Load: 25 %")
        self.assertEqual(window.mode_choice.toolTip().splitlines()[0], "Automatic · Balanced")
        self.assertEqual(window.fan_speed.text(), "1,700 RPM")
        self.assertIn("CPU: 1,780 RPM", window.fan_bar.toolTip())
        sent.assert_not_called()
        window._set_language("fr")
        self.assertEqual(window.cpu.subtitle.text(), "Processeur")
        self.assertEqual(window.duty_percent.value(), 83)

    def test_disconnected_language_switch_does_not_restore_stale_readings(self):
        self.window.show_error("Le moteur démarre ou ne répond plus.")
        self.window._set_language("en")
        self.assertEqual(self.window.mode_choice.toolTip(), "Unavailable")
        self.assertTrue(all(card.value.text() == "—" for card in self.window.sensor_cards.values()))
        self.assertFalse(self.window.apply_button.isEnabled())
        self.assertEqual(self.window.error.text(), "The engine is starting or no longer responding.")
        self.assertEqual(self.window.mode_choice.itemText(0), "Balanced")
        self.assertEqual(self.window.apply_button.text(), "Apply")

    def test_pending_thermal_notice_and_load_errors_switch_both_ways(self):
        self.payload["control_notice"] = control_error(NucError("thermal", code="temperature_limit_or_unavailable"))
        self.payload["load"] = {"gpu_error": "Charge GPU indisponible (NVML 7)"}
        self.window.update_telemetry(self.payload)
        self.window._set_language("en")
        self.assertIn("Automatic restore:", self.window.control_notice.text())
        self.assertEqual(self.window.gpu.load.toolTip(), "GPU load unavailable (NVML 7)")
        self.window._set_language("fr")
        self.assertEqual(self.window.gpu.load.toolTip(), "Charge GPU indisponible (NVML 7)")

    def test_saved_language_and_launch_override(self):
        self.window._set_language("en")
        window = CoolingWindow(self.settings, create_tray=False)
        try:
            self.assertEqual(window.language, "en")
            self.assertEqual(window.cpu.subtitle.text(), "Processor")
            window._set_language("fr")
            self.assertEqual(window.cpu.subtitle.text(), "Processeur")
        finally:
            window.deleteLater()
        override = CoolingWindow(self.settings, create_tray=False, language="en")
        try:
            self.assertEqual(override.language, "en")
            self.assertEqual(self.settings.value("language"), "fr")
        finally:
            override.deleteLater()

    def test_auto_uses_windows_and_removed_menu_entries_stay_removed(self):
        with patch("i18n.QLocale.system") as locale:
            locale.return_value.name.return_value = "en_US"
            self.window._set_language("auto")
        self.assertEqual(self.window.language, "en")
        actions = [a.text() for a in self.window.menu.actions() if not a.isSeparator()]
        self.assertEqual(actions, ["Keep panel on top", "Appearance", "Refresh interval", "Language", "Quit"])
        self.assertEqual([a.text() for a in self.window.interval_group.actions()], ["1 second", "2 seconds", "5 seconds"])


if __name__ == "__main__":
    unittest.main()
