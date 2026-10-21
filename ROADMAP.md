# InputWeave roadmap

## Implemented

- SDL gamepad discovery with positional defaults and controller-family labels
- Raw joystick support for devices without an SDL gamepad mapping, including editable buttons, hats, and analog axes
- Direct passthrough, virtual PlayStation 4 output, and virtual Xbox 360 output
- Per-game profiles and device-specific mappings for raw joysticks
- Live button, stick, and trigger telemetry
- Profile-local button-sequence macros with per-step timing, button assignment, optional looping, and cancellation
- Drift calibration, deadzones, sensitivity, and axis inversion
- German, English, and Arabic interface translations with platform button glyphs
- A portable single-file Windows executable with a bundled runtime and verified ViGEmBus installer
- Application paths based on Windows environment variables; existing ForzaPad profiles migrate once to the InputWeave profile folder

## Planned

1. **Guided mapping** — press a controller button to select it as an input and preview the output.
2. **Profile management** — automatic profile selection when a game starts, profile import/export, and reset to defaults.
3. **Calibration assistant** — guided stick and trigger checks with live curves and a drift report.
4. **Additional sensors** — optional gyro-to-stick mapping and device-specific touchpad or rear-button support.
5. **Macro expansion** — optional recording, conditional steps, and import/export after further compatibility review.
6. **Driver migration** — ViGEmBus is archived; any replacement must be separately evaluated for Windows and game compatibility before migration.

## Distribution

`build-portable.ps1` creates or replaces the single local `InputWeave-Test.exe` with Pygame, vgamepad, Python, the application logo, and the signed HidHide and ViGEmBus installers bundled. A separately named release build is created only after the version is agreed stable. Missing drivers use their visible signed installers and Windows UAC. InputWeave adds only its own executable to HidHide's allow list; the user chooses whether to hide the physical controller.
