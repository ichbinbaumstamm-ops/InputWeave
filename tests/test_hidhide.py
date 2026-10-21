import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from inputweave import hidhide


class HidHideIntegrationTests(unittest.TestCase):
    def test_finds_current_cli_install_layout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cli = root / hidhide.HIDHIDE_CLI_RELATIVE_PATH
            cli.parent.mkdir(parents=True)
            cli.touch()

            self.assertEqual(hidhide.find_cli(str(root), None), cli)

    def test_finds_legacy_x64_cli_install_layout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cli = root / hidhide.HIDHIDE_CLI_RELATIVE_PATH
            cli.parent.mkdir(parents=True)
            legacy_cli = cli.parent / "x64" / cli.name
            legacy_cli.parent.mkdir()
            legacy_cli.touch()

            self.assertEqual(hidhide.find_cli(str(root), None), legacy_cli)

    def test_allow_list_adds_only_the_requested_executable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app = Path(temp_dir) / "InputWeave.exe"
            cli = Path(temp_dir) / "HidHideCLI.exe"
            app.touch()
            cli.touch()
            inverse_state = subprocess.CompletedProcess([], 0, "--inv-off\n", "")
            completed = subprocess.CompletedProcess([], 0, "", "")

            with patch(
                "inputweave.hidhide.subprocess.run",
                side_effect=[inverse_state, completed],
            ) as run:
                ok, state = hidhide.register_application(app, cli)

            self.assertTrue(ok)
            self.assertEqual(state, "registered")
            args = run.call_args_list[1].args[0]
            self.assertEqual(args, [str(cli), "--app-reg", str(app.resolve())])

    def test_does_not_change_inverse_hidhide_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app = Path(temp_dir) / "InputWeave.exe"
            cli = Path(temp_dir) / "HidHideCLI.exe"
            app.touch()
            cli.touch()
            inverse_state = subprocess.CompletedProcess([], 0, "--inv-on\n", "")

            with patch(
                "inputweave.hidhide.subprocess.run", return_value=inverse_state
            ) as run:
                ok, state = hidhide.register_application(app, cli)

            self.assertFalse(ok)
            self.assertEqual(state, "inverse_mode_enabled")
            run.assert_called_once()

    def test_rejects_non_executable_application_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app = Path(temp_dir) / "not-an-app.txt"
            app.touch()
            with patch("inputweave.hidhide.subprocess.run") as run:
                ok, state = hidhide.register_application(
                    app, Path(temp_dir) / "HidHideCLI.exe"
                )

            self.assertFalse(ok)
            self.assertEqual(state, "application_path_invalid")
            run.assert_not_called()

    def test_source_run_does_not_whitelist_python(self):
        with patch.object(sys, "frozen", False, create=True):
            with patch("inputweave.hidhide.register_application") as register:
                self.assertEqual(
                    hidhide.configure_current_application(), (True, "source_run")
                )
            register.assert_not_called()

    def test_bundled_installer_matches_verified_official_asset(self):
        installer = (
            Path(__file__).resolve().parents[1]
            / "resources"
            / hidhide.HIDHIDE_INSTALLER_NAME
        )
        self.assertTrue(hidhide.verify_installer(installer))


if __name__ == "__main__":
    unittest.main()
