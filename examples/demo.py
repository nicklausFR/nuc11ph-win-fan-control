# SPDX-License-Identifier: GPL-3.0-only
"""Démonstration des mesures : aucune commande de ventilateur n’est envoyée."""
import argparse
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"python"))
from nuc_client import NucClient, NucError


def display(status):
    names = {"cpu":"CPU", "nvidia_gpu":"GPU NVIDIA", "integrated_gpu":"GPU intégré"}
    print("\nNUC — mesures réelles")
    print("─"*42)
    for sensor in status["temperatures"]:
        value = f"{sensor['celsius']} °C" if sensor["available"] else "indisponible"
        print(f"{names.get(sensor['id'],sensor['id']):22} {value:>17}")
    for fan in status["fans"]:
        print(f"{'Ventilateur '+names.get(fan['id'],fan['id']):22} {fan['rpm']:>10} tr/min")
    for disk in status["ssds"]:
        composite = next((s["celsius"] for s in disk["sensors"] if s["index"]==0),None)
        value = f"{composite} °C" if composite is not None else "indisponible"
        print(f"{disk['model']:22} {value:>17}")
    print("─"*42)
    print("Mode :",status["control"]["mode"]," |  aucune modification")


def main():
    if hasattr(sys.stdout,"reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch",action="store_true",help="Actualiser toutes les secondes ; Ctrl+C pour arrêter.")
    parser.add_argument("--samples",type=int,default=1,help="Nombre de relevés, de 1 à 60.")
    args = parser.parse_args()
    if not 1<=args.samples<=60:
        parser.error("--samples : entier de 1 à 60")
    process = None
    client = NucClient(timeout=5)
    try:
        try:
            client.capabilities()
        except NucError as exc:
            if exc.win32!=2:
                raise
            executable = ROOT/"nuc-engine.exe"
            if not executable.exists():
                executable = ROOT/"fan-control"/"build"/"nuc-engine.exe"
            process = subprocess.Popen([str(executable),"serve"],creationflags=subprocess.CREATE_NO_WINDOW,
                                       stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            deadline = time.monotonic()+5
            while True:
                try:
                    client.capabilities()
                    break
                except NucError:
                    if process.poll() is not None:
                        raise RuntimeError(process.stderr.read().decode(errors="replace"))
                    if time.monotonic()>=deadline:
                        raise
                    time.sleep(.05)
        count = 0
        while True:
            display(client.telemetry())
            count += 1
            if not args.watch and count>=args.samples:
                break
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        client.close()
        if process is not None:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)
            process.stderr.close()


if __name__=="__main__":
    main()
