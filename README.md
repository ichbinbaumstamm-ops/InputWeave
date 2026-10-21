# InputWeave

![InputWeave controller logo](resources/inputweave-logo.png)

**InputWeave maps wired and Windows-recognized Bluetooth PC gamepads to configurable virtual PlayStation 4 or Xbox 360 controllers, with per-game profiles, remappable buttons, assignable button-sequence macros with optional looping, deadzone tuning, and stick-drift calibration in a portable Windows app.**

InputWeave reads gamepads through Pygame and SDL. It uses SDL's standardized gamepad layout when available, with a raw joystick fallback for other devices. The application supports German, English, and Arabic; controller labels retain familiar glyphs such as `A (✕)`, `B (○)`, `X (□)`, and `Y (△)` regardless of language.

## Controller support and limits

- SDL-recognized gamepads use consistent positional controls across PlayStation, Xbox, Nintendo, and other supported models. InputWeave identifies the device family and applies positional defaults automatically.
- Raw SDL joystick devices are also listed. They receive a common starting layout for buttons, hats, and axes. Raw controls can be edited and saved per device and game profile; verify their order because generic HID devices do not report a universal layout.
- InputWeave scans all devices SDL exposes; it does not restrict discovery to USB. Pair a Bluetooth controller in Windows first. Once Windows exposes it as a game controller, InputWeave reads and maps it through the same SDL path as a wired device. Bluetooth pairing and device-specific firmware support remain the responsibility of Windows and the controller.
- The available virtual output identities are a virtual PlayStation 4 controller and a virtual Xbox 360 controller. Windows has no single generic virtual identity for every manufacturer's proprietary controller. Direct mode leaves the physical device visible and does not apply filters.
- Gyro, touchpad movement, adaptive triggers, and manufacturer-specific features are not currently translated. The DualShock 4 touchpad click can be mapped to a virtual DS4 touchpad click. When using Xbox 360 output, map the physical touchpad click to an Xbox button because XInput has no touchpad control. SDL mappings that omit Share or touchpad expose their common HID fallback buttons in the PlayStation mapping editor.

