import assert from "node:assert";
import * as path from "node:path";
import { describe, test } from "node:test";
import { Renderer } from "mancha";

const logsHtmlPath = path.join(process.cwd(), "src/lemming/web/logs.html");

const createInitialState = (overrides = {}) => {
  return {
    task: { id: "test-id", status: "pending", description: "Test Description" },
    taskLog: "",
    loading: true,
    formatDate: (ts) => (ts ? new Date(ts * 1000).toLocaleString() : ""),
    formatDuration: (seconds) => {
      if (!seconds) return "0s";
      if (seconds < 60) return `${Math.floor(seconds)}s`;
      const minutes = Math.floor(seconds / 60);
      const remainingSeconds = Math.floor(seconds % 60);
      return `${minutes}m ${remainingSeconds}s`;
    },
    formatTaskRunTime: function (task) {
      let total = task.run_time || 0;
      if (task.status === "in_progress" && task.last_started_at) {
        total += Date.now() / 1000 - task.last_started_at;
      }
      return this.formatDuration(total);
    },
    ...overrides,
  };
};

describe("Lemming Task Log Viewer", () => {
  test("renders log content correctly", async () => {
    const logContent = "This is a test log content.\nLine 2 of logs.";
    const renderer = new Renderer(
      createInitialState({
        task: { id: "t1", status: "completed", description: "Completed task" },
        taskLog: logContent,
        loading: false,
      }),
    );

    const fragment = await renderer.preprocessLocal(logsHtmlPath);

    // Ensure :data on body doesn't overwrite our test state
    const root =
      fragment.querySelector("body") || fragment.firstElementChild || fragment;
    if (root.hasAttribute(":data")) root.removeAttribute(":data");
    if (root.hasAttribute(":render")) root.removeAttribute(":render");

    await renderer.mount(fragment);

    const logContainer = fragment.querySelector("#log-container");
    assert.ok(logContainer, "Log container should exist");
    assert.strictEqual(logContainer.textContent.trim(), logContent);

    // Verify it doesn't contain the minified function string
    assert.ok(
      !logContainer.textContent.includes("(...t)=>e.call(n,...t)"),
      "Should not contain minified function string",
    );
  });

  test("shows loading message when taskLog is empty", async () => {
    const renderer = new Renderer(
      createInitialState({
        taskLog: "",
      }),
    );

    const fragment = await renderer.preprocessLocal(logsHtmlPath);
    const root =
      fragment.querySelector("body") || fragment.firstElementChild || fragment;
    if (root.hasAttribute(":data")) root.removeAttribute(":data");
    await renderer.mount(fragment);

    const logContainer = fragment.querySelector("#log-container");
    assert.strictEqual(
      logContainer.textContent.trim(),
      "Loading log content...",
    );
  });

  test("shows live indicator for in_progress tasks", async () => {
    const renderer = new Renderer(
      createInitialState({
        task: { id: "t1", status: "in_progress", description: "Running task" },
        loading: false,
      }),
    );

    const fragment = await renderer.preprocessLocal(logsHtmlPath);
    const root =
      fragment.querySelector("body") || fragment.firstElementChild || fragment;
    if (root.hasAttribute(":data")) root.removeAttribute(":data");
    await renderer.mount(fragment);

    const liveIndicator = fragment.querySelector(".text-blue-600");
    assert.ok(liveIndicator, "Live indicator should be present");
    assert.strictEqual(liveIndicator.textContent.trim(), "Live");
  });

  test("displays timing information correctly", async () => {
    const now = Date.now() / 1000;
    const taskInProgress = {
      id: "t-progress",
      status: "in_progress",
      description: "Running task",
      started_at: now - 3600, // 1 hour ago
      last_started_at: now - 600, // 10 mins ago (current attempt)
      run_time: 300,
    };

    const renderer = new Renderer(
      createInitialState({
        task: taskInProgress,
        loading: false,
      }),
    );

    const fragment = await renderer.preprocessLocal(logsHtmlPath);
    const root =
      fragment.querySelector("body") || fragment.firstElementChild || fragment;
    if (root.hasAttribute(":data")) root.removeAttribute(":data");
    await renderer.mount(fragment);

    const startedLabel = Array.from(fragment.querySelectorAll("span")).find(
      (s) => s.textContent.trim() === "Started:",
    );
    assert.ok(startedLabel, "Started label should be present");
    assert.ok(startedLabel.nextElementSibling.textContent.trim().length > 0);

    const runtimeLabel = Array.from(fragment.querySelectorAll("span")).find(
      (s) => s.textContent.trim() === "Runtime:",
    );
    assert.ok(runtimeLabel, "Runtime label should be present");
    // 300s baseline + 600s elapsed = 900s = 15m
    assert.ok(runtimeLabel.nextElementSibling.textContent.includes("15m 0s"));

    // Verify completed label is NOT present for in_progress
    const completedLabel = Array.from(fragment.querySelectorAll("span")).find(
      (s) => s.textContent.trim() === "Completed:",
    );
    assert.ok(
      !completedLabel || completedLabel.parentElement.style.display === "none",
      "Completed label should be hidden for in_progress",
    );

    // Test completed task
    const taskCompleted = {
      id: "t-completed",
      status: "completed",
      description: "Finished task",
      completed_at: now - 300,
      run_time: 450,
    };

    const renderer2 = new Renderer(
      createInitialState({
        task: taskCompleted,
        loading: false,
      }),
    );

    const fragment2 = await renderer2.preprocessLocal(logsHtmlPath);
    const root2 =
      fragment2.querySelector("body") ||
      fragment2.firstElementChild ||
      fragment2;
    if (root2.hasAttribute(":data")) root2.removeAttribute(":data");
    await renderer2.mount(fragment2);

    const completedLabel2 = Array.from(fragment2.querySelectorAll("span")).find(
      (s) => s.textContent.trim() === "Completed:",
    );
    assert.ok(completedLabel2, "Completed label should be present");
    assert.ok(completedLabel2.nextElementSibling.textContent.trim().length > 0);

    const runtimeLabel2 = Array.from(fragment2.querySelectorAll("span")).find(
      (s) => s.textContent.trim() === "Runtime:",
    );
    assert.ok(runtimeLabel2, "Runtime label should be present");
    // 450s = 7m 30s
    assert.ok(runtimeLabel2.nextElementSibling.textContent.includes("7m 30s"));

    // Verify started label is NOT present for completed
    const startedLabel2 = Array.from(fragment2.querySelectorAll("span")).find(
      (s) => s.textContent.trim() === "Started:",
    );
    assert.ok(
      !startedLabel2 || startedLabel2.parentElement.style.display === "none",
      "Started label should be hidden for completed",
    );
  });
});
