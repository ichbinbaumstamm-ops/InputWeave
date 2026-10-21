import unittest

from inputweave.macros import MacroPlayer, normalized_macros


class MacroTests(unittest.TestCase):
    def test_normalization_clamps_durations_and_rejects_invalid_steps(self):
        macros = normalized_macros(
            [
                {
                    "trigger": "south",
                    "enabled": True,
                    "loop": True,
                    "steps": [
                        {"target": "a", "press_ms": 2, "gap_ms": 5000},
                        {"target": "not-a-button", "press_ms": 100, "gap_ms": 50},
                    ],
                }
            ]
        )

        self.assertEqual(
            macros,
            [
                {
                    "trigger": "south",
                    "enabled": True,
                    "loop": True,
                    "steps": [{"target": "a", "press_ms": 30, "gap_ms": 1000}],
                }
            ],
        )

    def test_loop_defaults_to_off_for_existing_profiles(self):
        macros = normalized_macros(
            [
                {
                    "trigger": "south",
                    "steps": [{"target": "a"}],
                }
            ]
        )

        self.assertFalse(macros[0]["loop"])

    def test_touchpad_click_is_a_valid_macro_trigger(self):
        macros = normalized_macros(
            [
                {
                    "trigger": "touchpad",
                    "steps": [{"target": "a"}],
                }
            ]
        )

        self.assertEqual(macros[0]["trigger"], "touchpad")

    def test_raw_touchpad_fallback_button_is_a_valid_macro_trigger(self):
        macros = normalized_macros(
            [
                {
                    "trigger": "button_13",
                    "steps": [{"target": "a"}],
                }
            ]
        )

        self.assertEqual(macros[0]["trigger"], "button_13")

    def test_raw_trigger_drops_legacy_visual_mapping_metadata(self):
        macros = normalized_macros(
            [
                {
                    "trigger": "button_13",
                    "mapping_source": "touchpad",
                    "steps": [{"target": "a"}],
                }
            ]
        )

        self.assertEqual(macros[0]["trigger"], "button_13")
        self.assertNotIn("mapping_source", macros[0])

    def test_normalization_keeps_only_one_macro_per_trigger(self):
        macros = normalized_macros(
            [
                {"trigger": "button_0", "steps": [{"target": "a"}]},
                {"trigger": "button_0", "steps": [{"target": "b"}]},
                {"trigger": "unknown", "steps": [{"target": "x"}]},
            ]
        )

        self.assertEqual(len(macros), 1)
        self.assertEqual(macros[0]["steps"][0]["target"], "a")

    def test_player_emits_steps_and_release_gaps(self):
        macro = normalized_macros(
            [
                {
                    "trigger": "south",
                    "steps": [
                        {"target": "a", "press_ms": 100, "gap_ms": 50},
                        {"target": "b", "press_ms": 100, "gap_ms": 16},
                    ],
                }
            ]
        )[0]
        player = MacroPlayer()
        player.start(macro, 0.0)

        self.assertEqual(player.update(0.0), "a")
        self.assertEqual(player.update(0.099), "a")
        self.assertIsNone(player.update(0.100))
        self.assertIsNone(player.update(0.149))
        self.assertEqual(player.update(0.150), "b")
        self.assertEqual(player.update(0.249), "b")
        self.assertIsNone(player.update(0.250))
        self.assertIsNone(player.update(0.266))
        self.assertFalse(player.active)

    def test_zero_gap_still_releases_between_repeated_presses(self):
        macro = normalized_macros(
            [
                {
                    "trigger": "south",
                    "steps": [
                        {"target": "a", "press_ms": 30, "gap_ms": 0},
                        {"target": "a", "press_ms": 30, "gap_ms": 0},
                    ],
                }
            ]
        )[0]
        player = MacroPlayer()
        player.start(macro, 0.0)

        self.assertEqual(player.update(0.0), "a")
        self.assertIsNone(player.update(0.030))
        self.assertEqual(player.update(0.046), "a")

    def test_player_can_be_cancelled_immediately(self):
        macro = normalized_macros([{"trigger": "south", "steps": [{"target": "a"}]}])[0]
        player = MacroPlayer()
        player.start(macro, 0.0)
        self.assertEqual(player.update(0.0), "a")

        player.cancel()

        self.assertFalse(player.active)
        self.assertIsNone(player.update(1.0))

    def test_looping_macro_restarts_after_last_release_gap_until_cancelled(self):
        macro = normalized_macros(
            [
                {
                    "trigger": "touchpad",
                    "loop": True,
                    "steps": [{"target": "a", "press_ms": 30, "gap_ms": 16}],
                }
            ]
        )[0]
        player = MacroPlayer()
        player.start(macro, 0.0)

        self.assertEqual(player.update(0.0), "a")
        self.assertIsNone(player.update(0.030))
        self.assertEqual(player.update(0.046), "a")
        self.assertTrue(player.active)

        player.cancel()

        self.assertFalse(player.active)


if __name__ == "__main__":
    unittest.main()
