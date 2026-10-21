"""Validation and playback for profile-local controller macros."""

from __future__ import annotations

import re

from .controls import STANDARD_BUTTONS, TARGET_BUTTONS

MAX_MACROS = 32
MAX_MACRO_STEPS = 16
MIN_PRESS_MS = 30
MAX_PRESS_MS = 1000
MAX_GAP_MS = 1000
_TIME_EPSILON = 1e-9

_STANDARD_INPUTS = {source_id for source_id, _label, _button in STANDARD_BUTTONS}
_RAW_INPUT_PATTERN = re.compile(r"button_\d{1,3}|hat_\d{1,2}_(up|down|left|right)")
_OUTPUT_TARGETS = set(TARGET_BUTTONS) - {"unmapped"}


def normalized_macros(value) -> list[dict]:
    """Return safe macro definitions while ignoring malformed profile data."""
    if not isinstance(value, list):
        return []

    macros = []
    seen_triggers = set()
    for raw_macro in value[:MAX_MACROS]:
        if not isinstance(raw_macro, dict):
            continue
        trigger = raw_macro.get("trigger")
        if (
            not isinstance(trigger, str)
            or not (
                trigger in _STANDARD_INPUTS or _RAW_INPUT_PATTERN.fullmatch(trigger)
            )
            or trigger in seen_triggers
        ):
            continue

        raw_steps = raw_macro.get("steps")
        if not isinstance(raw_steps, list):
            continue
        steps = []
        for raw_step in raw_steps[:MAX_MACRO_STEPS]:
            if not isinstance(raw_step, dict):
                continue
            target = raw_step.get("target")
            if not isinstance(target, str) or target not in _OUTPUT_TARGETS:
                continue
            try:
                press_ms = int(raw_step.get("press_ms", 100))
                gap_ms = int(raw_step.get("gap_ms", 50))
            except (TypeError, ValueError, OverflowError):
                continue
            steps.append(
                {
                    "target": target,
                    "press_ms": min(MAX_PRESS_MS, max(MIN_PRESS_MS, press_ms)),
                    "gap_ms": min(MAX_GAP_MS, max(0, gap_ms)),
                }
            )
        if not steps:
            continue
        enabled = raw_macro.get("enabled", True)
        loop = raw_macro.get("loop", False)
        macros.append(
            {
                "trigger": trigger,
                "steps": steps,
                "enabled": enabled if isinstance(enabled, bool) else True,
                "loop": loop if isinstance(loop, bool) else False,
            }
        )
        seen_triggers.add(trigger)
    return macros


class MacroPlayer:
    """Runs one button sequence at a time using monotonic deadlines."""

    def __init__(self):
        self.macro: dict | None = None
        self.step_index = 0
        self.phase = "idle"
        self.deadline = 0.0

    @property
    def active(self) -> bool:
        return self.macro is not None

    @property
    def trigger(self) -> str | None:
        return self.macro["trigger"] if self.macro is not None else None

    def start(self, macro: dict, now: float) -> None:
        self.cancel()
        self.macro = macro
        self.step_index = 0
        self.phase = "press"
        self.deadline = now + macro["steps"][0]["press_ms"] / 1000.0

    def cancel(self) -> None:
        self.macro = None
        self.step_index = 0
        self.phase = "idle"
        self.deadline = 0.0

    def update(self, now: float) -> str | None:
        """Advance elapsed phases and return the target that should be held now."""
        while self.macro is not None and now + _TIME_EPSILON >= self.deadline:
            step = self.macro["steps"][self.step_index]
            if self.phase == "press":
                self.phase = "gap"
                # Even a configured zero gap needs one released poll frame so
                # repeated presses of the same virtual button remain distinct.
                gap_ms = max(16, step["gap_ms"])
                self.deadline += gap_ms / 1000.0
                break
            else:
                self.step_index += 1
                if self.step_index >= len(self.macro["steps"]):
                    if self.macro["loop"]:
                        self.step_index = 0
                    else:
                        self.cancel()
                        break
                self.phase = "press"
                self.deadline += (
                    self.macro["steps"][self.step_index]["press_ms"] / 1000.0
                )
        if self.macro is not None and self.phase == "press":
            return self.macro["steps"][self.step_index]["target"]
        return None
