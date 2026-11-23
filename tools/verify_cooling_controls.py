# SPDX-License-Identifier: GPL-3.0-only
"""Exercise the UI backend against the SIMULATED C engine only, never real writes."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ui"))
from backend import EngineConnection, NucClient, NucError


def wait_for(predicate, timeout=6):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(.05)
    raise RuntimeError("Délai de validation dépassé")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, default=ROOT / "fan-control/build/nuc-engine-test.exe")
    parser.add_argument("--output", type=Path, default=ROOT / "test-results/ui-controls.json")
    args = parser.parse_args()
    probe = NucClient(timeout=2)
    try:
        probe.capabilities()
    except NucError as exc:
        if exc.win32 not in (2, 3):
            raise
    else:
        raise RuntimeError("Arrêtez le moteur existant avant ce test isolé. Aucun moteur actif n’a été modifié.")
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen([str(args.engine.resolve()), "serve", "--allow-write"],
                                   stdout=subprocess.DEVNULL, stderr=log,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        connection = EngineConnection(autostart=False)
        checks = []
        try:
            def ready():
                if process.poll() is not None:
                    raise RuntimeError("Le moteur simulé s’est arrêté")
                try:
                    return probe.capabilities()
                except NucError:
                    return None
            caps = wait_for(ready)
            if not caps.get("simulated") or not caps.get("write_enabled"):
                raise RuntimeError("Ce test refuse toute commande sur le matériel réel")
            original = probe.telemetry()["ec_hex"]
            for profile in ("quiet", "balanced", "cool"):
                selected = connection.command("profile", profile)
                assert selected["control_owned"]
                assert selected["status"]["control"]["mode"] == "profile"
                connection.renew()
                connection.restore()
                assert probe.telemetry()["ec_hex"] == original
                checks.append("profile_" + profile)
            duty = connection.command("duty", 75)
            assert duty["status"]["manual_channels"][0]["duty_percent"] == 75
            # A command survives the initial ten-second lease through renewals.
            for _ in range(6):
                time.sleep(2)
                connection.renew()
            assert connection.read()["control_owned"]
            checks.append("forced_speed_renewed_beyond_initial_lease")
            probe._call(100, (0, 85))  # Fault injection is absent from the production engine.
            wait_for(lambda: probe.telemetry()["control"]["mode"] == "automatic")
            assert not connection.read()["control_owned"]
            connection.renew()
            assert probe.telemetry()["ec_hex"] == original
            checks.append("thermal_restore_without_reapplication")
            probe._call(100, (0, 55))
            connection.command("duty", 70)
            connection.close()
            assert probe.telemetry()["ec_hex"] == original
            checks.append("close_restores_original_configuration")
            report = dict(simulated=True, checks=checks, restored=True)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps(report, indent=2))
        finally:
            connection.close()
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)


if __name__ == "__main__":
    main()
