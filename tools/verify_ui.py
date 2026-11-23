# SPDX-License-Identifier: GPL-3.0-only
"""Render the real telemetry panel and verify polling and shutdown without writes."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ui"))
if os.name != "nt":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QObject, QSettings, QTimer, Slot
from PySide6.QtWidgets import QApplication
from app import CoolingController, CoolingWindow
from backend import EngineConnection, PollWorker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path)
    parser.add_argument("--language", choices=("fr", "en", "both"), default="both")
    parser.add_argument("--output", type=Path, default=ROOT / "test-results/ui")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    application = QApplication([])
    application.setQuitOnLastWindowClosed(False)
    temporary = tempfile.TemporaryDirectory()
    settings = QSettings(str(Path(temporary.name) / "verify.ini"), QSettings.IniFormat)
    window = CoolingWindow(settings, create_tray=False, language="fr")
    window._set_pinned(True)
    connection = EngineConnection(args.engine)
    worker = PollWorker(connection=connection, interval=250)
    samples, failures, owned = [], [], []

    completed = False

    def finish_if_ready():
        nonlocal completed
        if not completed and len(samples) >= 3 and window.current_load.get("cpu_percent") is not None:
            completed = True
            languages = ("fr", "en") if args.language == "both" else (args.language,)
            for language in languages:
                window._set_language(language)
                for theme in ("light", "dark"):
                    window._set_theme(theme)
                    window.fit_to_contents()
                    window.show()
                    application.processEvents()
                    suffix = "" if language == "fr" else "-en"
                    if not window.grab().save(str(args.output / f"nuc-cooling{suffix}-{theme}.png")):
                        failures.append("Capture impossible")
                    for widget in (window.mode_choice, window.duty_percent, window.apply_button, window.restore_button):
                        if widget.height() < widget.sizeHint().height():
                            failures.append(f"Contrôle tronqué ({language}) : " + widget.accessibleName())
            window.mode_choice.setCurrentIndex(window.mode_choice.findData("duty"))
            window.fit_to_contents()
            application.processEvents()
            if not window.grab().save(str(args.output / "nuc-cooling-forced.png")):
                failures.append("Capture des commandes impossible")
            if not window.mode_card.grab().save(str(args.output / "nuc-cooling-controls-fixed.png")):
                failures.append("Capture du panneau impossible")
            if not window.sensors_panel.grab().save(str(args.output / "nuc-cooling-sensors.png")):
                failures.append("Capture des vignettes impossible")
            for widget in (window.mode_choice, window.duty_percent, window.apply_button, window.restore_button):
                if widget.height() < widget.sizeHint().height():
                    failures.append("Contrôle tronqué : " + widget.accessibleName())
            controller.stop()

    def received(payload):
        samples.append(payload)
        if connection.process is not None and not owned:
            owned.append(connection.process)
        finish_if_ready()

    def failed(message):
        failures.append(message)
        controller.stop()

    class Receiver(QObject):
        @Slot(dict)
        def received(self, payload):
            received(payload)

        @Slot(str)
        def failed(self, message):
            failed(message)

        @Slot(dict)
        def loaded(self, _):
            finish_if_ready()

    receiver = Receiver()
    controller = CoolingController(window, worker)
    worker.updated.connect(receiver.received)
    worker.failed.connect(receiver.failed)
    controller.load_worker.updated.connect(receiver.loaded)
    QTimer.singleShot(15000, lambda: failed("Délai de vérification dépassé"))
    application.exec()
    report = {
        "samples": len(samples), "errors": failures,
        "languages": ["fr", "en"] if args.language == "both" else [args.language],
        "configuration_unchanged": bool(samples) and len({p["status"].get("ec_hex") for p in samples}) == 1,
        "worker_stopped": not controller.thread.isRunning(),
        "load_worker_stopped": not controller.load_thread.isRunning(),
        "owned_engine_stopped": all(p.poll() is not None for p in owned),
        "simulated": samples[0]["capabilities"].get("simulated") if samples else None,
        "cpu_load_available": window.current_load.get("cpu_percent") is not None,
        "gpu_load_available": window.current_load.get("gpu_percent") is not None,
        "load": window.current_load,
        "last_telemetry": samples[-1] if samples else None,
    }
    (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "last_telemetry"}, indent=2))
    temporary.cleanup()
    return 0 if len(samples) >= 3 and not failures and report["configuration_unchanged"] and report["worker_stopped"] and report["load_worker_stopped"] and report["owned_engine_stopped"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
