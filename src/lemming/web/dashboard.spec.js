import * as fs from 'node:fs';
import * as path from 'node:path';
import { expect, test } from '@playwright/test';

const indexHtmlPath = path.resolve(process.cwd(), 'src/lemming/web/index.html');

test.describe('Dashboard E2E', () => {
  test.beforeEach(async ({ page, context }) => {
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
            context: 'Mock context',
          },
        });
      } else if (url.includes('/api/runners')) {
        await route.fulfill({
          contentType: 'application/json',
          json: ['gemini', 'aider'],
        });
      } else if (url.includes('/api/hooks')) {
        await route.fulfill({
          contentType: 'application/json',
          json: ['roadmap'],
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
      localStorage.getItem('lemming_env_overrides'),
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
      localStorage.getItem('lemming_env_overrides'),
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

  test('selecting a folder opens it in a new tab', async ({
    page,
    context,
  }) => {
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
});
