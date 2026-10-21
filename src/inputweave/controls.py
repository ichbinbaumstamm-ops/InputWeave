"""Controller control names, profile defaults and input processing helpers."""

from __future__ import annotations

import math
import re

from .localization import target_label as localized_target_label

# SDL's standardized gamepad layout uses physical button positions. It maps
# Xbox A/B/X/Y, PlayStation Cross/Circle/Square/Triangle, and Nintendo B/A/Y/X
# to the same south/east/west/north identifiers.
STANDARD_BUTTONS = (
    ("south", "▼  A / ✕ / B", "CONTROLLER_BUTTON_A"),
    ("east", "▶  B / ○ / A", "CONTROLLER_BUTTON_B"),
    ("west", "◀  X / □ / Y", "CONTROLLER_BUTTON_X"),
    ("north", "▲  Y / △ / X", "CONTROLLER_BUTTON_Y"),
    ("back", "View / Share / −", "CONTROLLER_BUTTON_BACK"),
    ("guide", "Guide / PS / Home", "CONTROLLER_BUTTON_GUIDE"),
    ("start", "Start / Options / +", "CONTROLLER_BUTTON_START"),
    ("left_stick", "L3", "CONTROLLER_BUTTON_LEFTSTICK"),
    ("right_stick", "R3", "CONTROLLER_BUTTON_RIGHTSTICK"),
    ("left_shoulder", "L1 / LB", "CONTROLLER_BUTTON_LEFTSHOULDER"),
    ("right_shoulder", "R1 / RB", "CONTROLLER_BUTTON_RIGHTSHOULDER"),
    ("dpad_up", "D-Pad ↑", "CONTROLLER_BUTTON_DPAD_UP"),
    ("dpad_down", "D-Pad ↓", "CONTROLLER_BUTTON_DPAD_DOWN"),
    ("dpad_left", "D-Pad ←", "CONTROLLER_BUTTON_DPAD_LEFT"),
    ("dpad_right", "D-Pad →", "CONTROLLER_BUTTON_DPAD_RIGHT"),
    ("misc_1", "Extra button 1", 15),
    ("paddle_1", "Rear paddle 1", 16),
    ("paddle_2", "Rear paddle 2", 17),
    ("paddle_3", "Rear paddle 3", 18),
    ("paddle_4", "Rear paddle 4", 19),
    ("touchpad", "Touchpad · click", 20),
)

TARGET_BUTTONS = {
    "unmapped": ("Unbelegt", None),
    "a": ("Xbox A", "XUSB_GAMEPAD_A"),
    "b": ("Xbox B", "XUSB_GAMEPAD_B"),
    "x": ("Xbox X", "XUSB_GAMEPAD_X"),
    "y": ("Xbox Y", "XUSB_GAMEPAD_Y"),
    "back": ("Xbox View / Back", "XUSB_GAMEPAD_BACK"),
    "start": ("Xbox Menu / Start", "XUSB_GAMEPAD_START"),
    "lb": ("Xbox LB", "XUSB_GAMEPAD_LEFT_SHOULDER"),
    "rb": ("Xbox RB", "XUSB_GAMEPAD_RIGHT_SHOULDER"),
    "left_thumb": ("Xbox L3", "XUSB_GAMEPAD_LEFT_THUMB"),
    "right_thumb": ("Xbox R3", "XUSB_GAMEPAD_RIGHT_THUMB"),
    "dpad_up": ("Xbox D-Pad hoch", "XUSB_GAMEPAD_DPAD_UP"),
    "dpad_down": ("Xbox D-Pad runter", "XUSB_GAMEPAD_DPAD_DOWN"),
    "dpad_left": ("Xbox D-Pad links", "XUSB_GAMEPAD_DPAD_LEFT"),
    "dpad_right": ("Xbox D-Pad rechts", "XUSB_GAMEPAD_DPAD_RIGHT"),
    "guide": ("Xbox Guide", "XUSB_GAMEPAD_GUIDE"),
    # ViGEm exposes the DS4 touchpad click as a special DS4 report bit, not
    # as a regular DS4 button or an Xbox 360 control.
    "touchpad": ("PS Touchpad · Klick", None),
}