SDL standardizes face buttons by physical position: bottom, right, left, and top. InputWeave maps those positions to Xbox A, B, X, and Y respectively (PlayStation ✕, ○, □, and △). The mapping can be edited per profile. See the [Pygame SDL controller API](https://www.pygame.org/docs/ref/sdl2_controller.html), [Pygame joystick API](https://www.pygame.org/docs/ref/joystick.html), and SDL's [gamepad button definitions](https://wiki.libsdl.org/SDL3/SDL_GamepadButton).

## Single-file build

Run `build-portable.ps1` from this project on Windows with Python 3.11 installed. It creates or replaces the single local test build, `InputWeave-Test.exe`, on the current user's Desktop. Close the app before rebuilding if Windows reports that the file is in use. Create a separate release build only after the project has been agreed stable, using an explicit name such as `./build-portable.ps1 -OutputFileName 'InputWeave-Release.exe'`; custom output files are never overwritten. Python, app libraries, and the signed HidHide and ViGEmBus installers are packed into the executable, so recipients do not need to install Python or Pygame. On first use, InputWeave starts HidHide setup if it is missing, then adds the current InputWeave executable path to HidHide's application allow list. It also starts the ViGEmBus installer if that driver is missing. Both use Windows UAC and their standard installer windows; they may request a restart. This bundle uses the official HidHide x64 release for Windows 10/11 on Intel/AMD. The executable is not code-signed, so Windows may show a publisher warning when it is shared.

Before launching an installer, InputWeave verifies the bundled checksum and checks whether the corresponding driver service is already present. Windows displays its normal UAC prompt, so recipients can cancel the installation. It never replaces an existing ViGEmBus installation. HidHide is only installed if its service is absent; an existing installation is left intact. The app adds only its own current executable path to HidHide, preserving other application and device entries. In virtual modes, select only the physical controller in HidHide's Devices tab and enable device hiding to prevent games from seeing both the physical and virtual controllers. Direct mode does not need device hiding. InputWeave never hides all controllers or changes the global cloak setting.

The ViGEmBus project has been archived by its maintainer. The bundled signed installer is the final official release; if the driver is already installed, InputWeave leaves it alone. Its SHA-256 is `89220A7865076B342892F98865F3499FB7C4CFD673159E89D352C360FD014C6A`. See the included `resources/ViGEmBus-LICENSE.txt` and the official [ViGEmBus 1.22.0 release](https://github.com/nefarius/ViGEmBus/releases/tag/v1.22.0).

HidHide is bundled from the official [HidHide v1.5.230 release](https://github.com/nefarius/HidHide/releases/tag/v1.5.230.0), signed by Nefarius Software Solutions. Its SHA-256 is `F4BBBCB82E6258641B887C74BC81C4C5F66E4AA811808DFC304347687B7605F6`; the included `resources/HidHide-LICENSE.txt` contains its MIT license notice.

InputWeave also bundles the `vgamepad` library from its pinned upstream revision. Its MIT notice is included in `resources/vgamepad-LICENSE.txt`; only the runtime library and client DLLs are packaged, while its unused nested installer packages are excluded.

## Run from source

Use Python 3.11 or newer. In PowerShell, from this project directory:

```powershell
$env:VGAMEPAD_SKIP_VIGEMBUS_INSTALL = 'true'
python -m pip install -r requirements.txt
python src/inputweave/main.py
```

The environment variable prevents `vgamepad` from starting its own driver installer during package installation. InputWeave checks the driver service and installer hash itself, then starts the visible signed installer through Windows UAC when needed.

## Use

1. Connect a gamepad by USB or pair it in Windows over Bluetooth. InputWeave scans all SDL joystick devices rather than filtering by connection type or device name.
2. Select a game profile and output mode. **Direct** leaves the physical controller unchanged. **Virtual PlayStation** applies filters and presents a virtual DS4. **Virtual Xbox 360** translates input for games that require XInput.
3. SDL-recognized gamepads receive a positional default mapping. In **Map buttons**, click a control on the 2D controller diagram to select its mapping row; the diagram lights up while a button is held. Button and analog-axis changes save as soon as you select a new value; **Save** remains available to save and close the dialog. Each row also opens the macro editor for that input. On PlayStation controllers, the Share and Touchpad HID fallback rows help when an SDL mapping does not report those controls through its standardized layout. For generic raw devices, review the suggested buttons and assign analog axes. Raw controls are saved per device and game profile.
4. Open **Macros** from a mapping row or the profile toolbar to choose a controller button as a trigger, detect a physical button by pressing it, add output-button steps, and configure press and pause durations. Standard PlayStation layouts include Touchpad click. If SDL does not expose it as a standard control, detection also reads raw joystick buttons and can bind that raw input to the visible mapping row. Macros are saved with the game profile (and per raw device when needed), run once by default, or repeat until cancelled when **Repeat until cancelled** is enabled. Press the assigned trigger again or use **Cancel running macro** to stop playback. A macro replaces the trigger's normal button mapping. Macros require a virtual output; Direct mode leaves the physical controller unchanged.
5. In virtual modes, select only the physical gamepad in HidHide and enable device hiding. InputWeave automatically adds its current executable to HidHide's allow list. Direct mode does not need device hiding.

InputWeave automatically adds its current portable EXE path to HidHide's application allow list. It checks that HidHide's inverse mode is off and leaves that global option unchanged. If the HidHide Configuration Client is open, close it before using **Check** because HidHide allows one configuration client at a time. You only need to choose which physical controller HidHide should hide; this device choice is not automatic because hiding a different controller could make it disappear from other games. HidHide's command-line client is used from its official Program Files location, including older `x64` layouts.

Profiles store output mode, button mappings, analog-axis assignments, and stick/trigger settings per game. Settings include automatic stick-centre calibration, radial deadzones, trigger threshold, sensitivity, Y-axis inversion, and 60/125/250 Hz output rate. Filters work with either virtual output; direct mode is intentionally unchanged. Logs are stored under `%LOCALAPPDATA%\InputWeave\logs` and profiles under `%APPDATA%\InputWeave`. On first launch, InputWeave copies a valid profile store from an earlier ForzaPad installation when no InputWeave profile exists yet.

## Development

- App and UI: `src/inputweave/main.py`
- Controller controls and profile normalization: `src/inputweave/controls.py`
- Macro validation and playback: `src/inputweave/macros.py`
- SDL and generic joystick input: `src/inputweave/input_devices.py`
- Runtime dependencies pinned in `requirements.txt`
- Portable build: `build-portable.ps1`
- Feature plan: `ROADMAP.md`

InputWeave does not upload controller input or profile data. Each profile can contain up to 32 macros with at most 16 steps apiece. Looping is opt-in and continues until canceled; check the rules of online games before using automated input.

## License

InputWeave is distributed under the [MIT License](LICENSE). The bundled driver installers and libraries retain their separate license notices in `resources/`.

## Tests

From the repository root, run the standard-library unit tests with:

```powershell
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests -v
```

## Release security check

Install Git, PowerShell, and Gitleaks for Windows from the [official Gitleaks releases](https://github.com/gitleaks/gitleaks/releases). From the repository root, run:

```powershell
./scripts/release-check.ps1
```

The check scans current files (including untracked files), scans Git history when a commit exists, and checks that setup and release instructions remain present. A scan finding, scanner error, or README check failure blocks release. Before pushing, review the exact commit, repository, and target branch, then rerun the check after the commit so its history is included. The check never pushes to GitHub.

## Automatic GitHub releases

The GitHub Actions workflow in `.github/workflows/release.yml` builds the portable Windows executable and publishes it as a GitHub Release whenever a version tag such as `v1.0.0` is pushed. It runs the release security check first; any finding or error stops publication. The workflow needs GitHub Actions enabled and permission to write repository contents. It uses the repository's built-in `GITHUB_TOKEN` and does not need a personal access token.

To publish version 1.0, first commit the reviewed release-ready source to the intended default branch with `pyproject.toml` set to `1.0.0`. Make sure the GitHub repository contains the workflow and all build resources. Run `./scripts/release-check.ps1` locally, review the exact commit, repository, and target branch, then create and push the version tag only after explicitly deciding to publish:

```powershell
git tag v1.0.0
git push origin v1.0.0
```

Pushing the tag starts the workflow and creates a GitHub Release with generated notes and `InputWeave-v1.0.0.exe`. Review the Actions run and release page to confirm publication.
