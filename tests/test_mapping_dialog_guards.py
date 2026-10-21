import unittest
from pathlib import Path


class MappingDialogGuardTests(unittest.TestCase):
    def test_click_handler_is_defined_before_the_controller_canvas_binds_it(self):
        source_file = Path(__file__).parents[1] / "src" / "inputweave" / "main.py"
        source = source_file.read_text(encoding="utf-8")
        mapping_dialog = source.split(
            "    def edit_button_mapping(self) -> None:\n", 1
        )[1].split("    def open_log(self) -> None:\n", 1)[0]

        definition = mapping_dialog.index(
            "        def on_controller_click(event) -> None:"
        )
        binding = mapping_dialog.index(
            '        controller_canvas.bind("<Button-1>", on_controller_click)'
        )

        self.assertLess(definition, binding)
        self.assertEqual(
            mapping_dialog.count(
                'controller_canvas.bind("<Button-1>", on_controller_click)'
            ),
            1,
        )

    def test_dialogs_only_use_the_defined_text_variable_tracker(self):
        source_file = Path(__file__).parents[1] / "src" / "inputweave" / "main.py"
        source = source_file.read_text(encoding="utf-8")

        self.assertNotIn("self.register_textvar(", source)
        self.assertIn("def _track_textvar(self, variable:", source)


if __name__ == "__main__":
    unittest.main()
