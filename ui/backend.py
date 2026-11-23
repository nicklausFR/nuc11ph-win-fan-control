# SPDX-License-Identifier: GPL-3.0-only
"""Engine polling and leased cooling commands, isolated from the GUI thread."""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
from nuc_client import NucClient, NucError
from system_load import SystemLoad

LEASE_SECONDS = 10
RENEW_INTERVAL_MS = 2000
CONTROL_TIMEOUT_SECONDS = 8


def control_error(exc):
    return {
        "read_only": "Le moteur est en lecture seule. Relancez-le avec --allow-write pour commander le refroidissement.",
        "temperature_limit_or_unavailable": "Retour automatique : température CPU ≥ 85 °C, GPU ≥ 80 °C ou sonde indisponible.",
        "fan_unavailable": "Retour automatique : lecture d’un ventilateur indisponible.",
        "session_owner_mismatch": "Une autre application contrôle déjà le refroidissement.",
        "lease_expired": "La commande a expiré ; la configuration automatique est rétablie.",
        "timeout": "Le moteur n’a pas confirmé la commande à temps. Vérifiez le mode actif avant de réessayer.",
    }.get(getattr(exc, "code", str(exc)), str(exc))


def find_engine(explicit=None):
    if explicit:
        return Path(explicit).expanduser().resolve()
    base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else ROOT
    candidates = (base / "nuc-engine.exe", base / "fan-control/build/nuc-engine.exe")
    return next((path for path in candidates if path.is_file()), candidates[0])


