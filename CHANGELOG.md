# Changelog

## 1.0.0 — 2026-10-21

First stable release of InputWeave.

- Detect SDL-standard and generic USB or Windows-paired Bluetooth gamepads.
- Map a physical controller directly or to a virtual DualShock 4 or Xbox 360 controller.
- Save button mappings, output mode, and stick/trigger settings in per-game profiles.
- Tune deadzones, sensitivity, Y-axis inversion, and automatic stick-drift calibration.
- Create button-sequence macros with per-step timing, optional repeat, and cancellation.
- Use German, English, or Arabic UI labels while retaining controller button symbols.
- Package the Windows application and its runtime in a single portable executable.
- Verify bundled installer integrity and use the signed driver installers with standard Windows UAC prompts.

Touchpad movement and gestures, gyro translation, and manufacturer-specific controller features are not included. A physical touchpad click can be mapped when the controller exposes it through SDL or raw joystick input.
