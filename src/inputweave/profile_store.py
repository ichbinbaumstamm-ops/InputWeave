"""Validated, atomic persistence for InputWeave game profiles.

The UI deliberately delegates file handling here so a controller mapping can be
tested without constructing a Tk window.  Writes always use a unique temporary
file and retain the preceding valid store as a recovery backup.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from .controls import DEFAULT_BUTTON_MAPPING, normalized_mapping, normalized_settings
from .localization import LANGUAGES
from .macros import normalized_macros


def default_profile_store() -> dict[str, Any]:
    """Return a fresh initial profile store."""
    profile_name = "Forza Horizon 6"
    return {
        "language": "de",
        "active_profile": profile_name,
        "profiles": {
            profile_name: {
                "buttons": DEFAULT_BUTTON_MAPPING.copy(),
                "device_mappings": {},
                "device_axis_mappings": {},
                "macros": [],
                "device_macros": {},
                "settings": normalized_settings({}, profile_name),
            }
        },
    }


def normalize_profile_store(value: object) -> dict[str, Any] | None:
    """Validate stored JSON and return the canonical in-memory representation."""
    if not isinstance(value, dict):
        return None

    raw_profiles = value.get("profiles", {})
    profiles: dict[str, dict[str, Any]] = {}
    if isinstance(raw_profiles, dict):
        for name, raw_profile in raw_profiles.items():
            if (
                not isinstance(name, str)
                or not name.strip()
                or not isinstance(raw_profile, dict)
            ):
                continue

            raw_device_mappings = raw_profile.get("device_mappings", {})
            device_mappings: dict[str, dict[str, str]] = {}
            if isinstance(raw_device_mappings, dict):
                for device_key, device_mapping in raw_device_mappings.items():
                    if isinstance(device_key, str) and 0 < len(device_key) <= 128:
                        device_mappings[device_key] = normalized_mapping(
                            device_mapping, {}
                        )

            raw_axis_mappings = raw_profile.get("device_axis_mappings", {})
            device_axis_mappings: dict[str, dict[str, Any]] = {}
            if isinstance(raw_axis_mappings, dict):
                for device_key, axis_mapping in raw_axis_mappings.items():
                    if (
                        isinstance(device_key, str)
                        and 0 < len(device_key) <= 128
                        and isinstance(axis_mapping, dict)
                    ):
                        device_axis_mappings[device_key] = dict(axis_mapping)

            raw_device_macros = raw_profile.get("device_macros", {})
            device_macros: dict[str, list[dict[str, Any]]] = {}
            if isinstance(raw_device_macros, dict):
                for device_key, device_macro_list in raw_device_macros.items():
                    if isinstance(device_key, str) and 0 < len(device_key) <= 128:
                        device_macros[device_key] = normalized_macros(device_macro_list)

            profiles[name] = {
                "buttons": normalized_mapping(raw_profile.get("buttons", {})),
                "device_mappings": device_mappings,
                "device_axis_mappings": device_axis_mappings,
                "macros": normalized_macros(raw_profile.get("macros", [])),
                "device_macros": device_macros,
                "settings": normalized_settings(raw_profile.get("settings", {}), name),
            }

    if not profiles:
        return None

    active_profile = value.get("active_profile")
    if active_profile not in profiles:
        active_profile = next(iter(profiles))
    language = value.get("language", "de")
    if language not in LANGUAGES:
        language = "de"
    return {
        "language": language,
        "active_profile": active_profile,
        "profiles": profiles,
    }


def backup_path(profile_file: Path) -> Path:
    """Return the path holding the last successfully written profile store."""
    return profile_file.with_suffix(profile_file.suffix + ".bak")


def _read_store(profile_file: Path) -> dict[str, Any] | None:
    return normalize_profile_store(json.loads(profile_file.read_text(encoding="utf-8")))


def load_profile_store(profile_file: Path, logger: logging.Logger) -> dict[str, Any]:
    """Load the primary store, falling back to its last known-good backup."""
    if profile_file.exists():
        try:
            store = _read_store(profile_file)
            if store is not None:
                return store
            logger.warning("Profile file %s had no usable profiles", profile_file)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            logger.exception("Could not read profiles from %s", profile_file)

    previous_file = backup_path(profile_file)
    if previous_file.exists():
        try:
            store = _read_store(previous_file)
            if store is not None:
                logger.warning("Recovered profiles from backup %s", previous_file)
                try:
                    recovered_content = json.dumps(
                        store, ensure_ascii=False, indent=2
                    ).encode("utf-8")
                    _write_bytes_atomically(profile_file, recovered_content)
                except (OSError, TypeError, ValueError):
                    logger.exception(
                        "Could not repair profiles from backup %s", previous_file
                    )
                return store
            logger.warning("Profile backup %s had no usable profiles", previous_file)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            logger.exception("Could not read profile backup %s", previous_file)

    return default_profile_store()


def migrate_profile_store(
    profile_file: Path,
    legacy_profile_file: Path,
    logger: logging.Logger,
) -> bool:
    """Copy a valid legacy profile store to InputWeave's current location once.

    Existing InputWeave data, including a recoverable backup, always takes
    precedence.  The legacy source is read through the same validation and
    recovery path as a normal profile store before it is written atomically.
    """
    if profile_file.exists() or backup_path(profile_file).exists():
        return False
    if (
        not legacy_profile_file.exists()
        and not backup_path(legacy_profile_file).exists()
    ):
        return False

    store = load_profile_store(legacy_profile_file, logger)
    write_profile_store(profile_file, store)
    logger.info("Migrated profiles from %s to %s", legacy_profile_file, profile_file)
    return True


def _write_bytes_atomically(destination: Path, content: bytes) -> None:
    """Write content into destination's directory and atomically replace it."""
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary_file = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_file, destination)
    finally:
        try:
            temporary_file.unlink(missing_ok=True)
        except OSError:
            pass


