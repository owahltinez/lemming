import assert from "node:assert";
import * as path from "node:path";
import { describe, test } from "node:test";
import { Renderer } from "mancha";

const indexHtmlPath = path.join(process.cwd(), "src/lemming/web/index.html");

const createInitialState = (overrides = {}) => {
  const tasks = overrides.tasks || [];
  const hideCompleted = overrides.hideCompleted || false;
  return {
    tasks,
    context: "",
    cwd: "/test/cwd",
    newTask: "",
    loading: true,
    agents: [],
    selectedAgent: "gemini",
    hideCompleted,
    toasts: [],
    expanded: {},
    trim: (s, l = 60) =>
      s && s.length > l ? `${s.substring(0, l - 3)}...` : s,
    formatDate: (ts) => (ts ? new Date(ts * 1000).toLocaleString() : ""),
    filteredTasks: hideCompleted
      ? tasks.filter((t) => t.status !== "completed")
      : tasks,
    ...overrides,
  };
};

describe("Lemming Web Dashboard", () => {
  test("initial state", async () => {
    const renderer = new Renderer(createInitialState());

    const fragment = await renderer.preprocessLocal(indexHtmlPath);

    // Ensure :data on body doesn't overwrite our test state
    const root =
      fragment.querySelector("body") || fragment.firstElementChild || fragment;
    if (root.hasAttribute(":data")) {
      root.removeAttribute(":data");
    }

    await renderer.mount(fragment);

    const heading = fragment.querySelector("h1");
    assert.strictEqual(heading.textContent.trim(), "Lemming Task Runner");

    const noTasks = fragment.querySelector('[role="status"]');
    assert.ok(noTasks, "Should show 'No tasks yet'");
    assert.ok(noTasks.textContent.includes("No tasks yet"));

    const cwdDisplay = fragment.querySelector(
      '[title="Lemming Task Runner"] + section div div div.text-sm.font-mono',
    );
    // Wait, the selector might be fragile. Let's find it by the label.
    const labels = Array.from(fragment.querySelectorAll("label"));
    const cwdLabel = labels.find((l) =>
      l.textContent.includes("Current Working Directory"),
    );
    assert.ok(cwdLabel, "CWD label should exist");
    const cwdLink = cwdLabel.nextElementSibling;
    assert.strictEqual(cwdLink.tagName.toLowerCase(), "a");
    assert.strictEqual(cwdLink.getAttribute("href"), "/files/");
    assert.strictEqual(cwdLink.getAttribute("target"), "_blank");
    assert.strictEqual(cwdLink.textContent.trim(), "/test/cwd");
    assert.strictEqual(cwdLink.getAttribute("title"), "/test/cwd");
  });

  test("renders tasks correctly", async () => {
    const tasks = [
      {
        id: "123",
        description: "Test Task 1",
        status: "pending",
        attempts: 0,
        outcomes: [],
      },
      {
        id: "456",
        description: "Test Task 2",
        status: "completed",
        attempts: 1,
        outcomes: ["Success"],
      },
    ];

    const renderer = new Renderer(
      createInitialState({
        tasks,
        context: "Test context",
        loading: false,
        filteredTasks: tasks,
      }),
    );

    const fragment = await renderer.preprocessLocal(indexHtmlPath);

    // Ensure :data on body doesn't overwrite our test state
    const root =
      fragment.querySelector("body") || fragment.firstElementChild || fragment;
    if (root.hasAttribute(":data")) {
      root.removeAttribute(":data");
    }

    await renderer.mount(fragment);

    const taskItems = fragment.querySelectorAll('[role="listitem"]');
    assert.strictEqual(taskItems.length, 2);

    const descriptions = Array.from(taskItems).map((item) => {
      const p = item.querySelector("p");
      return p ? p.textContent.trim() : "";
    });
    assert.ok(descriptions.includes("Test Task 1"));
    assert.ok(descriptions.includes("Test Task 2"));
  });

  test("hides completed tasks when hideCompleted is true", async () => {
    const tasks = [
      {
        id: "123",
        description: "Pending Task",
        status: "pending",
        attempts: 0,
        outcomes: [],
      },
      {
        id: "456",
        description: "Completed Task",
        status: "completed",
        attempts: 1,
        outcomes: ["Success"],
      },
    ];

    const renderer = new Renderer(
      createInitialState({
        tasks,
        hideCompleted: true,
        loading: false,
        filteredTasks: tasks.filter((t) => t.status !== "completed"),
      }),
    );

    const fragment = await renderer.preprocessLocal(indexHtmlPath);

    const root =
      fragment.querySelector("body") || fragment.firstElementChild || fragment;
    if (root.hasAttribute(":data")) {
      root.removeAttribute(":data");
    }

    await renderer.mount(fragment);

    const taskItems = fragment.querySelectorAll('[role="listitem"]');
    assert.strictEqual(taskItems.length, 1);
    assert.ok(taskItems[0].textContent.includes("Pending Task"));
    assert.ok(!taskItems[0].textContent.includes("Completed Task"));
  });

  test("shows only stop button for in_progress tasks", async () => {
    const tasks = [
      {
        id: "123",
        description: "Running Task",
        status: "in_progress",
        attempts: 1,
        outcomes: [],
      },
    ];

    const renderer = new Renderer(
      createInitialState({
        tasks,
        loading: false,
        filteredTasks: tasks,
      }),
    );

    const fragment = await renderer.preprocessLocal(indexHtmlPath);

    const root =
      fragment.querySelector("body") || fragment.firstElementChild || fragment;
    if (root.hasAttribute(":data")) {
      root.removeAttribute(":data");
    }

    await renderer.mount(fragment);

    const taskItem = fragment.querySelector('[role="listitem"]');

    const expandButton = taskItem.querySelector('[aria-label="Show details"]');
    const cancelButton = taskItem.querySelector(
      '[aria-label="Cancel Task Execution"]',
    );
    const editButton = taskItem.querySelector('[aria-label="Edit Task"]');
    const uncompleteButton = taskItem.querySelector(
      '[aria-label="Mark as Pending"]',
    );
    const deleteButton = taskItem.querySelector('[aria-label="Delete Task"]');

    assert.ok(expandButton, "Expand button should be present");
    assert.strictEqual(
      expandButton.style.display,
      "",
      "Expand button should be visible",
    );

    assert.ok(cancelButton, "Cancel button should be present");
    assert.strictEqual(
      cancelButton.style.display,
      "",
      "Cancel button should be visible",
    );

    assert.ok(editButton, "Edit button should be present");
    assert.strictEqual(
      editButton.style.display,
      "none",
      "Edit button should be hidden",
    );

    assert.ok(uncompleteButton, "Uncomplete button should be present");
    assert.strictEqual(
      uncompleteButton.style.display,
      "none",
      "Uncomplete button should be hidden",
    );

    assert.ok(deleteButton, "Delete button should be present");
    assert.strictEqual(
      deleteButton.style.display,
      "none",
      "Delete button should be hidden",
    );
  });

  test("trims long descriptions in queue but shows full in details", async () => {
    const longDescription =
      "This is a very long task description that should be trimmed in the queue list but show fully when expanded and should be ellipsized properly.";
    const tasks = [
      {
        id: "123",
        description: longDescription,
        status: "pending",
        attempts: 0,
        outcomes: [],
      },
    ];

    const renderer = new Renderer(
      createInitialState({
        tasks,
        loading: false,
        expanded: { 123: true },
        filteredTasks: tasks,
      }),
    );

    const fragment = await renderer.preprocessLocal(indexHtmlPath);

    const root =
      fragment.querySelector("body") || fragment.firstElementChild || fragment;
    if (root.hasAttribute(":data")) {
      root.removeAttribute(":data");
    }

    await renderer.mount(fragment);

    const taskItem = fragment.querySelector('[role="listitem"]');
    const p = taskItem.querySelector("p");

    // The trimmed version should have 80 chars total including '...'
    assert.strictEqual(p.textContent.trim().length, 80);
    assert.ok(p.textContent.trim().endsWith("..."));

    const details = taskItem.querySelector(".px-12.pb-3");
    const fullDescriptionDiv = details.querySelector(".mt-2.text-gray-700");
    assert.strictEqual(fullDescriptionDiv.textContent.trim(), longDescription);
  });

  test("delete completed button visibility", async () => {
    // 1. No completed tasks
    const tasksNoCompleted = [
      {
        id: "1",
        description: "Pending",
        status: "pending",
        attempts: 0,
        outcomes: [],
      },
    ];
    let renderer = new Renderer(
      createInitialState({
        tasks: tasksNoCompleted,
        loading: false,
        completedCount: 0,
        filteredTasks: tasksNoCompleted,
      }),
    );
    let fragment = await renderer.preprocessLocal(indexHtmlPath);
    let root =
      fragment.querySelector("body") || fragment.firstElementChild || fragment;
    if (root.hasAttribute(":data")) root.removeAttribute(":data");
    await renderer.mount(fragment);

    let deleteCompletedBtn = fragment.querySelector(
      '[aria-label="Delete all completed tasks"]',
    );
    assert.ok(deleteCompletedBtn, "Delete Completed button should exist");
    assert.strictEqual(
      deleteCompletedBtn.style.display,
      "none",
      "Delete Completed button should be hidden when no completed tasks",
    );

    // 2. With completed tasks
    const tasksWithCompleted = [
      {
        id: "1",
        description: "Pending",
        status: "pending",
        attempts: 0,
        outcomes: [],
      },
      {
        id: "2",
        description: "Completed",
        status: "completed",
        attempts: 1,
        outcomes: [],
      },
    ];
    renderer = new Renderer(
      createInitialState({
        tasks: tasksWithCompleted,
        loading: false,
        completedCount: 1,
        filteredTasks: tasksWithCompleted,
      }),
    );
    fragment = await renderer.preprocessLocal(indexHtmlPath);
    root =
      fragment.querySelector("body") || fragment.firstElementChild || fragment;
    if (root.hasAttribute(":data")) root.removeAttribute(":data");
    await renderer.mount(fragment);

    deleteCompletedBtn = fragment.querySelector(
      '[aria-label="Delete all completed tasks"]',
    );
    assert.ok(deleteCompletedBtn, "Delete Completed button should exist");
    assert.strictEqual(
      deleteCompletedBtn.style.display,
      "",
      "Delete Completed button should be visible when there are completed tasks",
    );
  });

  test("run loop button disabled when loopRunning is true", async () => {
    const renderer = new Renderer(
      createInitialState({
        loopRunning: true,
        loading: false,
      }),
    );

    const fragment = await renderer.preprocessLocal(indexHtmlPath);
    const root =
      fragment.querySelector("body") || fragment.firstElementChild || fragment;
    if (root.hasAttribute(":data")) root.removeAttribute(":data");
    await renderer.mount(fragment);

    const runLoopBtn = fragment.querySelector('[aria-label="Run Loop"]');
    assert.ok(runLoopBtn, "Run Loop button should exist");
    assert.ok(
      runLoopBtn.hasAttribute("disabled"),
      "Run Loop button should be disabled when loopRunning is true",
    );
    assert.strictEqual(runLoopBtn.textContent.trim(), "Loop Running");
    assert.ok(
      runLoopBtn.classList.contains("bg-gray-400"),
      "Should have gray background when disabled",
    );

    // Test enabled state
    const renderer2 = new Renderer(
      createInitialState({
        loopRunning: false,
        loading: false,
      }),
    );

    const fragment2 = await renderer2.preprocessLocal(indexHtmlPath);
    const root2 =
      fragment2.querySelector("body") ||
      fragment2.firstElementChild ||
      fragment2;
    if (root2.hasAttribute(":data")) root2.removeAttribute(":data");
    await renderer2.mount(fragment2);

    const runLoopBtn2 = fragment2.querySelector('[aria-label="Run Loop"]');
    const isDisabled2 =
      runLoopBtn2.hasAttribute("disabled") &&
      runLoopBtn2.getAttribute("disabled") !== "false";
    assert.strictEqual(
      isDisabled2,
      false,
      "Run Loop button should not be disabled when loopRunning is false",
    );
    assert.strictEqual(runLoopBtn2.textContent.trim(), "Run Loop");
    assert.ok(
      runLoopBtn2.classList.contains("bg-indigo-600"),
      "Should have indigo background when enabled",
    );
  });

  test("renders outcomes but no add outcome form", async () => {
    const tasks = [
      {
        id: "123",
        description: "Task with outcomes",
        status: "pending",
        attempts: 1,
        outcomes: ["Outcome 1", "Outcome 2"],
      },
    ];

    const renderer = new Renderer(
      createInitialState({
        tasks,
        loading: false,
        expanded: { 123: true },
        filteredTasks: tasks,
      }),
    );

    const fragment = await renderer.preprocessLocal(indexHtmlPath);
    const root =
      fragment.querySelector("body") || fragment.firstElementChild || fragment;
    if (root.hasAttribute(":data")) root.removeAttribute(":data");
    await renderer.mount(fragment);

    const taskItem = fragment.querySelector('[role="listitem"]');
    const outcomesList = taskItem.querySelector("ul");
    assert.ok(outcomesList, "Outcomes list should be present");
    const outcomeItems = outcomesList.querySelectorAll("li");
    assert.strictEqual(outcomeItems.length, 2);
    assert.strictEqual(outcomeItems[0].textContent.trim(), "Outcome 1");
    assert.strictEqual(outcomeItems[1].textContent.trim(), "Outcome 2");

    const addOutcomeForm = taskItem.querySelector(
      'form[aria-label="Add outcome to task 123"]',
    );
    assert.ok(!addOutcomeForm, "Add outcome form should NOT be present");
  });

  test("hides edit button for completed tasks", async () => {
    const tasks = [
      {
        id: "123",
        description: "Completed Task",
        status: "completed",
        attempts: 1,
        outcomes: ["Success"],
      },
    ];

    const renderer = new Renderer(
      createInitialState({
        tasks,
        loading: false,
        filteredTasks: tasks,
      }),
    );

    const fragment = await renderer.preprocessLocal(indexHtmlPath);

    const root =
      fragment.querySelector("body") || fragment.firstElementChild || fragment;
    if (root.hasAttribute(":data")) {
      root.removeAttribute(":data");
    }

    await renderer.mount(fragment);

    const taskItem = fragment.querySelector('[role="listitem"]');
    const editButton = taskItem.querySelector('[aria-label="Edit Task"]');

    assert.ok(editButton, "Edit button should be present");
    assert.strictEqual(
      editButton.style.display,
      "none",
      "Edit button should be hidden for completed tasks",
    );
  });

  test("project context textarea attributes", async () => {
    const renderer = new Renderer(
      createInitialState({
        context: "Initial context",
        loading: false,
      }),
    );

    const fragment = await renderer.preprocessLocal(indexHtmlPath);

    const textarea = fragment.querySelector(
      'textarea[aria-label="Project context and guidelines"]',
    );
    assert.ok(textarea, "Project context textarea should exist");
    assert.strictEqual(textarea.getAttribute(":on:input"), "updateContext()");

    const root =
      fragment.querySelector("body") || fragment.firstElementChild || fragment;
    if (root.hasAttribute(":data")) root.removeAttribute(":data");
    await renderer.mount(fragment);

    assert.strictEqual(textarea.value, "Initial context");
  });
});
