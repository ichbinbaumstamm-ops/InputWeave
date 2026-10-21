"""SDL gamepad discovery and generic joystick input access."""

from __future__ import annotations

import logging
import statistics
import time
from dataclasses import asdict, dataclass

from .controls import STANDARD_BUTTONS, default_raw_mapping
from .localization import control_label, target_label, translate

LOGGER = logging.getLogger("InputWeave")


@dataclass(frozen=True)
class InputDevice:
    index: int
    name: str
    family: str
    kind: str
    guid: str
    axes: int
    buttons: int
    hats: int

    @property
    def mapping_key(self) -> str:
        return (
            self.guid if self.kind == "raw" and self.guid else f"{self.family}:standard"
        )

    @property
    def display_name(self) -> str:
        support = "SDL standard" if self.kind == "standard" else "generisch · Rohdaten"
        return f"{self.family} · {self.name} · {support}"

    def to_payload(self) -> dict[str, object]:
        return asdict(self) | {
            "mapping_key": self.mapping_key,
            "display_name": self.display_name,
        }


def _controller_family(name: str, is_standard: bool, guid: str = "") -> str:
    normalized = name.casefold()
    if any(
        term in normalized
        for term in (
            "playstation",
            "dualshock",
            "dualsense",
            "sony",
            "ps4",
            "ps5",
        )
    ):
        return "PlayStation"
    if "nintendo" in normalized or "switch" in normalized or "joy-con" in normalized:
        return "Nintendo"
    if "xbox" in normalized or "xinput" in normalized or "xbox 360" in normalized:
        return "Xbox"
    # Bluetooth DualShock devices commonly report only "Wireless Controller".
    # SDL's GUID contains the USB vendor ID in bytes 4..5 on Windows; using it
    # keeps USB and Bluetooth Sony/Microsoft/Nintendo devices in the same family.
    try:
        vendor_id = int.from_bytes(bytes.fromhex(guid[8:12]), "little")
    except (ValueError, TypeError):
        vendor_id = None
    if vendor_id == 0x054C:
        return "PlayStation"
    if vendor_id == 0x045E:
        return "Xbox"
    if vendor_id == 0x057E:
        return "Nintendo"
    return "Standard-Gamepad" if is_standard else "Generischer Controller"


def discover_devices(pygame, controller_api) -> list[InputDevice]:
    """Enumerate every SDL controller and joystick, not just named DS4 devices."""
    devices: list[InputDevice] = []
    for index in range(pygame.joystick.get_count()):
        joystick = None
        try:
            is_standard = bool(controller_api.is_controller(index))
            joystick = pygame.joystick.Joystick(index)
            name = joystick.get_name() or f"Controller {index + 1}"
            guid = joystick.get_guid() or ""
            device = InputDevice(
                index=index,
                name=name,
                family=_controller_family(name, is_standard, guid),
                kind="standard" if is_standard else "raw",
                guid=guid,
                axes=joystick.get_numaxes(),
                buttons=joystick.get_numbuttons(),
                hats=joystick.get_numhats(),
            )
            devices.append(device)
        except Exception:
            # A device can disappear while SDL is enumerating it.
            LOGGER.debug(
                "Controller disappeared while SDL was enumerating it", exc_info=True
            )
            continue
        finally:
            if joystick is not None:
                try:
                    joystick.quit()
                except Exception:
                    LOGGER.debug(
                        "Could not close an SDL joystick during discovery",
                        exc_info=True,
                    )
    return devices


def button_controls(
    device: InputDevice, language: str = "de"
) -> list[tuple[str, str, int | None]]:
    """Return stable control IDs, readable labels, and SDL button IDs if mapped."""
    if device.kind == "standard":
        return [
            (
                source_id,
                control_label(source_id, language, device.family),
                pygame_button,
            )
            for source_id, _label, pygame_button in STANDARD_BUTTONS
        ]
    suggested_mapping = default_raw_mapping(device.buttons, device.hats)
    output_mode = "virtual_ds4" if device.family == "PlayStation" else "xbox"
    controls = []
    for index in range(device.buttons):
        source_id = f"button_{index}"
        label = control_label(source_id, language)
        suggested_target = suggested_mapping.get(source_id)
        if suggested_target is not None:
            label = (
                f"{label} · {translate('Vorgeschlagene Belegung', language)}: "
                f"{target_label(suggested_target, output_mode, language)}"
            )
        controls.append((source_id, label, index))
    for hat_index in range(device.hats):
        controls.extend(
            (
                (
                    f"hat_{hat_index}_up",
                    control_label(f"hat_{hat_index}_up", language),
                    None,
                ),
                (
                    f"hat_{hat_index}_down",
                    control_label(f"hat_{hat_index}_down", language),
                    None,
                ),
                (
                    f"hat_{hat_index}_left",
                    control_label(f"hat_{hat_index}_left", language),
                    None,
                ),
                (
                    f"hat_{hat_index}_right",
                    control_label(f"hat_{hat_index}_right", language),
                    None,
                ),
            )
        )
    return controls


