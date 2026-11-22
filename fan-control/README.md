# Native C fan controller

- `src/`: Windows entry point, Intel IOCTL transport, control policy, named-pipe API and recovery process.
- `build.ps1`: MSVC x64 build and native self-checks.
- `build/`: generated executables and object files; excluded from Git.

Build from the repository root with `./build.ps1 -Analyze`, or run this directory's build script directly. The production executable is `fan-control/build/nuc-engine.exe`.

The engine owns hardware access and recovery. Python clients communicate through the local API documented in [API.md](../API.md). Hardware details are in [PROTOCOL.md](../PROTOCOL.md).
