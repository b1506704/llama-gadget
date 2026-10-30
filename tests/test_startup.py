import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from llama_gadget.theme import DARK_TOKENS, LIGHT_TOKENS, ThemeAdapter, stylesheet


class TestStartup(unittest.TestCase):
    def test_install_script_autostart_flags(self):
        root_dir = Path(__file__).resolve().parent.parent
        install_script = root_dir / "install.sh"
        self.assertTrue(install_script.exists())

        with tempfile.TemporaryDirectory() as tmpdir:
            home_path = Path(tmpdir)
            env = dict(os.environ, HOME=str(home_path))

            # Test --auto-start
            res = subprocess.run(["sh", str(install_script), "--auto-start"], cwd=str(root_dir), env=env, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"Failed with: {res.stderr}")
            desktop_app = home_path / ".local/share/applications/llama-gadget.desktop"
            autostart_app = home_path / ".config/autostart/llama-gadget.desktop"
            self.assertTrue(desktop_app.exists(), "Desktop file not created")
            self.assertTrue(autostart_app.exists(), "Autostart desktop file not created for --auto-start")

            # Remove autostart and test --autostart
            autostart_app.unlink()
            res2 = subprocess.run(["sh", str(install_script), "--autostart"], cwd=str(root_dir), env=env, capture_output=True, text=True)
            self.assertEqual(res2.returncode, 0, f"Failed with: {res2.stderr}")
            self.assertTrue(autostart_app.exists(), "Autostart desktop file not created for --autostart")

    def test_stylesheet_function_signatures(self):
        # Must support 0 args, 1 arg (tokens), or 3 args (tokens, scale, base_pt)
        qss_default = stylesheet()
        self.assertIn("QWidget", qss_default)
        self.assertIn('keyboardFocus="true"', qss_default)

        qss_custom = stylesheet(DARK_TOKENS, 1.2, 11.0)
        self.assertIn("QWidget", qss_custom)
        self.assertIn('keyboardFocus="true"', qss_custom)

        adapter = ThemeAdapter(LIGHT_TOKENS)
        self.assertTrue(hasattr(adapter, "stylesheet"))
        self.assertTrue(hasattr(adapter, "app_stylesheet"))
        self.assertEqual(adapter.stylesheet(1.0, 10.0), adapter.app_stylesheet(1.0, 10.0))


if __name__ == "__main__":
    unittest.main()
