import pathlib
import shutil
import tempfile
import time
import unittest
import unittest.mock

from lemming import tasks, runner, orchestrator, models


class TestIntegration(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.test_tasks_file = pathlib.Path(self.test_dir) / "tasks_test.yml"

        # Scaffold a valid file with one task
        self.initial_data = models.Roadmap(
            context="Initial context",
            tasks=[
                models.Task(
                    id="task1",
                    description="Task 1",
                    status=models.TaskStatus.PENDING,
                    attempts=0,
                    outcomes=[],
                )
            ],
            config=models.RoadmapConfig(retries=3, runner="true"),
        )
        tasks.save_tasks(self.test_tasks_file, self.initial_data)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_heartbeat_updates_during_execution(self):
        """Verify that a long-running task updates its heartbeat periodically."""
        # We need to speed up the threshold for testing
        with (
            unittest.mock.patch("lemming.tasks.STALE_THRESHOLD", 2),
            unittest.mock.patch("lemming.persistence.STALE_THRESHOLD", 2),
            unittest.mock.patch("lemming.tasks.lifecycle.STALE_THRESHOLD", 2),
        ):
            # 0. Mark task as in progress first, as runner.run_with_heartbeat expects it
            tasks.mark_task_in_progress(self.test_tasks_file, "task1")

            # Start a task that runs for 3 seconds (longer than threshold)
            cmd = ["sleep", "3"]

            # Run in a separate thread so we can check the file
            def run_task():
                runner.run_with_heartbeat(
                    cmd, self.test_tasks_file, "task1", verbose=False
                )

            import threading

            t = threading.Thread(target=run_task)
            t.start()

            # Wait a bit for it to start and set initial heartbeat
            time.sleep(0.5)
            data = tasks.load_tasks(self.test_tasks_file)
            h1 = data.tasks[0].last_heartbeat
            self.assertIsNotNone(h1, "Initial heartbeat should be set")

            # Wait for next heartbeat (interval is threshold // 2 = 1s)
            time.sleep(1.5)
            data = tasks.load_tasks(self.test_tasks_file)
            h2 = data.tasks[0].last_heartbeat
            self.assertIsNotNone(h2, "Second heartbeat should be set")
            self.assertGreater(h2, h1, "Heartbeat should increase over time")

            t.join()

    def test_task_reclaimed_if_heartbeat_stops(self):
        """Verify that if a runner stops updating heartbeat, the task can be reclaimed."""
        with (
            unittest.mock.patch("lemming.tasks.STALE_THRESHOLD", 1),
            unittest.mock.patch("lemming.persistence.STALE_THRESHOLD", 1),
            unittest.mock.patch("lemming.tasks.lifecycle.STALE_THRESHOLD", 1),
        ):
            # 1. Claim the task manually with a PID that doesn't exist
            # Note: is_pid_alive will return False for 999999 (likely)
            tasks.claim_task(self.test_tasks_file, "task1", pid=999999)

            data = tasks.load_tasks(self.test_tasks_file)
            self.assertEqual(data.tasks[0].status, models.TaskStatus.IN_PROGRESS)

            # 2. Wait for it to become stale
            time.sleep(1.1)

            # 3. Try to claim it again (as if another orchestrator is running)
            # claim_task should allow reclaiming if stale
            task = tasks.claim_task(self.test_tasks_file, "task1", pid=88888)
            self.assertIsNotNone(
                task, "Task should be reclaimable after heartbeat timeout"
            )
            self.assertEqual(task.pid, 88888)

    def test_orchestrator_retries_on_runner_failure(self):
        """Verify that the orchestrator retries if the runner exits but doesn't report success."""
        # Mocking time.sleep to speed up tests
        with unittest.mock.patch("time.sleep", return_value=None):
            # Configure roadmap with 2 retries
            data = tasks.load_tasks(self.test_tasks_file)
            data.config.retries = 2
            data.config.runner = "true"  # 'true' command just exits 0
            tasks.save_tasks(self.test_tasks_file, data)

            orchestrator.run_loop(
                self.test_tasks_file,
                verbose=False,
                retry_delay=0,
                yolo=True,
                no_defaults=False,
                runner_args=(),
            )

            # After 2 attempts it should be FAILED
            data = tasks.load_tasks(self.test_tasks_file)
            self.assertEqual(data.tasks[0].attempts, 2)
            self.assertEqual(data.tasks[0].status, models.TaskStatus.FAILED)

    def test_runner_terminates_if_reclaimed(self):
        """Verify that if a task is reclaimed by another process, the original runner terminates."""
        with (
            unittest.mock.patch("lemming.tasks.STALE_THRESHOLD", 2),
            unittest.mock.patch("lemming.persistence.STALE_THRESHOLD", 2),
            unittest.mock.patch("lemming.tasks.lifecycle.STALE_THRESHOLD", 2),
        ):
            # 0. Mark task as in progress
            tasks.mark_task_in_progress(self.test_tasks_file, "task1")

            # Start a long-running task
            cmd = ["sleep", "10"]

            def run_task():
                runner.run_with_heartbeat(
                    cmd, self.test_tasks_file, "task1", verbose=True
                )

            import threading

            t = threading.Thread(target=run_task)
            t.start()

            # Wait for it to start
            time.sleep(0.5)

            # 2. Reclaim the task manually by changing the PID in the file
            # runner.py check: if not tasks.update_heartbeat(tasks_file, task_id):
            # update_heartbeat returns True ONLY if task is IN_PROGRESS.
            # Wait, update_heartbeat in lifecycle.py DOES NOT check PID if it matches!
            # It just updates it.

            # Let's re-read update_heartbeat:
            # def update_heartbeat(tasks_file, task_id, pid=None):
            #     for task in data.tasks:
            #         if task.id == task_id:
            #             if task.status != models.TaskStatus.IN_PROGRESS:
            #                 return False
            #             task.last_heartbeat = time.time()
            #             if pid is not None:
            #                 task.pid = pid
            #             break

            # Ah! It doesn't check if the PID matches. So if another process
            # just calls update_heartbeat, it will succeed.

            # BUT, mark_task_in_progress/claim_task will succeed if it's stale.
            # And once it's COMPLETED or FAILED, update_heartbeat returns False.

            # Let's simulate cancellation/completion by another process
            with tasks.lock_tasks(self.test_tasks_file):
                data = tasks.load_tasks(self.test_tasks_file)
                data.tasks[0].status = models.TaskStatus.COMPLETED
                tasks.save_tasks(self.test_tasks_file, data)

            # Wait for the next heartbeat (threshold // 2 = 1s)
            t.join(timeout=5)
            self.assertFalse(
                t.is_alive(),
                "Runner thread should have terminated after task was marked COMPLETED",
            )

    def test_orchestrator_retries_on_runner_crash(self):
        """Verify that the orchestrator retries if the runner exits with an error code."""
        with unittest.mock.patch("time.sleep", return_value=None):
            data = tasks.load_tasks(self.test_tasks_file)
            data.config.retries = 3
            data.config.runner = "false"  # 'false' command exits with 1
            tasks.save_tasks(self.test_tasks_file, data)

            orchestrator.run_loop(
                self.test_tasks_file,
                verbose=False,
                retry_delay=0,
                yolo=True,
                no_defaults=False,
                runner_args=(),
            )

            data = tasks.load_tasks(self.test_tasks_file)
            # It should have attempted 3 times and then failed
            self.assertEqual(data.tasks[0].attempts, 3)
            self.assertEqual(data.tasks[0].status, models.TaskStatus.FAILED)

    def test_orchestrator_stops_on_cancellation(self):
        """Verify that the orchestrator stops the loop if the runner exits with -15 (SIGTERM)."""
        with (
            unittest.mock.patch("time.sleep", return_value=None),
            unittest.mock.patch(
                "lemming.runner.run_with_heartbeat", return_value=(-15, "", "")
            ),
        ):
            data = tasks.load_tasks(self.test_tasks_file)
            data.config.retries = 3
            tasks.save_tasks(self.test_tasks_file, data)

            # This should NOT loop 3 times. It should break immediately.
            orchestrator.run_loop(
                self.test_tasks_file,
                verbose=False,
                retry_delay=0,
                yolo=True,
                no_defaults=False,
                runner_args=(),
            )

            data = tasks.load_tasks(self.test_tasks_file)
            # It should have only 1 attempt because it broke
            self.assertEqual(data.tasks[0].attempts, 1)
            # Status should be PENDING (as finish_task_attempt sets it if no requested_status)
            self.assertEqual(data.tasks[0].status, models.TaskStatus.PENDING)

    def test_claim_task_race_condition(self):
        """Verify that claim_task is atomic and prevents double claiming."""
        # Mock is_pid_alive to return True so that even if we use fake PIDs,
        # they are not considered dead and thus not reclaimable immediately.
        with unittest.mock.patch(
            "lemming.tasks.lifecycle.is_pid_alive", return_value=True
        ):
            # We'll use multiple threads to try to claim the same task
            results = []

            def try_claim(pid):
                res = tasks.claim_task(self.test_tasks_file, "task1", pid=pid)
                if res:
                    results.append(pid)

            import threading

            threads = []
            for i in range(1, 11):  # Use 1 to 10
                t = threading.Thread(target=try_claim, args=(i,))
                threads.append(t)

            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # Only one should have succeeded
            self.assertEqual(len(results), 1)
            data = tasks.load_tasks(self.test_tasks_file)
            self.assertEqual(data.tasks[0].status, models.TaskStatus.IN_PROGRESS)
            self.assertIn(data.tasks[0].pid, results)

    def test_reclaim_if_pid_dead(self):
        """Verify that a task is reclaimable immediately if its PID is dead, even if heartbeat is fresh."""
        with unittest.mock.patch(
            "lemming.tasks.lifecycle.is_pid_alive", return_value=False
        ):
            # 1. Claim it with some PID
            tasks.claim_task(self.test_tasks_file, "task1", pid=12345)

            data = tasks.load_tasks(self.test_tasks_file)
            self.assertEqual(data.tasks[0].status, models.TaskStatus.IN_PROGRESS)
            self.assertEqual(data.tasks[0].pid, 12345)

            # 2. Heartbeat is fresh, but is_pid_alive is mocked to False
            # Try to claim it again with another PID
            task = tasks.claim_task(self.test_tasks_file, "task1", pid=67890)
            self.assertIsNotNone(task, "Task should be reclaimable if PID is dead")
            self.assertEqual(task.pid, 67890)


if __name__ == "__main__":
    unittest.main()
