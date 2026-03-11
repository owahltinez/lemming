import pathlib
import shutil
import tempfile
import time
import unittest
import yaml
from lemming.core import get_pending_task, load_tasks, save_tasks, STALE_THRESHOLD, is_pid_alive

class TestInProgress(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.test_tasks_file = pathlib.Path(self.test_dir) / "tasks_test.yml"

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_get_pending_task_normal(self):
        data = {
            "tasks": [
                {"id": "t1", "description": "Task 1", "status": "pending"}
            ]
        }
        task = get_pending_task(data)
        self.assertIsNotNone(task)
        self.assertEqual(task["id"], "t1")

    def test_get_pending_task_in_progress_not_stale(self):
        data = {
            "tasks": [
                {
                    "id": "t1", 
                    "description": "Task 1", 
                    "status": "in_progress", 
                    "last_heartbeat": time.time()
                }
            ]
        }
        task = get_pending_task(data)
        self.assertIsNone(task)

    def test_get_pending_task_in_progress_stale(self):
        data = {
            "tasks": [
                {
                    "id": "t1", 
                    "description": "Task 1", 
                    "status": "in_progress", 
                    "last_heartbeat": time.time() - (STALE_THRESHOLD + 1)
                }
            ]
        }
        task = get_pending_task(data)
        self.assertIsNotNone(task)
        self.assertEqual(task["id"], "t1")

    def test_get_pending_task_dead_pid(self):
        import subprocess
        p = subprocess.Popen(["true"])
        p.wait()
        dead_pid = p.pid

        data = {
            "tasks": [
                {
                    "id": "t1", 
                    "description": "Task 1", 
                    "status": "in_progress", 
                    "last_heartbeat": time.time(),
                    "pid": dead_pid
                }
            ]
        }
        task = get_pending_task(data)
        self.assertIsNotNone(task)
        self.assertEqual(task["id"], "t1")

    def test_get_pending_task_alive_pid(self):
        import os
        alive_pid = os.getpid()

        data = {
            "tasks": [
                {
                    "id": "t1", 
                    "description": "Task 1", 
                    "status": "in_progress", 
                    "last_heartbeat": time.time(),
                    "pid": alive_pid
                }
            ]
        }
        task = get_pending_task(data)
        self.assertIsNone(task)

    def test_complete_in_progress(self):
        from click.testing import CliRunner
        from lemming.main import cli
        runner = CliRunner()
        
        data = {
            "tasks": [
                {"id": "t1", "description": "Task 1", "status": "in_progress"}
            ]
        }
        with open(self.test_tasks_file, "w") as f:
            yaml.dump(data, f)
            
        result = runner.invoke(cli, ["--tasks-file", str(self.test_tasks_file), "complete", "t1"])
        self.assertEqual(result.exit_code, 0)
        
        with open(self.test_tasks_file, "r") as f:
            new_data = yaml.safe_load(f)
        self.assertEqual(new_data["tasks"][0]["status"], "completed")

    def test_fail_in_progress(self):
        from click.testing import CliRunner
        from lemming.main import cli
        runner = CliRunner()
        
        data = {
            "tasks": [
                {"id": "t1", "description": "Task 1", "status": "in_progress"}
            ]
        }
        with open(self.test_tasks_file, "w") as f:
            yaml.dump(data, f)
            
        result = runner.invoke(cli, ["--tasks-file", str(self.test_tasks_file), "fail", "t1", "--lesson", "failed"])
        self.assertEqual(result.exit_code, 0)
        
        with open(self.test_tasks_file, "r") as f:
            new_data = yaml.safe_load(f)
        self.assertEqual(new_data["tasks"][0]["status"], "pending")

if __name__ == "__main__":
    unittest.main()
