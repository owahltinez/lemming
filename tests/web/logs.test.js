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
});
