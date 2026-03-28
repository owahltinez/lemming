import assert from 'node:assert';
import * as path from 'node:path';
import { describe, test } from 'node:test';
import { Renderer } from 'mancha';

const indexHtmlPath = path.join(process.cwd(), 'src/lemming/web/index.html');

const createInitialState = (overrides = {}) => {
  const tasks = overrides.tasks || [];
  const hideCompleted = overrides.hideCompleted || false;
  return {
    tasks,
    context: '',
    cwd: '/test/cwd',
    newTask: '',
    loading: true,
    agents: [],
    selectedAgent: 'gemini',
    envOverrides: [],
    runners: [],
    availableHooks: [],
    config: {
      retries: 3,
      runner: 'gemini',
      hooks: ['roadmap'],
    },
    folderPickerBreadcrumbs: [],
    folderPickerDirs: [],
    hideCompleted,
    toasts: [],
    expanded: {},
    trim: (s, l = 60) =>
      s && s.length > l ? `${s.substring(0, l - 3)}...` : s,
    formatDate: (ts) => (ts ? new Date(ts * 1000).toLocaleString() : ''),
    formatDuration: (seconds) => {
      if (!seconds) return '0s';
      if (seconds < 60) return `${Math.floor(seconds)}s`;
      const minutes = Math.floor(seconds / 60);
      const remainingSeconds = Math.floor(seconds % 60);
      return `${minutes}m ${remainingSeconds}s`;
    },
    formatTaskRunTime: function (task) {
      let total = task.run_time || 0;
      if (task.status === 'in_progress' && task.last_started_at) {
        total += Date.now() / 1000 - task.last_started_at;
      }
      return this.formatDuration(total);
    },
    filteredTasks: $computed(() => {
      const ts = [...tasks];
      ts.sort((a, b) => {
        const aDone = a.status === 'completed' || a.status === 'failed' ? 1 : 0;
        const bDone = b.status === 'completed' || b.status === 'failed' ? 1 : 0;
        if (aDone !== bDone) return aDone - bDone;

        const aTime = a.completed_at || a.created_at || 0;
        const bTime = b.completed_at || b.created_at || 0;
        if (aTime !== bTime) return bTime - aTime;

        return (b.index || 0) - (a.index || 0);
      });
      return ts.filter((t) => t.status !== 'completed' || !hideCompleted);
    }),
    ...overrides,
  };
};

const $computed = (fn) => fn();