def inferred_button_mapping(device: InputDevice) -> dict[str, str]:
    """Return the conventional face/shoulder order for an unmapped raw device."""
    return default_raw_mapping(device.buttons, device.hats)


RAW_AXIS_CONTROLS = (
    ("left_x", "Linker Stick · X", 0),
    ("left_y", "Linker Stick · Y", 1),
    ("right_x", "Rechter Stick · X", 2),
    ("right_y", "Rechter Stick · Y", 3),
    ("left_trigger", "Linker Trigger", 4),
    ("right_trigger", "Rechter Trigger", 5),
)


def inferred_axis_mapping(device: InputDevice) -> dict[str, int | None]:
    return {
        control_id: default_index if default_index < device.axes else None
        for control_id, _label, default_index in RAW_AXIS_CONTROLS
    }


def normalized_axis_mapping(value, device: InputDevice) -> dict[str, int | None]:
    mapping = inferred_axis_mapping(device)
    if not isinstance(value, dict):
        return mapping
    for control_id, _label, _default_index in RAW_AXIS_CONTROLS:
        candidate = value.get(control_id)
        if candidate is None:
            mapping[control_id] = None
        elif (
            isinstance(candidate, int)
            and not isinstance(candidate, bool)
            and 0 <= candidate < device.axes
        ):
            mapping[control_id] = candidate
    return mapping


