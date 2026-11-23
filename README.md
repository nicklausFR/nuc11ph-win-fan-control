# NUC11phi7 Cooling

A compact Windows system tray app for monitoring and controlling cooling on the
Intel NUC11PH. Built with Python and Qt (PySide6), backed by a separate C17 engine
using the installed Intel `PerformanceDriver` (`ACPI\INTC1036`).

[Documentation en français](README.fr.md) · [Interface guide](ui/README.md) ·
[API reference](API.md) · [Hardware protocol](PROTOCOL.md)

## Features

- Temperature tiles for the CPU, NVIDIA GPU, integrated GPU and each NVMe SSD.
- CPU and NVIDIA GPU utilization, with one shared fan speed bar and RPM reading.
- OEM cooling profiles: **Quiet**, **Balanced** and **Cooling**.
- Forced fan duty from **50 to 99%**, using one verified shared control channel.
- French and English interface, with Windows language detection and an immediate,
  saved language choice.
- Light, dark and Windows appearance; refresh intervals of 1, 2 or 5 seconds.
- Local named-pipe API, leased commands and a separate native recovery process.

Sensor tiles appear first, followed by shared fan controls. Unavailable readings
show a dash. The shared speed display averages the available CPU/GPU RPM readings;
the exact readings remain in its tooltip. The verified duty command is shared
by both fans. Independent fan settings, RGB and
saved custom profiles are not implemented.

```text
Qt system tray app → local named pipe → C17 engine → Intel driver → EC
```

No WinRing0 or additional kernel driver is installed. OEM and NVIDIA libraries
are not redistributed.

## Requirements and compatibility

- Windows x64.
- Validated hardware: **NUC11PHi7**, board **NUC11PHBi7**,
  BIOS **PHTGL579.0079.2024.1014.1949**.
- Intel `PerformanceDriver` already installed.
- NUC Software Studio service stopped and disabled to avoid competing control.
- NVIDIA driver with NVML available for NVIDIA GPU utilization.
- Python x64 **3.11 or later** when running from source. The packaged executables
  do not require Python.

Writes require the exact board, BIOS and driver SHA-256 pinned in
`fan-control/src/hardware.c`. Other NUC11 models and BIOS versions are not validated.

## Quick start

Start with the [source instructions](#run-from-source): Python x64 3.11+ and
the native engine built with MSVC are required. A standalone Windows executable
can optionally be [built locally](#build).

The app connects to an existing engine or starts it in the background with
`--allow-write`.

Click the tray icon to show or hide the panel. Right-click the icon, or open **⋮**,
to choose appearance, refresh interval, language, panel pinning or **Quit**.
Use **Language → English**, **Français**, or **Windows language**. The default
follows Windows: French for a French locale, English otherwise.

Select a cooling mode and click **Apply**. For forced speed, set the percentage
before applying it. Editing the selection or slider alone sends no command.
**Restore original automatic control** returns to the configuration saved before
the control session. Hiding the panel keeps monitoring and the current session
active; **Quit** closes the app and restores its session.

If an existing engine was started without `--allow-write`, the interface stays
read-only. Restart that engine explicitly with `--allow-write` to enable controls.

## Run from source

Install Python x64 3.11+, build the native engine as described below, then run:

```powershell
python -m pip install -r ui/requirements.txt
python ui/app.py --show
```

Optional arguments:

```powershell
python ui/app.py --language en --show
python ui/app.py --engine "C:\path\nuc-engine.exe"
python ui/app.py --read-only
python ui/app.py --no-start
```

`--language` overrides the saved language for this launch. A language selected
in the menu is saved for future launches. `--read-only` disables cooling commands;
`--no-start` connects only to an engine that is already running.

See the [interface guide](ui/README.md) for measurement definitions and detailed behavior.

## Control and recovery

OEM profiles retain automatic firmware regulation. Forced duty is a percentage,
not an RPM target. Custom engine thresholds use firmware-encoded units; their
mapping to Celsius has not been verified.

The app requests a **10-second lease**, renewed every **2 seconds** independently
of panel visibility and display refresh. The engine permits 1–15 seconds per
renewal, with no total duration limit while renewal continues.

A separate C guardian verifies writes and restores the saved automatic
configuration when the client or engine exits, a lease expires, or a safeguard
triggers. For forced duty and custom thresholds, restoration is triggered by
invalid temperature/RPM readings, **CPU ≥ 85 °C**, or **NVIDIA GPU ≥ 80 °C**.
The interface does not automatically reapply an interrupted command.

Recovery depends on running user-mode processes and a responsive driver.
Service installation, endurance testing and real suspend/resume validation remain
pending. See [PROTOCOL.md](PROTOCOL.md) for the measured hardware behavior and limits.

## Engine and Python API

Start the local engine directly:

```powershell
.\nuc-engine.exe serve                 # Read-only
.\nuc-engine.exe serve --allow-write   # Allow temporary control
```

From a source checkout, the built engine is in `fan-control\build\`.
In another terminal:

```powershell
python examples\demo.py --watch
python examples\catalog.py
```

The transport uses `\\.\pipe\NucControl.v1.<SID>`, restricted to the owning
Windows account and SYSTEM. Remote clients are rejected. No HTTP server or
network port is opened. See [API.md](API.md) and `python/nuc_client.py`.

## Build

For the native engine, install MSVC 2022 x64 and the Windows SDK:

```powershell
.\build.ps1 -Analyze
```

For the standalone Qt app, with Python x64 3.11+:

```powershell
python -m pip install -r ui/requirements-build.txt
python tools/build_ui.py
```

This produces `dist/nuc-cooling.exe` and copies `nuc-engine.exe` alongside it.
To create the binary and source archives, including documentation, license and
checksums:

```powershell
python tools/package.py
```

Output names are `dist/nuc-engine-<version>-windows-x64.zip` and
`dist/nuc-engine-<version>-source.zip`. Both contain the GUI sources and native
engine sources; the source archive also includes tests and verification tools.

## Validation

The following tests use fake clients and do not write to hardware:

```powershell
python -m unittest discover -s tests -p test_ui.py -v
python -m unittest discover -s tests -p test_i18n.py -v
python -m unittest discover -s tests -p test_cooling_controls.py -v
python -m unittest discover -s tests -p test_editing_responsiveness.py -v
```

`python tools/verify_ui.py` performs read-only checks against the local engine,
verifies that configuration bytes stay unchanged, and renders both languages in
light and dark themes. Results are written to `test-results/ui/`.

## Repository layout

```text
ui/           PySide6 system tray app, translations and interface guides
fan-control/  Native C engine, recovery process and build script
python/       Python API client
examples/     Command-line examples
tests/        UI, API and control tests
tools/        Build, verification and packaging tools
```

## License

[GNU GPLv3](LICENSE), `GPL-3.0-only`. The project is an independent implementation;
hardware support and verification details are documented in [PROTOCOL.md](PROTOCOL.md).