DEFAULT_BUTTON_MAPPING = {
    "south": "a",
    "east": "b",
    "west": "x",
    "north": "y",
    "back": "back",
    "start": "start",
    "guide": "guide",
    "left_shoulder": "lb",
    "right_shoulder": "rb",
    "left_stick": "left_thumb",
    "right_stick": "right_thumb",
    "dpad_up": "dpad_up",
    "dpad_down": "dpad_down",
    "dpad_left": "dpad_left",
    "dpad_right": "dpad_right",
    "misc_1": "unmapped",
    "paddle_1": "unmapped",
    "paddle_2": "unmapped",
    "paddle_3": "unmapped",
    "paddle_4": "unmapped",
    "touchpad": "unmapped",
}

DEFAULT_PROFILE_SETTINGS = {
    "output_mode": "xbox",
    "drift_compensation": True,
    "left_deadzone": 0.10,
    "right_deadzone": 0.10,
    "trigger_deadzone": 0.02,
    "sensitivity": 1.0,
    "polling_rate": 125,
    "invert_left_y": False,
    "invert_right_y": False,
}

OUTPUT_MODES = {
    "direct": "Direkt · unverändert",
    "virtual_ds4": "Virtueller PlayStation-Controller",
    "xbox": "Virtueller Xbox-360-Controller",
}

DS4_TARGET_LABELS = {
    "unmapped": "Unbelegt",
    "a": "PS Kreuz (✕)",
    "b": "PS Kreis (○)",
    "x": "PS Quadrat (□)",
    "y": "PS Dreieck (△)",
    "back": "PS Share",
    "start": "PS Options",
    "lb": "PS L1",
    "rb": "PS R1",
    "left_thumb": "PS L3",
    "right_thumb": "PS R3",
    "dpad_up": "PS D-Pad hoch",
    "dpad_down": "PS D-Pad runter",
    "dpad_left": "PS D-Pad links",
    "dpad_right": "PS D-Pad rechts",
    "guide": "PS-Taste",
    "touchpad": "PS Touchpad · Klick",
}

DS4_BUTTON_TARGET_ENUMS = {
    "a": "DS4_BUTTON_CROSS",
    "b": "DS4_BUTTON_CIRCLE",
    "x": "DS4_BUTTON_SQUARE",
    "y": "DS4_BUTTON_TRIANGLE",
    "back": "DS4_BUTTON_SHARE",
    "start": "DS4_BUTTON_OPTIONS",
    "lb": "DS4_BUTTON_SHOULDER_LEFT",
    "rb": "DS4_BUTTON_SHOULDER_RIGHT",
    "left_thumb": "DS4_BUTTON_THUMB_LEFT",
    "right_thumb": "DS4_BUTTON_THUMB_RIGHT",
}

DS4_DPAD_ENUMS = {
    (0, 0): "DS4_BUTTON_DPAD_NONE",
    (0, 1): "DS4_BUTTON_DPAD_NORTH",
    (-1, 1): "DS4_BUTTON_DPAD_NORTHWEST",
    (1, 1): "DS4_BUTTON_DPAD_NORTHEAST",
    (1, 0): "DS4_BUTTON_DPAD_EAST",
    (1, -1): "DS4_BUTTON_DPAD_SOUTHEAST",
    (0, -1): "DS4_BUTTON_DPAD_SOUTH",
    (-1, -1): "DS4_BUTTON_DPAD_SOUTHWEST",
    (-1, 0): "DS4_BUTTON_DPAD_WEST",
}

LEGACY_SOURCE_IDS = {
    "cross": "south",
    "circle": "east",
    "square": "west",
    "triangle": "north",
    "share": "back",
    "options": "start",
    "ps": "guide",
    "l1": "left_shoulder",
    "r1": "right_shoulder",
    "l3": "left_stick",
    "r3": "right_stick",
}


