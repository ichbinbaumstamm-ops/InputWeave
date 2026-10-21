import json
import logging
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from inputweave.controls import DEFAULT_BUTTON_MAPPING
from inputweave.profile_store import (
    backup_path,
    default_profile_store,
    load_profile_store,
    migrate_profile_store,
    updated_mapping_profile,
    write_profile_store,
)

LOGGER = logging.getLogger("inputweave.profile_store.tests")
LOGGER.addHandler(logging.NullHandler())
LOGGER.propagate = False


def rocket_league_store() -> dict:
    store = default_profile_store()
    profile = store["profiles"].pop("Forza Horizon 6")
    profile["settings"]["output_mode"] = "virtual_ds4"
    store["profiles"]["Rocket League"] = profile
    store["active_profile"] = "Rocket League"
    return store


class ProfileStoreTests(unittest.TestCase):
    def test_touchpad_mapping_survives_save_and_reload(self):
        with TemporaryDirectory() as temporary_directory:
            profile_file = Path(temporary_directory) / "profiles.json"
            store = rocket_league_store()
            store["profiles"]["Rocket League"]["buttons"]["touchpad"] = "touchpad"

            write_profile_store(profile_file, store)
            loaded = load_profile_store(profile_file, LOGGER)

            self.assertEqual(
                loaded["profiles"]["Rocket League"]["buttons"]["touchpad"], "touchpad"
            )
            repaired = json.loads(profile_file.read_text(encoding="utf-8"))
            self.assertEqual(
                repaired["profiles"]["Rocket League"]["buttons"]["touchpad"], "touchpad"
            )
            self.assertEqual(
                loaded["profiles"]["Rocket League"]["settings"]["output_mode"],
                "virtual_ds4",
            )

    def test_mapping_update_keeps_the_last_confirmed_profile_unchanged(self):
        original = rocket_league_store()["profiles"]["Rocket League"]
        requested_mapping = dict(original["buttons"])
        requested_mapping["touchpad"] = "touchpad"

        updated = updated_mapping_profile(
            original, requested_mapping, DEFAULT_BUTTON_MAPPING
        )

        self.assertEqual(original["buttons"]["touchpad"], "unmapped")
        self.assertEqual(updated["buttons"]["touchpad"], "touchpad")

    def test_failed_write_leaves_the_existing_profile_file_unchanged(self):
        with TemporaryDirectory() as temporary_directory:
            profile_file = Path(temporary_directory) / "profiles.json"
            original = rocket_league_store()
            profile_file.write_text(json.dumps(original), encoding="utf-8")
            updated = rocket_league_store()
            updated["profiles"]["Rocket League"]["buttons"]["touchpad"] = "touchpad"

            with (
                patch(
                    "inputweave.profile_store.os.replace",
                    side_effect=OSError("disk unavailable"),
                ),
                self.assertRaises(OSError),
            ):
                write_profile_store(profile_file, updated)

            self.assertEqual(
                json.loads(profile_file.read_text(encoding="utf-8")), original
            )

    def test_last_known_good_backup_recovers_a_corrupt_primary_file(self):
        with TemporaryDirectory() as temporary_directory:
            profile_file = Path(temporary_directory) / "profiles.json"
            backup_store = rocket_league_store()
            backup_store["profiles"]["Rocket League"]["buttons"]["touchpad"] = (
                "touchpad"
            )
            backup_path(profile_file).write_text(
                json.dumps(backup_store), encoding="utf-8"
            )
            profile_file.write_text("{not valid json", encoding="utf-8")

            loaded = load_profile_store(profile_file, LOGGER)

            self.assertEqual(
                loaded["profiles"]["Rocket League"]["buttons"]["touchpad"], "touchpad"
            )

    def test_legacy_store_is_migrated_once_without_overwriting_newer_data(self):
        with TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            legacy_file = directory / "ForzaPad" / "profiles.json"
            profile_file = directory / "InputWeave" / "profiles.json"
            legacy_file.parent.mkdir()
            legacy_store = rocket_league_store()
            legacy_store["profiles"]["Rocket League"]["buttons"]["touchpad"] = (
                "touchpad"
            )
            legacy_file.write_text(json.dumps(legacy_store), encoding="utf-8")

            self.assertTrue(migrate_profile_store(profile_file, legacy_file, LOGGER))
            self.assertEqual(
                load_profile_store(profile_file, LOGGER)["profiles"]["Rocket League"][
                    "buttons"
                ]["touchpad"],
                "touchpad",
            )

            newer_store = rocket_league_store()
            newer_store["profiles"]["Rocket League"]["buttons"]["touchpad"] = "guide"
            write_profile_store(profile_file, newer_store)

            self.assertFalse(migrate_profile_store(profile_file, legacy_file, LOGGER))
            self.assertEqual(
                load_profile_store(profile_file, LOGGER)["profiles"]["Rocket League"][
                    "buttons"
                ]["touchpad"],
                "guide",
            )


if __name__ == "__main__":
    unittest.main()