describe('Lemming Web Dashboard', () => {
  test('tasks are sorted correctly in frontend', async () => {
    // Provide tasks in a "jumbled" order to test if the frontend sorts them.
    const tasks = [
      {
        id: 'c1',
        description: 'Oldest Completed',
        status: 'completed',
        completed_at: 1000,
        outcomes: [],
      },
      {
        id: 'p1',
        description: 'Pending 1',
        status: 'pending',
        created_at: 500,
        outcomes: [],
      },
      {
        id: 'r1',
        description: 'Running',
        status: 'in_progress',
        created_at: 1500,
        outcomes: [],
      },
      {
        id: 'c2',
        description: 'Newest Completed',
        status: 'completed',
        completed_at: 2000,
        outcomes: [],
      },
      {
        id: 'p2',
        description: 'Pending 2',
        status: 'pending',
        created_at: 3000,
        outcomes: [],
      },
    ];

    const renderer = new Renderer(
      createInitialState({
        tasks,
        loading: false,
      }),
    );

    const fragment = await renderer.preprocessLocal(indexHtmlPath);
    await renderer.mount(fragment);

    const taskItems = fragment.querySelectorAll('[role="listitem"]');
    assert.strictEqual(taskItems.length, 5);

    const descriptions = Array.from(taskItems).map((item) => {
      const p = item.querySelector('p');
      return p ? p.textContent.trim() : '';
    });

    // Check they are sorted:
    // 1. Pending 2 (p2, created_at 3000)
    // 2. Running (r1, created_at 1500)
    // 3. Pending 1 (p1, created_at 500)
    // 4. Newest Completed (c2, completed_at 2000)
    // 5. Oldest Completed (c1, completed_at 1000)
    assert.strictEqual(descriptions[0], 'Pending 2');
    assert.strictEqual(descriptions[1], 'Running');
    assert.strictEqual(descriptions[2], 'Pending 1');
    assert.strictEqual(descriptions[3], 'Newest Completed');
    assert.strictEqual(descriptions[4], 'Oldest Completed');
  });

  test('tasks sorting tie-breaker in frontend', async () => {
    // Tasks with same timestamps but different indices
    const tasks = [
      {
        id: 'a',
        description: 'Task A',
        status: 'pending',
        created_at: 1000,
        index: 0,
        outcomes: [],
      },
      {
        id: 'b',
        description: 'Task B',
        status: 'pending',
        created_at: 1000,
        index: 1,
        outcomes: [],
      },
      {
        id: 'c',
        description: 'Task C',
        status: 'pending',
        created_at: 1000,
        index: 2,
        outcomes: [],
      },
    ];

    const renderer = new Renderer(
      createInitialState({
        tasks,
        loading: false,
      }),
    );

    const fragment = await renderer.preprocessLocal(indexHtmlPath);
    await renderer.mount(fragment);

    const taskItems = fragment.querySelectorAll('[role="listitem"]');
    const descriptions = Array.from(taskItems).map((item) => {
      const p = item.querySelector('p');
      return p ? p.textContent.trim() : '';
    });

    // Should be C, B, A (reverse by index)
    assert.strictEqual(descriptions[0], 'Task C');
    assert.strictEqual(descriptions[1], 'Task B');
    assert.strictEqual(descriptions[2], 'Task A');
  });

  test('initial state', async () => {
    const renderer = new Renderer(createInitialState({ loading: false }));

    const fragment = await renderer.preprocessLocal(indexHtmlPath);

    // Ensure :data on body doesn't overwrite our test state
    const root =
      fragment.querySelector('body') || fragment.firstElementChild || fragment;
    if (root.hasAttribute(':data')) root.removeAttribute(':data');
    if (root.hasAttribute(':render')) root.removeAttribute(':render');

    await renderer.mount(fragment);

    const heading = fragment.querySelector('h1');
    assert.strictEqual(heading.textContent.trim(), 'Lemming Task Runner');

    const noTasks = fragment.querySelector('[role="status"]');
    assert.ok(noTasks, "Should show 'No tasks yet'");
    assert.ok(noTasks.textContent.includes('No tasks yet'));

    // Find the "Project Directory" label and verify the path display and file browser link.
    const labels = Array.from(fragment.querySelectorAll('label'));
    const cwdLabel = labels.find((l) =>
      l.textContent.includes('Project Directory'),
    );
    assert.ok(cwdLabel, 'Project Directory label should exist');
    const filesLink = cwdLabel
      .closest('div')
      .querySelector('a[title="Browse files"]');
    assert.ok(filesLink, 'Browse files link should exist');
    assert.strictEqual(filesLink.getAttribute('href'), '/files/');
    assert.strictEqual(filesLink.getAttribute('target'), '_blank');
    const cwdSpan = cwdLabel
      .closest('div')
      .parentElement.querySelector('span.font-mono');
    assert.ok(cwdSpan, 'CWD span should exist');
    assert.strictEqual(cwdSpan.textContent.trim(), '/test/cwd');
  });

  test('renders tasks correctly', async () => {
    const tasks = [
      {
        id: '123',
        description: 'Test Task 1',
        status: 'pending',
        attempts: 0,
        outcomes: [],
      },
      {
        id: '456',
        description: 'Test Task 2',
        status: 'completed',
        attempts: 1,
        outcomes: ['Success'],
      },
    ];

    const renderer = new Renderer(
      createInitialState({
        tasks,
        context: 'Test context',
        loading: false,
        filteredTasks: tasks,
      }),
    );

    const fragment = await renderer.preprocessLocal(indexHtmlPath);

    // Ensure :data on body doesn't overwrite our test state
    const root =
      fragment.querySelector('body') || fragment.firstElementChild || fragment;
    if (root.hasAttribute(':data')) root.removeAttribute(':data');
    if (root.hasAttribute(':render')) root.removeAttribute(':render');

    await renderer.mount(fragment);

    const taskItems = fragment.querySelectorAll('[role="listitem"]');
    assert.strictEqual(taskItems.length, 2);

    const descriptions = Array.from(taskItems).map((item) => {
      const p = item.querySelector('p');
      return p ? p.textContent.trim() : '';
    });
    assert.ok(descriptions.includes('Test Task 1'));
    assert.ok(descriptions.includes('Test Task 2'));
  });

  test('hides completed tasks when hideCompleted is true', async () => {
    const tasks = [
      {
        id: '123',
        description: 'Pending Task',
        status: 'pending',
        attempts: 0,
        outcomes: [],
      },
      {
        id: '456',
        description: 'Completed Task',
        status: 'completed',
        attempts: 1,
        outcomes: ['Success'],
      },
    ];

    const renderer = new Renderer(
      createInitialState({
        tasks,
        hideCompleted: true,
        loading: false,
        filteredTasks: tasks.filter((t) => t.status !== 'completed'),
      }),
    );

    const fragment = await renderer.preprocessLocal(indexHtmlPath);

    const root =
      fragment.querySelector('body') || fragment.firstElementChild || fragment;
    if (root.hasAttribute(':data')) root.removeAttribute(':data');
    if (root.hasAttribute(':render')) root.removeAttribute(':render');

    await renderer.mount(fragment);

    const taskItems = fragment.querySelectorAll('[role="listitem"]');
    assert.strictEqual(taskItems.length, 1);
    assert.ok(taskItems[0].textContent.includes('Pending Task'));
    assert.ok(!taskItems[0].textContent.includes('Completed Task'));
  });

  test('shows only stop button for in_progress tasks', async () => {
    const tasks = [
      {
        id: '123',
        description: 'Running Task',
        status: 'in_progress',
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
      fragment.querySelector('body') || fragment.firstElementChild || fragment;
    if (root.hasAttribute(':data')) root.removeAttribute(':data');
    if (root.hasAttribute(':render')) root.removeAttribute(':render');

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

    assert.ok(expandButton, 'Expand button should be present');
    assert.strictEqual(
      expandButton.style.display,
      '',
      'Expand button should be visible',
    );

    assert.ok(cancelButton, 'Cancel button should be present');
    assert.strictEqual(
      cancelButton.style.display,
      '',
      'Cancel button should be visible',
    );

    assert.ok(editButton, 'Edit button should be present');
    assert.strictEqual(
      editButton.style.display,
      'none',
      'Edit button should be hidden',
    );

    assert.ok(uncompleteButton, 'Uncomplete button should be present');
    assert.strictEqual(
      uncompleteButton.style.display,
      'none',
      'Uncomplete button should be hidden',
    );

    assert.ok(deleteButton, 'Delete button should be present');
    assert.strictEqual(
      deleteButton.style.display,
      'none',
      'Delete button should be hidden',
    );
  });

  test('shows long descriptions in queue and full in details', async () => {
    const longDescription =
      'This is a very long task description that should occupy as much space as possible in the queue list but show fully when expanded and should be ellipsized properly by CSS.';
    const tasks = [
      {
        id: '123',
        description: longDescription,
        status: 'pending',
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
      fragment.querySelector('body') || fragment.firstElementChild || fragment;
    if (root.hasAttribute(':data')) root.removeAttribute(':data');
    if (root.hasAttribute(':render')) root.removeAttribute(':render');

    await renderer.mount(fragment);

    const taskItem = fragment.querySelector('[role="listitem"]');
    const leftSide = taskItem.querySelector(
      '.flex.items-center.gap-2.overflow-hidden',
    );
    assert.ok(
      leftSide.classList.contains('flex-grow'),
      'Left side should have flex-grow',
    );
    assert.ok(
      leftSide.classList.contains('min-w-0'),
      'Left side should have min-w-0',
    );

    const descriptionWrapper = leftSide.querySelector(
      '.overflow-hidden.flex-grow.min-w-0',
    );
    assert.ok(
      descriptionWrapper,
      'Description wrapper should have flex-grow and min-w-0',
    );

    const p = descriptionWrapper.querySelector('p');

    // Should now contain the full description (truncation handled by CSS)
    assert.strictEqual(p.textContent.trim(), longDescription);
    assert.ok(p.classList.contains('truncate'), 'Should have truncate class');

    const details = taskItem.querySelector('.px-12.pb-3');
    const fullDescriptionDiv = details.querySelector('.mt-2.text-gray-700');
    assert.strictEqual(fullDescriptionDiv.textContent.trim(), longDescription);
  });

  test('delete completed button visibility', async () => {
    // 1. No completed tasks
    const tasksNoCompleted = [
      {
        id: '1',
        description: 'Pending',
        status: 'pending',
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
      fragment.querySelector('body') || fragment.firstElementChild || fragment;
    if (root.hasAttribute(':data')) root.removeAttribute(':data');
    if (root.hasAttribute(':render')) root.removeAttribute(':render');
    await renderer.mount(fragment);

    let deleteCompletedBtn = fragment.querySelector(
      '[aria-label="Delete all completed tasks"]',
    );
    assert.ok(deleteCompletedBtn, 'Delete Completed button should exist');
    assert.strictEqual(
      deleteCompletedBtn.style.display,
      'none',
      'Delete Completed button should be hidden when no completed tasks',
    );

    // 2. With completed tasks
    const tasksWithCompleted = [
      {
        id: '1',
        description: 'Pending',
        status: 'pending',
        attempts: 0,
        outcomes: [],
      },
      {
        id: '2',
        description: 'Completed',
        status: 'completed',
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
      fragment.querySelector('body') || fragment.firstElementChild || fragment;
    if (root.hasAttribute(':data')) root.removeAttribute(':data');
    if (root.hasAttribute(':render')) root.removeAttribute(':render');
    await renderer.mount(fragment);

    deleteCompletedBtn = fragment.querySelector(
      '[aria-label="Delete all completed tasks"]',
    );
    assert.ok(deleteCompletedBtn, 'Delete Completed button should exist');
    assert.strictEqual(
      deleteCompletedBtn.style.display,
      '',
      'Delete Completed button should be visible when there are completed tasks',
    );
  });

  test('run loop button disabled when loopRunning is true', async () => {
    const renderer = new Renderer(
      createInitialState({
        loopRunning: true,
        loading: false,
      }),
    );

    const fragment = await renderer.preprocessLocal(indexHtmlPath);
    const root =
      fragment.querySelector('body') || fragment.firstElementChild || fragment;
    await renderer.mount(fragment);

    const runLoopBtn = fragment.querySelector('[aria-label="Execute Tasks"]');
    assert.ok(runLoopBtn, 'Execute Tasks button should exist');
    assert.ok(
      runLoopBtn.hasAttribute('disabled'),
      'Execute Tasks button should be disabled when loopRunning is true',
    );
    assert.strictEqual(runLoopBtn.textContent.trim(), 'Executing...');
    assert.ok(
      runLoopBtn.classList.contains('bg-gray-400'),
      'Should have gray background when disabled',
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
      fragment2.querySelector('body') ||
      fragment2.firstElementChild ||
      fragment2;
    if (root2.hasAttribute(':data')) root2.removeAttribute(':data');
    await renderer2.mount(fragment2);

    const runLoopBtn2 = fragment2.querySelector('[aria-label="Execute Tasks"]');
    const isDisabled2 =
      runLoopBtn2.hasAttribute('disabled') &&
      runLoopBtn2.getAttribute('disabled') !== 'false';
    assert.strictEqual(
      isDisabled2,
      false,
      'Execute Tasks button should not be disabled when loopRunning is false',
    );
    assert.strictEqual(runLoopBtn2.textContent.trim(), 'Execute Tasks');
    assert.ok(
      runLoopBtn2.classList.contains('bg-indigo-600'),
      'Should have indigo background when enabled',
    );
  });

  test('renders outcomes with whitespace-pre-wrap class', async () => {
    const initialState = createInitialState({
      tasks: [
        {
          id: '123',
          description: 'Task with outcomes',
          status: 'completed',
          outcomes: ['Outcome 1', 'Outcome 2'],
        },
      ],
    });

    const renderer = new Renderer(initialState);
    const fragment = await renderer.preprocessLocal(indexHtmlPath);

    const root =
      fragment.querySelector('body') || fragment.firstElementChild || fragment;
    if (root.hasAttribute(':data')) root.removeAttribute(':data');
    if (root.hasAttribute(':render')) root.removeAttribute(':render');

    await renderer.mount(fragment);

    const taskItem = fragment.querySelector('[role="listitem"]');
    const outcomesList = taskItem.querySelector('ul');
    const outcomeItems = outcomesList.querySelectorAll('li');
    assert.strictEqual(outcomeItems.length, 2);
    assert.ok(
      outcomeItems[0].classList.contains('whitespace-pre-wrap'),
      'First outcome should have whitespace-pre-wrap class',
    );
    assert.ok(
      outcomeItems[1].classList.contains('whitespace-pre-wrap'),
      'Second outcome should have whitespace-pre-wrap class',
    );
  });

  test('renders outcomes but no add outcome form', async () => {
    const tasks = [
      {
        id: '123',
        description: 'Task with outcomes',
        status: 'pending',
        attempts: 1,
        outcomes: ['Outcome 1', 'Outcome 2'],
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
      fragment.querySelector('body') || fragment.firstElementChild || fragment;
    await renderer.mount(fragment);

    const taskItem = fragment.querySelector('[role="listitem"]');
    const outcomesList = taskItem.querySelector('ul');
    assert.ok(outcomesList, 'Outcomes list should be present');
    const outcomeItems = outcomesList.querySelectorAll('li');
    assert.strictEqual(outcomeItems.length, 2);
    assert.strictEqual(outcomeItems[0].textContent.trim(), 'Outcome 1');
    assert.strictEqual(outcomeItems[1].textContent.trim(), 'Outcome 2');

    const addOutcomeForm = taskItem.querySelector(
      'form[aria-label="Add outcome to task 123"]',
    );
    assert.ok(!addOutcomeForm, 'Add outcome form should NOT be present');
  });

  test('hides outcomes section when no outcomes are present', async () => {
    const tasks = [
      {
        id: '123',
        description: 'Task with no outcomes',
        status: 'pending',
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
      fragment.querySelector('body') || fragment.firstElementChild || fragment;
    await renderer.mount(fragment);

    const taskItem = fragment.querySelector('[role="listitem"]');
    const outcomesHeader = Array.from(taskItem.querySelectorAll('span')).find(
      (s) => s.textContent.trim() === 'Outcomes:',
    );
    if (outcomesHeader) {
      const outcomesDiv = outcomesHeader.parentElement;
      assert.strictEqual(
        outcomesDiv.style.display,
        'none',
        'Outcomes section should be hidden',
      );
    }
  });

  test('hides edit button for completed tasks', async () => {
    const tasks = [
      {
        id: '123',
        description: 'Completed Task',
        status: 'completed',
        attempts: 1,
        outcomes: ['Success'],
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
      fragment.querySelector('body') || fragment.firstElementChild || fragment;
    if (root.hasAttribute(':data')) root.removeAttribute(':data');
    if (root.hasAttribute(':render')) root.removeAttribute(':render');

    await renderer.mount(fragment);

    const taskItem = fragment.querySelector('[role="listitem"]');
    const editButton = taskItem.querySelector('[aria-label="Edit Task"]');

    assert.ok(editButton, 'Edit button should be present');
    assert.strictEqual(
      editButton.style.display,
      'none',
      'Edit button should be hidden for completed tasks',
    );
  });

  test('project context textarea attributes', async () => {
    const renderer = new Renderer(
      createInitialState({
        context: 'Initial context',
        loading: false,
      }),
    );

    const fragment = await renderer.preprocessLocal(indexHtmlPath);

    const textarea = fragment.querySelector(
      'textarea[aria-label="Project context and guidelines"]',
    );
    assert.ok(textarea, 'Project context textarea should exist');
    assert.strictEqual(textarea.getAttribute(':on:input'), 'updateContext()');

    const root =
      fragment.querySelector('body') || fragment.firstElementChild || fragment;
    await renderer.mount(fragment);

    assert.strictEqual(textarea.value, 'Initial context');
  });

  test('status chip colors and labels', async () => {
    const tasks = [
      {
        id: 'r1',
        description: 'Running Task',
        status: 'in_progress',
        attempts: 1,
        outcomes: [],
      },
      {
        id: 'p1',
        description: 'Pending Task',
        status: 'pending',
        attempts: 0,
        outcomes: [],
      },
      {
        id: 'f1',
        description: 'Terminal Failed Task',
        status: 'failed',
        attempts: 1,
        outcomes: ['Fatal error'],
      },
      {
        id: 'f2',
        description: 'Retriable Failed Task',
        status: 'pending',
        attempts: 1,
        outcomes: ['Temporary error'],
      },
      {
        id: 'c1',
        description: 'Completed Task',
        status: 'completed',
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
      fragment.querySelector('body') || fragment.firstElementChild || fragment;
    await renderer.mount(fragment);

    const taskItems = fragment.querySelectorAll('[role="listitem"]');
    assert.strictEqual(taskItems.length, 5);

    // ... (rest of tests)

    // 3. Terminal Failed Task
    const failedChip = taskItems[2].querySelector('[role="status"]');
    assert.strictEqual(failedChip.textContent.trim(), 'Failed');
    assert.ok(failedChip.classList.contains('bg-red-100'));
    assert.ok(failedChip.classList.contains('text-red-700'));
    const failedUncompleteBtn = taskItems[2].querySelector(
      '[aria-label="Mark as Pending"]',
    );
    assert.ok(
      failedUncompleteBtn,
      'Uncomplete button should be present for failed task',
    );
    assert.strictEqual(
      failedUncompleteBtn.style.display,
      '',
      'Uncomplete button should be visible for failed task',
    );

    // 4. Retriable Failed Task (pending with attempts)
    const retriableChip = taskItems[3].querySelector('[role="status"]');
    assert.strictEqual(retriableChip.textContent.trim(), 'Failed');
    assert.ok(retriableChip.classList.contains('bg-red-100'));
    const retriableUncompleteBtn = taskItems[3].querySelector(
      '[aria-label="Mark as Pending"]',
    );
    assert.strictEqual(
      retriableUncompleteBtn.style.display,
      'none',
      'Uncomplete button should be hidden for retriable failed task',
    );

    // 5. Completed Task
    const completedChip = taskItems[4].querySelector('[role="status"]');
    assert.strictEqual(completedChip.textContent.trim(), 'Completed');
    assert.ok(completedChip.classList.contains('bg-green-100'));
    assert.ok(completedChip.classList.contains('text-green-700'));
    const completedUncompleteBtn = taskItems[4].querySelector(
      '[aria-label="Mark as Pending"]',
    );
    assert.strictEqual(
      completedUncompleteBtn.style.display,
      '',
      'Uncomplete button should be visible for completed task',
    );
  });

  test('displays real-time run time for in-progress tasks', async () => {
    const now = Date.now() / 1000;
    const tasks = [
      {
        id: 'r1',
        description: 'Running Task',
        status: 'in_progress',
        attempts: 1,
        run_time: 50.0,
        last_started_at: now - 20, // Started 20 seconds ago
        outcomes: [],
      },
    ];

    const renderer = new Renderer(
      createInitialState({
        tasks,
        loading: false,
        expanded: { r1: true },
        filteredTasks: tasks,
      }),
    );

    const fragment = await renderer.preprocessLocal(indexHtmlPath);
    const root =
      fragment.querySelector('body') || fragment.firstElementChild || fragment;
    await renderer.mount(fragment);

    const taskItem = fragment.querySelector('[role="listitem"]');
    const runTimeValue = Array.from(taskItem.querySelectorAll('span')).find(
      (s) => s.previousElementSibling?.textContent?.includes('Run Time:'),
    );
    assert.ok(runTimeValue, 'Run Time value should be present');
    // Should be around 70s (50 + 20)
    assert.ok(runTimeValue.textContent.includes('1m 10s'));
  });

  test('displays started at for in-progress tasks and completed at for completed tasks', async () => {
    const now = Date.now() / 1000;
    const tasks = [
      {
        id: 'r1',
        description: 'Running Task',
        status: 'in_progress',
        attempts: 1,
        started_at: now - 3600, // Started 1 hour ago
        outcomes: [],
      },
      {
        id: 'c1',
        description: 'Completed Task',
        status: 'completed',
        completed_at: now - 1800, // Completed 30 mins ago
        outcomes: [],
      },
    ];

    const renderer = new Renderer(
      createInitialState({
        tasks,
        loading: false,
        expanded: { r1: true, c1: true },
        filteredTasks: tasks,
      }),
    );

    const fragment = await renderer.preprocessLocal(indexHtmlPath);
    const root =
      fragment.querySelector('body') || fragment.firstElementChild || fragment;
    if (root.hasAttribute(':data')) root.removeAttribute(':data');
    if (root.hasAttribute(':render')) root.removeAttribute(':render');

    await renderer.mount(fragment);

    const taskItems = fragment.querySelectorAll('[role="listitem"]');
    assert.strictEqual(taskItems.length, 2);

    // 1. Running Task
    const runningDetails = taskItems[0].querySelector('.px-12.pb-3');
    const startedAtLabel = Array.from(
      runningDetails.querySelectorAll('span'),
    ).find((s) => s.textContent.trim() === 'Started At:');
    assert.ok(
      startedAtLabel,
      'Started At label should be present for in-progress task',
    );
    const startedAtValue = startedAtLabel.nextElementSibling;
    assert.ok(
      startedAtValue.textContent.trim().length > 0,
      'Started At value should not be empty',
    );

    const completedAtLabelR = Array.from(
      runningDetails.querySelectorAll('span'),
    ).find((s) => s.textContent.trim() === 'Completed At:');
    assert.ok(
      !completedAtLabelR ||
        completedAtLabelR.parentElement.style.display === 'none',
      'Completed At label should NOT be present for in-progress task',
    );

    // 2. Completed Task
    const completedDetails = taskItems[1].querySelector('.px-12.pb-3');
    const completedAtLabel = Array.from(
      completedDetails.querySelectorAll('span'),
    ).find((s) => s.textContent.trim() === 'Completed At:');
    assert.ok(
      completedAtLabel,
      'Completed At label should be present for completed task',
    );
    const completedAtValue = completedAtLabel.nextElementSibling;
    assert.ok(
      completedAtValue.textContent.trim().length > 0,
      'Completed At value should not be empty',
    );

    const startedAtLabelC = Array.from(
      completedDetails.querySelectorAll('span'),
    ).find((s) => s.textContent.trim() === 'Started At:');
    assert.ok(
      !startedAtLabelC ||
        startedAtLabelC.parentElement.style.display === 'none',
      'Started At label should NOT be present for completed task',
    );
  });

  test('renders copy task id button', async () => {
    const tasks = [
      { id: '123', description: 'Test', status: 'pending', outcomes: [] },
    ];
    const renderer = new Renderer(
      createInitialState({
        tasks,
        loading: false,
      }),
    );
    const fragment = await renderer.preprocessLocal(indexHtmlPath);
    await renderer.mount(fragment);

    const copyBtn = fragment.querySelector('[aria-label="Copy Task ID"]');
    assert.ok(copyBtn, 'Copy Task ID button should exist');
  });
});
