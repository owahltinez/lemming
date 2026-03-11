import pathlib
import unittest
from unittest.mock import patch
from click.testing import CliRunner
from lemming.main import cli

class TestTasksLocation(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner()

    @patch("pathlib.Path.home")
    def test_default_location_no_local_file(self, mock_home):
        with self.runner.isolated_filesystem() as td:
            temp_home = pathlib.Path(td) / "fake_home"
            temp_home.mkdir()
            mock_home.return_text = str(temp_home) # Wait, Path.home() returns a Path object
            mock_home.return_value = temp_home
            
            # Run lemming info, it should default to fake_home/.local/lemming/tasks.yml
            # But currently it defaults to ./tasks.yml
            
            result = self.runner.invoke(cli, ["-v", "info"])
            # In the current implementation, it will look for tasks.yml in the current directory.
            # If it doesn't find it, load_tasks returns default data.
            # ctx.obj["TASKS_FILE"] will be Path("tasks.yml")
            
            # We want to check what ctx.obj["TASKS_FILE"] is.
            # We can't easily check ctx.obj from outside unless we modify info to print it or use a hook.
            # Alternatively, we can check if the file is created after an 'add' command.
            
            self.runner.invoke(cli, ["add", "Test Task"])
            
            expected_local_path = pathlib.Path("tasks.yml")
            expected_global_path = temp_home / ".local" / "lemming" / "tasks.yml"
            
            # Desired behavior:
            self.assertFalse(expected_local_path.exists())
            self.assertTrue(expected_global_path.exists())

    @patch("pathlib.Path.home")
    def test_precedence_local_file_exists(self, mock_home):
        with self.runner.isolated_filesystem() as td:
            temp_home = pathlib.Path(td) / "fake_home"
            temp_home.mkdir()
            mock_home.return_value = temp_home
            
            # Create a local tasks.yml
            local_tasks = pathlib.Path("tasks.yml")
            local_tasks.write_text("context: Local Context\ntasks: []", encoding="utf-8")
            
            # Create a global tasks.yml
            global_dir = temp_home / ".local" / "lemming"
            global_dir.mkdir(parents=True)
            global_tasks = global_dir / "tasks.yml"
            global_tasks.write_text("context: Global Context\ntasks: []", encoding="utf-8")
            
            result = self.runner.invoke(cli, ["-v", "info"])
            self.assertIn("Local Context", result.output)
            self.assertNotIn("Global Context", result.output)

if __name__ == "__main__":
    unittest.main()