class EngineConnection:
    def __init__(self, engine=None, *, autostart=True, allow_write=False, control_allowed=True, client_factory=NucClient, load=None):
        self.engine = find_engine(engine)
        self.autostart = autostart
        self.allow_write = allow_write
        self.control_allowed = control_allowed
        self.client_factory = client_factory
        self.client = None
        self.process = None
        self.log = None
        self.capabilities = None
        self.profiles = []
        self.engine_pid = None
        self.next_start = 0.0
        self.lease_token = None
        self.renew_enabled = False
        self.notice = ""
        self.load = load if load is not None else SystemLoad()
        self.last_load = {}

    def _start_engine(self):
        if not self.autostart:
            raise RuntimeError("Moteur arrêté. Démarrez nuc-engine.exe serve, puis réessayez.")
        if not self.engine.is_file():
            raise RuntimeError(f"Moteur introuvable : {self.engine}. Utilisez --engine pour indiquer nuc-engine.exe.")
        if time.monotonic() < self.next_start:
            raise RuntimeError("Moteur inaccessible. Nouvelle tentative dans quelques secondes.")
        self.next_start = time.monotonic() + 10
        if self.process is not None:
            if self.process.poll() is None:
                raise RuntimeError("Le moteur démarre ou ne répond plus.")
            self.process.wait()
            self.process = None
        if self.log is not None:
            self.log.close()
        self.log = tempfile.TemporaryFile()
        self.process = subprocess.Popen(
            [str(self.engine), "serve"] + (["--allow-write"] if self.allow_write else []),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=self.log, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if QThread.currentThread().isInterruptionRequested():
                raise RuntimeError("Arrêt demandé")
            if self.process.poll() is not None:
                self.log.seek(0)
                reason = self.log.read(4096).decode("utf-8", errors="replace").strip()
                raise RuntimeError(reason or "Le moteur s’est arrêté au démarrage.")
            try:
                return self.client.telemetry()
            except NucError as exc:
                if exc.win32 not in (2, 3, 231):
                    raise
                time.sleep(0.05)
        raise RuntimeError("Le moteur n’a pas répondu dans les cinq secondes.")

    def read(self, *, sample_load=True):
        if self.client is None:
            self.client = self.client_factory(timeout=1.5)
        try:
            status = self.client.telemetry()
        except NucError as exc:
            self.capabilities = None
            if exc.win32 not in (2, 3):
                raise
            status = self._start_engine()
        if self.capabilities is None or self.engine_pid != status.get("engine_pid"):
            self.capabilities = self.client.capabilities()
            self.profiles = self.client.profiles()
            self.engine_pid = status.get("engine_pid")
        self._observe(status)
        if sample_load:
            self.last_load = self.load.read()
        return {"status": status, "capabilities": self.capabilities, "profiles": self.profiles,
                "load": self.last_load, "control_owned": self.lease_token is not None,
                "control_notice": self.notice}

    def _observe(self, status):
        control = status.get("control", {})
        if self.lease_token is not None and (
                control.get("active_lease") != self.lease_token or control.get("owner_pid") != os.getpid()):
            self.lease_token = None
            self.renew_enabled = False
            reason = control.get("last_restoration", {}).get("reason", "")
            self.notice = control_error(NucError("Le contrôle a pris fin ; aucune commande n’est réappliquée.", code=reason))

    @contextmanager
    def _control_timeout(self):
        # The C controller allows 5 s for acknowledgement, plus IPC and readback.
        previous = getattr(self.client, "timeout", 1.5)
        self.client.timeout = max(previous, CONTROL_TIMEOUT_SECONDS)
        try:
            yield
        finally:
            self.client.timeout = previous

    def _cancel_unconfirmed(self):
        """A lost reply may hide an applied write. Release only our own session."""
        self.renew_enabled = False
        try:
            status = self.client.telemetry()
            control = status.get("control", {})
            if control.get("active_lease") and control.get("owner_pid") == os.getpid():
                self.client.restore()
                self.lease_token = None
                self.renew_enabled = False
        except Exception:
            # Without a confirmed owner, let the C lease expire; never reapply.
            self.renew_enabled = False

    def command(self, kind, value):
        if not self.control_allowed:
            raise RuntimeError("Surveillance seule : commandes désactivées.")
        payload = self.read(sample_load=False)
        control = payload["status"].get("control", {})
        if not payload["capabilities"].get("write_enabled"):
            raise NucError("Le moteur est en lecture seule", code="read_only")
        if control.get("active_lease") and self.lease_token is None:
            raise RuntimeError("Une autre application contrôle déjà le refroidissement.")
        with self._control_timeout():
            try:
                if kind == "profile":
                    if value not in {p["id"] for p in self.profiles}:
                        raise ValueError("Profil inconnu")
                    result = self.client.set_profile(value, lease_seconds=LEASE_SECONDS)
                elif kind == "duty":
                    result = self.client.set_duty(value, lease_seconds=LEASE_SECONDS)
                else:
                    raise ValueError("Commande inconnue")
            except NucError as exc:
                if exc.code in ("timeout", "transport_error"):
                    self._cancel_unconfirmed()
                raise
        observed = result.get("control", {})
        if not observed.get("active_lease") or observed.get("owner_pid") != os.getpid():
            raise RuntimeError("La prise de contrôle n’a pas été confirmée par le moteur.")
        self.lease_token = observed["active_lease"]
        self.renew_enabled = True
        self.notice = ""
        return self.read(sample_load=False)

    def renew(self):
        if self.lease_token is None or not self.renew_enabled:
            return
        try:
            with self._control_timeout():
                result = self.client.renew(lease_seconds=LEASE_SECONDS)
            self._observe(result)
        except Exception as exc:
            # Stop renewing; never reapply a command after a safeguard or timeout.
            self.renew_enabled = False
            self.notice = control_error(exc)
            raise

    def restore(self):
        payload = self.read(sample_load=False)
        if self.lease_token is None:
            if payload["status"].get("control", {}).get("active_lease"):
                raise RuntimeError("La session appartient à une autre application.")
            return payload
        self.renew_enabled = False
        with self._control_timeout():
            self.client.restore()
        self.lease_token = None
        self.notice = "Configuration automatique initiale rétablie."
        return self.read(sample_load=False)

    def close(self):
        self.renew_enabled = False
        try:
            if self.client is not None and self.lease_token is not None:
                self.restore()
        finally:
            if self.process is not None:
                safe_to_stop = not self.allow_write
                if self.allow_write and self.client is not None:
                    try:
                        safe_to_stop = not self.client.telemetry().get("control", {}).get("active_lease")
                    except Exception:
                        safe_to_stop = False
                if self.process.poll() is None and safe_to_stop:
                    self.process.terminate()
                if self.process.poll() is not None or safe_to_stop:
                    self.process.wait(timeout=5)
                self.process = None
            if self.log is not None:
                self.log.close()
                self.log = None
            self.load.close()


class PollWorker(QObject):
    updated = Signal(dict)
    failed = Signal(str)
    stopped = Signal()
    control_failed = Signal(str)
    busy = Signal(bool)

    def __init__(self, engine=None, *, autostart=True, allow_write=False, control_allowed=True, interval=2000, connection=None):
        super().__init__()
        self.connection = connection or EngineConnection(engine, autostart=autostart, allow_write=allow_write,
                                                        control_allowed=control_allowed)
        self.interval = interval
        self.timer = None
        self.lease_timer = None

    @Slot()
    def start(self):
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.poll)
        self.lease_timer = QTimer(self)
        self.lease_timer.setInterval(RENEW_INTERVAL_MS)
        self.lease_timer.timeout.connect(self.renew)
        self.lease_timer.start()
        self.poll()

    @Slot()
    def poll(self):
        if QThread.currentThread().isInterruptionRequested():
            return
        try:
            self.updated.emit(self.connection.read(sample_load=False))
        except Exception as exc:
            self.failed.emit(str(exc))
        if self.timer is not None:
            self.timer.start(self.interval)

    @Slot()
    def renew(self):
        if QThread.currentThread().isInterruptionRequested():
            return
        try:
            self.connection.renew()
        except Exception as exc:
            self.control_failed.emit(control_error(exc))
            self.poll()

    def _command(self, kind, value=None):
        if QThread.currentThread().isInterruptionRequested():
            return
        self.busy.emit(True)
        try:
            result = self.connection.restore() if kind == "restore" else self.connection.command(kind, value)
            self.updated.emit(result)
        except Exception as exc:
            self.connection.notice = control_error(exc)
            self.control_failed.emit(self.connection.notice)
            self.poll()
        finally:
            self.busy.emit(False)

    @Slot(str)
    def set_profile(self, profile):
        self._command("profile", profile)

    @Slot(int)
    def set_duty(self, percent):
        self._command("duty", percent)

    @Slot()
    def restore(self):
        self._command("restore")

    @Slot(int)
    def set_interval(self, interval):
        self.interval = interval
        if self.timer is not None:
            self.timer.start(interval)

    @Slot(str)
    def set_engine(self, path):
        try:
            self.connection.close()
            self.connection.engine = Path(path)
            self.connection.next_start = 0
            self.connection.capabilities = None
        except Exception as exc:
            self.connection.notice = control_error(exc)
            self.control_failed.emit(self.connection.notice)
        self.poll()

    @Slot()
    def stop(self):
        if self.timer is not None:
            self.timer.stop()
        if self.lease_timer is not None:
            self.lease_timer.stop()
        try:
            self.connection.close()
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.stopped.emit()
