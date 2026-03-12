import pathlib
import shutil
import tempfile
import unittest
import yaml

from click.testing import CliRunner

from lemming.main import cli


class TestStatusSorting(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner()
        self.test_dir = tempfile.mkdtemp()
        self.test_tasks_file = pathlib.Path(self.test_dir) / "tasks_test.yml"
        self.base_args = ["--verbose", "--tasks-file", str(self.test_tasks_file)]

        # Setup tasks in a specific order
        # 1. Pending 1
        # 2. Completed 1 (earlier)
        # 3. In Progress 1
        # 4. Completed 2 (later)
        # 5. Pending 2

        data = {
            "context": "Context",
            "tasks": [
                {
                    "id": "p1",
                    "description": "Pending 1",
                    "status": "pending",
                    "attempts": 0,
                    "outcomes": [],
                },
                {
                    "id": "c1",
                    "description": "Completed 1",
                    "status": "completed",
                    "completed_at": 1000,
                    "attempts": 1,
                    "outcomes": ["done"],
                },
                {
                    "id": "i1",
                    "description": "In Progress 1",
                    "status": "in_progress",
                    "attempts": 1,
                    "outcomes": [],
                },
                {
                    "id": "c2",
                    "description": "Completed 2",
                    "status": "completed",
                    "completed_at": 2000,
                    "attempts": 1,
                    "outcomes": ["done"],
                },
                {
                    "id": "p2",
                    "description": "Pending 2",
                    "status": "pending",
                    "attempts": 0,
                    "outcomes": [],
                },
            ],
        }
        with open(self.test_tasks_file, "w", encoding="utf-8") as f:
            yaml.dump(data, f)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_status_sorting(self):
        result = self.runner.invoke(cli, self.base_args + ["status"])
        self.assertEqual(result.exit_code, 0)

        # Order should be:
        # 1. In Progress 1 (i1)
        # 2. Pending 1 (p1)
        # 3. Pending 2 (p2)
        # 4. Completed 2 (c2) (later completed_at)
        # 5. Completed 1 (c1) (earlier completed_at)

        lines = [
            line.strip()
            for line in result.output.split("\n")
            if line.strip() and not line.startswith("===")
        ]

        task_ids = []
        for line in lines:
            if "(" in line and ")" in line:
                task_id = line.split("(")[1].split(")")[0]
                task_ids.append(task_id)

        # Check expected order
        expected_order = ["i1", "p1", "p2", "c2", "c1"]
        self.assertEqual(task_ids, expected_order)


if __name__ == "__main__":
    unittest.main()