class ControllerReader:
    """Reads a standardized SDL gamepad or a generic SDL joystick."""

    def __init__(self, pygame, controller_api, device: InputDevice, axis_mapping=None):
        self.pygame = pygame
        self.device = device
        self.controller = None
        self.raw_joystick = None
        if device.kind == "standard":
            self.controller = controller_api.Controller(device.index)
            self.joystick = None
            # SDL's standardized gamepad API omits vendor-specific controls on
            # some mappings (notably the DualShock touchpad click). Keep a
            # joystick handle alongside it so callers can inspect raw buttons.
            self.raw_joystick = pygame.joystick.Joystick(device.index)
        else:
            self.joystick = pygame.joystick.Joystick(device.index)
            self.controller = None
        self.trigger_rest: list[float | None] = [None, None]
        self.trigger_samples: list[list[float]] = [[], []]
        self.axis_mapping = normalized_axis_mapping(axis_mapping, device)
        self._last_attachment_check = 0.0
        self._is_attached = True
        raw_source = self.joystick if self.joystick is not None else self.raw_joystick
        self._instance_id = (
            raw_source.get_instance_id() if raw_source is not None else None
        )

    def set_axis_mapping(self, value) -> None:
        self.axis_mapping = normalized_axis_mapping(value, self.device)
        self.trigger_rest = [None, None]
        self.trigger_samples = [[], []]

    def attached(self) -> bool:
        if self.controller is not None:
            return bool(self.controller.attached())
        if self.joystick is None:
            return False
        now = time.monotonic()
        if now - self._last_attachment_check >= 0.5:
            self._last_attachment_check = now
            try:
                self._is_attached = any(
                    self._probe_instance_id(index) == self._instance_id
                    for index in range(self.pygame.joystick.get_count())
                )
            except Exception:
                LOGGER.debug(
                    "Could not check whether the raw SDL joystick is still attached",
                    exc_info=True,
                )
                self._is_attached = False
        return self._is_attached

    def _probe_instance_id(self, index: int) -> int | None:
        probe = self.pygame.joystick.Joystick(index)
        try:
            return probe.get_instance_id()
        finally:
            probe.quit()

    def close(self) -> None:
        for source in (self.controller, self.joystick, self.raw_joystick):
            if source is not None:
                source.quit()
        self.controller = None
        self.joystick = None
        self.raw_joystick = None

    def _raw_trigger(self, trigger_index: int) -> float:
        if self.joystick is None:
            return 0.0
        mapped_axis = self.axis_mapping.get(
            "left_trigger" if trigger_index == 0 else "right_trigger"
        )
        if mapped_axis is None or mapped_axis >= self.device.axes:
            return 0.0
        value = float(self.joystick.get_axis(mapped_axis))
        samples = self.trigger_samples[trigger_index]
        if self.trigger_rest[trigger_index] is None:
            if len(samples) < 30:
                samples.append(value)
            if len(samples) >= 30:
                self.trigger_rest[trigger_index] = statistics.median(samples)
        rest = self.trigger_rest[trigger_index]
        if rest is None:
            rest = value
        if rest < -0.55:
            return max(0.0, min(1.0, (value + 1.0) / 2.0))
        if rest > 0.55:
            return max(0.0, min(1.0, (rest - value) / 2.0))
        return max(0.0, min(1.0, value))

    def read_analog(self) -> dict[str, float]:
        if self.controller is not None:
            c = self.pygame
            axis = self.controller.get_axis
            return {
                "lx": axis(c.CONTROLLER_AXIS_LEFTX) / 32768.0,
                "ly": -axis(c.CONTROLLER_AXIS_LEFTY) / 32768.0,
                "rx": axis(c.CONTROLLER_AXIS_RIGHTX) / 32768.0,
                "ry": -axis(c.CONTROLLER_AXIS_RIGHTY) / 32768.0,
                "lt": max(0.0, axis(c.CONTROLLER_AXIS_TRIGGERLEFT) / 32768.0),
                "rt": max(0.0, axis(c.CONTROLLER_AXIS_TRIGGERRIGHT) / 32768.0),
            }

        joystick = self.joystick
        if joystick is None:
            return dict.fromkeys(("lx", "ly", "rx", "ry", "lt", "rt"), 0.0)

        def axis_value(control_id: str) -> float:
            index = self.axis_mapping.get(control_id)
            return (
                float(joystick.get_axis(index))
                if index is not None and index < self.device.axes
                else 0.0
            )

        # Generic HID devices do not carry a standard axis layout. Use the
        # common 0/1 left stick, 2/3 right stick and 4/5 trigger convention.
        return {
            "lx": axis_value("left_x"),
            "ly": -axis_value("left_y"),
            "rx": axis_value("right_x"),
            "ry": -axis_value("right_y"),
            "lt": self._raw_trigger(0),
            "rt": self._raw_trigger(1),
        }

    def read_buttons(self) -> dict[str, bool]:
        if self.controller is not None:
            c = self.pygame
            pressed = {
                source_id: bool(
                    self.controller.get_button(
                        getattr(c, pygame_button)
                        if isinstance(pygame_button, str)
                        else pygame_button
                    )
                )
                for source_id, _label, pygame_button in STANDARD_BUTTONS
            }
            if self.raw_joystick is not None:
                raw_pressed = {
                    f"button_{index}": bool(self.raw_joystick.get_button(index))
                    for index in range(self.device.buttons)
                }
                if self.device.family == "PlayStation":
                    # The standardized Share and Touchpad controls already
                    # consume these raw HID bits below. Do not also expose the
                    # same physical presses as button_4/button_15; doing so
                    # lets a single click emit the same virtual button twice.
                    share_pressed = raw_pressed.pop("button_4", False)
                    touchpad_pressed = raw_pressed.pop("button_15", False)
                else:
                    share_pressed = False
                    touchpad_pressed = False
                pressed.update(raw_pressed)
                if self.device.family == "PlayStation":
                    # SDL's Windows DualShock mapping may omit these logical
                    # controls, so merge the raw HID bits into each one.
                    pressed["back"] = pressed.get("back", False) or share_pressed
                    pressed["touchpad"] = (
                        pressed.get("touchpad", False) or touchpad_pressed
                    )
            return pressed

        joystick = self.joystick
        if joystick is None:
            return {}
        pressed = {
            f"button_{index}": bool(joystick.get_button(index))
            for index in range(self.device.buttons)
        }
        for hat_index in range(self.device.hats):
            hat_x, hat_y = joystick.get_hat(hat_index)
            pressed.update(
                {
                    f"hat_{hat_index}_up": hat_y > 0,
                    f"hat_{hat_index}_down": hat_y < 0,
                    f"hat_{hat_index}_left": hat_x < 0,
                    f"hat_{hat_index}_right": hat_x > 0,
                }
            )
        return pressed
