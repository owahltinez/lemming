import * as fs from 'node:fs';
import * as path from 'node:path';
import { expect, test } from '@playwright/test';

const filesHtmlPath = path.resolve(process.cwd(), 'src/lemming/web/files.html');
const manchaJsPath = path.resolve(process.cwd(), 'src/lemming/web/mancha.js');

test.describe('Files Browser UI', () => {
  test.beforeEach(async ({ page }) => {
    // Serve files over mocked HTTP
    await page.route('http://localhost:8000/', async (route) => {
      await route.fulfill({
        contentType: 'text/html',
        body: fs.readFileSync(filesHtmlPath, 'utf8'),
      });
    });
    await page.route(
      'http://localhost:8000/static/mancha.js',
      async (route) => {
        await route.fulfill({
          contentType: 'application/javascript',
          body: fs.readFileSync(manchaJsPath, 'utf8'),
        });
      },
    );

    // Mock API
    await page.route('**/api/files/**', async (route) => {
      await route.fulfill({
        contentType: 'application/json',
        json: {
          path: 'test/dir',
          contents: [
            {
              name: 'file1.txt',
              is_dir: false,
              size: 1500,
              modified: 1715000000,
              path: 'test/dir/file1.txt',
            },
            {
              name: 'subdir',
              is_dir: true,
              size: 4096,
              modified: 1715001000,
              path: 'test/dir/subdir',
            },
          ],
        },
      });
    });

    await page.route('**/api/data**', async (route) => {
      await route.fulfill({
        contentType: 'application/json',
        json: {
          loop_running: false,
          tasks: [],
        },
      });
    });

    await page.goto('http://localhost:8000/');
    // Wait for ManchaApp to be ready
    await page.evaluate(async () => {
      while (!window.ManchaApp) await new Promise((r) => setTimeout(r, 50));
      await window.ManchaApp;
    });
  });

  test('renders file list correctly', async ({ page }) => {
    const headerPath = page.locator('header div.text-sm');
    await expect(headerPath).toContainText('/test/dir');

    const rows = page.locator('tbody tr');
    // Row 0: Parent directory
    // Row 1: file1.txt
    // Row 2: subdir
    await expect(rows).toHaveCount(3);

    const file1Link = page.getByText('file1.txt');
    await expect(file1Link).toBeVisible();
    await expect(file1Link).toHaveAttribute(
      'href',
      '/files/test/dir/file1.txt',
    );

    const subdirLink = page.getByText('subdir');
    await expect(subdirLink).toBeVisible();
    await expect(subdirLink).toHaveAttribute('href', '/files/test/dir/subdir');
  });

  test('formats file sizes', async ({ page }) => {
    const sizeCell = page.getByText('1.46 KB');
    await expect(sizeCell).toBeVisible();
  });
});
