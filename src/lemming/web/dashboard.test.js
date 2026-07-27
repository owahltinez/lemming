import assert from 'node:assert';
import * as path from 'node:path';
import { describe, test } from 'node:test';
import { Renderer } from 'mancha';

const indexHtmlPath = path.join(process.cwd(), 'src/lemming/web/index.html');

const createInitialState = (overrides = {}) => {
  const tasks = overrides.tasks || [];
  const hideCompleted = overrides.hideCompleted || false;
  const isHistoryTask = (task) =>
    ['completed', 'failed', 'cancelled', 'superseded'].includes(task.status);
  return {
    tasks,
    goal: '',
    cwd: '/test/cwd',
    newTask: '',
    loading: true,
    agents: [],
    selectedAgent: 'agy',
    envOverrides: [],
    runners: [],
    availableHooks: [],
    config: {
      retries: 3,
      runner: 'agy',
    },
    folderPickerBreadcrumbs: [],
    folderPickerDirs: [],
    hideCompleted,
    isHistoryTask,
    getTaskStatusLabel: (task) => {
      if (task.status === 'completed') return 'Completed';
      if (task.status === 'failed') return 'Failed';
      if (task.status === 'cancelled') return 'Cancelled';
      if (task.status === 'superseded') return 'Superseded';
      if (task.status === 'in_progress') {
        return task.requested_status ? 'Finalizing' : 'Running';
      }
      return task.attempts > 0 ? 'Retrying' : 'Pending';
    },
    getTaskStatusClass: (task) => {
      if (task.status === 'completed') return 'bg-green-100 text-green-700';
      if (task.status === 'failed') return 'bg-red-100 text-red-700';
      if (task.status === 'cancelled') return 'bg-orange-100 text-orange-700';
      if (task.status === 'superseded') {
        return 'bg-purple-100 text-purple-700';
      }
      if (task.status === 'in_progress') {
        return 'bg-blue-100 text-blue-700 animate-pulse';
      }
      return task.attempts > 0
        ? 'bg-amber-100 text-amber-700'
        : 'bg-gray-100 text-gray-500';
    },
    getTaskTextClass: (task) => {
      if (task.status === 'completed') return 'text-gray-400 line-through';
      if (task.status === 'cancelled') return 'text-orange-600 line-through';
      if (task.status === 'superseded') return 'text-purple-600';
      if (task.status === 'failed') return 'text-red-600';
      if (task.status === 'in_progress') return 'text-blue-600';
      return task.attempts > 0 ? 'text-amber-600' : '';
    },
    toasts: [],
    expanded: [],
    timeLimit: '3600',
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
    getExecutionSegments: (task) => {
      const hookColors = [
        '#059669',
        '#d97706',
        '#db2777',
        '#0891b2',
        '#7c3aed',
        '#dc2626',
      ];
      const hookColorClasses = [
        'bg-green-600',
        'bg-amber-600',
        'bg-pink-600',
        'bg-cyan-600',
        'bg-purple-600',
        'bg-red-600',
      ];
      const executionTimes = { ...(task.execution_times || {}) };
      if (
        task.status === 'in_progress' &&
        task.active_execution_component &&
        task.active_execution_started_at
      ) {
        const activeDuration = Math.max(
          0,
          Date.now() / 1000 - task.active_execution_started_at,
        );
        executionTimes[task.active_execution_component] =
          Number(executionTimes[task.active_execution_component] || 0) +
          activeDuration;
      }
      const entries = Object.entries(executionTimes).filter(
        ([, duration]) => Number.isFinite(Number(duration)) && duration > 0,
      );
      entries.sort(([a], [b]) => {
        if (a === 'runner') return -1;
        if (b === 'runner') return 1;
        return 0;
      });
      const total = entries.reduce(
        (sum, [, duration]) => sum + Number(duration),
        0,
      );
      const usedHookColors = new Set();

      return entries.map(([key, duration], index) => {
        const label = key === 'runner' ? 'Runner' : key.replace(/^hook:/, '');
        let hash = 0;
        for (const char of key) hash = (hash * 31 + char.charCodeAt(0)) | 0;
        let color = '#4f46e5';
        let colorClass = 'bg-indigo-600';
        if (key !== 'runner') {
          let colorIndex = Math.abs(hash || index) % hookColors.length;
          while (
            usedHookColors.has(colorIndex) &&
            usedHookColors.size < hookColors.length
          ) {
            colorIndex = (colorIndex + 1) % hookColors.length;
          }
          usedHookColors.add(colorIndex);
          color = hookColors[colorIndex];
          colorClass = hookColorClasses[colorIndex];
        }
        return {
          key,
          label,
          duration: Number(duration),
          percent: (Number(duration) / total) * 100,
          color,
          colorClass,
        };
      });
    },
    getExecutionSummary: function (task) {
      return this.getExecutionSegments(task)
        .map(
          (segment) =>
            `${segment.label} ${this.formatDuration(segment.duration)}`,
        )
        .join(', ');
    },
    getParent: (parentId) => tasks.find((t) => t.id === parentId),
    getChildren: (parentId) => tasks.filter((t) => t.parent === parentId),
    focusTask: () => {},
    supersededCount: tasks.filter((t) => t.status === 'superseded').length,
    historyCount: tasks.filter(isHistoryTask).length,
    filteredTasks: $computed(() => {
      const ts = [...tasks];
      ts.sort((a, b) => {
        const aDone = isHistoryTask(a) ? 1 : 0;
        const bDone = isHistoryTask(b) ? 1 : 0;
        if (aDone !== bDone) return aDone - bDone;

        if (!aDone) {
          const aInProgress = a.status === 'in_progress' ? 0 : 1;
          const bInProgress = b.status === 'in_progress' ? 0 : 1;
          if (aInProgress !== bInProgress) return aInProgress - bInProgress;

          if (a.index !== b.index) return (a.index || 0) - (b.index || 0);
          return (a.created_at || 0) - (b.created_at || 0);
        }

        const aTime = a.completed_at || a.superseded_at || a.created_at || 0;
        const bTime = b.completed_at || b.superseded_at || b.created_at || 0;
        if (aTime !== bTime) return bTime - aTime;

        return (b.index || 0) - (a.index || 0);
      });
      return ts.filter((t) => !isHistoryTask(t) || !hideCompleted);
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
        progress: [],
      },
      {
        id: 'p1',
        description: 'Pending 1',
        status: 'pending',
        created_at: 500,
        progress: [],
      },
      {
        id: 'r1',
        description: 'Running',
        status: 'in_progress',
        created_at: 1500,
        progress: [],
      },
      {
        id: 'c2',
        description: 'Newest Completed',
        status: 'completed',
        completed_at: 2000,
        progress: [],
      },
      {
        id: 'p2',
        description: 'Pending 2',
        status: 'pending',
        created_at: 3000,
        progress: [],
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
    // 1. Running (r1, in_progress)
    // 2. Pending 1 (p1, created_at 500)
    // 3. Pending 2 (p2, created_at 3000)
    // 4. Newest Completed (c2, completed_at 2000)
    // 5. Oldest Completed (c1, completed_at 1000)
    assert.strictEqual(descriptions[0], 'Running');
    assert.strictEqual(descriptions[1], 'Pending 1');
    assert.strictEqual(descriptions[2], 'Pending 2');
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
        progress: [],
      },
      {
        id: 'b',
        description: 'Task B',
        status: 'pending',
        created_at: 1000,
        index: 1,
        progress: [],
      },
      {
        id: 'c',
        description: 'Task C',
        status: 'pending',
        created_at: 1000,
        index: 2,
        progress: [],
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

    // Should be A, B, C (FIFO by index)
    assert.strictEqual(descriptions[0], 'Task A');
    assert.strictEqual(descriptions[1], 'Task B');
    assert.strictEqual(descriptions[2], 'Task C');
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
        progress: [],
      },
      {
        id: '456',
        description: 'Test Task 2',
        status: 'completed',
        attempts: 1,
        progress: ['Success'],
      },
    ];

    const renderer = new Renderer(
      createInitialState({
        tasks,
        goal: 'Test goal',
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

  test('hides terminal task history when hideCompleted is true', async () => {
    const tasks = [
      {
        id: '123',
        description: 'Pending Task',
        status: 'pending',
        attempts: 0,
        progress: [],
      },
      {
        id: '456',
        description: 'Completed Task',
        status: 'completed',
        attempts: 1,
        progress: ['Success'],
      },
      {
        id: '789',
        description: 'Superseded Task',
        status: 'superseded',
        attempts: 1,
        progress: ['Partial work'],
      },
    ];

    const renderer = new Renderer(
      createInitialState({
        tasks,
        hideCompleted: true,
        loading: false,
        filteredTasks: tasks.filter(
          (t) => !['completed', 'superseded'].includes(t.status),
        ),
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
    assert.ok(!taskItems[0].textContent.includes('Superseded Task'));
  });

  test('renders superseded task lineage without failure styling', async () => {
    const tasks = [
      {
        id: 'parent1',
        description: 'Oversized implementation task',
        status: 'superseded',
        attempts: 2,
        superseded_at: 2000,
        superseded_reason: 'split after reaching the time limit',
        progress: ['Committed the completed foundation'],
      },
      {
        id: 'child1',
        description: 'Finish the smaller remaining slice',
        status: 'pending',
        attempts: 0,
        parent: 'parent1',
        progress: [],
      },
    ];
    const renderer = new Renderer(
      createInitialState({
        tasks,
        loading: false,
        expanded: { parent1: true, child1: true },
        filteredTasks: tasks,
      }),
    );

    const fragment = await renderer.preprocessLocal(indexHtmlPath);
    await renderer.mount(fragment);

    const parent = fragment.querySelector('[data-task-id="parent1"]');
    assert.ok(parent.textContent.includes('Superseded'));
    assert.ok(
      parent.textContent.includes('split after reaching the time limit'),
    );
    assert.ok(parent.querySelector('.bg-purple-100'));
    const replacements = parent.querySelector(
      '[data-testid="replacement-tasks"]',
    );
    assert.ok(replacements.textContent.includes('child1'));

    const child = fragment.querySelector('[data-task-id="child1"]');
    assert.ok(child.textContent.includes('parent1'));
    assert.ok(!parent.textContent.includes('Failed'));
  });

  test('shows only stop button for in_progress tasks', async () => {
    const tasks = [
      {
        id: '123',
        description: 'Running Task',
        status: 'in_progress',
        attempts: 1,
        progress: [],
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
    const taskActionsButton = taskItem.querySelector(
      '[aria-label="Task Actions"]',
    );

    assert.ok(expandButton, 'Expand button should be present');
    assert.strictEqual(
      expandButton.style.display,
      '',
      'Expand button should be visible',
    );

    assert.ok(taskActionsButton, 'Task Actions button should be present');
    assert.strictEqual(
      taskActionsButton.style.display,
      '',
      'Task Actions button should be visible',
    );

    // Inline edit and uncomplete buttons were consolidated into the task action modal
    const editButton = taskItem.querySelector('[aria-label="Edit Task"]');
    assert.ok(!editButton, 'Inline Edit button should not exist');

    const uncompleteButton = taskItem.querySelector(
      '[aria-label="Mark as Pending"]',
    );
    assert.ok(!uncompleteButton, 'Inline Uncomplete button should not exist');
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
        progress: [],
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
        progress: [],
      },
    ];
    let renderer = new Renderer(
      createInitialState({
        tasks: tasksNoCompleted,
        loading: false,
        runningCount: 0,
        pendingCount: 1,
        completedCount: 0,
        failedCount: 0,
        historyCount: 0,
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
      '[aria-label="Delete all task history"]',
    );
    assert.ok(deleteCompletedBtn, 'Delete History button should exist');
    assert.strictEqual(
      deleteCompletedBtn.style.display,
      'none',
      'Delete History button should be hidden when there is no history',
    );

    // 2. With completed tasks
    const tasksWithCompleted = [
      {
        id: '1',
        description: 'Pending',
        status: 'pending',
        attempts: 0,
        progress: [],
      },
      {
        id: '2',
        description: 'Completed',
        status: 'completed',
        attempts: 1,
        progress: [],
      },
    ];
    renderer = new Renderer(
      createInitialState({
        tasks: tasksWithCompleted,
        loading: false,
        runningCount: 0,
        pendingCount: 1,
        completedCount: 1,
        failedCount: 0,
        historyCount: 1,
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
      '[aria-label="Delete all task history"]',
    );
    assert.ok(deleteCompletedBtn, 'Delete History button should exist');
    assert.strictEqual(
      deleteCompletedBtn.style.display,
      '',
      'Delete History button should be visible when history exists',
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

  test('renders progress with whitespace-pre-wrap class', async () => {
    const initialState = createInitialState({
      tasks: [
        {
          id: '123',
          description: 'Task with progress',
          status: 'completed',
          progress: ['Progress 1', 'Progress 2'],
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
    const progressList = taskItem.querySelector('ul');
    const progressItems = progressList.querySelectorAll('li');
    assert.strictEqual(progressItems.length, 2);
    assert.ok(
      progressItems[0].classList.contains('whitespace-pre-wrap'),
      'First progress entry should have whitespace-pre-wrap class',
    );
    assert.ok(
      progressItems[1].classList.contains('whitespace-pre-wrap'),
      'Second progress entry should have whitespace-pre-wrap class',
    );
  });

  test('renders progress entries in expanded details', async () => {
    const tasks = [
      {
        id: '123',
        description: 'Task with progress',
        status: 'pending',
        attempts: 1,
        progress: ['Step 1 done', 'Step 2 done'],
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
    const progressList = taskItem.querySelector('ul');
    assert.ok(progressList, 'Progress list should be present');
    const progressItems = progressList.querySelectorAll('li');
    assert.strictEqual(progressItems.length, 2);
    assert.strictEqual(progressItems[0].textContent.trim(), 'Step 1 done');
    assert.strictEqual(progressItems[1].textContent.trim(), 'Step 2 done');
  });

  test('renders proportional runner and hook execution times', async () => {
    const tasks = [
      {
        id: 'timed',
        description: 'Task with execution timings',
        status: 'completed',
        attempts: 1,
        progress: [],
        execution_times: {
          runner: 30,
          'hook:readability': 10,
          'hook:testing': 20,
        },
      },
    ];
    const initialState = createInitialState({
      tasks,
      loading: false,
      expanded: { timed: true },
      filteredTasks: tasks,
    });
    const calculated = initialState.getExecutionSegments(tasks[0]);
    assert.deepStrictEqual(
      calculated.map((segment) => segment.duration),
      [30, 10, 20],
    );
    assert.strictEqual(calculated[0].percent, 50);
    assert.ok(Math.abs(calculated[1].percent - 100 / 6) < 0.001);
    assert.ok(Math.abs(calculated[2].percent - 100 / 3) < 0.001);
    assert.notStrictEqual(calculated[0].color, calculated[1].color);
    assert.strictEqual(
      initialState.getExecutionSegments({
        execution_times: { runner: 0.257 },
      })[0].percent,
      100,
    );

    const renderer = new Renderer(initialState);

    const fragment = await renderer.preprocessLocal(indexHtmlPath);
    await renderer.mount(fragment);

    const breakdown = fragment.querySelector(
      '[data-testid="execution-breakdown"]',
    );
    assert.ok(breakdown, 'Execution breakdown should be present');

    const bar = breakdown.querySelector('[role="img"]');
    assert.strictEqual(
      bar.getAttribute('aria-label'),
      'Execution time: Runner 30s, readability 10s, testing 20s',
    );

    assert.strictEqual(bar.tagName.toLowerCase(), 'div');
    const segments = bar.querySelectorAll(':scope > span');
    assert.strictEqual(segments.length, 3);
    assert.strictEqual(
      segments[0].getAttribute('style'),
      'width: 50%; background-color: #4f46e5',
    );
    assert.ok(breakdown.textContent.includes('Runner 30s'));
    assert.ok(breakdown.textContent.includes('readability 10s'));
    assert.ok(breakdown.textContent.includes('testing 20s'));
  });

  test('adds live elapsed time to the active execution segment', () => {
    const originalDateNow = Date.now;
    let now = 1_000_000;
    Date.now = () => now;
    try {
      const task = {
        status: 'in_progress',
        execution_times: { runner: 30 },
        active_execution_component: 'hook:testing',
        active_execution_started_at: 990,
      };
      const initialState = createInitialState();

      let segments = initialState.getExecutionSegments(task);
      assert.deepStrictEqual(
        segments.map(({ key, duration }) => [key, duration]),
        [
          ['runner', 30],
          ['hook:testing', 10],
        ],
      );

      now += 5_000;
      segments = initialState.getExecutionSegments(task);
      assert.strictEqual(segments[1].duration, 15);
      assert.ok(Math.abs(segments[1].percent - 100 / 3) < 0.001);
    } finally {
      Date.now = originalDateNow;
    }
  });

  test('hides execution breakdown when timing data is unavailable', async () => {
    const tasks = [
      {
        id: 'legacy',
        description: 'Older task without component timings',
        status: 'completed',
        attempts: 1,
        progress: [],
      },
    ];
    const renderer = new Renderer(
      createInitialState({
        tasks,
        loading: false,
        expanded: { legacy: true },
        filteredTasks: tasks,
      }),
    );

    const fragment = await renderer.preprocessLocal(indexHtmlPath);
    await renderer.mount(fragment);

    assert.strictEqual(
      fragment.querySelector('[data-testid="execution-breakdown"]'),
      null,
    );
  });

  test('hides progress section when no progress is present', async () => {
    const tasks = [
      {
        id: '123',
        description: 'Task with no progress',
        status: 'pending',
        attempts: 0,
        progress: [],
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
    const progressHeader = Array.from(taskItem.querySelectorAll('span')).find(
      (s) => s.textContent.trim() === 'Progress:',
    );
    if (progressHeader) {
      const progressDiv = progressHeader.parentElement;
      assert.strictEqual(
        progressDiv.style.display,
        'none',
        'Progress section should be hidden',
      );
    }
  });

  test('no inline edit button for completed tasks', async () => {
    const tasks = [
      {
        id: '123',
        description: 'Completed Task',
        status: 'completed',
        attempts: 1,
        progress: ['Success'],
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
    // Edit button was consolidated into the task action modal
    const editButton = taskItem.querySelector('[aria-label="Edit Task"]');
    assert.ok(!editButton, 'Inline Edit button should not exist');

    // Task Actions button should still be present
    const taskActionsButton = taskItem.querySelector(
      '[aria-label="Task Actions"]',
    );
    assert.ok(taskActionsButton, 'Task Actions button should be present');
  });

  test('long-term goal textarea attributes', async () => {
    const renderer = new Renderer(
      createInitialState({
        goal: 'Initial goal',
        loading: false,
      }),
    );

    const fragment = await renderer.preprocessLocal(indexHtmlPath);

    const textarea = fragment.querySelector(
      'textarea[aria-label="Long-term goal"]',
    );
    assert.ok(textarea, 'Long-term goal textarea should exist');
    assert.strictEqual(textarea.getAttribute(':on:input'), 'updateGoal()');

    const root =
      fragment.querySelector('body') || fragment.firstElementChild || fragment;
    await renderer.mount(fragment);

    assert.strictEqual(textarea.value, 'Initial goal');
  });

  test('status chip colors and labels', async () => {
    const tasks = [
      {
        id: 'r1',
        description: 'Running Task',
        status: 'in_progress',
        attempts: 1,
        progress: [],
      },
      {
        id: 'p1',
        description: 'Pending Task',
        status: 'pending',
        attempts: 0,
        progress: [],
      },
      {
        id: 'f1',
        description: 'Terminal Failed Task',
        status: 'failed',
        attempts: 1,
        progress: ['Fatal error'],
      },
      {
        id: 'f2',
        description: 'Retriable Failed Task',
        status: 'pending',
        attempts: 1,
        progress: ['Temporary error'],
      },
      {
        id: 'c1',
        description: 'Completed Task',
        status: 'completed',
        attempts: 1,
        progress: [],
      },
      {
        id: 's1',
        description: 'Superseded Task',
        status: 'superseded',
        attempts: 1,
        progress: ['Partial implementation retained'],
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
    assert.strictEqual(taskItems.length, 6);

    // 1. Running Task
    const runningChip = taskItems[0].querySelector('[role="status"]');
    assert.strictEqual(runningChip.textContent.trim(), 'Running');
    assert.ok(runningChip.classList.contains('bg-blue-100'));
    assert.ok(runningChip.classList.contains('text-blue-700'));

    // 2. Pending Task
    const pendingChip = taskItems[1].querySelector('[role="status"]');
    assert.strictEqual(pendingChip.textContent.trim(), 'Pending');
    assert.ok(pendingChip.classList.contains('bg-gray-100'));
    assert.ok(pendingChip.classList.contains('text-gray-500'));

    // 3. Terminal Failed Task
    const failedChip = taskItems[2].querySelector('[role="status"]');
    assert.strictEqual(failedChip.textContent.trim(), 'Failed');
    assert.ok(failedChip.classList.contains('bg-red-100'));
    assert.ok(failedChip.classList.contains('text-red-700'));

    // 4. Retriable Failed Task (pending with attempts)
    const retriableChip = taskItems[3].querySelector('[role="status"]');
    assert.strictEqual(retriableChip.textContent.trim(), 'Retrying');
    assert.ok(retriableChip.classList.contains('bg-amber-100'));

    // 5. Completed Task
    const completedChip = taskItems[4].querySelector('[role="status"]');
    assert.strictEqual(completedChip.textContent.trim(), 'Completed');
    assert.ok(completedChip.classList.contains('bg-green-100'));
    assert.ok(completedChip.classList.contains('text-green-700'));

    // 6. Superseded Task
    const supersededChip = taskItems[5].querySelector('[role="status"]');
    assert.strictEqual(supersededChip.textContent.trim(), 'Superseded');
    assert.ok(supersededChip.classList.contains('bg-purple-100'));
    assert.ok(supersededChip.classList.contains('text-purple-700'));
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
        progress: [],
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
        progress: [],
      },
      {
        id: 'c1',
        description: 'Completed Task',
        status: 'completed',
        completed_at: now - 1800, // Completed 30 mins ago
        progress: [],
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
      { id: '123', description: 'Test', status: 'pending', progress: [] },
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
