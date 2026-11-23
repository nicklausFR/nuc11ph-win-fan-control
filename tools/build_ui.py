# SPDX-License-Identifier: GPL-3.0-only
"""Build the Qt tray application and its separate guarded native engine."""
import argparse
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ui"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, default=ROOT / "fan-control/build/nuc-engine.exe")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Windows x64 requis pour construire cet exécutable")
    if not args.engine.is_file():
        parser.error("Compilez d’abord le moteur avec .\\build.ps1")
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtWidgets import QApplication
    from app import fan_icon
    application = QApplication([])
    build = ROOT / "build/ui"
    build.mkdir(parents=True, exist_ok=True)
    images = []
    for size in (16, 24, 32, 48, 64, 128, 256):
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        if not fan_icon(256).pixmap(size, size).save(buffer, "PNG"):
            raise RuntimeError("Impossible de créer l’icône")
        images.append((size, bytes(buffer.data())))
    offset = 6 + 16 * len(images)
    directory = bytearray()
    for size, data in images:
        directory.extend(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    icon = build / "nuc-cooling.ico"
    icon.write_bytes(struct.pack("<HHH", 0, 1, len(images)) + directory + b"".join(data for _, data in images))
    environment = os.environ.copy()
    environment["PYINSTALLER_CONFIG_DIR"] = str(build / "cache")
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--windowed", "--noupx",
        "--name", "nuc-cooling", "--icon", str(icon), "--paths", str(ROOT / "python"),
        "--distpath", str(ROOT / "dist"), "--workpath", str(build / "pyinstaller"),
        "--specpath", str(build), "--exclude-module", "PyQt5", "--exclude-module", "PyQt6",
        "--exclude-module", "PySide2", str(ROOT / "ui/app.py"),
    ], cwd=ROOT, env=environment, check=True)
    shutil.copy2(args.engine, ROOT / "dist/nuc-engine.exe")
    print(f"Prêt : {ROOT / 'dist/nuc-cooling.exe'} (conserver nuc-engine.exe à côté)")
    application.quit()


if __name__ == "__main__":
    main()
