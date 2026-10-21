import unittest

from inputweave.controls import (
    DEFAULT_BUTTON_MAPPING,
    apply_radial_deadzone,
    apply_trigger_deadzone,
    default_output_mode,
    normalized_mapping,
    normalized_settings,
    target_label,
)


class ControlTests(unittest.TestCase):
    def test_standard_face_buttons_keep_physical_position(self):
        self.assertEqual(DEFAULT_BUTTON_MAPPING["south"], "a")
        self.assertEqual(DEFAULT_BUTTON_MAPPING["east"], "b")
        self.assertEqual(DEFAULT_BUTTON_MAPPING["west"], "x")
        self.assertEqual(DEFAULT_BUTTON_MAPPING["north"], "y")

    def test_rocket_league_uses_playstation_output_by_default(self):
        self.assertEqual(default_output_mode("Rocket League"), "virtual_ds4")
        self.assertEqual(default_output_mode("Forza Horizon 6"), "xbox")

    def test_mapping_discards_invalid_targets_and_keeps_defaults(self):
        mapping = normalized_mapping({"south": "b", "east": "not-a-button"})
        self.assertEqual(mapping["south"], "b")
        self.assertEqual(mapping["east"], DEFAULT_BUTTON_MAPPING["east"])

    def test_profile_settings_are_clamped_and_validated(self):
        settings = normalized_settings(
            {
                "left_deadzone": -5,
                "sensitivity": 9,
                "polling_rate": 200,
                "drift_compensation": False,
                "output_mode": "unknown",
            }
        )
        self.assertEqual(settings["left_deadzone"], 0.0)
        self.assertEqual(settings["sensitivity"], 2.0)
        self.assertEqual(settings["polling_rate"], 250)
        self.assertFalse(settings["drift_compensation"])
        self.assertEqual(settings["output_mode"], "xbox")

    def test_deadzone_helpers_keep_values_in_range(self):
        self.assertEqual(apply_radial_deadzone(0.05, 0.05, 0.1), (0.0, 0.0))
        x, y = apply_radial_deadzone(0.0, 1.0, 0.1)
        self.assertAlmostEqual(x, 0.0)
        self.assertAlmostEqual(y, 1.0)
        self.assertEqual(apply_trigger_deadzone(-1.0, 0.1), 0.0)
        self.assertEqual(apply_trigger_deadzone(1.0, 0.1), 1.0)

    def test_xbox_face_button_targets_show_playstation_glyphs(self):
        expected = {"a": "✕", "b": "○", "x": "□", "y": "△"}
        for target, glyph in expected.items():
            with self.subTest(target=target):
                self.assertIn(glyph, target_label(target, "xbox", "en"))
                self.assertIn(glyph, target_label(target, "xbox", "ar"))


if __name__ == "__main__":
    unittest.main()
