# nuc11ph-win-fan-control

Windows x64 fan-control backend for Intel NUC11PH. Replaces the control layer of legacy Intel NUC Software Studio using the installed Intel `PerformanceDriver` (`ACPI\INTC1036`).

```text
Python client / future UI -> named pipe -> C17 engine -> Intel driver -> EC
```

No WinRing0 or additional kernel driver. Keep the NUC Software Studio service stopped and disabled.

## Hardware

Validated: `NUC11PHi7`, board `NUC11PHBi7`, BIOS `PHTGL579.0079.2024.1014.1949`.

Writes require the exact board, BIOS and driver SHA-256 pinned in `fan-control/src/hardware.c`. Other NUC11 models are not validated.

## Layout

```text
fan-control/  Native C fan controller, recovery process and build script
python/       Python API client
ui/           Future Python graphical interface
examples/     Command-line demos
tests/        API integration tests
tools/        Hardware verification and packaging
```

## Features

- CPU/GPU temperatures and fan RPM; NVMe SMART temperatures.
- OEM profiles: `quiet`, `balanced`, `cool`.
- Direct duty: **50-99%**, one verified shared fan channel.
- Custom thresholds in firmware-encoded units; Celsius mapping unverified.
- Local API with profile and safeguard discovery.

Python client included; GUI pending. Independent fan control, custom profile storage and RGB are not implemented.

## Run

From the binary package:

```powershell
.\nuc-engine.exe serve                 # Read-only
.\nuc-engine.exe serve --allow-write   # Temporary control
```

In another terminal, with Python x64 >= 3.11:

```powershell
python examples\demo.py --watch
python examples\catalog.py
```

Named-pipe access is restricted to the owner account and SYSTEM. Remote clients are rejected.

## Control and recovery

A separate C guardian verifies writes and restores the saved automatic configuration on client/engine exit or lease expiry.

Current leases allow **1-15 seconds per renewal**, with unlimited total duration while renewed. This is software policy, not a hardware limit.

For direct duty and custom thresholds, invalid temperature/RPM readings or CPU >= 85 C / NVIDIA GPU >= 80 C trigger restoration. OEM profiles retain firmware regulation.

Recovery requires running user-mode processes and a responsive driver.

## Build

MSVC 2022 x64 and Windows SDK:

```powershell
.\build.ps1 -Analyze
```

From a source checkout, run `./fan-control/build/nuc-engine.exe serve` after building.

Prototype: console control and crash recovery tested. Service installation, endurance and real suspend/resume validation pending.

[API](API.md) | [Hardware protocol](PROTOCOL.md) | [French documentation](README.fr.md) | [GNU GPLv3](LICENSE) (`GPL-3.0-only`)

Sources and the build script are included in both archives.
