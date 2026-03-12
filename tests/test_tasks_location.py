import hashlib
import os
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
            temp_home = pathlib.Path(td).resolve() / "fake_home"
            temp_home.mkdir()
            mock_home.return_value = temp_home

            # The current working directory in isolated_filesystem will be some temp dir inside td
            cwd_path = str(pathlib.Path.cwd().resolve())
            path_hash = hashlib.sha256(cwd_path.encode()).hexdigest()[:12]

            self.runner.invoke(cli, ["add", "Test Task"])

            expected_local_path = pathlib.Path("tasks.yml")
            expected_global_path = (
                temp_home / ".local" / "lemming" / "projects" / path_hash / "tasks.yml"
            )

            # Desired behavior:
            self.assertFalse(expected_local_path.exists())
            self.assertTrue(expected_global_path.exists())

    @patch("pathlib.Path.home")
    def test_precedence_local_file_exists(self, mock_home):
        with self.runner.isolated_filesystem() as td:
            temp_home = pathlib.Path(td).resolve() / "fake_home"
            temp_home.mkdir()
            mock_home.return_value = temp_home

            # Create a local tasks.yml
            local_tasks = pathlib.Path("tasks.yml")
            local_tasks.write_text(
                "context: Local Context\ntasks: []", encoding="utf-8"
            )

            # Create a global tasks.yml
            cwd_path = str(pathlib.Path.cwd().resolve())
            path_hash = hashlib.sha256(cwd_path.encode()).hexdigest()[:12]
            global_dir = temp_home / ".local" / "lemming" / "projects" / path_hash
            global_dir.mkdir(parents=True)
            global_tasks = global_dir / "tasks.yml"
            global_tasks.write_text(
                "context: Global Context\ntasks: []", encoding="utf-8"
            )

    @patch("pathlib.Path.home")
    def test_different_directories_different_hashes(self, mock_home):
        with self.runner.isolated_filesystem() as td:
            temp_home = pathlib.Path(td).resolve() / "fake_home"
            temp_home.mkdir()
            mock_home.return_value = temp_home

            # Create two different project directories
            proj1 = pathlib.Path(td) / "project1"
            proj2 = pathlib.Path(td) / "project2"
            proj1.mkdir()
            proj2.mkdir()

            # In proj1
            os.chdir(proj1)
            cwd1 = str(pathlib.Path.cwd().resolve())
            hash1 = hashlib.sha256(cwd1.encode()).hexdigest()[:12]
            self.runner.invoke(cli, ["add", "Task 1"])
            path1 = temp_home / ".local" / "lemming" / "projects" / hash1 / "tasks.yml"
            self.assertTrue(path1.exists())

            # In proj2
            os.chdir(proj2)
            cwd2 = str(pathlib.Path.cwd().resolve())
            hash2 = hashlib.sha256(cwd2.encode()).hexdigest()[:12]
            self.runner.invoke(cli, ["add", "Task 2"])
            path2 = temp_home / ".local" / "lemming" / "projects" / hash2 / "tasks.yml"
            self.assertTrue(path2.exists())

            self.assertNotEqual(hash1, hash2)
            self.assertNotEqual(path1, path2)


if __name__ == "__main__":
    unittest.main()
