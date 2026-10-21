import unittest

from inputweave.localization import (
    LANGUAGES,
    TRANSLATIONS,
    canonical_text,
    control_label,
    target_label,
    translate,
    translate_format,
)


class LocalizationTests(unittest.TestCase):
    def test_every_supported_language_has_the_complete_catalog(self):
        catalog_keys = set().union(*(set(items) for items in TRANSLATIONS.values()))
        for language in LANGUAGES.keys() - {"de"}:
            with self.subTest(language=language):
                self.assertEqual(
                    catalog_keys - set(TRANSLATIONS.get(language, {})), set()
                )

    def test_unknown_text_stays_readable_and_translated_text_round_trips(self):
        self.assertEqual(translate("Keep this label", "ar"), "Keep this label")
        german = "Controller Studio  ·  Eingabe passend zum Spiel ausgeben"
        english = translate(german, "en")
        self.assertEqual(canonical_text(english), german)

    def test_formatted_messages_preserve_values(self):
        message = translate_format("Gerät {index}", "en", index="2")
        self.assertEqual(message, "Device 2")

    def test_face_button_source_labels_always_include_platform_glyphs(self):
        expected = {"south": "✕", "east": "○", "west": "□", "north": "△"}
        for language in LANGUAGES:
            for control_id, glyph in expected.items():
                with self.subTest(language=language, control=control_id):
                    self.assertIn(glyph, control_label(control_id, language))
        self.assertIn("✕", target_label("a", "virtual_ds4", "ar"))

    def test_playstation_and_xbox_sources_show_both_button_conventions(self):
        self.assertIn("✕", control_label("south", "de", "PlayStation"))
        self.assertIn("Xbox A", control_label("south", "de", "PlayStation"))
        self.assertIn("A", control_label("south", "en", "Xbox"))
        self.assertIn("PlayStation ✕", control_label("south", "en", "Xbox"))


if __name__ == "__main__":
    unittest.main()
