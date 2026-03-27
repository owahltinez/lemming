import * as fs from "node:fs";
import * as path from "node:path";
import { expect, test } from "@playwright/test";

const indexHtmlPath = path.resolve(process.cwd(), "src/lemming/web/index.html");

test.describe("Folder Picker UI", () => {
  test.beforeEach(async ({ page, context }) => {
    await context.route("**/*", async (route) => {
      const url = route.request().url();
      if (
        url === "http://localhost:8000/" ||
        url.startsWith("http://localhost:8000/?")
      ) {
        await route.fulfill({
          contentType: "text/html",
          body: fs.readFileSync(indexHtmlPath, "utf8"),
        });
      } else if (url.endsWith("/static/mancha.js")) {
        await route.fulfill({
          contentType: "application/javascript",
          body: fs.readFileSync(
            path.resolve(process.cwd(), "src/lemming/web/mancha.js"),
            "utf8",
          ),
        });
      } else if (url.endsWith("/static/favicon.js")) {
        await route.fulfill({
          contentType: "application/javascript",
          body: fs.readFileSync(
            path.resolve(process.cwd(), "src/lemming/web/favicon.js"),
            "utf8",
          ),
        });
      } else if (url.endsWith("/static/index.js")) {
        await route.fulfill({
          contentType: "application/javascript",
          body: fs.readFileSync(
            path.resolve(process.cwd(), "src/lemming/web/index.js"),
            "utf8",
          ),
        });
      } else if (url.includes("/api/data")) {
        await route.fulfill({
          contentType: "application/json",
          json: {
            cwd: "/mock/cwd",
            loop_running: false,
            tasks: [],
            context: "Mock context",
          },
        });
      } else if (url.includes("/api/runners")) {
        await route.fulfill({
          contentType: "application/json",
          json: ["gemini"],
        });
      } else if (url.includes("/api/hooks")) {
        await route.fulfill({
          contentType: "application/json",
          json: ["roadmap"],
        });
      } else if (
        url.includes("/api/directories") &&
        route.request().method() === "POST"
      ) {
        await route.fulfill({
          contentType: "application/json",
          json: { name: "new-folder", path: "/mock/cwd/new-folder" },
        });
      } else if (url.includes("/api/directories")) {
        await route.fulfill({
          contentType: "application/json",
          json: {
            status: "success",
            path: "/mock/cwd",
            directories: [{ name: "subdir", path: "/mock/cwd/subdir" }],
          },
        });
      } else {
        await route.continue();
      }
    });
  });

  async function gotoAndAwaitMancha(page) {
    await page.goto("http://localhost:8000/");
    await page.evaluate(async () => {
      while (!window.ManchaApp) await new Promise((r) => setTimeout(r, 50));
      await window.ManchaApp;
    });
  }

  test("selecting a folder opens it in a new tab", async ({
    page,
    context,
  }) => {
    await gotoAndAwaitMancha(page);
    await page.waitForLoadState("networkidle");

    // Open folder picker
    await page.click('button[title="Switch project"]');
    await page.waitForSelector("#folder-picker-modal[open]");

    // Select a folder and wait for the popup (new tab)
    const [popup] = await Promise.all([
      page.waitForEvent("popup"),
      page.click('button:has-text("Select This Folder")'),
    ]);

    // Verify the popup URL contains the project parameter
    expect(popup.url()).toContain("project=%2Fmock%2Fcwd");

    // Verify modal is closed in the original page
    await expect(page.locator("#folder-picker-modal")).not.toHaveAttribute(
      "open",
    );
  });

  test("creating a new folder", async ({ page }) => {
    await gotoAndAwaitMancha(page);
    await page.waitForLoadState("networkidle");

    // Open folder picker
    await page.click('button[title="Switch project"]');
    await page.waitForSelector("#folder-picker-modal[open]");

    // Wait for the button and ensure it's visible
    const newFolderBtn = page.locator('button[title="Create new folder"]');
    await expect(newFolderBtn).toBeVisible();

    // Click "New Folder" button
    await newFolderBtn.click();

    // Fill the folder name
    await page.fill('input[placeholder="Folder name"]', "new-folder");

    // Click "Create" button
    await page.click('button:has-text("Create")');

    // Verify toast notification (mocked behavior)
    await expect(page.locator('div[role="alert"]')).toContainText(
      "Folder created!",
    );

    // The form should be hidden again
    await expect(
      page.locator('input[placeholder="Folder name"]'),
    ).not.toBeVisible();
  });
});
