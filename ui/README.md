# NUC11phi7 Cooling — Qt system tray interface

[Français](README.fr.md) · [Project README](../README.md)

A Python/PySide6 panel for Windows x64 showing CPU, GPU and NVMe temperatures,
CPU and NVIDIA GPU utilization, measured fan speeds and cooling controls.
The interface supports French and English, with light, dark and Windows themes.

Temperature/load tiles appear first: CPU, NVIDIA GPU, integrated GPU and each
SSD. Load appears only for supported components. The **Fan control** section
below the tiles contains the current mode, a single duty setting and one shared
speed bar. Its RPM value is the average of the available CPU/GPU readings;
the exact counters and automatic display scale are in its tooltip. This scale
is a display range, not a measured hardware maximum. Additional SMART temperature
sensors are in SSD tooltips.

## Launch

From the project root, with Python x64 3.11+ and a built engine:

```powershell
python -m pip install -r ui/requirements.txt
python ui/app.py --show
```

The app connects to an existing engine or starts `nuc-engine.exe serve --allow-write`
without a console, from `fan-control/build/` or beside the packaged executable.
No cooling command is sent until **Apply** is clicked. Hardware compatibility and
safeguards are checked by the native engine.

```powershell
python ui/app.py --engine "C:\path\nuc-engine.exe" --show
python ui/app.py --language en --show
python ui/app.py --no-start
python ui/app.py --read-only
```

`--no-start` only uses an existing engine. `--read-only` disables controls and
starts a read-only engine if necessary. An explicit missing engine path is
reported rather than silently replaced.

An external engine started without `--allow-write` remains read-only. Restart
it explicitly with `--allow-write` to enable control. The app does not replace or
restore another application's control session.

## Usage and language

- Click the tray icon to show or hide the panel near the notification area.
- Click outside to hide it, unless **Keep panel on top** is enabled in the menu.
- Press Escape to hide the panel.
- Right-click the icon, or click **⋮**, to open the options menu.
- Choose **Language → English**, **Français**, or **Windows language**.
- Hover over the tray icon for temperatures, load, fan speeds and cooling mode.
- Choose a refresh interval of 1, 2 or 5 seconds; the default is 2 seconds.
- Hover over an SSD tile for its identifier and additional SMART sensors.

Language changes take effect immediately and are saved in `QSettings` under
`nicklausFR/NucCooling`, along with appearance, refresh and pinning preferences.
**Windows language** uses French for French Windows locales and English otherwise.
`--language fr|en|auto` overrides the saved language for one launch; a menu choice
updates the saved preference. Switching languages preserves the selected mode,
draft percentage and active control session. Raw, unrecognized driver diagnostics
are preserved as reported.

Hiding or closing the panel keeps the app running. Choose **Quit** to exit.

## Profiles and forced speed

Select **Quiet**, **Balanced** or **Cooling**, then click **Apply**.
These OEM profiles retain automatic firmware regulation.

Select **Shared forced speed**, set a duty between **50 and 99%**, then click
**Apply**. The percentage controls the shared CPU/GPU fan channel; it is not an
RPM target. Both exact RPM readings remain available in the shared speed tooltip. Independent fan commands
and fan stop are not exposed.

**Restore original automatic control** restores the configuration saved before
the session. Changing a selection or moving the slider sends no command.
Hardware settings are not automatically reapplied at startup.

An edited percentage remains **Pending** until **Apply** is clicked. During
**Applying…**, the slider and field remain editable. A new draft does not change
the command already sent and can be applied after confirmation. Refreshes do not
overwrite an edit. The panel grows to fit its contents and scrolls on small screens.

Each command uses a **10-second lease**, renewed every **2 seconds**, even while
the panel is hidden and independently of display refresh. Normal exit restores
the app's session. The C engine and its guardian also handle expiry and client
disappearance. Recovery limits are described in the [project README](../README.md).

Forced duty is refused or interrupted if CPU temperature reaches **85 °C**, NVIDIA
GPU temperature reaches **80 °C**, or a temperature/fan reading becomes invalid.
The interface reports the automatic restoration and does not reapply the duty.
The OEM **Cooling** profile retains its own firmware regulation.

## Measurements

CPU load is the percentage of busy time between Windows `GetSystemTimes` samples;
the first sample shows a dash. NVIDIA GPU load comes from
`nvmlDeviceGetUtilizationRates` in the installed NVIDIA driver's NVML library.
No NVIDIA driver or DLL is redistributed. Sampling periods and counter definitions
can differ from Task Manager. Integrated GPU load is not included in NVIDIA load.
A missing counter shows a dash, distinct from a measured load of 0%.

The current mode comes from `telemetry.control.mode`. In automatic mode, a profile
is identified only if both encoded thresholds and both minimum fan duties match
exactly one entry in the engine's `profiles()` catalog. This does not verify every
OEM byte. Encoded thresholds are not displayed as Celsius.

SSDs use SMART composite temperature (index 0), cached by the engine for 10 seconds.
Reconnection is automatic, with stale readings cleared. A simulated engine is
explicitly marked **SIMULATION**.

CPU/GPU load sampling runs in its own thread, separate from commands and lease
renewal. Slow NVIDIA reads therefore do not delay fan control. Command IPC waits
up to 8 seconds to cover the engine's 5-second acknowledgement window. A lost
reply triggers a restoration attempt only when the engine confirms this instance
owns the session.

## Build and validation

```powershell
python -m pip install -r ui/requirements-build.txt
python tools/build_ui.py
python -m unittest discover -s tests -p test_ui.py -v
python -m unittest discover -s tests -p test_i18n.py -v
python -m unittest discover -s tests -p test_cooling_controls.py -v
python -m unittest discover -s tests -p test_editing_responsiveness.py -v
python tools/verify_ui.py
```

Run `dist/nuc-cooling.exe` with `dist/nuc-engine.exe` beside it.
`verify_ui.py` does not apply a mode: it reads three samples, checks EC bytes and
thread shutdown, then saves French/English light/dark captures and forced-control
layout captures in `test-results/ui/`. Use `--language fr` or `--language en`
to render one language only.

`python tools/verify_cooling_controls.py` requires a compiled simulator and no
other engine. It refuses commands unless `simulated` is true. It verifies the
three profiles, duty renewal beyond its initial duration, thermal restoration
without reapplication and restoration on exit.

Sources are licensed under GPL-3.0-only, like the engine.
