"""Controller capture, filtering, and virtual gamepad output."""

from __future__ import annotations

import logging
import math
import queue
import statistics
import threading
import time

from .controls import (
    DEFAULT_BUTTON_MAPPING,
    DS4_BUTTON_TARGET_ENUMS,
    DS4_DPAD_ENUMS,
    TARGET_BUTTONS,
    apply_radial_deadzone,
    apply_trigger_deadzone,
    default_output_mode,
    normalized_mapping,
    normalized_settings,
    target_label,
)
from .input_devices import (
    ControllerReader,
    InputDevice,
    button_controls,
    discover_devices,
)
from .localization import control_label
from .macros import MacroPlayer, normalized_macros

LOGGER = logging.getLogger("InputWeave")


class BridgeWorker(threading.Thread):
    """Owns SDL and ViGEm objects on one worker thread."""

    def __init__(
        self, events: queue.Queue, commands: queue.Queue, shutdown: threading.Event
    ):
        super().__init__(name="InputWeaveBridge", daemon=True)
        self.events = events
        self.commands = commands
        self.shutdown = shutdown
        self.last_buttons: dict[str, bool] = {}
        self.last_published_buttons: tuple[str, ...] | None = None

    def post(self, kind: str, payload=None) -> None:
        self.events.put((kind, payload))

    def run(self) -> None:
        try:
            import pygame
            import pygame._sdl2.controller as controller_api
        except Exception as exc:
            LOGGER.exception("Could not import pygame SDL controller support")
            self.post("error", f"Pygame fehlt oder konnte nicht geladen werden: {exc}")
            return

        try:
            import vgamepad as vg
        except Exception as exc:
            LOGGER.exception("Could not import vgamepad")
            self.post(
                "error", f"vgamepad fehlt oder ViGEmBus ist nicht verfügbar: {exc}"
            )
            return

        pygame.init()
        pygame.joystick.init()
        controller_api.init()
        LOGGER.info(
            "SDL initialized; pygame=%s; controllers=%d",
            pygame.version.ver,
            controller_api.get_count(),
        )
        self.post("status", "Bereit – Gamepad verbinden und Eingabegerät auswählen.")

        active = None
        virtual_pad = None
        active_profile = "Forza Horizon 6"
        output_mode = default_output_mode(active_profile)
        output_button_targets = {}
        button_mapping = DEFAULT_BUTTON_MAPPING.copy()
        profile_settings = normalized_settings({}, active_profile)
        macro_definitions = []
        macro_player = MacroPlayer()
        button_capture = {"reader": None, "previous": {}, "deadline": 0.0}
        button_monitor = {"reader": None, "last": None}

        def close_button_monitor() -> None:
            reader = button_monitor["reader"]
            if reader is not None:
                try:
                    reader.close()
                except Exception:
                    LOGGER.exception("Could not close controller monitor")
            button_monitor.update(reader=None, last=None)
            self.post("button_states", {})

        def publish_button_states(pressed: dict[str, bool]) -> None:
            current = tuple(
                sorted(source_id for source_id, state in pressed.items() if state)
            )
            if current != button_monitor["last"]:
                button_monitor["last"] = current
                self.post(
                    "button_states",
                    {source_id: bool(state) for source_id, state in pressed.items()},
                )

        def finish_button_capture(
            state: str, source_id: str | None = None, detail: str | None = None
        ) -> None:
            reader = button_capture["reader"]
            if reader is not None:
                try:
                    reader.close()
                except Exception:
                    LOGGER.exception("Could not close temporary controller reader")
            button_capture.update(reader=None, previous={}, deadline=0.0)
            payload = {"state": state}
            if source_id is not None:
                payload["source_id"] = source_id
            if detail is not None:
                payload["detail"] = detail
            self.post("button_capture", payload)

        def scan_button_capture(pressed: dict[str, bool]) -> None:
            if not button_capture["deadline"]:
                return
            if time.monotonic() >= button_capture["deadline"]:
                finish_button_capture("timeout")
                return
            previous = button_capture["previous"]
            for source_id, is_pressed in pressed.items():
                if is_pressed and not previous.get(source_id, False):
                    LOGGER.info("Controller button captured: %s", source_id)
                    finish_button_capture("detected", source_id)
                    return
            button_capture["previous"] = dict(pressed)

        stick_centers = {"left": (0.0, 0.0), "right": (0.0, 0.0)}
        calibration_samples = {"left": [], "right": []}
        calibration_until = 0.0
        last_telemetry = 0.0
        last_analog_log = 0.0
        last_scan = 0.0
        last_devices = None

        try:
            while not self.shutdown.is_set():
                now = time.monotonic()
                if (
                    now - last_scan >= 1.0
                    and not active
                    and button_capture["reader"] is None
                    and button_monitor["reader"] is None
                ):
                    pygame.event.pump()
                    devices = discover_devices(pygame, controller_api)
                    if devices != last_devices:
                        payload_devices = [device.to_payload() for device in devices]
                        LOGGER.info("Gamepad devices detected: %s", payload_devices)
                        self.post("devices", payload_devices)
                        last_devices = devices
                    last_scan = now

                try:
                    command, payload = self.commands.get(
                        timeout=(1.0 / profile_settings["polling_rate"])
                        if active
                        else (
                            1.0 / 125.0
                            if button_capture["reader"] is not None
                            or button_monitor["reader"] is not None
                            else 0.1
                        )
                    )
                except queue.Empty:
                    command = None
                    payload = None

                if command == "refresh":
                    last_scan = 0.0
                elif command == "mapping":
                    if macro_player.active:
                        macro_player.cancel()
                        self.post("macro", {"state": "cancelled"})
                    active_profile = payload["profile_name"]
                    button_mapping = normalized_mapping(payload["mapping"])
                    profile_settings = normalized_settings(
                        payload.get("settings"), active_profile
                    )
                    macro_definitions = normalized_macros(payload.get("macros", []))
                    if active:
                        active.set_axis_mapping(payload.get("axis_mapping"))
                    LOGGER.info(
                        "Active profile changed to %s; mapping=%s; settings=%s; enabled_macros=%d",
                        active_profile,
                        button_mapping,
                        profile_settings,
                        sum(1 for macro in macro_definitions if macro["enabled"]),
                    )
                elif command == "cancel_macro":
                    if macro_player.active:
                        LOGGER.info("Macro cancelled from the application UI")
                        macro_player.cancel()
                    self.post("macro", {"state": "cancelled"})
                elif command == "capture_button":
                    if button_capture["deadline"]:
                        finish_button_capture("cancelled")
                    close_button_monitor()
                    if active:
                        self.post(
                            "button_capture",
                            {
                                "state": "error",
                                "detail": "Stop the virtual output before detecting a controller button.",
                            },
                        )
                        continue
                    try:
                        device = InputDevice(
                            index=int(payload["index"]),
                            name=str(payload["name"]),
                            family=str(payload["family"]),
                            kind=str(payload["kind"]),
                            guid=str(payload.get("guid", "")),
                            axes=int(payload.get("axes", 0)),
                            buttons=int(payload.get("buttons", 0)),
                            hats=int(payload.get("hats", 0)),
                        )
                        reader = ControllerReader(pygame, controller_api, device)
                        pygame.event.pump()
                        button_capture.update(
                            reader=reader,
                            previous=reader.read_buttons(),
                            deadline=time.monotonic() + 20.0,
                        )
                        self.post(
                            "button_capture",
                            {"state": "waiting", "timeout_seconds": 20},
                        )
                    except Exception as exc:
                        LOGGER.exception("Could not start controller button detection")
                        finish_button_capture("error", detail=str(exc))
                elif command == "cancel_button_capture":
                    if button_capture["deadline"]:
                        finish_button_capture("cancelled")
                elif command == "monitor_buttons":
                    close_button_monitor()
                    if not active and not button_capture["deadline"]:
                        try:
                            device = InputDevice(
                                index=int(payload["index"]),
                                name=str(payload["name"]),
                                family=str(payload["family"]),
                                kind=str(payload["kind"]),
                                guid=str(payload.get("guid", "")),
                                axes=int(payload.get("axes", 0)),
                                buttons=int(payload.get("buttons", 0)),
                                hats=int(payload.get("hats", 0)),
                            )
                            reader = ControllerReader(pygame, controller_api, device)
                            button_monitor.update(reader=reader, last=None)
                            pygame.event.pump()
                            publish_button_states(reader.read_buttons())
                        except Exception:
                            LOGGER.exception("Could not start live controller monitor")
                            close_button_monitor()
                elif command == "stop_button_monitor":
                    close_button_monitor()
                elif (
                    command == "calibrate"
                    and active
                    and profile_settings["drift_compensation"]
                ):
                    calibration_samples = {"left": [], "right": []}
                    calibration_until = time.monotonic() + 1.0
                    self.post(
                        "status", "Driftkalibrierung: Sticks eine Sekunde loslassen."
                    )
                    LOGGER.info("Started one-second stick center calibration")
                elif command == "stop" and active:
                    if button_capture["deadline"]:
                        finish_button_capture("cancelled")
                    if macro_player.active:
                        macro_player.cancel()
                        self.post("macro", {"state": "cancelled"})
                    try:
                        virtual_pad.reset()
                        virtual_pad.update()
                    except Exception:
                        LOGGER.exception(
                            "Failed to reset virtual controller during stop"
                        )
                    active.close()
                    active = None
                    virtual_pad = None
                    close_button_monitor()
                    self.last_buttons.clear()
                    self.last_published_buttons = None
                    LOGGER.info("Bridge stopped")
                    self.post("status", "Gestoppt.")
                    # Let Windows remove the ViGEm-created output before the
                    # next input scan so InputWeave cannot select its own output.
                    last_scan = time.monotonic()
                elif command == "start" and not active:
                    close_button_monitor()
                    device = InputDevice(
                        index=int(payload["index"]),
                        name=str(payload["name"]),
                        family=str(payload["family"]),
                        kind=str(payload["kind"]),
                        guid=str(payload.get("guid", "")),
                        axes=int(payload.get("axes", 0)),
                        buttons=int(payload.get("buttons", 0)),
                        hats=int(payload.get("hats", 0)),
                    )
                    active_profile = payload["profile_name"]
                    button_mapping = normalized_mapping(payload["mapping"])
                    profile_settings = normalized_settings(
                        payload.get("settings"), active_profile
                    )
                    macro_definitions = normalized_macros(payload.get("macros", []))
                    macro_player.cancel()
                    output_mode = profile_settings["output_mode"]
                    stick_centers = {"left": (0.0, 0.0), "right": (0.0, 0.0)}
                    calibration_samples = {"left": [], "right": []}
                    calibration_until = 0.0
                    self.last_buttons.clear()
                    self.last_published_buttons = None
                    if output_mode == "direct":
                        self.post(
                            "status",
                            "Direktmodus: physischer Controller bleibt unverändert sichtbar; keine Filter oder virtuelle Ausgabe aktiv.",
                        )
                        continue
                    try:
                        active = ControllerReader(
                            pygame, controller_api, device, payload.get("axis_mapping")
                        )
                        sdl_mapping = (
                            active.controller.get_mapping()
                            if active.controller is not None
                            else "raw HID/DirectInput"
                        )
                        LOGGER.info(
                            "Selected controller index=%d family=%s kind=%s name=%s GUID=%s axes=%d buttons=%d hats=%d mapping=%s",
                            device.index,
                            device.family,
                            device.kind,
                            device.name,
                            device.guid,
                            device.axes,
                            device.buttons,
                            device.hats,
                            sdl_mapping,
                        )
                        LOGGER.info(
                            "Starting profile %s; button mapping=%s; settings=%s; enabled_macros=%d",
                            active_profile,
                            button_mapping,
                            profile_settings,
                            sum(1 for macro in macro_definitions if macro["enabled"]),
                        )
                        if output_mode == "virtual_ds4":
                            virtual_pad = vg.VDS4Gamepad()
                            output_button_targets = {
                                target_id: getattr(vg.DS4_BUTTONS, enum_name)
                                for target_id, enum_name in DS4_BUTTON_TARGET_ENUMS.items()
                            }
                            LOGGER.info("Virtual DualShock 4 controller created")
                        else:
                            virtual_pad = vg.VX360Gamepad()
                            output_button_targets = {
                                target_id: getattr(vg.XUSB_BUTTON, enum_name)
                                for target_id, (
                                    _label,
                                    enum_name,
                                ) in TARGET_BUTTONS.items()
                                if enum_name is not None
                            }
                            LOGGER.info("Virtual Xbox 360 controller created")
                        if profile_settings["drift_compensation"]:
                            calibration_until = time.monotonic() + 1.0
                            self.post(
                                "status",
                                f"Aktiv – Profil „{active_profile}“; Sticks 1 Sekunde für Driftkalibrierung loslassen.",
                            )
                        else:
                            label = (
                                "PS4-Ausgabe"
                                if output_mode == "virtual_ds4"
                                else "Xbox-Ausgabe"
                            )
                            self.post(
                                "status",
                                f"Aktiv – Profil „{active_profile}“; {label} bereit.",
                            )
                    except Exception as exc:
                        LOGGER.exception(
                            "Failed to create virtual controller (mode=%s)", output_mode
                        )
                        if active:
                            active.close()
                        active = None
                        virtual_pad = None
                        target_name = (
                            "PS4" if output_mode == "virtual_ds4" else "Xbox 360"
                        )
                        self.post(
                            "error",
                            f"Virtueller {target_name}-Controller konnte nicht gestartet werden: {exc}",
                        )

                if active and virtual_pad:
                    macro_was_active = macro_player.active
                    macro_target = macro_player.update(now)
                    if macro_was_active and not macro_player.active:
                        LOGGER.info("Macro playback completed")
                        self.post("macro", {"state": "completed"})
                    pygame.event.pump()
                    if not active.attached():
                        if macro_player.active:
                            self.post("macro", {"state": "cancelled"})
                        macro_player.cancel()
                        LOGGER.error("Selected controller disconnected")
                        virtual_pad.reset()
                        virtual_pad.update()
                        active.close()
                        active = None
                        virtual_pad = None
                        self.post(
                            "status",
                            "Controller getrennt. Verbinden und erneut starten.",
                        )
                        last_scan = time.monotonic()
                        continue

                    analog = active.read_analog()
                    raw_lx, raw_ly = analog["lx"], analog["ly"]
                    raw_rx, raw_ry = analog["rx"], analog["ry"]
                    raw_lt, raw_rt = analog["lt"], analog["rt"]

                    if calibration_until and now < calibration_until:
                        if math.hypot(raw_lx, raw_ly) < 0.25:
                            calibration_samples["left"].append((raw_lx, raw_ly))
                        if math.hypot(raw_rx, raw_ry) < 0.25:
                            calibration_samples["right"].append((raw_rx, raw_ry))
                    elif calibration_until:
                        for stick_name, samples in calibration_samples.items():
                            if len(samples) < 10:
                                LOGGER.warning(
                                    "Not enough stationary samples to calibrate %s stick",
                                    stick_name,
                                )
                                continue
                            center = (
                                statistics.median(sample[0] for sample in samples),
                                statistics.median(sample[1] for sample in samples),
                            )
                            spread = max(
                                math.hypot(sample[0] - center[0], sample[1] - center[1])
                                for sample in samples
                            )
                            if math.hypot(*center) <= 0.18 and spread <= 0.10:
                                stick_centers[stick_name] = center
                                LOGGER.info(
                                    "Calibrated %s stick center=(%.4f, %.4f) from %d samples",
                                    stick_name,
                                    center[0],
                                    center[1],
                                    len(samples),
                                )
                            else:
                                LOGGER.warning(
                                    "Rejected %s stick calibration center=(%.4f, %.4f), spread=%.4f",
                                    stick_name,
                                    center[0],
                                    center[1],
                                    spread,
                                )
                        calibration_until = 0.0
                        self.post("status", "Driftkalibrierung abgeschlossen.")

                    source_buttons = button_controls(active.device)
                    pressed_sources = active.read_buttons()
                    publish_button_states(pressed_sources)
                    if button_capture["reader"] is None and button_capture["deadline"]:
                        scan_button_capture(pressed_sources)
                    destination_pressed = {
                        target_id: False
                        for target_id in TARGET_BUTTONS
                        if target_id != "unmapped"
                    }
                    pressed_names = []
                    macros_by_trigger = {
                        macro["trigger"]: macro
                        for macro in macro_definitions
                        if macro["enabled"]
                    }
                    source_names = {
                        source_id: source_name
                        for source_id, source_name, _ in source_buttons
                    }
                    monitored_ids = list(source_names)
                    for source_id in (*button_mapping, *macros_by_trigger):
                        if (
                            source_id not in source_names
                            and source_id not in monitored_ids
                        ):
                            monitored_ids.append(source_id)
                    for source_id in monitored_ids:
                        source_name = source_names.get(
                            source_id, control_label(source_id)
                        )
                        is_pressed = pressed_sources.get(source_id, False)
                        target_id = button_mapping.get(source_id, "unmapped")
                        target_name = target_label(target_id, output_mode)
                        macro = macros_by_trigger.get(source_id)
                        if is_pressed and (
                            target_id != "unmapped" or macro is not None
                        ):
                            pressed_names.append(f"{source_name} → {target_name}")
                        prior = self.last_buttons.get(source_id, False)
                        if is_pressed != prior:
                            LOGGER.info(
                                "Button %s: %s -> %s",
                                "down" if is_pressed else "up",
                                source_name,
                                target_name,
                            )
                            self.last_buttons[source_id] = is_pressed
                        if macro is not None and is_pressed and not prior:
                            if (
                                macro_player.active
                                and macro_player.trigger == source_id
                            ):
                                macro_player.cancel()
                                macro_target = None
                                LOGGER.info(
                                    "Macro cancelled by pressing its trigger again: %s",
                                    source_id,
                                )
                                self.post("macro", {"state": "cancelled"})
                            else:
                                if macro_player.active:
                                    LOGGER.info(
                                        "Replacing active macro after new trigger %s",
                                        source_id,
                                    )
                                    macro_player.cancel()
                                macro_player.start(macro, time.monotonic())
                                macro_target = macro_player.update(time.monotonic())
                                LOGGER.info(
                                    "Started macro trigger=%s steps=%d",
                                    source_id,
                                    len(macro["steps"]),
                                )
                                self.post(
                                    "macro",
                                    {
                                        "state": "started",
                                        "trigger": source_id,
                                        "steps": len(macro["steps"]),
                                        "loop": macro["loop"],
                                    },
                                )
                        # A configured macro replaces its trigger button's normal
                        # mapping, preventing both the macro and the original tap.
                        if target_id != "unmapped" and not (
                            macro is not None and is_pressed
                        ):
                            destination_pressed[target_id] |= is_pressed

                    if macro_target is not None:
                        destination_pressed[macro_target] = True

                    # Aggregate source buttons before updating the virtual pad so
                    # multiple DS4 buttons can share a target without releasing it early.
                    for target_id, target_button in output_button_targets.items():
                        if destination_pressed[target_id]:
                            virtual_pad.press_button(target_button)
                        else:
                            virtual_pad.release_button(target_button)
                    if output_mode == "virtual_ds4":
                        ps_button = vg.DS4_SPECIAL_BUTTONS.DS4_SPECIAL_BUTTON_PS
                        if destination_pressed["guide"]:
                            virtual_pad.press_special_button(ps_button)
                        else:
                            virtual_pad.release_special_button(ps_button)
                        touchpad_button = (
                            vg.DS4_SPECIAL_BUTTONS.DS4_SPECIAL_BUTTON_TOUCHPAD
                        )
                        if destination_pressed["touchpad"]:
                            virtual_pad.press_special_button(touchpad_button)
                        else:
                            virtual_pad.release_special_button(touchpad_button)
                        dpad_x = int(destination_pressed["dpad_right"]) - int(
                            destination_pressed["dpad_left"]
                        )
                        dpad_y = int(destination_pressed["dpad_up"]) - int(
                            destination_pressed["dpad_down"]
                        )
                        direction = getattr(
                            vg.DS4_DPAD_DIRECTIONS, DS4_DPAD_ENUMS[(dpad_x, dpad_y)]
                        )
                        virtual_pad.directional_pad(direction=direction)

                    current_buttons = tuple(pressed_names)
                    if current_buttons != self.last_published_buttons:
                        visible = ", ".join(pressed_names) if pressed_names else "keine"
                        self.post("input", f"Erkannte Taste(n): {visible}")
                        self.last_published_buttons = current_buttons

                    # SDL stick axes are -1..1 with positive Y downward; XInput's Y is upward.
                    if profile_settings["drift_compensation"]:
                        raw_lx -= stick_centers["left"][0]
                        raw_ly -= stick_centers["left"][1]
                        raw_rx -= stick_centers["right"][0]
                        raw_ry -= stick_centers["right"][1]
                    if profile_settings["invert_left_y"]:
                        raw_ly = -raw_ly
                    if profile_settings["invert_right_y"]:
                        raw_ry = -raw_ry
                    lx, ly = apply_radial_deadzone(
                        raw_lx, raw_ly, profile_settings["left_deadzone"]
                    )
                    rx, ry = apply_radial_deadzone(
                        raw_rx, raw_ry, profile_settings["right_deadzone"]
                    )
                    sensitivity = profile_settings["sensitivity"]
                    lx, ly = (
                        max(-1.0, min(1.0, lx * sensitivity)),
                        max(-1.0, min(1.0, ly * sensitivity)),
                    )
                    rx, ry = (
                        max(-1.0, min(1.0, rx * sensitivity)),
                        max(-1.0, min(1.0, ry * sensitivity)),
                    )
                    lt = apply_trigger_deadzone(
                        raw_lt, profile_settings["trigger_deadzone"]
                    )
                    rt = apply_trigger_deadzone(
                        raw_rt, profile_settings["trigger_deadzone"]
                    )

                    telemetry_time = time.monotonic()
                    if telemetry_time - last_telemetry >= 0.1:
                        self.post(
                            "analog",
                            {
                                "lx": raw_lx,
                                "ly": raw_ly,
                                "rx": raw_rx,
                                "ry": raw_ry,
                                "lt": raw_lt,
                                "rt": raw_rt,
                            },
                        )
                        last_telemetry = telemetry_time
                    if (
                        telemetry_time - last_analog_log >= 2.0
                        and max(
                            abs(raw_lx),
                            abs(raw_ly),
                            abs(raw_rx),
                            abs(raw_ry),
                            raw_lt,
                            raw_rt,
                        )
                        >= 0.08
                    ):
                        LOGGER.info(
                            "Analog input L=(%.2f, %.2f) R=(%.2f, %.2f) LT=%.2f RT=%.2f",
                            raw_lx,
                            raw_ly,
                            raw_rx,
                            raw_ry,
                            raw_lt,
                            raw_rt,
                        )
                        last_analog_log = telemetry_time

                    # vgamepad's virtual DS4 Y axes follow ViGEm's DS4 convention,
                    # opposite to XInput; convert the app's positive-up values.
                    output_ly, output_ry = (
                        (ly, ry) if output_mode == "xbox" else (-ly, -ry)
                    )
                    virtual_pad.left_joystick_float(
                        x_value_float=lx, y_value_float=output_ly
                    )
                    virtual_pad.right_joystick_float(
                        x_value_float=rx, y_value_float=output_ry
                    )
                    virtual_pad.left_trigger_float(value_float=lt)
                    virtual_pad.right_trigger_float(value_float=rt)
                    virtual_pad.update()

                if button_capture["reader"] is not None:
                    try:
                        pygame.event.pump()
                        if not button_capture["reader"].attached():
                            finish_button_capture(
                                "error",
                                detail="The selected controller was disconnected.",
                            )
                        else:
                            scan_button_capture(button_capture["reader"].read_buttons())
                    except Exception as exc:
                        LOGGER.exception("Controller button detection failed")
                        finish_button_capture("error", detail=str(exc))
                if button_monitor["reader"] is not None:
                    try:
                        pygame.event.pump()
                        if not button_monitor["reader"].attached():
                            close_button_monitor()
                        else:
                            publish_button_states(
                                button_monitor["reader"].read_buttons()
                            )
                    except Exception:
                        LOGGER.exception("Live controller monitoring failed")
                        close_button_monitor()
        except Exception as exc:
            LOGGER.exception("Bridge worker failed")
            self.post("error", f"Bridge-Fehler: {exc}")
        finally:
            if button_capture["deadline"]:
                finish_button_capture("cancelled")
            close_button_monitor()
            if active:
                try:
                    virtual_pad.reset()
                    virtual_pad.update()
                    active.close()
                except Exception:
                    LOGGER.debug(
                        "Could not release active controller during bridge shutdown",
                        exc_info=True,
                    )
            controller_api.quit()
            pygame.quit()
            LOGGER.info("SDL shutdown complete")