def write_profile_store(
    profile_file: Path,
    profile_store: Mapping[str, Any],
    *,
    keep_backup: bool = True,
) -> None:
    """Atomically write a store and preserve the prior version as ``.bak``.

    The function raises an ``OSError`` or serialization error instead of hiding
    it.  The caller can then leave the existing in-memory profile untouched.
    """
    profile_file.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(profile_store, ensure_ascii=False, indent=2).encode("utf-8")

    if keep_backup and profile_file.exists():
        backup_file = backup_path(profile_file)
        backup_descriptor, backup_name = tempfile.mkstemp(
            prefix=f".{backup_file.name}.",
            suffix=".tmp",
            dir=profile_file.parent,
        )
        temporary_backup = Path(backup_name)
        try:
            with os.fdopen(backup_descriptor, "wb") as backup_handle:
                with profile_file.open("rb") as current_handle:
                    shutil.copyfileobj(current_handle, backup_handle)
                backup_handle.flush()
                os.fsync(backup_handle.fileno())
            os.replace(temporary_backup, backup_file)
        finally:
            try:
                temporary_backup.unlink(missing_ok=True)
            except OSError:
                pass

    _write_bytes_atomically(profile_file, content)


def updated_mapping_profile(
    profile: Mapping[str, Any],
    mapping: Mapping[str, str],
    defaults: Mapping[str, str],
    *,
    device_key: str | None = None,
    axis_mapping: Mapping[str, int | None] | None = None,
) -> dict[str, Any]:
    """Return a new profile with a validated mapping, leaving ``profile`` intact."""
    updated_profile = deepcopy(dict(profile))
    validated_mapping = normalized_mapping(dict(mapping), dict(defaults))
    if device_key is None:
        updated_profile["buttons"] = validated_mapping
        return updated_profile

    device_mappings = dict(updated_profile.get("device_mappings", {}))
    device_mappings[device_key] = validated_mapping
    updated_profile["device_mappings"] = device_mappings
    device_axis_mappings = dict(updated_profile.get("device_axis_mappings", {}))
    device_axis_mappings[device_key] = dict(axis_mapping or {})
    updated_profile["device_axis_mappings"] = device_axis_mappings
    return updated_profile
