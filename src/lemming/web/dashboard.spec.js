import * as fs from 'node:fs';
import * as path from 'node:path';
import { expect, test } from '@playwright/test';

const indexHtmlPath = path.resolve(process.cwd(), 'src/lemming/web/index.html');

test.describe('Dashboard E2E', () => {
  test.beforeEach(async ({ context }) => {
    // Serve the HTML file and static assets over mocked URLs
    await context.route('**/*', async (route) => {
      const url = route.request().url();
      if (
        url === 'http://localhost:8000/' ||
        url.startsWith('http://localhost:8000/?')
      ) {
        await route.fulfill({
          contentType: 'text/html',
          body: fs.readFileSync(indexHtmlPath, 'utf8'),
        });
      } else if (url.endsWith('/static/mancha.js')) {
        await route.fulfill({
          contentType: 'application/javascript',
          body: fs.readFileSync(
            path.resolve(process.cwd(), 'src/lemming/web/mancha.js'),
            'utf8',
          ),
        });
      } else if (url.endsWith('/static/favicon.js')) {
        await route.fulfill({
          contentType: 'application/javascript',
          body: fs.readFileSync(
            path.resolve(process.cwd(), 'src/lemming/web/favicon.js'),
            'utf8',
          ),
        });
      } else if (url.endsWith('/static/index.js')) {
        await route.fulfill({
          contentType: 'application/javascript',
          body: fs.readFileSync(
            path.resolve(process.cwd(), 'src/lemming/web/index.js'),
            'utf8',
          ),
          headers: { 'Access-Control-Allow-Origin': '*' },
        });
      } else if (url.includes('/api/data')) {
        await route.fulfill({
          contentType: 'application/json',
          json: {
            cwd: '/mock/cwd',
            loop_running: false,
            tasks: [],
            goal: 'Mock goal',
          },
        });
      } else if (url.includes('/api/runners')) {
        await route.fulfill({
          contentType: 'application/json',
          json: ['agy', 'opencode'],
        });
      } else if (url.includes('/api/hooks')) {
        await route.fulfill({
          contentType: 'application/json',
          json: [
            {
              name: 'roadmap',
              priority: 90,
              source: 'built-in',
              masked: false,
              runs_on_failure: true,
            },
          ],
        });
      } else if (
        url.includes('/api/directories') &&
        route.request().method() === 'POST'
      ) {
        await route.fulfill({
          contentType: 'application/json',
          json: { name: 'new-folder', path: '/mock/cwd/new-folder' },
        });
      } else if (url.includes('/api/directories')) {
        await route.fulfill({
          contentType: 'application/json',
          json: {
            status: 'success',
            path: '/mock/cwd',
            directories: [{ name: 'subdir', path: '/mock/cwd/subdir' }],
          },
        });
      } else {
        await route.continue();
      }
    });
  });

  async function gotoAndAwaitMancha(page) {
    await page.goto('http://localhost:8000/');
    await page.evaluate(async () => {
      while (!window.ManchaApp) await new Promise((r) => setTimeout(r, 50));
      await window.ManchaApp;
    });
  }

  test('shows proportional runner and hook execution times', async ({
    page,
  }) => {
    const pageErrors = [];
    page.on('pageerror', (error) => pageErrors.push(error.message));
    await page.route('**/api/data', async (route) => {
      await route.fulfill({
        contentType: 'application/json',
        json: {
          cwd: '/mock/cwd',
          loop_running: false,
          goal: 'Timed project',
          tasks: [
            {
              id: 'timed',
              description: 'Task with timing breakdown',
              status: 'completed',
              attempts: 1,
              progress: [],
              execution_times: {
                runner: 30,
                'hook:readability': 10,
                'hook:testing': 20,
              },
            },
          ],
          config: { retries: 3, runner: 'agy', time_limit: 60 },
        },
      });
    });

    await gotoAndAwaitMancha(page);
    await page.getByRole('button', { name: 'Show details' }).click();

    const breakdown = page.getByTestId('execution-breakdown');
    await expect(breakdown).toBeVisible();
    await expect(
      breakdown.getByRole('img', { name: /Runner 30s/ }),
    ).toBeVisible();

    const measurements = await breakdown
      .getByRole('img')
      .locator(':scope > span')
      .evaluateAll((segments) =>
        segments.map((segment) => ({
          width: segment.getBoundingClientRect().width,
          color: segment.style.backgroundColor,
        })),
      );
    const totalWidth = measurements.reduce(
      (sum, segment) => sum + segment.width,
      0,
    );

    expect(pageErrors).toEqual([]);
    expect(measurements).toHaveLength(3);
    expect(measurements[0].width / totalWidth).toBeCloseTo(0.5, 2);
    expect(measurements[1].width / totalWidth).toBeCloseTo(1 / 6, 2);
    expect(measurements[2].width / totalWidth).toBeCloseTo(1 / 3, 2);
    expect(new Set(measurements.map((segment) => segment.color)).size).toBe(3);
  });

  test('advances the active execution segment while a task is running', async ({
    page,
  }) => {
    const activeStartedAt = Date.now() / 1000 - 5;
    await page.route('**/api/data', async (route) => {
      await route.fulfill({
        contentType: 'application/json',
        json: {
          cwd: '/mock/cwd',
          loop_running: true,
          goal: 'Live timing project',
          tasks: [
            {
              id: 'running',
              description: 'Task with live execution timing',
              status: 'in_progress',
              attempts: 1,
              progress: [],
              active_execution_component: 'runner',
              active_execution_started_at: activeStartedAt,
            },
          ],
          config: { retries: 3, runner: 'agy', time_limit: 60 },
        },
      });
    });

    await gotoAndAwaitMancha(page);
    await page.getByRole('button', { name: 'Show details' }).click();

    const timingBar = page.getByTestId('execution-breakdown').getByRole('img');
    const initialLabel = await timingBar.getAttribute('aria-label');
    await expect
      .poll(() => timingBar.getAttribute('aria-label'))
      .not.toBe(initialLabel);
  });

  // --- Environment Overrides Tests ---

  test('adds an environment override, stores it in localStorage, and sends it on run', async ({
    page,
  }) => {
    let runRequestPayload = null;
    await page.route('**/api/run', async (route) => {
      runRequestPayload = route.request().postDataJSON();
      await route.fulfill({
        contentType: 'application/json',
        json: { status: 'started' },
      });
    });

    await gotoAndAwaitMancha(page);
    await page.waitForLoadState('networkidle');

    await page.evaluate(() => localStorage.clear());
    await gotoAndAwaitMancha(page);

    const addButton = page.getByRole('button', { name: 'Add override' });
    await expect(addButton).toBeVisible();
    await addButton.click();

    const keyInput = page.getByPlaceholder('KEY (e.g. OPENAI_API_KEY)');
    const valueInput = page.getByPlaceholder('VALUE');

    await expect(keyInput).toBeVisible();
    await keyInput.fill('MY_MOCK_KEY');
    await valueInput.fill('MY_MOCK_VALUE');

    await page.waitForTimeout(600);

    const localStorageData = await page.evaluate(() =>
      localStorage.getItem('lemming[]_env_overrides'),
    );
    expect(localStorageData).toContain('MY_MOCK_KEY');
    expect(localStorageData).toContain('MY_MOCK_VALUE');

    const runResponsePromise = page.waitForResponse('**/api/run');
    await page.getByRole('button', { name: 'Execute Tasks' }).click();
    await runResponsePromise;

    expect(runRequestPayload).not.toBeNull();
    expect(runRequestPayload.env).toEqual({
      MY_MOCK_KEY: 'MY_MOCK_VALUE',
    });
  });

  test('different projects store different environment overrides', async ({
    page,
  }) => {
    // Project A
    await page.goto('http://localhost:8000/?project=ProjectA');
    await page.evaluate(async () => {
      while (!window.ManchaApp) await new Promise((r) => setTimeout(r, 50));
      await window.ManchaApp;
    });
    await page.waitForLoadState('networkidle');

    await page.getByRole('button', { name: 'Add override' }).click();
    await page.getByPlaceholder('KEY (e.g. OPENAI_API_KEY)').fill('KEY_A');
    await page.getByPlaceholder('VALUE').fill('VAL_A');
    await page.waitForTimeout(600); // debounce

    // Project B
    await page.goto('http://localhost:8000/?project=ProjectB');
    await page.evaluate(async () => {
      while (!window.ManchaApp) await new Promise((r) => setTimeout(r, 50));
      await window.ManchaApp;
    });
    await page.waitForLoadState('networkidle');

    await page.getByRole('button', { name: 'Add override' }).click();
    await page.getByPlaceholder('KEY (e.g. OPENAI_API_KEY)').fill('KEY_B');
    await page.getByPlaceholder('VALUE').fill('VAL_B');
    await page.waitForTimeout(600); // debounce

    // Verify Project B only has KEY_B
    const inputsB = page.getByPlaceholder('KEY (e.g. OPENAI_API_KEY)');
    await expect(inputsB).toHaveCount(1);
    await expect(inputsB).toHaveValue('KEY_B');

    // Go back to Project A
    await page.goto('http://localhost:8000/?project=ProjectA');
    await page.evaluate(async () => {
      while (!window.ManchaApp) await new Promise((r) => setTimeout(r, 50));
      await window.ManchaApp;
    });
    await page.waitForLoadState('networkidle');

    // Verify Project A still has KEY_A
    const inputsA = page.getByPlaceholder('KEY (e.g. OPENAI_API_KEY)');
    await expect(inputsA).toHaveCount(1);
    await expect(inputsA).toHaveValue('KEY_A');

    // Check localStorage keys directly
    const keys = await page.evaluate(() => Object.keys(localStorage));
    expect(keys).toContain('lemming[ProjectA]_env_overrides');
    expect(keys).toContain('lemming[ProjectB]_env_overrides');
  });

  test('removes an environment override', async ({ page }) => {
    await gotoAndAwaitMancha(page);
    await page.evaluate(() => localStorage.clear());
    await gotoAndAwaitMancha(page);

    const keyInputs = page.getByPlaceholder('KEY (e.g. OPENAI_API_KEY)');

    await page.getByRole('button', { name: 'Add override' }).click();
    await expect(keyInputs).toHaveCount(1);

    await page.getByRole('button', { name: 'Add override' }).click();
    await expect(keyInputs).toHaveCount(2);

    await keyInputs.nth(0).fill('KEY1');
    await keyInputs.nth(1).fill('KEY2');

    await page.waitForTimeout(500);

    const removeButtons = page.getByRole('button', { name: 'Remove override' });
    await removeButtons.nth(0).click();

    await expect(keyInputs).toHaveCount(1);
    await expect(keyInputs.nth(0)).toHaveValue('KEY2');

    await page.waitForTimeout(600);

    const localStorageData = await page.evaluate(() =>
      localStorage.getItem('lemming[]_env_overrides'),
    );
    expect(localStorageData).toContain('KEY2');
    expect(localStorageData).not.toContain('KEY1');
  });

  test('ignores empty keys when sending payload', async ({ page }) => {
    let runRequestPayload = null;
    await page.route('**/api/run', async (route) => {
      runRequestPayload = route.request().postDataJSON();
      await route.fulfill({
        contentType: 'application/json',
        json: { status: 'started' },
      });
    });

    await gotoAndAwaitMancha(page);
    await page.evaluate(() => localStorage.clear());
    await gotoAndAwaitMancha(page);

    await page.getByRole('button', { name: 'Add override' }).click();
    const keyInputs = page.getByPlaceholder('KEY (e.g. OPENAI_API_KEY)');
    await expect(keyInputs).toHaveCount(1);

    await page.waitForTimeout(600);

    const runResponsePromise = page.waitForResponse('**/api/run');
    await page.getByRole('button', { name: 'Execute Tasks' }).click();
    await runResponsePromise;

    expect(runRequestPayload).not.toBeNull();
    expect(runRequestPayload.env).toBeUndefined();
  });

  test('persists overrides across page reloads', async ({ page }) => {
    await gotoAndAwaitMancha(page);
    await page.evaluate(() => localStorage.clear());
    await gotoAndAwaitMancha(page);

    // Add override
    await page.getByRole('button', { name: 'Add override' }).click();
    await page.getByPlaceholder('KEY (e.g. OPENAI_API_KEY)').fill('TEST_KEY');
    await page.getByPlaceholder('VALUE').fill('TEST_VAL');

    // Wait for save debounce
    await page.waitForTimeout(600);

    // Reload page
    await page.reload();
    await page.waitForLoadState('networkidle');

    // Check that we're dealing with Mancha actually being ready
    await expect(
      page.getByRole('heading', { name: 'Lemming Task Runner' }),
    ).toBeVisible();

    // Give render time
    await page.waitForTimeout(500);

    // Verify values restored
    await expect(
      page.getByPlaceholder('KEY (e.g. OPENAI_API_KEY)'),
    ).toHaveValue('TEST_KEY');
    await expect(page.getByPlaceholder('VALUE')).toHaveValue('TEST_VAL');
  });

  // --- Folder Picker Tests ---

  test('selecting a folder opens it in a new tab', async ({ page }) => {
    await gotoAndAwaitMancha(page);
    await page.waitForLoadState('networkidle');

    // Open folder picker
    await page.click('button[title="Switch project"]');
    await page.waitForSelector('#folder-picker-modal[open]');

    // Select a folder and wait for the popup (new tab)
    const [popup] = await Promise.all([
      page.waitForEvent('popup'),
      page.click('button:has-text("Select This Folder")'),
    ]);

    // Verify the popup URL contains the project parameter
    expect(popup.url()).toContain('project=%2Fmock%2Fcwd');

    // Verify modal is closed in the original page
    await expect(page.locator('#folder-picker-modal')).not.toHaveAttribute(
      'open',
    );
  });

  test('creating a new folder', async ({ page }) => {
    await gotoAndAwaitMancha(page);
    await page.waitForLoadState('networkidle');

    // Open folder picker
    await page.click('button[title="Switch project"]');
    await page.waitForSelector('#folder-picker-modal[open]');

    // Wait for the button and ensure it's visible
    const newFolderBtn = page.locator('button[title="Create new folder"]');
    await expect(newFolderBtn).toBeVisible();

    // Click "New Folder" button
    await newFolderBtn.click();

    // Fill the folder name
    await page.fill('input[placeholder="Folder name"]', 'new-folder');

    // Click "Create" button
    await page.click('button:has-text("Create")');

    // Verify toast notification (mocked behavior)
    await expect(page.locator('div[role="alert"]')).toContainText(
      'Folder created!',
    );

    // The form should be hidden again
    await expect(
      page.locator('input[placeholder="Folder name"]'),
    ).not.toBeVisible();
  });

  test('reports an unreadable tasks file through a toast', async ({ page }) => {
    let dataRequests = 0;
    await page.route('**/api/data', async (route) => {
      dataRequests++;
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        json: {
          detail:
            'Tasks file /mock/tasks.yml could not be parsed: while scanning' +
            ' a quoted scalar. Refusing to continue so it is not overwritten' +
            ' with an empty roadmap.',
        },
      });
    });

    await gotoAndAwaitMancha(page);

    const alerts = page.locator('div[role="alert"]');
    await expect(alerts).toContainText('Could not load tasks');
    // The server's full reason is far too long for a toast.
    expect((await alerts.first().innerText()).length).toBeLessThan(60);

    // Polling runs every second, so a persistent failure must report once
    // rather than stack up a toast per tick.
    await page.waitForTimeout(2500);
    expect(dataRequests).toBeGreaterThan(1);
    await expect(alerts).toHaveCount(1);
  });
});
