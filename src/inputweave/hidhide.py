"""Install and configure the official HidHide runtime for portable builds."""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import subprocess
import sys
from pathlib import Path

LOGGER = logging.getLogger("InputWeave")

HIDHIDE_VERSION = "1.5.230"
HIDHIDE_INSTALLER_NAME = "HidHide_1.5.230_x64.exe"
HIDHIDE_INSTALLER_SHA256 = (
    "F4BBBCB82E6258641B887C74BC81C4C5F66E4AA811808DFC304347687B7605F6"
)
HIDHIDE_RELEASE_URL = "https://github.com/nefarius/HidHide/releases/tag/v1.5.230.0"
HIDHIDE_CLI_RELATIVE_PATH = (
    Path("Nefarius Software Solutions") / "HidHide" / "HidHideCLI.exe"
)


def service_state() -> str:
    """Return the Windows service state without treating errors as absence."""
    if os.name != "nt":
        return "unsupported"

    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    try:
        result = subprocess.run(
            [str(system_root / "System32" / "sc.exe"), "query", "HidHide"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        LOGGER.exception("Could not inspect the HidHide service")
        return "unknown"

    output = result.stdout + result.stderr
    if "1060" in output or "does not exist" in output.lower():
        return "missing"
    if result.returncode != 0:
        return "unknown"
    return "running" if "RUNNING" in output else "installed"


def find_cli(
    program_files: str | None = None,
    program_files_x86: str | None = None,
) -> Path | None:
    """Find HidHideCLI across current and older official install layouts."""
    roots = [
        program_files
        or os.environ.get("ProgramW6432")
        or os.environ.get("ProgramFiles"),
        program_files_x86 or os.environ.get("ProgramFiles(x86)"),
    ]
    seen: set[str] = set()
    for root in roots:
        if not root:
            continue
        base = Path(root) / HIDHIDE_CLI_RELATIVE_PATH
        for candidate in (base, base.parent / "x64" / base.name):
            key = os.path.normcase(str(candidate))
            if key in seen:
                continue
            seen.add(key)
            if candidate.is_file():
                return candidate
    return None


def verify_installer(installer: Path) -> bool:
    """Verify the bundled release asset before starting it with elevation."""
    try:
        with installer.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest().upper()
        return digest == HIDHIDE_INSTALLER_SHA256
    except (OSError, ValueError):
        return False


def launch_installer(installer: Path) -> tuple[bool, str]:
    """Launch the official setup through Windows UAC when HidHide is absent."""
    current_state = service_state()
    if current_state in ("running", "installed"):
        return False, "already_installed"
    if current_state != "missing":
        return False, "service_state_unknown"
    architecture = (
        os.environ.get("PROCESSOR_ARCHITEW6432")
        or os.environ.get("PROCESSOR_ARCHITECTURE")
        or platform.machine()
    ).casefold()
    if architecture not in {"amd64", "x86_64", "x64"}:
        return False, "unsupported_architecture"
    if not verify_installer(installer):
        return False, "installer_verification_failed"
    if os.name != "nt":
        return False, "windows_required"

    try:
        import ctypes

        result = ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            str(installer),
            None,
            str(installer.parent),
            1,
        )
    except (AttributeError, OSError):
        LOGGER.exception("Could not start the HidHide installer")
        return False, "installer_launch_failed"

    if result <= 32:
        LOGGER.warning(
            "Windows did not start the HidHide installer (ShellExecute=%s)", result
        )
        return (
            False,
            "installer_cancelled" if result == 5 else "installer_launch_failed",
        )
    LOGGER.info("Started signed HidHide %s installer through UAC", HIDHIDE_VERSION)
    return True, "installer_started"


def register_application(
    executable: str | Path, cli: Path | None = None
) -> tuple[bool, str]:
    """Add only this executable to HidHide's allow list; preserve all other entries."""
    cli = cli or find_cli()
    if cli is None:
        return False, "cli_missing"

    try:
        app_path = Path(executable).resolve(strict=True)
    except (OSError, RuntimeError):
        return False, "application_path_unavailable"
    if app_path.suffix.lower() != ".exe" or not app_path.is_absolute():
        return False, "application_path_invalid"

    try:
        state_result = subprocess.run(
            [str(cli), "--inv-state"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if state_result.returncode != 0:
            LOGGER.warning(
                "HidHide inverse mode could not be checked: %s",
                (state_result.stderr or state_result.stdout).strip(),
            )
            return False, "cli_failed"
        if "--inv-on" in state_result.stdout.casefold():
            LOGGER.warning(
                "HidHide inverse mode is enabled; leaving the user's global setting unchanged"
            )
            return False, "inverse_mode_enabled"

        result = subprocess.run(
            [str(cli), "--app-reg", str(app_path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        LOGGER.exception("Could not register InputWeave with HidHide")
        return False, "cli_failed"

    if result.returncode != 0:
        LOGGER.warning(
            "HidHide registration failed with exit code %s: %s",
            result.returncode,
            (result.stderr or result.stdout).strip(),
        )
        return False, "cli_failed"

    LOGGER.info("Registered the current InputWeave executable with HidHide")
    return True, "registered"


def configure_current_application(
    executable: str | Path | None = None,
) -> tuple[bool, str]:
    """Whitelist a packaged app build, never the system-wide Python interpreter."""
    if executable is None:
        if not getattr(sys, "frozen", False):
            return True, "source_run"
        executable = sys.executable
    return register_application(executable)