def default_output_mode(profile_name: str | None) -> str:
    if profile_name and profile_name.strip().casefold() == "rocket league":
        return "virtual_ds4"
    return "xbox"


def target_label(target_id: str, output_mode: str, language: str = "de") -> str:
    """Localized target label; the PS face-button glyphs are retained in every language."""
    return localized_target_label(target_id, output_mode, language)


def available_target_ids(output_mode: str) -> tuple[str, ...]:
    """Return targets that the selected virtual controller can actually emit."""
    return tuple(
        target_id
        for target_id in TARGET_BUTTONS
        if target_id != "touchpad" or output_mode == "virtual_ds4"
    )


def default_raw_mapping(button_count: int, hat_count: int) -> dict[str, str]:
    """Provide a common HID/DirectInput starting layout, editable per device."""
    targets = (
        "a",
        "b",
        "x",
        "y",
        "lb",
        "rb",
        "back",
        "start",
        "left_thumb",
        "right_thumb",
        "guide",
    )
    mapping = {
        f"button_{index}": target for index, target in enumerate(targets[:button_count])
    }
    if hat_count:
        mapping.update(
            {
                "hat_0_up": "dpad_up",
                "hat_0_down": "dpad_down",
                "hat_0_left": "dpad_left",
                "hat_0_right": "dpad_right",
            }
        )
    return mapping


def normalized_mapping(value, defaults: dict[str, str] | None = None) -> dict[str, str]:
    mapping = (DEFAULT_BUTTON_MAPPING if defaults is None else defaults).copy()
    if not isinstance(value, dict):
        return mapping
    for source_id, target_id in value.items():
        if (
            not isinstance(source_id, str)
            or not isinstance(target_id, str)
            or target_id not in TARGET_BUTTONS
        ):
            continue
        if source_id in mapping or re.fullmatch(
            r"button_\d{1,3}|hat_\d{1,2}_(up|down|left|right)", source_id
        ):
            mapping[source_id] = target_id
        elif source_id in LEGACY_SOURCE_IDS:
            mapping[LEGACY_SOURCE_IDS[source_id]] = target_id
    return mapping


def normalized_settings(value, profile_name: str | None = None) -> dict:
    settings = DEFAULT_PROFILE_SETTINGS.copy()
    settings["output_mode"] = default_output_mode(profile_name)
    if not isinstance(value, dict):
        return settings
    output_mode = value.get("output_mode")
    if isinstance(output_mode, str) and output_mode in OUTPUT_MODES:
        settings["output_mode"] = output_mode
    for key in ("drift_compensation", "invert_left_y", "invert_right_y"):
        if isinstance(value.get(key), bool):
            settings[key] = value[key]
    for key, low, high in (
        ("left_deadzone", 0.0, 0.40),
        ("right_deadzone", 0.0, 0.40),
        ("trigger_deadzone", 0.0, 0.40),
        ("sensitivity", 0.50, 2.00),
    ):
        try:
            settings[key] = min(high, max(low, float(value.get(key, settings[key]))))
        except (TypeError, ValueError):
            pass
    try:
        rate = int(value.get("polling_rate", settings["polling_rate"]))
        settings["polling_rate"] = min(
            (60, 125, 250), key=lambda allowed: abs(allowed - rate)
        )
    except (TypeError, ValueError):
        pass
    return settings


def apply_radial_deadzone(x: float, y: float, deadzone: float) -> tuple[float, float]:
    magnitude = math.hypot(x, y)
    if magnitude <= deadzone or magnitude == 0:
        return 0.0, 0.0
    adjusted = min(1.0, (magnitude - deadzone) / max(1e-6, 1.0 - deadzone))
    scale = adjusted / magnitude
    return x * scale, y * scale


def apply_trigger_deadzone(value: float, deadzone: float) -> float:
    value = min(1.0, max(0.0, value))
    if value <= deadzone:
        return 0.0
    return min(1.0, (value - deadzone) / max(1e-6, 1.0 - deadzone))
