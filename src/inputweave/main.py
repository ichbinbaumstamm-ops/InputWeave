"""InputWeave: configurable gamepad input and virtual-controller bridge."""

from __future__ import annotations

import ctypes
import hashlib
import importlib.util
import logging
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

# Support both `python src/inputweave/main.py` and package imports in builds.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inputweave.bridge import BridgeWorker
from inputweave.controls import (
    DEFAULT_BUTTON_MAPPING,
    OUTPUT_MODES,
    STANDARD_BUTTONS,
    available_target_ids,
    default_output_mode,
    normalized_mapping,
    normalized_settings,
    target_label,
)
from inputweave.hidhide import (
    HIDHIDE_INSTALLER_NAME,
    HIDHIDE_VERSION,
    configure_current_application,
)
from inputweave.hidhide import (
    find_cli as find_hidhide_cli,
)
from inputweave.hidhide import (
    launch_installer as launch_hidhide_installer,
)
from inputweave.hidhide import (
    service_state as hidhide_service_state,
)
from inputweave.hidhide import (
    verify_installer as verify_hidhide_installer,
)
from inputweave.input_devices import (
    RAW_AXIS_CONTROLS,
    InputDevice,
    button_controls,
    inferred_button_mapping,
    normalized_axis_mapping,
)
from inputweave.localization import (
    LANGUAGES,
    axis_label,
    canonical_text,
    control_label,
    language_code,
    language_name,
    output_mode_label,
    translate,
    translate_format,
)
from inputweave.macros import MAX_MACRO_STEPS, MAX_MACROS, normalized_macros
from inputweave.profile_store import (
    load_profile_store as read_profile_store,
)
from inputweave.profile_store import (
    migrate_profile_store,
    updated_mapping_profile,
    write_profile_store,
)

LOCAL_APP_DATA = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
ROAMING_APP_DATA = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
APP_DATA_NAME = "InputWeave"
LOG_DIR = LOCAL_APP_DATA / APP_DATA_NAME / "logs"
LOG_FILE = LOG_DIR / "inputweave.log"
PROFILE_DIR = ROAMING_APP_DATA / APP_DATA_NAME
PROFILE_FILE = PROFILE_DIR / "profiles.json"
LEGACY_PROFILE_FILE = ROAMING_APP_DATA / "ForzaPad" / "profiles.json"
LOGGER = logging.getLogger("InputWeave")
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
DRIVER_INSTALLER = RESOURCE_ROOT / "resources" / "ViGEmBus_1.22.0_x64_x86_arm64.exe"
DRIVER_INSTALLER_SHA256 = (
    "89220A7865076B342892F98865F3499FB7C4CFD673159E89D352C360FD014C6A"
)
DRIVER_RELEASE_URL = "https://github.com/nefarius/ViGEmBus/releases/tag/v1.22.0"
HIDHIDE_INSTALLER = RESOURCE_ROOT / "resources" / HIDHIDE_INSTALLER_NAME
SC_EXECUTABLE = (
    Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "sc.exe"
)


def _service_state(service_name: str) -> str:
    if os.name != "nt":
        return "unsupported"
    try:
        result = subprocess.run(
            [str(SC_EXECUTABLE), "query", service_name],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    output = result.stdout + result.stderr
    if "1060" in output or "does not exist" in output.lower():
        return "missing"
    if result.returncode != 0:
        return "unknown"
    if "RUNNING" in output:
        return "running"
    return "installed"


def _verified_driver_installer() -> bool:
    try:
        with DRIVER_INSTALLER.open("rb") as installer:
            digest = hashlib.file_digest(installer, "sha256").hexdigest().upper()
        return digest == DRIVER_INSTALLER_SHA256
    except (OSError, ValueError):
        return False


def dependency_report() -> dict[str, dict[str, object]]:
    driver_state = _service_state("ViGEmBus")
    hidhide_state = hidhide_service_state()
    hidhide_cli = find_hidhide_cli()
    return {
        "Pygame": {
            "ok": importlib.util.find_spec("pygame") is not None,
            "detail": "Gamepad-Eingabe",
        },
        "vgamepad": {
            "ok": importlib.util.find_spec("vgamepad") is not None,
            "detail": "Virtuelle Xbox-Ausgabe",
        },
        "ViGEmBus-Treiber": {
            "ok": driver_state in ("running", "installed"),
            "state": driver_state,
            "detail": {
                "running": "läuft",
                "installed": "installiert",
                "missing": "fehlt",
                "unknown": "Status nicht lesbar; Installation bleibt gesperrt",
                "unsupported": "nur unter Windows verfügbar",
            }.get(driver_state, "Status unbekannt"),
        },
        "HidHide": {
            "ok": hidhide_state in ("running", "installed"),
            "state": hidhide_state,
            "cli_available": hidhide_cli is not None,
            "detail": "installiert"
            if hidhide_state in ("running", "installed")
            else "nicht installiert",
        },
        "HidHide-Installer": {
            "verified": _verified_hidhide_installer(),
            "version": HIDHIDE_VERSION,
        },
        "Treiberinstaller": {
            "verified": _verified_driver_installer(),
            "version": "1.22.0",
        },
    }


def _verified_hidhide_installer() -> bool:
    return verify_hidhide_installer(HIDHIDE_INSTALLER)


def launch_driver_installer() -> tuple[bool, str]:
    state = _service_state("ViGEmBus")
    if state in ("running", "installed"):
        return (
            False,
            "ViGEmBus ist bereits installiert. InputWeave ändert oder ersetzt den vorhandenen Treiber nicht.",
        )
    if state != "missing":
        return (
            False,
            "Der Treiberstatus ist nicht eindeutig. Zur Sicherheit wird keine Installation gestartet.",
        )
    if not _verified_driver_installer():
        return (
            False,
            "Der gebündelte Installer fehlt oder seine Prüfsumme stimmt nicht.",
        )
    if os.name != "nt" or not hasattr(ctypes, "windll"):
        return False, "Die Treiberinstallation ist nur unter Windows möglich."
    result = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", str(DRIVER_INSTALLER), None, str(DRIVER_INSTALLER.parent), 1
    )
    if result <= 32:
        if result == 5:
            return False, "Windows hat die UAC-Anfrage abgelehnt oder abgebrochen."
        return (
            False,
            f"Windows konnte den Treiberinstaller nicht starten (Fehler {result}).",
        )
    return (
        True,
        "Windows hat den signierten Nefarius-Installer mit Administratorbestätigung geöffnet. Nach Abschluss bitte den Treiberstatus erneut prüfen.",
    )


def load_profile_store() -> dict:
    try:
        migrate_profile_store(PROFILE_FILE, LEGACY_PROFILE_FILE, LOGGER)
    except (OSError, TypeError, ValueError):
        LOGGER.exception("Could not migrate the legacy profile store")
    return read_profile_store(PROFILE_FILE, LOGGER)


def configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(message)s")
    )
    LOGGER.setLevel(logging.INFO)
    LOGGER.handlers.clear()
    LOGGER.addHandler(handler)
    LOGGER.info("InputWeave starting; executable=%s", sys.executable)
    LOGGER.info("Profile storage=%s", PROFILE_FILE)


class SingleInstanceGuard:
    """Own the Windows mutex that prevents conflicting profile writers."""

    def __init__(self, kernel32=None, handle=None):
        self._kernel32 = kernel32
        self._handle = handle

    def release(self) -> None:
        if self._kernel32 is not None and self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


def acquire_single_instance() -> tuple[SingleInstanceGuard, bool]:
    """Return a guard and whether another InputWeave instance already exists."""
    if os.name != "nt":
        return SingleInstanceGuard(), False
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = (
            ctypes.c_void_p,
            ctypes.c_bool,
            ctypes.c_wchar_p,
        )
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        kernel32.CloseHandle.restype = ctypes.c_bool
        handle = kernel32.CreateMutexW(None, False, "Local\\InputWeave.SingleInstance")
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        already_running = ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS
        if already_running:
            kernel32.CloseHandle(handle)
            return SingleInstanceGuard(), True
        return SingleInstanceGuard(kernel32, handle), False
    except (AttributeError, OSError):
        LOGGER.exception("Could not create the InputWeave single-instance mutex")
        return SingleInstanceGuard(), False


class ToggleSwitch(ttk.Frame):
    """Small accessible-feeling switch control drawn with Tk Canvas."""

    def __init__(self, master, text: str, variable: tk.BooleanVar):
        super().__init__(master)
        self.variable = variable
        self.canvas = tk.Canvas(
            self,
            width=52,
            height=30,
            bd=0,
            highlightthickness=0,
            background="#0b1220",
            cursor="hand2",
        )
        self.canvas.pack(side="left", padx=(0, 9))
        self.label = ttk.Label(self, text=text, cursor="hand2")
        self.label.pack(side="left", anchor="w")
        self.canvas.bind("<Button-1>", self.toggle)
        self.label.bind("<Button-1>", self.toggle)
        self.variable.trace_add("write", self.draw)
        self.draw()

    def toggle(self, _event=None) -> None:
        self.variable.set(not self.variable.get())

    def draw(self, *_args) -> None:
        enabled = self.variable.get()
        fill = "#0284c7" if enabled else "#334155"
        self.canvas.delete("all")
        self.canvas.create_oval(0, 2, 28, 28, fill=fill, outline=fill)
        self.canvas.create_rectangle(14, 2, 38, 28, fill=fill, outline=fill)
        self.canvas.create_oval(24, 2, 52, 28, fill=fill, outline=fill)
        knob_left = 28 if enabled else 4
        self.canvas.create_oval(
            knob_left, 5, knob_left + 22, 27, fill="#ffffff", outline="#ffffff"
        )


class InputWeaveApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("InputWeave")
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        window_width = min(1020, max(360, screen_width - 48))
        window_height = min(820, max(360, screen_height - 80))
        root.minsize(min(620, window_width), min(480, window_height))
        left = max(0, (screen_width - window_width) // 2)
        top = max(0, (screen_height - window_height) // 2)
        root.geometry(f"{window_width}x{window_height}+{left}+{top}")
        self.root.configure(background="#0b1220")
        style = ttk.Style(root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background="#0b1220")
        style.configure(
            "Card.TFrame", background="#111c2f", relief="flat", borderwidth=0
        )
        style.configure(
            "TLabel", background="#0b1220", foreground="#e2e8f0", font=("Segoe UI", 10)
        )
        style.configure(
            "Title.TLabel", font=("Segoe UI", 25, "bold"), foreground="#f8fafc"
        )
        style.configure("SubTitle.TLabel", foreground="#94a3b8")
        style.configure(
            "Section.TLabel", font=("Segoe UI", 9, "bold"), foreground="#67e8f9"
        )
        style.configure("Card.TLabel", background="#111c2f", foreground="#e2e8f0")
        style.configure("CardMuted.TLabel", background="#111c2f", foreground="#94a3b8")
        style.configure(
            "TButton", padding=(10, 7), background="#1e293b", foreground="#f8fafc"
        )
        style.map("TButton", background=[("active", "#334155"), ("pressed", "#0ea5e9")])
        style.configure(
            "Accent.TButton",
            padding=(16, 10),
            background="#0284c7",
            foreground="#ffffff",
            font=("Segoe UI", 10, "bold"),
        )
        style.map(
            "Accent.TButton", background=[("active", "#0369a1"), ("pressed", "#075985")]
        )
        style.configure(
            "Mode.TRadiobutton",
            background="#18263a",
            foreground="#cbd5e1",
            padding=(12, 12),
            font=("Segoe UI", 10, "bold"),
        )
        style.map(
            "Mode.TRadiobutton",
            background=[("selected", "#083344"), ("active", "#22334a")],
            foreground=[("selected", "#a5f3fc"), ("!selected", "#cbd5e1")],
        )
        style.configure(
            "Dark.TCombobox",
            fieldbackground="#162235",
            background="#162235",
            foreground="#f8fafc",
            selectbackground="#164e63",
            selectforeground="#f8fafc",
            arrowcolor="#e2e8f0",
            padding=6,
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[
                ("disabled", "#0f172a"),
                ("readonly", "#162235"),
                ("!disabled", "#162235"),
            ],
            foreground=[
                ("disabled", "#64748b"),
                ("readonly", "#f8fafc"),
                ("!disabled", "#f8fafc"),
            ],
            selectbackground=[("readonly", "#164e63"), ("!disabled", "#164e63")],
            selectforeground=[("readonly", "#f8fafc"), ("!disabled", "#f8fafc")],
            background=[("active", "#1e293b"), ("readonly", "#162235")],
        )
        style.configure("Dark.TEntry", fieldbackground="#162235", foreground="#f8fafc")
        style.map(
            "Dark.TEntry",
            fieldbackground=[("disabled", "#0f172a"), ("!disabled", "#162235")],
            foreground=[("disabled", "#64748b"), ("!disabled", "#f8fafc")],
        )
        root.option_add("*Entry.background", "#162235")
        root.option_add("*Entry.foreground", "#f8fafc")
        root.option_add("*Entry.insertBackground", "#f8fafc")
        root.option_add("*Entry.selectBackground", "#164e63")
        root.option_add("*Entry.selectForeground", "#ffffff")
        root.option_add("*TCombobox*Listbox.background", "#162235")
        root.option_add("*TCombobox*Listbox.foreground", "#f8fafc")
        root.option_add("*TCombobox*Listbox.selectBackground", "#164e63")
        root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        style.configure("TScale", background="#0b1220", troughcolor="#334155")

        try:
            root.iconbitmap(default=str(RESOURCE_ROOT / "resources" / "inputweave.ico"))
        except tk.TclError:
            LOGGER.warning("Could not load the InputWeave window icon")

        viewport = ttk.Frame(root)
        viewport.grid(row=0, column=0, sticky="nsew")
        root.rowconfigure(0, weight=1)
        root.columnconfigure(0, weight=1)
        viewport.rowconfigure(0, weight=1)
        viewport.columnconfigure(0, weight=1)
        canvas = tk.Canvas(
            viewport, background="#0b1220", highlightthickness=0, borderwidth=0
        )
        scrollbar = ttk.Scrollbar(viewport, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        scrollbar.grid_remove()
        wrapped_labels = []
        dependency_label = None

        self.events: queue.Queue = queue.Queue()
        self.commands: queue.Queue = queue.Queue()
        self.shutdown = threading.Event()
        self.devices: list[dict[str, object]] = []
        self.running = False
        self.running_output_mode: str | None = None
        self._closing = False
        self.button_capture_handler = None
        self.button_state_handler = None
        self.mapping_dialog: tk.Toplevel | None = None
        self.mapping_dialog_save = None
        self.settings_dialog_save = None
        self.latest_dependency_report: dict[str, dict[str, object]] | None = None
        self.profile_store = load_profile_store()
        self.language = self.profile_store.get("language", "de")
        self.profiles: dict[str, dict] = self.profile_store["profiles"]
        self.active_profile_name: str = self.profile_store["active_profile"]
        removed_legacy_aliases = False
        for profile in self.profiles.values():
            buttons = profile.get("buttons", {})
            if isinstance(buttons, dict):
                for source_id in ("button_4", "button_15"):
                    if source_id in buttons:
                        del buttons[source_id]
                        removed_legacy_aliases = True
        if removed_legacy_aliases:
            LOGGER.info(
                "Removing duplicate standardized PlayStation fallback mappings from profiles"
            )
            self.persist_profiles()
        self._textvar_sources: dict[str, tuple[tk.StringVar, str, dict[str, str]]] = {}

        container = ttk.Frame(canvas, padding=(20, 12))
        canvas_window = canvas.create_window((0, 0), window=container, anchor="nw")

        def update_scroll_region(_event=None) -> None:
            bounds = canvas.bbox("all")
            if bounds is not None:
                canvas.configure(scrollregion=bounds)
                overflows = bounds[3] > canvas.winfo_height() + 1
                if overflows and not scrollbar.winfo_ismapped():
                    scrollbar.grid()
                elif not overflows and scrollbar.winfo_ismapped():
                    scrollbar.grid_remove()

        container.bind("<Configure>", update_scroll_region)
        narrow_layout: bool | None = None
        device_card = None
        output_card = None
        mode_note = None

        def resize_content(event) -> None:
            nonlocal narrow_layout
            canvas.itemconfigure(canvas_window, width=event.width)
            is_narrow = event.width < 820
            if is_narrow != narrow_layout:
                narrow_layout = is_narrow
                if device_card is not None and output_card is not None:
                    if is_narrow:
                        device_card.grid_configure(row=0, column=0, padx=0, pady=(0, 8))
                        output_card.grid_configure(row=1, column=0, padx=0, pady=0)
                        main_row.columnconfigure(1, weight=0)
                    else:
                        device_card.grid_configure(row=0, column=0, padx=(0, 6), pady=0)
                        output_card.grid_configure(row=0, column=1, padx=(6, 0), pady=0)
                        main_row.columnconfigure(1, weight=1)
            if mode_note is not None:
                mode_note.configure(wraplength=max(240, event.width - 56))
            for label in wrapped_labels:
                label.configure(wraplength=max(240, event.width - 56))
            if dependency_label is not None:
                dependency_label.configure(wraplength=max(180, event.width - 360))
            root.after_idle(update_scroll_region)

        canvas.bind("<Configure>", resize_content)

        def scroll_main(event) -> None:
            widget = root.winfo_containing(event.x_root, event.y_root)
            while widget is not None and widget is not root:
                if widget is container:
                    canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
                    return
                widget = widget.master

        canvas.bind_all("<MouseWheel>", scroll_main)

        header = ttk.Frame(container)
        header.pack(fill="x", pady=(0, 12))
        logo_path = RESOURCE_ROOT / "resources" / "inputweave-logo.png"
        try:
            logo_image = tk.PhotoImage(file=str(logo_path))
            scale = max(1, round(logo_image.width() / 40))
            self.logo_image = logo_image.subsample(scale, scale)
            tk.Label(header, image=self.logo_image, bg="#0b1220", bd=0).pack(
                side="left", padx=(0, 12)
            )
        except tk.TclError:
            self.logo_image = None
            tk.Label(
                header,
                text="IW",
                width=3,
                height=1,
                bg="#0891b2",
                fg="#ecfeff",
                font=("Segoe UI", 16, "bold"),
                padx=4,
                pady=4,
            ).pack(side="left", padx=(0, 12))
        brand = ttk.Frame(header)
        brand.pack(side="left", fill="x", expand=True)
        ttk.Label(brand, text="InputWeave", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            brand,
            text="Controller Studio  ·  Eingabe passend zum Spiel ausgeben",
            style="SubTitle.TLabel",
        ).pack(anchor="w")
        self.language_var = tk.StringVar(value=language_name(self.language))
        self.language_box = ttk.Combobox(
            header,
            state="readonly",
            style="Dark.TCombobox",
            width=12,
            values=tuple(LANGUAGES.values()),
            textvariable=self.language_var,
        )
        self.language_box.pack(side="right", padx=(10, 0), ipady=2)
        self.language_box.bind("<<ComboboxSelected>>", self.on_language_selected)

        profile_card = ttk.Frame(container, style="Card.TFrame", padding=14)
        profile_card.pack(fill="x", pady=(0, 8))
        ttk.Label(profile_card, text="SPIELPROFIL", style="Section.TLabel").pack(
            anchor="w", pady=(0, 8)
        )
        profile_row = ttk.Frame(profile_card, style="Card.TFrame")
        profile_row.pack(fill="x")
        self.profile_box = ttk.Combobox(
            profile_row, state="readonly", style="Dark.TCombobox"
        )
        self.profile_box.pack(fill="x", expand=True, ipady=3)
        self.profile_box.bind("<<ComboboxSelected>>", self.select_profile)
        profile_actions = ttk.Frame(profile_card, style="Card.TFrame")
        profile_actions.pack(fill="x", pady=(8, 0))
        ttk.Button(profile_actions, text="Neu", command=self.new_profile).pack(
            side="right", padx=(8, 0)
        )
        ttk.Button(profile_actions, text="Makros", command=self.edit_macros).pack(
            side="right", padx=(8, 0)
        )
        self.mapping_button = ttk.Button(
            profile_actions, text="Tasten belegen", command=self.open_button_mapping
        )
        self.mapping_button.pack(side="right", padx=(8, 0))
        ttk.Button(
            profile_actions, text="Einstellungen", command=self.edit_profile_settings
        ).pack(side="right", padx=(8, 0))
        ttk.Button(profile_actions, text="Löschen", command=self.delete_profile).pack(
            side="right"
        )
        self.refresh_profile_choices()

        mode_card = ttk.Frame(container, style="Card.TFrame", padding=14)
        mode_card.pack(fill="x", pady=(0, 8))
        ttk.Label(mode_card, text="AUSGABEMODUS", style="Section.TLabel").pack(
            anchor="w", pady=(0, 9)
        )
        self.output_mode_var = tk.StringVar(
            value=self.current_settings()["output_mode"]
        )
        mode_row = ttk.Frame(mode_card, style="Card.TFrame")
        mode_row.pack(fill="x")
        for column in range(3):
            mode_row.columnconfigure(column, weight=1, uniform="output-modes")
        mode_choices = (
            ("direct", "Direkt", "Originalsignal · ohne Verarbeitung"),
            (
                "virtual_ds4",
                "Virtueller PlayStation",
                "PlayStation-Erkennung · mit Filtern",
            ),
            ("xbox", "Virtueller Xbox 360", "Xbox-Erkennung · mit Filtern"),
        )
        for column, (mode, title, subtitle) in enumerate(mode_choices):
            radio = ttk.Radiobutton(
                mode_row,
                text=f"{title}\n{subtitle}",
                value=mode,
                variable=self.output_mode_var,
                style="Mode.TRadiobutton",
                command=lambda selected=mode: self.set_output_mode(selected),
            )
            radio.grid(
                row=0, column=column, sticky="ew", padx=(0 if column == 0 else 6, 0)
            )
        self.mode_note = ttk.Label(
            mode_card, text="", style="CardMuted.TLabel", wraplength=900
        )
        mode_note = self.mode_note
        self.mode_note.pack(anchor="w", fill="x", pady=(10, 0))

        main_row = ttk.Frame(container)
        main_row.pack(fill="x", pady=(0, 8))
        for column in range(2):
            main_row.columnconfigure(column, weight=1, uniform="main-cards")

        device_card = ttk.Frame(main_row, style="Card.TFrame", padding=14)
        device_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        ttk.Label(device_card, text="EINGABEGERÄT", style="Section.TLabel").pack(
            anchor="w", pady=(0, 8)
        )
        device_row = ttk.Frame(device_card, style="Card.TFrame")
        device_row.pack(fill="x")
        self.device_box = ttk.Combobox(
            device_row, state="readonly", style="Dark.TCombobox"
        )
        self.device_box.pack(side="left", fill="x", expand=True, ipady=3)
        self.refresh_button = ttk.Button(
            device_row, text="↻", width=4, command=self.refresh
        )
        self.refresh_button.pack(side="left", padx=(8, 0))
        self.device_status = tk.StringVar(value="Suche nach Gamepads …")
        device_status_label = ttk.Label(
            device_card,
            textvariable=self.device_status,
            style="CardMuted.TLabel",
            wraplength=420,
        )
        device_status_label.pack(anchor="w", fill="x", pady=(9, 0))
        wrapped_labels.append(device_status_label)
        self.device_box.bind("<<ComboboxSelected>>", self.on_device_selected)

        output_card = ttk.Frame(main_row, style="Card.TFrame", padding=14)
        output_card.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        ttk.Label(output_card, text="AUSGABE", style="Section.TLabel").pack(
            anchor="w", pady=(0, 8)
        )
        self.status = tk.StringVar(value="Initialisiere …")
        output_status_label = ttk.Label(
            output_card, textvariable=self.status, style="Card.TLabel", wraplength=420
        )
        output_status_label.pack(anchor="w", fill="x", pady=(0, 10))
        wrapped_labels.append(output_status_label)
        self.start_button = ttk.Button(
            output_card,
            text="Emulation starten",
            style="Accent.TButton",
            command=self.toggle,
        )
        self.start_button.pack(anchor="w")

        telemetry_card = ttk.Frame(container, style="Card.TFrame", padding=14)
        telemetry_card.pack(fill="x", pady=(0, 8))
        ttk.Label(telemetry_card, text="LIVE-EINGABE", style="Section.TLabel").pack(
            anchor="w", pady=(0, 7)
        )
        self.input_status = tk.StringVar(value="Noch keine Taste gedrückt.")
        input_status_label = ttk.Label(
            telemetry_card,
            textvariable=self.input_status,
            style="Card.TLabel",
            wraplength=900,
        )
        input_status_label.pack(anchor="w", fill="x")
        wrapped_labels.append(input_status_label)
        self.analog_status = tk.StringVar(
            value="Sticks: X 0.00 / Y 0.00   ·   Trigger: L2 0% / R2 0%"
        )
        analog_status_label = ttk.Label(
            telemetry_card,
            textvariable=self.analog_status,
            style="CardMuted.TLabel",
            wraplength=900,
        )
        analog_status_label.pack(anchor="w", fill="x", pady=(6, 0))
        wrapped_labels.append(analog_status_label)
        self.macro_status = tk.StringVar(value="Makros inaktiv.")
        macro_status_label = ttk.Label(
            telemetry_card,
            textvariable=self.macro_status,
            style="CardMuted.TLabel",
            wraplength=900,
        )
        macro_status_label.pack(anchor="w", fill="x", pady=(6, 0))
        wrapped_labels.append(macro_status_label)

        self.dependency_status = tk.StringVar(value="Abhängigkeiten werden geprüft …")
        dependency_row = ttk.Frame(container, style="Card.TFrame", padding=10)
        dependency_row.pack(fill="x", pady=(0, 4))
        dependency_label = ttk.Label(
            dependency_row,
            textvariable=self.dependency_status,
            style="Card.TLabel",
            wraplength=520,
        )
        dependency_label.pack(side="left", anchor="w", fill="x", expand=True)
        self.driver_install_button = ttk.Button(
            dependency_row,
            text="Treiber installieren",
            command=self.install_driver,
            state="disabled",
        )
        self.driver_install_button.pack(side="right", padx=(8, 0))
        ttk.Button(
            dependency_row, text="Prüfen", command=self.refresh_dependency_status
        ).pack(side="right", padx=(8, 0))
        ttk.Button(
            dependency_row, text="Treiberinfo", command=self.open_driver_source
        ).pack(side="right")

        log_row = ttk.Frame(container)
        log_row.pack(fill="x", pady=(0, 4))
        ttk.Label(
            log_row,
            text="Protokolle und Profile bleiben lokal auf diesem PC.",
            style="SubTitle.TLabel",
        ).pack(side="left", anchor="w", fill="x", expand=True)
        ttk.Button(log_row, text="Log öffnen", command=self.open_log).pack(side="right")

        self.refresh_output_controls()
        for variable in (
            self.device_status,
            self.status,
            self.input_status,
            self.analog_status,
            self.macro_status,
            self.dependency_status,
        ):
            self._track_textvar(variable)
        self.apply_localization(self.root)
        self.language_var.set(language_name(self.language))

        self.worker = BridgeWorker(self.events, self.commands, self.shutdown)
        self.hidhide_registration_result: tuple[bool, str] | None = None
        if (
            getattr(sys, "frozen", False)
            and hidhide_service_state() in ("running", "installed")
            and find_hidhide_cli()
        ):
            # Configure the portable executable before SDL scans for devices
            # which HidHide may currently hide from this process.
            self.hidhide_registration_result = configure_current_application()
        self.worker.start()
        self.root.after(100, self.process_events)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh_dependency_status(auto_setup_missing_dependencies=True)

    def t(self, text: str) -> str:
        return translate(text, self.language)

    def size_dialog(
        self,
        dialog: tk.Toplevel,
        width: int,
        height: int,
        minimum_width: int,
        minimum_height: int,
    ) -> None:
        screen_width = dialog.winfo_screenwidth()
        screen_height = dialog.winfo_screenheight()
        fitted_width = min(width, max(320, screen_width - 32))
        fitted_height = min(height, max(320, screen_height - 64))
        dialog.minsize(
            min(minimum_width, fitted_width), min(minimum_height, fitted_height)
        )
        left = max(0, (screen_width - fitted_width) // 2)
        top = max(0, (screen_height - fitted_height) // 2)
        dialog.geometry(f"{fitted_width}x{fitted_height}+{left}+{top}")

    def tf(self, template: str, **values: str) -> str:
        return translate_format(template, self.language, **values)

    def _track_textvar(self, variable: tk.StringVar) -> None:
        source = canonical_text(variable.get())
        self._textvar_sources[variable._name] = (variable, source, {})

    def set_textvar(
        self,
        variable: tk.StringVar,
        text: str,
        *,
        template: str | None = None,
        **values: str,
    ) -> None:
        source = template or canonical_text(text)
        self._textvar_sources[variable._name] = (variable, source, values)
        rendered = (
            translate_format(source, self.language, **values)
            if values
            else translate(source, self.language)
        )
        variable.set(rendered)

    def set_localized_widget(self, widget, source: str, **values: str) -> None:
        widget._inputweave_source_text = source
        widget._inputweave_text_values = values
        rendered = (
            translate_format(source, self.language, **values)
            if values
            else translate(source, self.language)
        )
        widget.configure(text=rendered)

    def set_localized_title(self, widget, source: str, **values: str) -> None:
        widget._inputweave_title_source = source
        widget._inputweave_title_values = values
        widget.title(
            translate_format(source, self.language, **values)
            if values
            else translate(source, self.language)
        )

    def apply_localization(self, parent) -> None:
        def localize_subtree(node) -> None:
            for widget in node.winfo_children():
                if hasattr(widget, "_inputweave_title_source"):
                    source = widget._inputweave_title_source
                    values = widget._inputweave_title_values
                    widget.title(
                        translate_format(source, self.language, **values)
                        if values
                        else translate(source, self.language)
                    )
                try:
                    current_text = widget.cget("text")
                except (tk.TclError, TypeError):
                    current_text = ""
                if current_text:
                    source = getattr(
                        widget, "_inputweave_source_text", canonical_text(current_text)
                    )
                    values = getattr(widget, "_inputweave_text_values", {})
                    widget._inputweave_source_text = source
                    widget._inputweave_text_values = values
                    rendered = (
                        translate_format(source, self.language, **values)
                        if values
                        else translate(source, self.language)
                    )
                    try:
                        widget.configure(text=rendered)
                        if widget.winfo_class() in (
                            "TLabel",
                            "Label",
                            "TCheckbutton",
                            "TRadiobutton",
                        ):
                            if not hasattr(widget, "_inputweave_justify"):
                                widget._inputweave_justify = widget.cget("justify")
                            widget.configure(
                                justify="right"
                                if self.language == "ar"
                                else widget._inputweave_justify
                            )
                    except tk.TclError:
                        pass
                localize_subtree(widget)

        localize_subtree(parent)
        for variable, source, values in self._textvar_sources.values():
            try:
                rendered = (
                    translate_format(source, self.language, **values)
                    if values
                    else translate(source, self.language)
                )
                if variable.get() != rendered:
                    variable.set(rendered)
            except tk.TclError:
                continue

    def on_language_selected(self, _event=None) -> None:
        selected = language_code(self.language_var.get())
        if selected == self.language:
            return
        self.language = selected
        self.persist_profiles()
        self.apply_localization(self.root)
        self.language_var.set(language_name(self.language))
        self.refresh_output_controls()

    def refresh_output_controls(self) -> None:
        mode = self.current_settings()["output_mode"]
        self.output_mode_var.set(mode)
        if mode == "direct":
            note = (
                "Das physische Gamepad bleibt unverändert sichtbar. Es gibt keine virtuelle Ausgabe; "
                "Drift-Ausgleich und Tastenänderungen sind in diesem Modus aus."
            )
            self.set_localized_widget(self.start_button, "Direktmodus · keine Bridge")
            self.start_button.configure(state="disabled")
            self.device_box.configure(state="readonly" if self.devices else "disabled")
            self.refresh_button.configure(state="normal")
            self.set_textvar(
                self.status, "Direktmodus – InputWeave verändert keine Eingaben."
            )
        elif mode == "virtual_ds4":
            note = (
                "InputWeave verarbeitet die Eingabe und gibt einen virtuellen PlayStation-Controller aus. "
                "InputWeave wird automatisch in HidHide freigegeben. Für genau ein Gerät im Spiel: nur das physische Gamepad in HidHide verbergen."
            )
            button_text = (
                "Virtuelle Ausgabe stoppen"
                if self.running
                else "PlayStation-Ausgabe starten"
            )
            self.set_localized_widget(self.start_button, button_text)
            self.start_button.configure(
                state="normal" if self.running or self.devices else "disabled"
            )
            self.device_box.configure(
                state="disabled" if self.running or not self.devices else "readonly"
            )
            self.refresh_button.configure(
                state="normal" if not self.running else "disabled"
            )
        else:
            note = (
                "InputWeave gibt einen virtuellen Xbox-360-Controller aus. Für ein Gerät im Spiel: "
                "nur das physische Gamepad in HidHide verbergen; InputWeave wird automatisch freigegeben und die virtuelle Ausgabe bleibt sichtbar."
            )
            button_text = (
                "Virtuelle Ausgabe stoppen"
                if self.running
                else "Xbox-Emulation starten"
            )
            self.set_localized_widget(self.start_button, button_text)
            self.start_button.configure(
                state="normal" if self.running or self.devices else "disabled"
            )
            self.device_box.configure(
                state="disabled" if self.running or not self.devices else "readonly"
            )
            self.refresh_button.configure(
                state="normal" if not self.running else "disabled"
            )
        self.set_localized_widget(self.mode_note, note)

    def set_output_mode(self, mode: str) -> None:
        if mode not in OUTPUT_MODES:
            return
        settings = self.current_settings()
        previous_mode = settings["output_mode"]
        if previous_mode == mode:
            self.refresh_output_controls()
            return
        settings["output_mode"] = mode
        self.profiles[self.active_profile_name]["settings"] = settings
        if not self.persist_profiles():
            settings["output_mode"] = previous_mode
            self.profiles[self.active_profile_name]["settings"] = settings
            self.output_mode_var.set(previous_mode)
            self.refresh_output_controls()
            return
        LOGGER.info(
            "Output mode for profile %s changed to %s", self.active_profile_name, mode
        )
        self.refresh_output_controls()
        self.apply_profile_to_bridge()
        self.set_textvar(
            self.status,
            "",
            template="{mode} für „{profile}“ ausgewählt.",
            mode=output_mode_label(mode, self.language),
            profile=self.active_profile_name,
        )

    def refresh(self) -> None:
        self.set_textvar(self.status, "Suche nach Gamepads …")
        self.commands.put(("refresh", None))

    def refresh_dependency_status(
        self, auto_setup_missing_dependencies: bool = False
    ) -> None:
        self.set_textvar(self.dependency_status, "Abhängigkeiten werden geprüft …")
        threading.Thread(
            target=self._dependency_check,
            args=(auto_setup_missing_dependencies,),
            name="DependencyCheck",
            daemon=True,
        ).start()

    def _dependency_check(self, auto_setup_missing_dependencies: bool = False) -> None:
        report = dependency_report()
        hidhide = report["HidHide"]
        if hidhide["state"] in ("running", "installed") and hidhide["cli_available"]:
            startup_result = self.hidhide_registration_result
            if startup_result is not None and startup_result[0]:
                registered, registration_state = startup_result
            else:
                registered, registration_state = configure_current_application()
            hidhide["app_registered"] = (
                registered if registration_state != "source_run" else None
            )
            hidhide["registration_state"] = registration_state
            if not registered:
                LOGGER.warning(
                    "HidHide could not allow-list this application (%s)",
                    registration_state,
                )
        elif hidhide["state"] in ("running", "installed"):
            hidhide["app_registered"] = False
            hidhide["registration_state"] = "cli_missing"
        self.events.put(("dependencies", report))

        if not auto_setup_missing_dependencies:
            return
        if hidhide["state"] == "missing":
            if not report["HidHide-Installer"]["verified"]:
                self.events.put(
                    (
                        "hidhide_setup",
                        "HidHide-Installerprüfung fehlgeschlagen. Die Installation wurde nicht gestartet.",
                    )
                )
                self._install_missing_vigem()
                return

            started, launch_state = launch_hidhide_installer(HIDHIDE_INSTALLER)
            if not started:
                self.events.put(
                    (
                        "hidhide_setup",
                        {
                            "already_installed": "HidHide ist bereits installiert.",
                            "installer_cancelled": "HidHide-Installation abgebrochen. Ohne HidHide bleibt die App nutzbar, Geräte können in Spielen aber doppelt erscheinen.",
                            "installer_verification_failed": "HidHide-Installerprüfung fehlgeschlagen. Die Installation wurde nicht gestartet.",
                            "service_state_unknown": "HidHide-Status ist unklar. Zur Sicherheit wurde nichts installiert.",
                            "installer_launch_failed": "Windows konnte den HidHide-Installer nicht starten.",
                            "windows_required": "HidHide kann nur unter Windows installiert werden.",
                            "unsupported_architecture": "Die gebündelte HidHide-Version unterstützt Windows x64 (Intel/AMD), nicht ARM oder 32-Bit-Windows.",
                        }.get(launch_state, "HidHide konnte nicht gestartet werden."),
                    )
                )
                if launch_state == "installer_cancelled":
                    return
            else:
                self.events.put(
                    (
                        "hidhide_setup",
                        "HidHide-Installer gestartet. Bitte die Einrichtung abschließen; InputWeave wird danach automatisch freigegeben.",
                    )
                )
                for _ in range(90):
                    if self.shutdown.wait(2):
                        return
                    current_state = hidhide_service_state()
                    cli = (
                        find_hidhide_cli()
                        if current_state in ("running", "installed")
                        else None
                    )
                    if cli is None:
                        continue
                    registered, registration_state = configure_current_application()
                    if not registered:
                        continue
                    refreshed = dependency_report()
                    refreshed["HidHide"]["app_registered"] = True
                    refreshed["HidHide"]["registration_state"] = registration_state
                    LOGGER.info(
                        "HidHide setup completed and InputWeave was allow-listed"
                    )
                    self.events.put(("dependencies", refreshed))
                    self.events.put(
                        (
                            "hidhide_setup",
                            "HidHide ist installiert und InputWeave wurde freigegeben. Falls der Controller fehlt, bitte einmal abziehen und neu verbinden.",
                        )
                    )
                    self._install_missing_vigem()
                    return
                self.events.put(
                    (
                        "hidhide_setup",
                        "HidHide-Setup noch nicht abgeschlossen. Beende den Installer und drücke danach „Prüfen“.",
                    )
                )
                return

        self._install_missing_vigem()

    def _install_missing_vigem(self) -> None:
        """Install the bundled virtual-controller driver on first launch if absent."""
        state = _service_state("ViGEmBus")
        if state != "missing":
            return
        if not _verified_driver_installer():
            self.events.put(
                (
                    "hidhide_setup",
                    "Der gebündelte ViGEmBus-Installer fehlt oder ist beschädigt. Virtuelle Ausgaben sind nicht verfügbar.",
                )
            )
            return

        started, _detail = launch_driver_installer()
        if not started:
            self.events.put(
                (
                    "hidhide_setup",
                    "ViGEmBus-Installation wurde nicht gestartet. Der Treiber kann unten manuell installiert werden.",
                )
            )
            return
        self.events.put(
            (
                "hidhide_setup",
                "ViGEmBus-Installer gestartet. Bitte die Einrichtung abschließen; Windows fragt nach Administratorrechten.",
            )
        )
        for _ in range(60):
            if self.shutdown.wait(2):
                return
            if _service_state("ViGEmBus") in ("running", "installed"):
                self.events.put(("dependencies", dependency_report()))
                self.events.put(
                    (
                        "hidhide_setup",
                        "ViGEmBus ist installiert. Falls Windows einen Neustart verlangt, bitte danach neu starten.",
                    )
                )
                return
        self.events.put(
            (
                "hidhide_setup",
                "ViGEmBus-Setup noch nicht abgeschlossen. Beende den Installer und drücke danach „Prüfen“.",
            )
        )

    def install_driver(self) -> None:
        report = self.latest_dependency_report or {}
        driver = report.get("ViGEmBus-Treiber", {})
        installer = report.get("Treiberinstaller", {})
        state = driver.get("state")
        if state in ("running", "installed"):
            messagebox.showinfo(
                self.t("ViGEmBus ist bereits installiert"),
                self.t(
                    "Der vorhandene Treiber bleibt unverändert. InputWeave installiert keine zweite Version."
                ),
                parent=self.root,
            )
            return
        if state != "missing":
            messagebox.showwarning(
                self.t("Treiberstatus unklar"),
                self.t(
                    "Windows meldet keinen eindeutig fehlenden Treiber. Zur Sicherheit wird keine Installation gestartet."
                ),
                parent=self.root,
            )
            return
        if not installer.get("verified", False):
            messagebox.showerror(
                self.t("Installerprüfung fehlgeschlagen"),
                self.t(
                    "Der mitgelieferte Treiberinstaller fehlt oder stimmt nicht mit der geprüften SHA-256-Prüfsumme überein."
                ),
                parent=self.root,
            )
            return
        if not messagebox.askyesno(
            self.t("ViGEmBus installieren"),
            self.t(
                "ViGEmBus fehlt. InputWeave startet den signierten Nefarius-Installer Version 1.22.0. "
                "Windows zeigt dafür eine Administratorbestätigung (UAC). Die Installation beginnt nur, wenn du bestätigst."
            ),
            parent=self.root,
        ):
            return
        started, detail = launch_driver_installer()
        self.set_textvar(self.status, detail)
        if started:
            messagebox.showinfo(
                self.t("Treiberinstaller gestartet"), self.t(detail), parent=self.root
            )
            self.root.after(5000, self.refresh_dependency_status)
        else:
            messagebox.showerror(
                self.t("Treiberinstallation nicht gestartet"),
                self.t(detail),
                parent=self.root,
            )

    def open_driver_source(self) -> None:
        webbrowser.open(DRIVER_RELEASE_URL)

    def edit_profile_settings(self) -> None:
        dialog = tk.Toplevel(self.root)
        self.set_localized_title(
            dialog, "Einstellungen – {profile}", profile=self.active_profile_name
        )
        self.size_dialog(dialog, 560, 610, 520, 560)
        dialog.transient(self.root)
        dialog.grab_set()

        settings = self.current_settings()
        frame = ttk.Frame(dialog, padding=20)
        frame.pack(fill="both", expand=True)
        profile_title = ttk.Label(frame, style="Title.TLabel")
        self.set_localized_widget(
            profile_title, "Profil: {profile}", profile=self.active_profile_name
        )
        profile_title.pack(anchor="w")
        ttk.Label(
            frame,
            text="Diese Werte werden pro Spielprofil gespeichert.",
            style="SubTitle.TLabel",
        ).pack(anchor="w", pady=(3, 16))
        if self.current_settings()["output_mode"] == "direct":
            ttk.Label(
                frame,
                text="Im Direktmodus bleiben alle Eingaben unverändert. Drift-Ausgleich und Filter benötigen eine virtuelle PlayStation- oder Xbox-Ausgabe.",
                style="SubTitle.TLabel",
                wraplength=500,
            ).pack(anchor="w", pady=(0, 12))

        drift_var = tk.BooleanVar(value=settings["drift_compensation"])
        invert_left_var = tk.BooleanVar(value=settings["invert_left_y"])
        invert_right_var = tk.BooleanVar(value=settings["invert_right_y"])
        ToggleSwitch(frame, "Drift-Ausgleich aktivieren", drift_var).pack(
            anchor="w", pady=4
        )
        ToggleSwitch(
            frame, "Linke Y-Achse zusätzlich invertieren", invert_left_var
        ).pack(anchor="w", pady=4)
        ToggleSwitch(
            frame, "Rechte Y-Achse zusätzlich invertieren", invert_right_var
        ).pack(anchor="w", pady=4)

        controls = (
            ("Linke Stick-Deadzone", "left_deadzone", 0.0, 0.35, " %"),
            ("Rechte Stick-Deadzone", "right_deadzone", 0.0, 0.35, " %"),
            ("L2/R2-Schwelle", "trigger_deadzone", 0.0, 0.30, " %"),
            ("Stick-Empfindlichkeit", "sensitivity", 0.50, 2.00, "×"),
        )
        variables = {}
        for label_text, key, low, high, suffix in controls:
            row = ttk.Frame(frame)
            row.pack(fill="x", pady=(10, 2))
            ttk.Label(row, text=label_text).pack(side="left")
            value = tk.DoubleVar(value=settings[key])
            variables[key] = value
            shown = tk.StringVar()

            def format_value(number, unit=suffix, output=shown):
                number = float(number)
                output.set(
                    f"{number * 100:.0f}{unit}"
                    if unit == " %"
                    else f"{number:.2f}{unit}"
                )

            format_value(settings[key])
            ttk.Label(row, textvariable=shown, width=8, anchor="e").pack(side="right")
            slider = ttk.Scale(
                frame,
                from_=low,
                to=high,
                variable=value,
                orient="horizontal",
                command=lambda number, update=format_value: update(number),
            )
            slider.pack(fill="x")

        rate_row = ttk.Frame(frame)
        rate_row.pack(fill="x", pady=(14, 2))
        ttk.Label(rate_row, text="Ausgaberate").pack(side="left")
        rate_var = tk.StringVar(value=f"{settings['polling_rate']} Hz")
        ttk.Combobox(
            rate_row,
            state="readonly",
            style="Dark.TCombobox",
            values=("60 Hz", "125 Hz", "250 Hz"),
            textvariable=rate_var,
            width=12,
        ).pack(side="right")
        ttk.Label(
            frame,
            text="125 Hz ist ein guter Standard; 60 Hz kann bei hoher Systemlast entlasten.",
            style="SubTitle.TLabel",
        ).pack(anchor="w", pady=(3, 0))

        note = (
            "Beim Start der Bridge werden die Sticks eine Sekunde lang automatisch auf ihre Ruhelage geprüft. "
            "Für eine neue Kalibrierung die Sticks loslassen und unten drücken."
        )
        ttk.Label(frame, text=note, wraplength=500, style="SubTitle.TLabel").pack(
            anchor="w", pady=(14, 8)
        )

        def calibrate_now() -> None:
            if not self.running:
                messagebox.showinfo(
                    self.t("Driftkalibrierung"),
                    self.t("Zuerst die Bridge starten."),
                    parent=dialog,
                )
                return
            if not drift_var.get():
                messagebox.showinfo(
                    self.t("Driftkalibrierung"),
                    self.t("Bitte zuerst den Drift-Ausgleich aktivieren."),
                    parent=dialog,
                )
                return
            self.commands.put(("calibrate", None))
            self.set_textvar(
                self.status, "Driftkalibrierung läuft – Sticks kurz loslassen."
            )

        footer = ttk.Frame(frame)
        footer.pack(fill="x", side="bottom", pady=(16, 0))
        ttk.Button(footer, text="Jetzt kalibrieren", command=calibrate_now).pack(
            side="left"
        )

        settings_feedback_var = tk.StringVar(value="")
        self._track_textvar(settings_feedback_var)
        ttk.Label(
            footer, textvariable=settings_feedback_var, style="CardMuted.TLabel"
        ).pack(side="left", padx=(12, 0))
        pending_save: list[str | None] = [None]
        profile_name = self.active_profile_name

        def persist_settings() -> bool:
            pending_save[0] = None
            profile = self.profiles[profile_name]
            previous = normalized_settings(profile.get("settings", {}), profile_name)
            updated = previous.copy()
            updated.update(
                {
                    "drift_compensation": drift_var.get(),
                    "invert_left_y": invert_left_var.get(),
                    "invert_right_y": invert_right_var.get(),
                    "polling_rate": int(rate_var.get().split()[0]),
                    **{key: value.get() for key, value in variables.items()},
                }
            )
            updated = normalized_settings(updated, profile_name)
            if updated == previous:
                return True
            profile["settings"] = updated
            if not self.persist_profiles():
                profile["settings"] = previous
                self.set_textvar(settings_feedback_var, "Speichern fehlgeschlagen.")
                return False
            LOGGER.info("Auto-saved settings for profile %s: %s", profile_name, updated)
            self.apply_profile_to_bridge()
            self.set_textvar(
                settings_feedback_var, "Einstellungen automatisch gespeichert."
            )
            self.set_textvar(
                self.status,
                "",
                template="Einstellungen für „{profile}“ gespeichert.",
                profile=profile_name,
            )
            return True

        def schedule_settings_save(*_args) -> None:
            if pending_save[0] is not None:
                try:
                    dialog.after_cancel(pending_save[0])
                except tk.TclError:
                    pass
            pending_save[0] = dialog.after(150, persist_settings)

        for variable in (
            drift_var,
            invert_left_var,
            invert_right_var,
            rate_var,
            *variables.values(),
        ):
            variable.trace_add("write", schedule_settings_save)

        def cancel_settings_save() -> None:
            if pending_save[0] is not None:
                try:
                    dialog.after_cancel(pending_save[0])
                except tk.TclError:
                    pass
                pending_save[0] = None

        def flush_settings() -> bool:
            cancel_settings_save()
            return persist_settings()

        def close_settings_dialog() -> None:
            try:
                flush_settings()
            finally:
                self.settings_dialog_save = None
                dialog.destroy()

        def save_settings_button() -> None:
            if flush_settings():
                self.set_textvar(settings_feedback_var, "Einstellungen gespeichert.")

        dialog.protocol("WM_DELETE_WINDOW", close_settings_dialog)
        self.settings_dialog_save = flush_settings
        ttk.Button(
            footer,
            text="Jetzt speichern",
            style="Accent.TButton",
            command=save_settings_button,
        ).pack(side="right")
        ttk.Button(footer, text="Schließen", command=close_settings_dialog).pack(
            side="right", padx=(0, 8)
        )
        self.apply_localization(dialog)

    def refresh_profile_choices(self) -> None:
        names = sorted(self.profiles, key=str.casefold)
        self.profile_box["values"] = names
        if self.active_profile_name in names:
            self.profile_box.set(self.active_profile_name)
        elif names:
            self.active_profile_name = names[0]
            self.profile_box.set(names[0])

    def current_mapping(
        self, device: dict[str, object] | None = None
    ) -> dict[str, str]:
        device = device or self.selected_device()
        profile = self.profiles[self.active_profile_name]
        if device and device.get("kind") == "raw":
            defaults = inferred_button_mapping(self.device_from_payload(device))
            stored = profile.get("device_mappings", {}).get(
                str(device.get("mapping_key", "")), {}
            )
            return normalized_mapping(stored, defaults)
        return normalized_mapping(profile.get("buttons", {}))

    def current_axis_mapping(
        self, device: dict[str, object] | None = None
    ) -> dict[str, int | None]:
        device = device or self.selected_device()
        if not device or device.get("kind") != "raw":
            return {}
        descriptor = self.device_from_payload(device)
        profile = self.profiles[self.active_profile_name]
        stored = profile.get("device_axis_mappings", {}).get(
            str(device.get("mapping_key", "")), {}
        )
        return normalized_axis_mapping(stored, descriptor)

    def current_macros(self, device: dict[str, object] | None = None) -> list[dict]:
        device = device or self.selected_device()
        profile = self.profiles[self.active_profile_name]
        if device and device.get("kind") == "raw":
            stored = profile.get("device_macros", {}).get(
                str(device.get("mapping_key", "")), []
            )
        else:
            stored = profile.get("macros", [])
        return normalized_macros(stored)

    def selected_device(self) -> dict[str, object] | None:
        selected = self.device_box.current()
        if 0 <= selected < len(self.devices):
            return self.devices[selected]
        return None

    @staticmethod
    def device_from_payload(payload: dict[str, object]) -> InputDevice:
        return InputDevice(
            index=int(payload["index"]),
            name=str(payload["name"]),
            family=str(payload["family"]),
            kind=str(payload["kind"]),
            guid=str(payload.get("guid", "")),
            axes=int(payload.get("axes", 0)),
            buttons=int(payload.get("buttons", 0)),
            hats=int(payload.get("hats", 0)),
        )

    def on_device_selected(self, _event=None) -> None:
        device = self.selected_device()
        if not device:
            return
        if device["kind"] == "raw":
            self.set_textvar(
                self.device_status,
                "",
                template="{family} erkannt · {name} · Rohdaten: {buttons} Tasten, {axes} Achsen, {hats} Hats",
                family=str(device["family"]),
                name=str(device["name"]),
                buttons=str(device["buttons"]),
                axes=str(device["axes"]),
                hats=str(device["hats"]),
            )
        else:
            self.set_textvar(
                self.device_status,
                "",
                template="{family} erkannt · {name} · standardisierte automatische Belegung",
                family=str(device["family"]),
                name=str(device["name"]),
            )
        self.set_localized_widget(self.mapping_button, "Tasten belegen")

    def current_settings(self) -> dict:
        return normalized_settings(
            self.profiles[self.active_profile_name].get("settings", {}),
            self.active_profile_name,
        )

    def persist_profiles(self) -> bool:
        self.profile_store = {
            "language": self.language,
            "active_profile": self.active_profile_name,
            "profiles": self.profiles,
        }
        try:
            write_profile_store(PROFILE_FILE, self.profile_store)
            LOGGER.info("Saved %d profile(s) to %s", len(self.profiles), PROFILE_FILE)
            return True
        except Exception as exc:
            LOGGER.exception("Could not save profiles to %s", PROFILE_FILE)
            messagebox.showerror(
                self.t("Profile konnten nicht gespeichert werden"),
                str(exc),
                parent=self.root,
            )
            return False

    def apply_profile_to_bridge(self) -> None:
        if self.running:
            selected_mode = self.current_settings()["output_mode"]
            if selected_mode != self.running_output_mode:
                self.commands.put(("stop", None))
                self.running = False
                self.running_output_mode = None
                if selected_mode == "direct":
                    self.set_textvar(
                        self.status,
                        "Direktmodus – InputWeave verändert keine Eingaben.",
                    )
                else:
                    self.set_textvar(
                        self.status,
                        "Ausgabemodus geändert – virtuelle Ausgabe startet neu.",
                    )
                self.refresh_output_controls()
                if selected_mode != "direct" and self.devices:
                    self.root.after(120, self.toggle)
                return
            self.commands.put(
                (
                    "mapping",
                    {
                        "profile_name": self.active_profile_name,
                        "mapping": self.current_mapping(),
                        "axis_mapping": self.current_axis_mapping(),
                        "settings": self.current_settings(),
                        "macros": self.current_macros(),
                    },
                )
            )

    def select_profile(self, _event=None) -> None:
        selected = self.profile_box.get()
        if selected not in self.profiles:
            return
        self.active_profile_name = selected
        self.persist_profiles()
        LOGGER.info("Selected profile %s", selected)
        self.refresh_output_controls()
        self.apply_profile_to_bridge()
        if self.current_settings()["output_mode"] != "direct":
            self.set_textvar(
                self.status,
                "",
                template="Profil „{profile}“ ausgewählt.",
                profile=selected,
            )

    def new_profile(self) -> None:
        name = simpledialog.askstring(
            self.t("Neues Spielprofil"),
            self.t("Profilname (z. B. Forza Horizon 6):"),
            parent=self.root,
        )
        if name is None:
            return
        name = name.strip()
        if not name:
            messagebox.showinfo(
                self.t("Profil"),
                self.t("Bitte einen Profilnamen eingeben."),
                parent=self.root,
            )
            return
        if name in self.profiles:
            self.active_profile_name = name
            self.refresh_profile_choices()
            self.persist_profiles()
            self.refresh_output_controls()
            self.apply_profile_to_bridge()
            return
        new_settings = self.current_settings()
        new_settings["output_mode"] = default_output_mode(name)
        self.profiles[name] = {
            "buttons": normalized_mapping(
                self.profiles[self.active_profile_name].get("buttons", {})
            ),
            "device_mappings": {},
            "device_axis_mappings": {},
            "macros": [],
            "device_macros": {},
            "settings": new_settings,
        }
        self.active_profile_name = name
        self.refresh_profile_choices()
        self.persist_profiles()
        LOGGER.info("Created profile %s", name)
        self.refresh_output_controls()
        self.apply_profile_to_bridge()
        self.set_textvar(
            self.status,
            "",
            template="Neues Profil „{profile}“ erstellt. Tastenbelegung bei Bedarf anpassen.",
            profile=name,
        )

    def delete_profile(self) -> None:
        if len(self.profiles) <= 1:
            messagebox.showinfo(
                self.t("Profil löschen"),
                self.t("Mindestens ein Profil muss vorhanden sein."),
                parent=self.root,
            )
            return
        if not messagebox.askyesno(
            self.t("Profil löschen"),
            self.tf("Profil „{profile}“ löschen?", profile=self.active_profile_name),
            parent=self.root,
        ):
            return
        del self.profiles[self.active_profile_name]
        self.active_profile_name = next(iter(self.profiles))
        self.refresh_profile_choices()
        self.persist_profiles()
        LOGGER.info(
            "Deleted profile; active profile is now %s", self.active_profile_name
        )
        self.refresh_output_controls()
        self.apply_profile_to_bridge()
        self.set_textvar(
            self.status,
            "",
            template="Profil „{profile}“ aktiv.",
            profile=self.active_profile_name,
        )

    def edit_macros(
        self, preset_trigger: str | None = None, restore_grab: tk.Toplevel | None = None
    ) -> None:
        device = self.selected_device()
        profile = self.profiles[self.active_profile_name]
        raw_device = bool(device and device.get("kind") == "raw")
        device_key = str(device.get("mapping_key", "")) if raw_device else ""
        if device:
            source_rows = button_controls(
                self.device_from_payload(device), self.language
            )
            scope = (
                self.tf("Dieses Gerät · {name}", name=str(device["name"]))
                if raw_device
                else self.tf(
                    "Alle SDL-Gamepads · {family}",
                    family=str(device["family"]),
                )
            )
        else:
            source_rows = [
                (source_id, control_label(source_id, self.language), None)
                for source_id, _label, _button in STANDARD_BUTTONS
            ]
            scope = self.t("Standardisierte SDL-Belegung")

        macros = self.current_macros(device)
        source_labels = {source_id: label for source_id, label, _button in source_rows}
        options = {
            f"{label} [{source_id}]": source_id
            for source_id, label, _button in source_rows
        }
        for macro in macros:
            trigger = macro["trigger"]
            if trigger not in source_labels:
                source_labels[trigger] = control_label(
                    trigger,
                    self.language,
                    str(device.get("family", "")) if device else None,
                )
                options[f"{source_labels[trigger]} [{trigger}]"] = trigger
        first_source = next(iter(options), "")
        output_mode = self.current_settings()["output_mode"]
        target_options = {
            target_label(target_id, output_mode, self.language): target_id
            for target_id in available_target_ids(output_mode)
            if target_id != "unmapped"
        }
        first_target = next(iter(target_options), "")
        dialog = tk.Toplevel(self.root)
        self.set_localized_title(
            dialog, "Makros – {profile}", profile=self.active_profile_name
        )
        self.size_dialog(dialog, 680, 650, 500, 420)
        dialog.transient(self.root)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=18)
        frame.pack(fill="both", expand=True)
        scroll_shell = ttk.Frame(frame)
        scroll_shell.pack(fill="both", expand=True)
        scroll_canvas = tk.Canvas(
            scroll_shell, background="#0b1220", highlightthickness=0, borderwidth=0
        )
        scroll_bar = ttk.Scrollbar(
            scroll_shell, orient="vertical", command=scroll_canvas.yview
        )
        body = ttk.Frame(scroll_canvas)
        body.bind(
            "<Configure>",
            lambda _event: scroll_canvas.configure(
                scrollregion=scroll_canvas.bbox("all")
            ),
        )
        body_window = scroll_canvas.create_window((0, 0), window=body, anchor="nw")
        scroll_canvas.bind(
            "<Configure>",
            lambda event: scroll_canvas.itemconfigure(body_window, width=event.width),
        )
        scroll_canvas.configure(yscrollcommand=scroll_bar.set)
        scroll_canvas.pack(side="left", fill="both", expand=True)
        scroll_bar.pack(side="right", fill="y")

        scroll_canvas.bind_all(
            "<MouseWheel>",
            lambda event: scroll_canvas.yview_scroll(
                -1 * int(event.delta / 120), "units"
            ),
        )

        def disable_wheel(_event=None) -> None:
            scroll_canvas.unbind_all("<MouseWheel>")

        heading = ttk.Label(body, style="Title.TLabel")
        self.set_localized_widget(
            heading, "Makros für {profile}", profile=self.active_profile_name
        )
        heading.pack(anchor="w")
        ttk.Label(body, text=scope, style="SubTitle.TLabel").pack(
            anchor="w", pady=(3, 4)
        )
        ttk.Label(
            body,
            text="Weise das Makro einer Controller-Taste zu. Ein Tastendruck startet die Sequenz und ersetzt die normale Belegung dieser Taste. Dieselbe Taste erneut drücken, um das Makro abzubrechen.",
            style="SubTitle.TLabel",
            wraplength=700,
        ).pack(anchor="w", pady=(0, 12))
        if output_mode == "direct":
            ttk.Label(
                body,
                text="Im Direktmodus bleiben Makros aus, weil dort keine virtuelle Ausgabe verarbeitet wird.",
                style="CardMuted.TLabel",
                wraplength=700,
            ).pack(anchor="w", pady=(0, 10))

        list_area = ttk.Frame(body, style="Card.TFrame", padding=10)
        list_area.pack(fill="x", pady=(0, 12))
        ttk.Label(list_area, text="Gespeicherte Makros", style="Section.TLabel").pack(
            anchor="w", pady=(0, 6)
        )
        list_row = ttk.Frame(list_area, style="Card.TFrame")
        list_row.pack(fill="x")
        macro_list = tk.Listbox(
            list_row,
            height=3,
            background="#162235",
            foreground="#e2e8f0",
            selectbackground="#164e63",
            selectforeground="#ffffff",
            highlightthickness=0,
            relief="flat",
            activestyle="none",
            font=("Segoe UI", 10),
        )
        macro_list.pack(side="left", fill="x", expand=True)
        list_scrollbar = ttk.Scrollbar(
            list_row, orient="vertical", command=macro_list.yview
        )
        list_scrollbar.pack(side="right", fill="y")
        macro_list.configure(yscrollcommand=list_scrollbar.set)

        list_actions = ttk.Frame(list_area, style="Card.TFrame")
        list_actions.pack(fill="x", pady=(8, 0))
        ttk.Button(list_actions, text="Neues Makro", command=lambda: new_macro()).pack(
            side="left"
        )
        ttk.Button(
            list_actions, text="Makro löschen", command=lambda: delete_macro()
        ).pack(side="left", padx=(8, 0))

        form = ttk.Frame(body, style="Card.TFrame", padding=12)
        form.pack(fill="x")
        editor_heading = ttk.Label(form, style="Section.TLabel")
        editor_heading.pack(anchor="w", pady=(0, 3))
        editor_hint = ttk.Label(form, style="SubTitle.TLabel", wraplength=680)
        editor_hint.pack(anchor="w", pady=(0, 8))

        def set_editor_state(title: str, hint: str) -> None:
            self.set_localized_widget(editor_heading, title)
            self.set_localized_widget(editor_hint, hint)

        set_editor_state(
            "Makro erstellen",
            "Klicke auf „Neues Makro“, um einen Entwurf zu öffnen.",
        )
        enabled_var = tk.BooleanVar(value=True)
        loop_var = tk.BooleanVar(value=False)
        switch_row = ttk.Frame(form, style="Card.TFrame")
        switch_row.pack(fill="x", pady=(0, 8))
        ToggleSwitch(switch_row, "Makro aktiv", enabled_var).pack(
            side="left", padx=(0, 18)
        )
        ToggleSwitch(switch_row, "Unbegrenzt wiederholen", loop_var).pack(side="left")

        trigger_row = ttk.Frame(form, style="Card.TFrame")
        trigger_row.pack(fill="x", pady=(0, 8))
        ttk.Label(trigger_row, text="Makro zuweisen an", style="Card.TLabel").pack(
            side="left"
        )
        trigger_var = tk.StringVar(value=first_source)
        trigger_box = ttk.Combobox(
            trigger_row,
            state="readonly",
            style="Dark.TCombobox",
            values=tuple(options),
            textvariable=trigger_var,
            width=24,
        )
        trigger_box.pack(side="left", fill="x", expand=True, padx=(10, 8))
        capture_status_var = tk.StringVar()
        self.set_textvar(capture_status_var, "Noch keine Taste erkannt.")
        capture_status = ttk.Label(
            form, textvariable=capture_status_var, style="CardMuted.TLabel"
        )
        capture_status.pack(anchor="w", pady=(0, 6))
        capture_active: list[bool] = [False]
        capture_button = ttk.Button(trigger_row, text="Taste erkennen")

        def finish_capture_ui(
            state: str, source_id: str | None = None, detail: str | None = None
        ) -> None:
            if state == "waiting":
                capture_active[0] = True
                self.set_textvar(capture_status_var, "Controller-Taste drücken …")
                self.set_localized_widget(capture_button, "Abbrechen")
                return
            capture_active[0] = False
            self.button_capture_handler = None
            self.set_localized_widget(capture_button, "Taste erkennen")
            if state == "detected" and source_id is not None:
                selected = next(
                    (
                        label
                        for label, candidate in options.items()
                        if candidate == source_id
                    ),
                    None,
                )
                if selected is None:
                    family = str(device.get("family", "")) if device else None
                    source_labels[source_id] = control_label(
                        source_id, self.language, family
                    )
                    selected = f"{source_labels[source_id]} [{source_id}]"
                    options[selected] = source_id
                    trigger_box.configure(values=tuple(options))
                trigger_var.set(selected)
                self.set_textvar(
                    capture_status_var,
                    "",
                    template="Controller-Taste erkannt: {button}",
                    button=source_labels.get(
                        source_id, control_label(source_id, self.language)
                    ),
                )
            elif state == "timeout":
                self.set_textvar(capture_status_var, "Keine Taste erkannt.")
            elif state == "error":
                LOGGER.warning(
                    "Controller button detection could not complete: %s",
                    detail or "unknown error",
                )
                self.set_textvar(
                    capture_status_var, "Taste konnte nicht erkannt werden."
                )
            elif state == "cancelled":
                self.set_textvar(capture_status_var, "Tastenerkennung abgebrochen.")

        def toggle_button_capture() -> None:
            if capture_active[0]:
                self.commands.put(("cancel_button_capture", None))
                return
            if self.running:
                self.set_textvar(
                    capture_status_var, "Emulation anhalten, um eine Taste zu erkennen."
                )
                return
            if device is None:
                self.set_textvar(
                    capture_status_var,
                    "Zuerst einen Controller verbinden und auswählen.",
                )
                return
            capture_active[0] = True
            self.button_capture_handler = finish_capture_ui
            self.set_localized_widget(capture_button, "Abbrechen")
            self.set_textvar(capture_status_var, "Controller-Taste drücken …")
            self.commands.put(("capture_button", dict(device)))

        capture_button.configure(command=toggle_button_capture)
        capture_button.pack(side="right")

        steps_row = ttk.Frame(form, style="Card.TFrame")
        steps_row.pack(fill="x")
        step_list = tk.Listbox(
            steps_row,
            height=4,
            background="#162235",
            foreground="#e2e8f0",
            selectbackground="#164e63",
            selectforeground="#ffffff",
            highlightthickness=0,
            relief="flat",
            activestyle="none",
            font=("Segoe UI", 10),
        )
        step_list.pack(side="left", fill="both", expand=True)
        step_scrollbar = ttk.Scrollbar(
            steps_row, orient="vertical", command=step_list.yview
        )
        step_scrollbar.pack(side="right", fill="y")
        step_list.configure(yscrollcommand=step_scrollbar.set)
        draft_steps: list[dict] = []
        current_macro_index: list[int | None] = [None]
        loading_macro: list[bool] = [False]

        def render_steps() -> None:
            step_list.delete(0, "end")
            for index, step in enumerate(draft_steps, start=1):
                label = target_label(step["target"], output_mode, self.language)
                step_list.insert(
                    "end",
                    f"{index:02d}. {label} · {step['press_ms']} ms + {step['gap_ms']} ms Pause",
                )

        def macro_summary(macro: dict) -> str:
            trigger = source_labels.get(
                macro["trigger"],
                control_label(
                    macro["trigger"],
                    self.language,
                    str(device.get("family", "")) if device else None,
                ),
            )
            sequence = " → ".join(
                target_label(step["target"], output_mode, self.language)
                for step in macro["steps"]
            )
            status_mark = "✓" if macro["enabled"] else "○"
            loop_mark = "∞  " if macro["loop"] else ""
            return f"{status_mark}  {loop_mark}{trigger}  →  {sequence}"

        def render_macros(selected_index: int | None = None) -> None:
            macro_list.delete(0, "end")
            for macro in macros:
                macro_list.insert("end", macro_summary(macro))
            if selected_index is not None and 0 <= selected_index < len(macros):
                macro_list.selection_clear(0, "end")
                macro_list.selection_set(selected_index)
                macro_list.activate(selected_index)

        def load_macro(index: int) -> None:
            if not 0 <= index < len(macros):
                return
            macro = macros[index]
            current_macro_index[0] = index
            set_editor_state(
                "Makro bearbeiten", "Änderungen mit „Makro übernehmen“ bestätigen."
            )
            loading_macro[0] = True
            enabled_var.set(macro["enabled"])
            loop_var.set(macro["loop"])
            loading_macro[0] = False
            trigger_var.set(
                next(
                    (
                        label
                        for label, source_id in options.items()
                        if source_id == macro["trigger"]
                    ),
                    first_source,
                )
            )
            draft_steps[:] = [step.copy() for step in macro["steps"]]
            render_steps()

        def on_macro_selected(_event=None) -> None:
            selection = macro_list.curselection()
            if selection:
                load_macro(int(selection[0]))

        macro_list.bind("<<ListboxSelect>>", on_macro_selected)

        def new_macro() -> None:
            current_macro_index[0] = None
            set_editor_state(
                "Neues Makro · Entwurf",
                "Auslösetaste wählen, Ausgabeschritte hinzufügen und danach „Makro übernehmen“ klicken.",
            )
            enabled_var.set(True)
            loop_var.set(False)
            preferred = next(
                (
                    label
                    for label, source_id in options.items()
                    if source_id == preset_trigger
                ),
                None,
            )
            trigger_var.set(preferred or first_source)
            draft_steps.clear()
            macro_list.selection_clear(0, "end")
            render_steps()

        target_row = ttk.Frame(form, style="Card.TFrame")
        target_row.pack(fill="x", pady=(10, 0))
        ttk.Label(target_row, text="Ausgabeschritt", style="Card.TLabel").pack(
            side="left"
        )
        target_var = tk.StringVar(value=first_target)
        target_box = ttk.Combobox(
            target_row,
            state="readonly",
            style="Dark.TCombobox",
            values=tuple(target_options),
            textvariable=target_var,
            width=27,
        )
        target_box.pack(side="left", fill="x", expand=True, padx=(8, 0))

        timing_row = ttk.Frame(form, style="Card.TFrame")
        timing_row.pack(fill="x", pady=(8, 0))
        ttk.Label(timing_row, text="Halten ms", style="Card.TLabel").pack(side="left")
        press_var = tk.StringVar(value="100")
        ttk.Spinbox(
            timing_row,
            from_=30,
            to=1000,
            increment=10,
            textvariable=press_var,
            width=6,
            style="Dark.TEntry",
        ).pack(side="left", padx=(5, 10))
        ttk.Label(timing_row, text="Pause ms", style="Card.TLabel").pack(side="left")
        gap_var = tk.StringVar(value="50")
        ttk.Spinbox(
            timing_row,
            from_=16,
            to=1000,
            increment=10,
            textvariable=gap_var,
            width=6,
            style="Dark.TEntry",
        ).pack(side="left", padx=(5, 10))
        ttk.Button(
            timing_row, text="Schritt hinzufügen", command=lambda: add_step()
        ).pack(side="right")

        step_actions = ttk.Frame(form, style="Card.TFrame")
        step_actions.pack(fill="x", pady=(8, 0))

        def add_step() -> None:
            if len(draft_steps) >= MAX_MACRO_STEPS:
                messagebox.showinfo(
                    self.t("Makro-Limit"),
                    self.t("Ein Makro kann höchstens 16 Schritte enthalten."),
                    parent=dialog,
                )
                return
            try:
                hold_ms = int(press_var.get())
                delay_ms = int(gap_var.get())
            except ValueError:
                messagebox.showinfo(
                    self.t("Ungültige Dauer"),
                    self.t("Halte- und Pausenzeit müssen Zahlen sein."),
                    parent=dialog,
                )
                return
            if not 30 <= hold_ms <= 1000 or not 16 <= delay_ms <= 1000:
                messagebox.showinfo(
                    self.t("Ungültige Dauer"),
                    self.t("Haltezeit: 30–1000 ms; Pause: 16–1000 ms."),
                    parent=dialog,
                )
                return
            target_id = target_options.get(target_var.get())
            if target_id is None:
                return
            draft_steps.append(
                {"target": target_id, "press_ms": hold_ms, "gap_ms": delay_ms}
            )
            render_steps()
            step_list.selection_set("end")

        def remove_step() -> None:
            selection = step_list.curselection()
            if selection:
                del draft_steps[int(selection[0])]
                render_steps()

        def move_step(offset: int) -> None:
            selection = step_list.curselection()
            if not selection:
                return
            old_index = int(selection[0])
            new_index = old_index + offset
            if 0 <= new_index < len(draft_steps):
                draft_steps[old_index], draft_steps[new_index] = (
                    draft_steps[new_index],
                    draft_steps[old_index],
                )
                render_steps()
                step_list.selection_set(new_index)

        ttk.Button(step_actions, text="Entfernen", command=remove_step).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(step_actions, text="↑", width=3, command=lambda: move_step(-1)).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(step_actions, text="↓", width=3, command=lambda: move_step(1)).pack(
            side="left", padx=(6, 0)
        )

        def persist_macro_profile(previous_macros: list[dict]) -> bool:
            key = "device_macros" if raw_device else "macros"
            updated_macros = normalized_macros(macros)
            if raw_device:
                profile.setdefault(key, {})[device_key] = updated_macros
            else:
                profile[key] = updated_macros
            if not self.persist_profiles():
                if raw_device:
                    profile.setdefault(key, {})[device_key] = previous_macros
                else:
                    profile[key] = previous_macros
                return False
            macros[:] = updated_macros
            LOGGER.info(
                "Auto-saved %d macro(s) for profile=%s device_scope=%s",
                len(macros),
                self.active_profile_name,
                device_key or "standard",
            )
            self.apply_profile_to_bridge()
            self.set_textvar(self.macro_status, "Makros gespeichert.")
            return True

        def persist_macro_toggles(*_args) -> None:
            index = current_macro_index[0]
            if loading_macro[0] or index is None or not 0 <= index < len(macros):
                return
            previous_macros = normalized_macros(macros)
            macros[index]["enabled"] = enabled_var.get()
            macros[index]["loop"] = loop_var.get()
            if persist_macro_profile(previous_macros):
                render_macros(index)
                return
            macros[:] = previous_macros
            loading_macro[0] = True
            enabled_var.set(macros[index]["enabled"])
            loop_var.set(macros[index]["loop"])
            loading_macro[0] = False
            render_macros(index)

        enabled_var.trace_add("write", persist_macro_toggles)
        loop_var.trace_add("write", persist_macro_toggles)

        def save_macro() -> None:
            trigger = options.get(trigger_var.get())
            if trigger is None:
                messagebox.showinfo(
                    self.t("Auslösetaste fehlt"),
                    self.t("Bitte eine Auslösetaste auswählen."),
                    parent=dialog,
                )
                return
            if not draft_steps:
                messagebox.showinfo(
                    self.t("Sequenz fehlt"),
                    self.t("Füge mindestens einen Ausgabeschritt hinzu."),
                    parent=dialog,
                )
                return
            existing_index = current_macro_index[0]
            if existing_index is None and len(macros) >= MAX_MACROS:
                messagebox.showinfo(
                    self.t("Makro-Limit"),
                    self.t("Ein Profil kann höchstens 32 Makros enthalten."),
                    parent=dialog,
                )
                return
            if any(
                index != existing_index and macro["trigger"] == trigger
                for index, macro in enumerate(macros)
            ):
                messagebox.showinfo(
                    self.t("Taste bereits belegt"),
                    self.t("Für diese Auslösetaste gibt es bereits ein Makro."),
                    parent=dialog,
                )
                return
            previous_macros = normalized_macros(macros)
            macro = {
                "trigger": trigger,
                "steps": [step.copy() for step in draft_steps],
                "enabled": enabled_var.get(),
                "loop": loop_var.get(),
            }
            if existing_index is None:
                macros.append(macro)
                current_macro_index[0] = len(macros) - 1
            else:
                macros[existing_index] = macro
            if not persist_macro_profile(previous_macros):
                macros[:] = previous_macros
                current_macro_index[0] = existing_index
                render_macros(existing_index if existing_index is not None else None)
                if existing_index is not None:
                    load_macro(existing_index)
                set_editor_state(
                    "Speichern fehlgeschlagen",
                    "Das Makro konnte nicht im Profil gespeichert werden.",
                )
                return
            render_macros(current_macro_index[0])
            load_macro(current_macro_index[0])
            set_editor_state(
                "Makro übernommen",
                "Das Makro wurde sofort im Profil gespeichert und übernommen.",
            )

        def delete_macro() -> None:
            index = current_macro_index[0]
            if index is None:
                selection = macro_list.curselection()
                index = int(selection[0]) if selection else None
            if index is None or not 0 <= index < len(macros):
                return
            previous_macros = normalized_macros(macros)
            del macros[index]
            if not persist_macro_profile(previous_macros):
                macros[:] = previous_macros
                render_macros(index)
                load_macro(index)
                return
            current_macro_index[0] = None
            new_macro()
            render_macros()

        ttk.Button(
            step_actions,
            text="Makro übernehmen",
            style="Accent.TButton",
            command=save_macro,
        ).pack(side="right")
        render_macros()
        if macros:
            selected_macro = next(
                (
                    index
                    for index, macro in enumerate(macros)
                    if preset_trigger is not None
                    and (macro["trigger"] == preset_trigger)
                ),
                0,
            )
            load_macro(selected_macro)
            macro_list.selection_set(selected_macro)
        elif preset_trigger is not None:
            new_macro()

        footer = ttk.Frame(frame)
        footer.pack(fill="x", side="bottom", pady=(12, 0))
        ttk.Button(
            footer,
            text="Laufendes Makro abbrechen",
            command=lambda: self.commands.put(("cancel_macro", None)),
        ).pack(side="left")

        def close_macro_dialog() -> None:
            self.button_capture_handler = None
            self.commands.put(("cancel_button_capture", None))
            disable_wheel()
            dialog.destroy()
            if restore_grab is not None and restore_grab.winfo_exists():
                restore_grab.grab_set()
                restore_grab.event_generate("<<MacrosChanged>>", when="tail")
                if device is not None:
                    self.commands.put(("monitor_buttons", dict(device)))

        dialog.protocol("WM_DELETE_WINDOW", close_macro_dialog)

        ttk.Button(footer, text="Schließen", command=close_macro_dialog).pack(
            side="right", padx=(0, 8)
        )
        self.apply_localization(dialog)

    def open_button_mapping(self) -> None:
        """Open the mapping dialog and leave the main window usable on failure."""
        try:
            self.edit_button_mapping()
        except Exception:
            LOGGER.exception("Could not open the button mapping dialog")
            dialog = self.mapping_dialog
            self.mapping_dialog = None
            self.mapping_dialog_save = None
            self.button_state_handler = None
            if dialog is not None:
                try:
                    if dialog.winfo_exists():
                        dialog.grab_release()
                        dialog.destroy()
                except tk.TclError:
                    pass
            try:
                self.commands.put(("stop_button_monitor", None))
            except Exception:
                LOGGER.exception(
                    "Could not stop button monitor after mapping dialog failure"
                )
            messagebox.showerror(
                self.t("Tastenbelegung konnte nicht geöffnet werden"),
                self.t(
                    "Die Tastenbelegung konnte nicht geöffnet werden. Details stehen im Protokoll."
                ),
                parent=self.root,
            )

    def edit_button_mapping(self) -> None:
        device = self.selected_device()
        macros = self.current_macros(device)
        if device is not None:
            source_rows = button_controls(
                self.device_from_payload(device), self.language
            )
            profile = self.profiles[self.active_profile_name]
            if device["kind"] == "raw":
                defaults = inferred_button_mapping(self.device_from_payload(device))
                stored = profile.get("device_mappings", {}).get(
                    str(device.get("mapping_key", "")), {}
                )
                mapping = normalized_mapping(stored, defaults)
                mapping_scope = self.tf(
                    "Dieses Gerät · {name}", name=str(device["name"])
                )
            else:
                defaults = DEFAULT_BUTTON_MAPPING
                mapping = normalized_mapping(profile.get("buttons", {}))
                mapping_scope = self.tf(
                    "Alle SDL-Gamepads · {family}", family=str(device["family"])
                )
        else:
            source_rows = [
                (source_id, control_label(source_id, self.language), None)
                for source_id, _label, _ in STANDARD_BUTTONS
            ]
            defaults = DEFAULT_BUTTON_MAPPING
            mapping = self.current_mapping()
            mapping_scope = self.t("Standardisierte SDL-Belegung")

        for source_id, _label, _button in source_rows:
            mapping.setdefault(source_id, "unmapped")

        # Prepare all state and the save callback before creating the dialog.
        # A partially built Tk window must never expose widgets whose callback
        # closes over a function that has not been defined yet.
        output_mode = self.current_settings()["output_mode"]
        target_type = (
            self.t("Xbox-Profilbelegung")
            if output_mode == "direct"
            else (self.t("DS4") if output_mode == "virtual_ds4" else self.t("Xbox"))
        )
        target_ids = list(available_target_ids(output_mode))
        target_labels = {
            target_label(target_id, output_mode, self.language): target_id
            for target_id in target_ids
        }
        target_values = [
            target_label(target_id, output_mode, self.language)
            for target_id in target_ids
        ]
        selectors: dict[str, ttk.Combobox] = {}
        axis_selectors: dict[str, ttk.Combobox] = {}
        is_raw_device = device is not None and device["kind"] == "raw"
        device_key = str(device.get("mapping_key", "")) if is_raw_device else None
        axis_mapping = self.current_axis_mapping(device) if is_raw_device else {}
        save_feedback_var = tk.StringVar(value="")
        self._track_textvar(save_feedback_var)

        def save_mapping(close_after_save: bool = True) -> bool:
            updated = normalized_mapping(mapping, defaults)
            for source_id, selector in selectors.items():
                selected_index = selector.current()
                target_id = (
                    target_ids[selected_index]
                    if 0 <= selected_index < len(target_ids)
                    else target_labels.get(selector.get())
                )
                if target_id is None:
                    self.set_textvar(
                        save_feedback_var,
                        "Eine Tastenbelegung ist ungültig und wurde nicht gespeichert.",
                    )
                    LOGGER.error(
                        "Could not resolve displayed mapping profile=%s source=%s value=%r index=%s",
                        self.active_profile_name,
                        source_id,
                        selector.get(),
                        selected_index,
                    )
                    return False
                updated[source_id] = target_id

            updated_axes = {
                axis_id: None if selector.current() <= 0 else selector.current() - 1
                for axis_id, selector in axis_selectors.items()
            }
            profile_name = self.active_profile_name
            previous_profile = self.profiles[profile_name]
            self.profiles[profile_name] = updated_mapping_profile(
                previous_profile,
                updated,
                defaults,
                device_key=device_key,
                axis_mapping=updated_axes,
            )
            if not self.persist_profiles():
                self.profiles[profile_name] = previous_profile
                return False
            try:
                written_store = read_profile_store(PROFILE_FILE, LOGGER)
                written_profile = written_store["profiles"][profile_name]
                if is_raw_device:
                    written_mapping = written_profile.get("device_mappings", {}).get(
                        device_key or "", {}
                    )
                    written_axes = written_profile.get("device_axis_mappings", {}).get(
                        device_key, {}
                    )
                    if written_axes != updated_axes:
                        raise ValueError(
                            "The stored axis mapping differs from the selected mapping"
                        )
                else:
                    written_mapping = written_profile.get("buttons", {})
                if normalized_mapping(written_mapping, defaults) != updated:
                    raise ValueError(
                        "The stored mapping differs from the selected mapping"
                    )
            except (KeyError, ValueError, TypeError):
                LOGGER.exception(
                    "Mapping read-back verification failed for profile %s", profile_name
                )
                self.profiles[profile_name] = previous_profile
                try:
                    write_profile_store(
                        PROFILE_FILE, self.profile_store, keep_backup=False
                    )
                except (OSError, TypeError, ValueError):
                    LOGGER.exception(
                        "Could not restore the last verified mapping for profile %s",
                        profile_name,
                    )
                self.set_textvar(
                    save_feedback_var,
                    "Speichern fehlgeschlagen – Profil-Datei konnte nicht bestätigt werden.",
                )
                return False
            mapping.clear()
            mapping.update(updated)
            axis_mapping.clear()
            axis_mapping.update(updated_axes)
            LOGGER.info(
                "Button mapping saved for profile %s: %s",
                self.active_profile_name,
                updated,
            )
            self.set_textvar(
                save_feedback_var,
                "",
                template="Gespeichert im Profil „{profile}“.",
                profile=self.active_profile_name,
            )
            self.apply_profile_to_bridge()
            self.set_textvar(
                self.status,
                "",
                template="Tastenbelegung für „{profile}“ gespeichert.",
                profile=self.active_profile_name,
            )
            if close_after_save:
                close_mapping_dialog(save_first=False)
            return True

        dialog = tk.Toplevel(self.root)
        self.mapping_dialog = dialog
        self.set_localized_title(
            dialog, "Tastenbelegung – {profile}", profile=self.active_profile_name
        )
        self.size_dialog(dialog, 900, 740, 700, 520)
        dialog.transient(self.root)
        dialog.grab_set()

        frame = ttk.Frame(dialog, padding=18)
        frame.pack(fill="both", expand=True)
        mapping_title = ttk.Label(frame, style="Title.TLabel")
        self.set_localized_widget(
            mapping_title,
            "{profile} · Tastenbelegung",
            profile=self.active_profile_name,
        )
        mapping_title.pack(anchor="w")
        ttk.Label(frame, text=mapping_scope, style="SubTitle.TLabel").pack(
            anchor="w", pady=(3, 10)
        )
        if output_mode == "direct":
            ttk.Label(
                frame,
                text="Im Direktmodus werden Tasten nicht verarbeitet. Die Belegung greift bei virtueller PS- oder Xbox-Ausgabe.",
                style="SubTitle.TLabel",
                wraplength=640,
            ).pack(anchor="w", pady=(0, 8))

        if device is not None and device["kind"] == "raw":
            note = (
                "Generisches HID/DirectInput-Gerät: Die Roh-Tasten sind mit einer üblichen Belegung vorbelegt. "
                "Bitte vor dem Spielen prüfen. Achsen verwenden automatisch die häufige Reihenfolge 0/1 links, 2/3 rechts, 4/5 Trigger."
            )
            ttk.Label(frame, text=note, style="SubTitle.TLabel", wraplength=640).pack(
                anchor="w", pady=(0, 10)
            )

        family = str(device.get("family", "")) if device else ""
        controller_card = ttk.Frame(frame, style="Card.TFrame", padding=8)
        controller_card.pack(fill="x", pady=(0, 8))
        controller_title_row = ttk.Frame(controller_card, style="Card.TFrame")
        controller_title_row.pack(fill="x")
        ttk.Label(
            controller_title_row,
            text="Controller · Taste anklicken",
            style="Section.TLabel",
        ).pack(side="left")
        selected_control_var = tk.StringVar(value="")
        selected_control_label = ttk.Label(
            controller_title_row,
            textvariable=selected_control_var,
            style="CardMuted.TLabel",
        )
        selected_control_label.pack(side="left", padx=(12, 0))
        controller_canvas = tk.Canvas(
            controller_card,
            height=190,
            background="#111c2f",
            highlightthickness=0,
            borderwidth=0,
        )
        controller_canvas.pack(fill="x", expand=True, pady=(4, 0))
        pressed_state: dict[str, bool] = {}
        selected_source: list[str | None] = [source_rows[0][0] if source_rows else None]
        source_labels = {source_id: label for source_id, label, _ in source_rows}
        macro_triggers = {
            trigger
            for macro in macros
            if macro.get("enabled", True)
            for trigger in (macro["trigger"],)
            if trigger
        }

        def select_source(source_id: str) -> None:
            selected_source[0] = source_id
            selected_control_var.set(
                source_labels.get(
                    source_id, control_label(source_id, self.language, family)
                )
            )
            selector = selectors.get(source_id)
            if selector is not None:
                selector.focus_set()
            draw_controller()

        def draw_controller() -> None:
            controller_canvas.delete("all")
            width = max(controller_canvas.winfo_width(), 760)
            height = max(controller_canvas.winfo_height(), 190)
            sx, sy = width / 760, height / 190

            def point(x: float, y: float) -> tuple[float, float]:
                return x * sx, y * sy

            silhouette = [
                point(x, y)
                for x, y in (
                    (160, 28),
                    (600, 28),
                    (645, 58),
                    (710, 160),
                    (650, 180),
                    (565, 126),
                    (195, 126),
                    (110, 180),
                    (50, 160),
                    (115, 58),
                )
            ]
            controller_canvas.create_polygon(
                silhouette,
                smooth=True,
                fill="#18263b",
                outline="#33445e",
                width=2,
            )
            semantic_positions = {
                "left_shoulder": (250, 45, "rect"),
                "right_shoulder": (510, 45, "rect"),
                "left_stick": (265, 132, "stick"),
                "right_stick": (495, 132, "stick"),
                "dpad_up": (165, 92, "small"),
                "dpad_down": (165, 130, "small"),
                "dpad_left": (146, 111, "small"),
                "dpad_right": (184, 111, "small"),
                "back": (350, 64, "tiny"),
                "start": (410, 64, "tiny"),
                "guide": (380, 137, "tiny"),
                "touchpad": (380, 93, "pad"),
                "south": (580, 119, "face"),
                "east": (602, 99, "face"),
                "west": (558, 99, "face"),
                "north": (580, 79, "face"),
                "misc_1": (380, 54, "tiny"),
                "paddle_1": (215, 160, "tiny"),
                "paddle_2": (545, 160, "tiny"),
                "paddle_3": (235, 160, "tiny"),
                "paddle_4": (525, 160, "tiny"),
            }
            raw_positions = {
                0: "south",
                1: "east",
                2: "west",
                3: "north",
                4: "left_shoulder",
                5: "right_shoulder",
                6: "back",
                7: "start",
                8: "left_stick",
                9: "right_stick",
                10: "guide",
                11: "touchpad",
            }
            symbols = {
                "PlayStation": {
                    "south": "✕",
                    "east": "○",
                    "west": "□",
                    "north": "△",
                    "back": "SHARE",
                    "start": "OPTIONS",
                    "guide": "PS",
                    "left_shoulder": "L1",
                    "right_shoulder": "R1",
                },
                "Xbox": {
                    "south": "A",
                    "east": "B",
                    "west": "X",
                    "north": "Y",
                    "back": "VIEW",
                    "start": "MENU",
                    "guide": "XBOX",
                    "left_shoulder": "LB",
                    "right_shoulder": "RB",
                },
            }
            symbols.setdefault(
                "default",
                {"south": "A/✕", "east": "B/○", "west": "X/□", "north": "Y/△"},
            )
            family_symbols = symbols.get(family, symbols["default"])
            for source_id, _label, _button in source_rows:
                base_id = source_id
                if source_id.startswith("button_"):
                    try:
                        raw_index = int(source_id.split("_")[1])
                        if device and device.get("kind") == "standard":
                            semantic_positions[source_id] = (
                                330 + ((raw_index - 4) // 11) * 38,
                                160,
                                "tiny",
                            )
                            base_id = source_id
                        else:
                            base_id = raw_positions.get(raw_index, "")
                        if raw_index >= 12 and not (
                            device and device.get("kind") == "standard"
                        ):
                            semantic_positions[source_id] = (
                                300 + ((raw_index - 12) % 7) * 28,
                                158 + ((raw_index - 12) // 7) * 16,
                                "tiny",
                            )
                            base_id = source_id
                    except ValueError:
                        base_id = ""
                if source_id.startswith("hat_"):
                    direction = source_id.split("_")[-1]
                    base_id = f"dpad_{direction}"
                if base_id not in semantic_positions:
                    continue
                x, y, shape = semantic_positions[base_id]
                cx, cy = point(x, y)
                pressed = pressed_state.get(source_id, False)
                selected = selected_source[0] == source_id
                fill = "#14b88a" if pressed else ("#0f4c64" if selected else "#26374f")
                outline = (
                    "#5eead4" if selected else ("#5f7898" if pressed else "#40536d")
                )
                radius = {"face": 14, "small": 9, "stick": 21, "tiny": 8}.get(shape, 0)
                if shape == "pad":
                    x1, y1 = point(x - 28, y - 11)
                    x2, y2 = point(x + 28, y + 11)
                    item = controller_canvas.create_rectangle(
                        x1,
                        y1,
                        x2,
                        y2,
                        fill=fill,
                        outline=outline,
                        width=2,
                    )
                    text_value = "TOUCH"
                elif shape == "rect":
                    x1, y1 = point(x - 28, y - 8)
                    x2, y2 = point(x + 28, y + 8)
                    item = controller_canvas.create_rectangle(
                        x1, y1, x2, y2, fill=fill, outline=outline, width=2
                    )
                    text_value = family_symbols.get(
                        base_id, "L1" if base_id == "left_shoulder" else "R1"
                    )
                else:
                    rx, ry = radius * sx, radius * sy
                    item = controller_canvas.create_oval(
                        cx - rx,
                        cy - ry,
                        cx + rx,
                        cy + ry,
                        fill=fill,
                        outline=outline,
                        width=2,
                    )
                    if base_id.startswith("dpad_"):
                        text_value = {
                            "dpad_up": "↑",
                            "dpad_down": "↓",
                            "dpad_left": "←",
                            "dpad_right": "→",
                        }[base_id]
                    elif base_id in ("left_stick", "right_stick"):
                        text_value = "L3" if base_id == "left_stick" else "R3"
                    elif source_id.startswith("button_"):
                        text_value = f"B{source_id.split('_')[1]}"
                    else:
                        text_value = family_symbols.get(
                            base_id, source_labels.get(source_id, source_id)
                        )
                tag = f"control:{source_id}"
                controller_canvas.addtag_withtag(tag, item)
                controller_canvas.addtag_withtag(
                    tag,
                    controller_canvas.create_text(
                        cx,
                        cy,
                        text=text_value,
                        fill="#f1f5f9",
                        font=("Segoe UI", 8, "bold"),
                    ),
                )
                if source_id in macro_triggers:
                    dot_x, dot_y = point(x + 11, y - 11)
                    controller_canvas.create_oval(
                        dot_x - 3,
                        dot_y - 3,
                        dot_x + 3,
                        dot_y + 3,
                        fill="#fbbf24",
                        outline="",
                    )

        def on_controller_click(event) -> None:
            for item in reversed(
                controller_canvas.find_overlapping(
                    event.x - 1, event.y - 1, event.x + 1, event.y + 1
                )
            ):
                for tag in controller_canvas.gettags(item):
                    if tag.startswith("control:"):
                        select_source(tag.removeprefix("control:"))
                        return

        controller_canvas.bind("<Button-1>", on_controller_click)
        controller_canvas.bind("<Configure>", lambda _event: draw_controller())

        table = ttk.Frame(frame, style="Card.TFrame", padding=12)
        table.pack(fill="both", expand=True)
        canvas = tk.Canvas(
            table, background="#111c2f", highlightthickness=0, borderwidth=0
        )
        scrollbar = ttk.Scrollbar(table, orient="vertical", command=canvas.yview)
        content = ttk.Frame(canvas, style="Card.TFrame")
        content.bind(
            "<Configure>",
            lambda _event: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas_window = canvas.create_window((0, 0), window=content, anchor="nw")
        canvas.bind(
            "<Configure>",
            lambda event: canvas.itemconfigure(canvas_window, width=event.width),
        )
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        content.columnconfigure(0, weight=1)
        content.columnconfigure(1, weight=1)
        content.columnconfigure(2, weight=0)
        ttk.Label(content, text="Eingabe am Controller", style="Section.TLabel").grid(
            row=0, column=0, sticky="w", pady=(2, 8)
        )
        output_header = ttk.Label(content, style="Section.TLabel")
        self.set_localized_widget(
            output_header, "Ausgabe · {target}", target=target_type
        )
        output_header.grid(row=0, column=1, sticky="w", pady=(2, 8))
        ttk.Label(content, text="Makro", style="Section.TLabel").grid(
            row=0, column=2, sticky="w", padx=(8, 0), pady=(2, 8)
        )

        # Keep the stable target IDs parallel to the displayed labels. Saving
        # by label alone is fragile when translations or UI text change.
        macro_buttons: dict[str, ttk.Button] = {}

        def restore_mapping_controls() -> None:
            """Put the dialog back to the last mapping confirmed on disk."""
            for source_id, selector in selectors.items():
                target_id = mapping.get(source_id, "unmapped")
                try:
                    selector.current(target_ids.index(target_id))
                except ValueError:
                    selector.current(target_ids.index("unmapped"))
            for axis_id, selector in axis_selectors.items():
                axis_index = axis_mapping.get(axis_id)
                selector.current(0 if axis_index is None else int(axis_index) + 1)

        def assign_macro(source_id: str) -> None:
            dialog.grab_release()
            self.edit_macros(source_id, restore_grab=dialog)

        def on_mapping_target_selected(source_id: str) -> None:
            selector = selectors[source_id]
            selected_index = selector.current()
            selected_target = (
                target_ids[selected_index]
                if 0 <= selected_index < len(target_ids)
                else target_labels.get(selector.get())
            )
            LOGGER.info(
                "Mapping selection profile=%s source=%s target=%s index=%s",
                self.active_profile_name,
                source_id,
                selected_target,
                selected_index,
            )
            if selected_target is None:
                self.set_textvar(
                    save_feedback_var,
                    "Diese Auswahl konnte nicht zugeordnet werden. Bitte erneut wählen.",
                )
                LOGGER.error(
                    "Could not resolve mapping selection profile=%s source=%s value=%r",
                    self.active_profile_name,
                    source_id,
                    selector.get(),
                )
                return
            previous_mapping = mapping.copy()
            mapping[source_id] = selected_target
            try:
                if not save_mapping(close_after_save=False):
                    mapping.clear()
                    mapping.update(previous_mapping)
                    restore_mapping_controls()
                    return
                select_source(source_id)
            except Exception:
                mapping.clear()
                mapping.update(previous_mapping)
                restore_mapping_controls()
                LOGGER.exception(
                    "Automatic mapping save failed for profile=%s source=%s target=%s",
                    self.active_profile_name,
                    source_id,
                    selected_target,
                )
                self.set_textvar(
                    save_feedback_var,
                    "Änderung konnte nicht gespeichert werden. Details stehen im Protokoll.",
                )

        for row_index, (source_id, source_label, _source_button) in enumerate(
            source_rows, start=1
        ):
            source_widget = ttk.Label(
                content, text=source_label, style="Card.TLabel", cursor="hand2"
            )
            source_widget.grid(
                row=row_index, column=0, sticky="w", padx=(0, 16), pady=3
            )
            source_widget.bind(
                "<Button-1>", lambda _event, source=source_id: select_source(source)
            )
            selector = ttk.Combobox(
                content, state="readonly", style="Dark.TCombobox", values=target_values
            )
            try:
                selector.current(target_ids.index(mapping[source_id]))
            except ValueError:
                selector.current(target_ids.index("unmapped"))
            selector.grid(row=row_index, column=1, sticky="ew", pady=3)
            selectors[source_id] = selector
            selector.bind(
                "<<ComboboxSelected>>",
                lambda _event, source=source_id: on_mapping_target_selected(source),
            )
            has_macro = any(macro["trigger"] == source_id for macro in macros)
            macro_button = ttk.Button(
                content,
                text="Makro ✓" if has_macro else "Makro…",
                width=10,
                command=lambda source=source_id: assign_macro(source),
            )
            macro_button.grid(row=row_index, column=2, sticky="ew", padx=(8, 0), pady=3)
            macro_buttons[source_id] = macro_button

        if selected_source[0] is not None:
            select_source(selected_source[0])
        draw_controller()

        def update_button_view(states: dict[str, bool]) -> None:
            pressed_state.clear()
            pressed_state.update(states or {})
            draw_controller()

        self.button_state_handler = update_button_view
        if device is not None:
            self.commands.put(("monitor_buttons", dict(device)))

        def close_mapping_dialog(save_first: bool = True) -> None:
            # Save the values currently shown in the widgets as a final guard.
            # This covers window-close and the Schließen button even if a theme
            # or Tk version failed to deliver <<ComboboxSelected>>.
            LOGGER.info(
                "Mapping dialog close requested for profile %s",
                self.active_profile_name,
            )
            if save_first:
                try:
                    if not save_mapping(close_after_save=False):
                        LOGGER.error(
                            "Profile save failed while closing mapping dialog; closing anyway"
                        )
                except Exception:
                    LOGGER.exception(
                        "Profile save raised while closing mapping dialog; closing anyway"
                    )
            self.mapping_dialog_save = None
            self.button_state_handler = None
            self.mapping_dialog = None
            self.commands.put(("stop_button_monitor", None))
            dialog.destroy()

        def refresh_macro_markers() -> None:
            current_macros = self.current_macros(device)
            macro_triggers.clear()
            macro_triggers.update(
                macro["trigger"]
                for macro in current_macros
                if macro.get("enabled", True)
            )
            for source_id, macro_button in macro_buttons.items():
                has_macro = any(
                    macro["trigger"] == source_id for macro in current_macros
                )
                self.set_localized_widget(
                    macro_button, "Makro ✓" if has_macro else "Makro…"
                )
            draw_controller()

        dialog.protocol("WM_DELETE_WINDOW", close_mapping_dialog)
        dialog.bind("<<MacrosChanged>>", lambda _event: refresh_macro_markers())

        if device is not None and device["kind"] == "raw":
            descriptor = self.device_from_payload(device)
            axis_frame = ttk.LabelFrame(
                frame, text="Analoge Achsen · nur dieses Gerät", padding=10
            )
            axis_frame.pack(fill="x", pady=(10, 0))
            available_axes = [self.t("Nicht zugeordnet")] + [
                self.tf("Achse {index}", index=str(index))
                for index in range(descriptor.axes)
            ]
            for axis_row, (axis_id, _axis_source_label, _default_index) in enumerate(
                RAW_AXIS_CONTROLS
            ):
                row = axis_row // 2
                column = (axis_row % 2) * 2
                ttk.Label(axis_frame, text=axis_label(axis_id, self.language)).grid(
                    row=row, column=column, sticky="w", padx=(4, 8), pady=3
                )
                selector = ttk.Combobox(
                    axis_frame,
                    state="readonly",
                    style="Dark.TCombobox",
                    width=16,
                    values=available_axes,
                )
                selected_axis = axis_mapping.get(axis_id)
                selector.current(0 if selected_axis is None else int(selected_axis) + 1)
                selector.grid(row=row, column=column + 1, sticky="ew", pady=3)
                axis_selectors[axis_id] = selector
                selector.bind(
                    "<<ComboboxSelected>>",
                    lambda _event: (
                        save_mapping(close_after_save=False)
                        or restore_mapping_controls()
                    ),
                )
            axis_frame.columnconfigure(1, weight=1)
            axis_frame.columnconfigure(3, weight=1)

        footer = ttk.Frame(frame)
        footer.pack(fill="x", pady=(12, 0))
        ttk.Label(
            footer, textvariable=save_feedback_var, style="CardMuted.TLabel"
        ).pack(side="left")
        ttk.Button(footer, text="Schließen", command=close_mapping_dialog).pack(
            side="right", padx=(8, 0)
        )

        def save_mapping_from_button() -> None:
            if not save_mapping():
                restore_mapping_controls()

        # Keep the live dialog state reachable by the main-window shutdown
        # handler too; destroying the root does not send WM_DELETE_WINDOW to
        # each child Toplevel.
        self.mapping_dialog_save = lambda: save_mapping(close_after_save=False)
        ttk.Button(
            footer,
            text="Speichern",
            style="Accent.TButton",
            command=save_mapping_from_button,
        ).pack(side="right")
        self.apply_localization(dialog)

    def open_log(self) -> None:
        try:
            os.startfile(str(LOG_FILE))
        except Exception as exc:
            LOGGER.exception("Could not open log file")
            self.set_textvar(
                self.status,
                "",
                template="Log konnte nicht geöffnet werden: {error}",
                error=str(exc),
            )

    def toggle(self) -> None:
        if self.running:
            self.commands.put(("stop", None))
            self.running = False
            self.running_output_mode = None
            self.refresh_output_controls()
            self.set_textvar(self.status, "Virtuelle Ausgabe wird gestoppt.")
            return

        output_mode = self.current_settings()["output_mode"]
        if output_mode == "direct":
            self.set_textvar(
                self.status,
                "Direktmodus: Das physische Gamepad bleibt unverändert; es wird keine Bridge benötigt.",
            )
            return
        selected = self.device_box.current()
        if selected < 0 or selected >= len(self.devices):
            self.set_textvar(self.status, "Bitte zuerst einen Controller auswählen.")
            return
        device = self.devices[selected]
        self.commands.put(
            (
                "start",
                {
                    **device,
                    "profile_name": self.active_profile_name,
                    "mapping": self.current_mapping(device),
                    "axis_mapping": self.current_axis_mapping(device),
                    "settings": self.current_settings(),
                    "macros": self.current_macros(device),
                },
            )
        )
        self.running = True
        self.running_output_mode = output_mode
        self.refresh_output_controls()
        self.set_textvar(
            self.status,
            "",
            template="Starte {mode} für „{profile}“…",
            mode=output_mode_label(output_mode, self.language),
            profile=self.active_profile_name,
        )

    def process_events(self) -> None:
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break

            if kind == "devices":
                previous_device = self.selected_device()
                previous_key = (
                    previous_device.get("mapping_key") if previous_device else None
                )
                self.devices = payload or []
                self.device_box["values"] = [
                    f"{device['family']} · {device['name']} · "
                    f"{self.t('SDL standard') if device['kind'] == 'standard' else self.t('generisch · Rohdaten')} · "
                    f"{self.tf('Gerät {index}', index=str(device['index'] + 1))}"
                    for device in self.devices
                ]
                if self.devices:
                    matching_device = next(
                        (
                            index
                            for index, device in enumerate(self.devices)
                            if device.get("mapping_key") == previous_key
                        ),
                        None,
                    )
                    self.device_box.current(
                        matching_device if matching_device is not None else 0
                    )
                    self.on_device_selected()
                    self.refresh_output_controls()
                else:
                    self.set_textvar(
                        self.device_status,
                        "Kein Gamepad erkannt · USB/Bluetooth und HidHide-Freigabe prüfen",
                    )
                    if not self.running:
                        self.set_textvar(
                            self.status,
                            "Gamepad fehlt. Verbindung prüfen oder die Suche aktualisieren.",
                        )
                    self.device_box.set("")
                    self.refresh_output_controls()
            elif kind == "status":
                if self.current_settings()[
                    "output_mode"
                ] == "direct" and payload.startswith("Bereit"):
                    self.set_textvar(
                        self.status,
                        "Direktmodus – InputWeave verändert keine Eingaben.",
                    )
                elif payload.startswith("Aktiv – Profil „"):
                    profile_name = payload.partition("Profil „")[2].partition("“")[0]
                    if "Sticks 1 Sekunde" in payload:
                        self.set_textvar(
                            self.status,
                            "",
                            template="Aktiv – Profil „{profile}“; Sticks 1 Sekunde für Driftkalibrierung loslassen.",
                            profile=profile_name,
                        )
                    else:
                        target = (
                            "PS4-Ausgabe"
                            if "PS4-Ausgabe" in payload
                            else "Xbox-Ausgabe"
                        )
                        self.set_textvar(
                            self.status,
                            "",
                            template="Aktiv – Profil „{profile}“; {target} bereit.",
                            profile=profile_name,
                            target=self.t(target),
                        )
                else:
                    self.set_textvar(self.status, payload)
                if payload == "Gestoppt." or payload.startswith("Controller getrennt"):
                    self.running = False
                    self.running_output_mode = None
                    self.refresh_output_controls()
            elif kind == "error":
                self.set_textvar(self.status, payload)
                self.running = False
                self.running_output_mode = None
                self.refresh_output_controls()
            elif kind == "input":
                visible = payload.removeprefix("Erkannte Taste(n): ")
                if visible == "keine":
                    visible = self.t("keine")
                self.set_textvar(
                    self.input_status,
                    "",
                    template="Erkannte Taste(n): {visible}",
                    visible=visible,
                )
            elif kind == "macro":
                if payload.get("state") == "started":
                    device = self.selected_device()
                    trigger = control_label(
                        payload.get("trigger", ""),
                        self.language,
                        str(device.get("family", "")) if device else None,
                    )
                    if payload.get("loop", False):
                        self.set_textvar(
                            self.macro_status,
                            "",
                            template="Makro läuft endlos: {trigger} · {steps} Schritte – erneut drücken zum Abbrechen.",
                            trigger=trigger,
                            steps=str(payload.get("steps", 0)),
                        )
                    else:
                        self.set_textvar(
                            self.macro_status,
                            "",
                            template="Makro aktiv: {trigger} · {steps} Schritte – dieselbe Taste erneut drücken zum Abbrechen.",
                            trigger=trigger,
                            steps=str(payload.get("steps", 0)),
                        )
                elif payload.get("state") == "completed":
                    self.set_textvar(self.macro_status, "Makro abgeschlossen.")
                else:
                    self.set_textvar(self.macro_status, "Makro abgebrochen.")
            elif kind == "button_capture":
                if self.button_capture_handler is not None:
                    self.button_capture_handler(payload)
            elif kind == "button_states":
                if self.button_state_handler is not None:
                    self.button_state_handler(payload or {})
            elif kind == "analog":
                self.set_textvar(
                    self.analog_status,
                    "",
                    template="L-Stick X {lx}  Y {ly}    R-Stick X {rx}  Y {ry}    L2 {lt}%  R2 {rt}%",
                    lx=f"{payload['lx']:+.2f}",
                    ly=f"{payload['ly']:+.2f}",
                    rx=f"{payload['rx']:+.2f}",
                    ry=f"{payload['ry']:+.2f}",
                    lt=f"{payload['lt'] * 100:.0f}",
                    rt=f"{payload['rt'] * 100:.0f}",
                )
            elif kind == "dependencies":
                self.latest_dependency_report = payload
                missing_runtime = [
                    name
                    for name in ("Pygame", "vgamepad")
                    if not payload.get(name, {}).get("ok", False)
                ]
                driver = payload.get("ViGEmBus-Treiber", {})
                installer = payload.get("Treiberinstaller", {})
                hidhide = payload.get("HidHide", {})
                hidhide_state = hidhide.get("state")
                if hidhide.get("app_registered") is True:
                    hidhide_suffix = self.t(" · HidHide eingerichtet")
                elif hidhide.get("registration_state") == "cli_failed":
                    hidhide_suffix = self.t(" · HidHide-Client schließen und prüfen")
                elif hidhide.get("registration_state") == "inverse_mode_enabled":
                    hidhide_suffix = self.t(" · HidHide-Invert-Modus deaktivieren")
                elif hidhide_state in ("running", "installed") and not hidhide.get(
                    "cli_available", False
                ):
                    hidhide_suffix = self.t(" · HidHide-CLI fehlt")
                elif hidhide_state in ("running", "installed"):
                    hidhide_suffix = self.t(" · HidHide-Freigabe prüfen")
                elif hidhide_state == "missing":
                    hidhide_suffix = self.t(" · HidHide fehlt")
                else:
                    hidhide_suffix = ""
                can_install = driver.get("state") == "missing" and installer.get(
                    "verified", False
                )
                if can_install:
                    button_label = "Treiber installieren"
                elif driver.get("state") in ("running", "installed"):
                    button_label = "Treiber installiert"
                elif driver.get("state") == "missing":
                    button_label = "Installer prüfen"
                else:
                    button_label = "Status unklar"
                self.set_localized_widget(self.driver_install_button, button_label)
                self.driver_install_button.configure(
                    state="normal" if can_install else "disabled"
                )
                if missing_runtime:
                    self.set_textvar(
                        self.dependency_status,
                        "",
                        template="App-Bibliotheken fehlen: {libraries}. Portable Ausgabe reparieren.",
                        libraries=", ".join(missing_runtime),
                    )
                elif driver.get("state") == "missing":
                    suffix = (
                        "Installerprüfung fehlgeschlagen"
                        if not installer.get("verified", False)
                        else ""
                    )
                    if self.current_settings()["output_mode"] == "direct":
                        self.set_textvar(
                            self.dependency_status,
                            "",
                            template="Direktmodus ist verfügbar · ViGEmBus wird nur für virtuelle Modi benötigt.{hidhide}",
                            hidhide=hidhide_suffix,
                        )
                    else:
                        self.set_textvar(
                            self.dependency_status,
                            "",
                            template="ViGEmBus-Treiber fehlt.{suffix}{hidhide}",
                            suffix=(" " + self.t(suffix)) if suffix else "",
                            hidhide=hidhide_suffix,
                        )
                elif driver.get("state") == "unknown":
                    self.set_textvar(
                        self.dependency_status,
                        "Treiberstatus nicht lesbar; InputWeave nimmt aus Sicherheitsgründen keine Änderung vor.",
                    )
                elif driver.get("state") == "unsupported":
                    self.set_textvar(
                        self.dependency_status,
                        "",
                        template="Virtuelle Xbox-Ausgabe benötigt Windows und ViGEmBus.{hidhide}",
                        hidhide=hidhide_suffix,
                    )
                else:
                    state_text = driver.get("detail", "installiert")
                    self.set_textvar(
                        self.dependency_status,
                        "",
                        template="Bereit · Pygame · vgamepad · ViGEmBus {state}{hidhide}",
                        state=self.t(str(state_text)),
                        hidhide=hidhide_suffix,
                    )
            elif kind == "hidhide_setup":
                self.set_textvar(self.dependency_status, payload)

        if self.root.winfo_exists():
            self.root.after(100, self.process_events)

    def close(self) -> None:
        """Stop input work and tear down the UI without leaving a locked EXE behind."""
        if self._closing:
            return
        self._closing = True
        LOGGER.info("InputWeave window closing")

        # Stop the bridge before any UI or profile work.  A modal dialog can
        # otherwise hold up its final save callback and leave the one-file EXE
        # alive invisibly after its window has gone away.
        self.shutdown.set()
        self.button_capture_handler = None
        self.button_state_handler = None
        self.settings_dialog_save = None
        self.mapping_dialog_save = None
        self.mapping_dialog = None
        for command in ("cancel_button_capture", "stop_button_monitor", "stop"):
            try:
                self.commands.put_nowait((command, None))
            except Exception:
                LOGGER.exception("Could not queue %s during shutdown", command)

        try:
            if self.worker.is_alive():
                self.worker.join(timeout=1.0)
                if self.worker.is_alive():
                    LOGGER.warning(
                        "Bridge did not stop within one second; exiting with daemon bridge thread"
                    )
        finally:
            try:
                self.root.quit()
            except Exception:
                LOGGER.exception("Could not quit the app event loop")
            try:
                self.root.destroy()
            except Exception:
                LOGGER.exception("Could not destroy the app window")


def main() -> None:
    configure_logging()
    instance_guard, already_running = acquire_single_instance()
    if already_running:
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo("InputWeave", "InputWeave läuft bereits.", parent=root)
        root.destroy()
        return
    try:
        root = tk.Tk()
        app = InputWeaveApp(root)
        LOGGER.info(
            "InputWeave started; executable=%s; profile_store=%s; active_profile=%s",
            sys.executable,
            PROFILE_FILE,
            app.active_profile_name,
        )
        root.mainloop()
    finally:
        instance_guard.release()


if __name__ == "__main__":
    main()
