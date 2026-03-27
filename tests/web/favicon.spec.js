import * as fs from "node:fs";
import * as path from "node:path";
import { expect, test } from "@playwright/test";

const indexHtmlPath = path.resolve(process.cwd(), "src/lemming/web/index.html");
const indexJsPath = path.resolve(process.cwd(), "src/lemming/web/index.js");
const filesHtmlPath = path.resolve(process.cwd(), "src/lemming/web/files.html");
const logsHtmlPath = path.resolve(process.cwd(), "src/lemming/web/logs.html");
const manchaJsPath = path.resolve(process.cwd(), "src/lemming/web/mancha.js");
const faviconJsPath = path.resolve(process.cwd(), "src/lemming/web/favicon.js");

test.describe("Favicon Status Synchronization", () => {
  test.beforeEach(async ({ page }) => {
    // Serve static files via mocks
    await page.route("**/static/mancha.js", async (route) => {
      await route.fulfill({
        contentType: "application/javascript",
        body: fs.readFileSync(manchaJsPath, "utf8"),
      });
    });
    await page.route("**/static/favicon.js", async (route) => {
      await route.fulfill({
        contentType: "application/javascript",
        body: fs.readFileSync(faviconJsPath, "utf8"),
      });
    });
    await page.route("**/static/index.js", async (route) => {
      await route.fulfill({
        contentType: "application/javascript",
        body: fs.readFileSync(indexJsPath, "utf8"),
      });
    });
  });

  test("Dashboard (index.html) favicon status and seen state", async ({
    page,
  }) => {
    let loopRunning = false;
    let tasks = [];

    await page.route("**/api/data**", async (route) => {
      await route.fulfill({
        contentType: "application/json",
        json: { loop_running: loopRunning, tasks: tasks, context: "", cwd: "" },
      });
    });
    await page.route("**/api/runners", async (route) => {
      await route.fulfill({
        contentType: "application/json",
        json: ["gemini", "aider"],
      });
    });

    await page.route("**/api/hooks", async (route) => {
      await route.fulfill({
        contentType: "application/json",
        json: ["roadmap"],
      });
    });

    await page.route("http://localhost:8000/", async (route) => {
      await route.fulfill({
        contentType: "text/html",
        body: fs.readFileSync(indexHtmlPath, "utf8"),
      });
    });

    await page.goto("http://localhost:8000/");
    await page.waitForFunction(() => window.ManchaApp !== undefined);
    await page.evaluate(async () => await window.ManchaApp);

    // Initial idle
    await expect(async () => {
      const href = await page.evaluate(
        () => document.querySelector('link[rel="icon"]').href,
      );
      const svg = decodeURIComponent(href);
      expect(svg).toContain("🐹");
      expect(svg).not.toContain("circle");
    }).toPass();

    // Running
    loopRunning = true;
    tasks = [{ id: "t1", status: "in_progress", attempts: 1 }];
    await expect(async () => {
      const href = await page.evaluate(
        () => document.querySelector('link[rel="icon"]').href,
      );
      const svg = decodeURIComponent(href);
      expect(svg).toContain("circle");
      expect(svg).toContain("animate");
    }).toPass();

    // Success
    loopRunning = false;
    tasks = [{ id: "t1", status: "completed", attempts: 1 }];
    await expect(async () => {
      const href = await page.evaluate(
        () => document.querySelector('link[rel="icon"]').href,
      );
      const svg = decodeURIComponent(href);
      expect(svg).toContain("circle");
      expect(svg).toContain("#065f46"); // green
    }).toPass();

    // Mark as seen
    await page.evaluate(() => {
      Object.defineProperty(document, "visibilityState", {
        value: "visible",
        writable: true,
      });
      document.dispatchEvent(new Event("visibilitychange"));
    });

    // Reload shows idle
    await page.reload();
    await page.waitForFunction(() => window.ManchaApp !== undefined);
    await page.evaluate(async () => await window.ManchaApp);
    await expect(async () => {
      const href = await page.evaluate(
        () => document.querySelector('link[rel="icon"]').href,
      );
      const svg = decodeURIComponent(href);
      expect(svg).not.toContain("circle");
    }).toPass();
  });

  test("Files (files.html) favicon status reflects project status", async ({
    page,
  }) => {
    let loopRunning = true;
    let tasks = [{ id: "t1", status: "in_progress", attempts: 1 }];

    await page.route("**/api/data**", async (route) => {
      await route.fulfill({
        contentType: "application/json",
        json: { loop_running: loopRunning, tasks: tasks, context: "", cwd: "" },
      });
    });
    await page.route("**/api/files/**", async (route) => {
      await route.fulfill({
        contentType: "application/json",
        json: { path: "", contents: [] },
      });
    });
    await page.route("http://localhost:8000/files/", async (route) => {
      await route.fulfill({
        contentType: "text/html",
        body: fs.readFileSync(filesHtmlPath, "utf8"),
      });
    });

    await page.goto("http://localhost:8000/files/");
    await page.waitForFunction(() => window.ManchaApp !== undefined);
    await page.evaluate(async () => await window.ManchaApp);

    await expect(async () => {
      const href = await page.evaluate(
        () => document.querySelector('link[rel="icon"]').href,
      );
      const svg = decodeURIComponent(href);
      expect(svg).toContain("circle"); // running
    }).toPass();

    // Error
    loopRunning = false;
    tasks = [{ id: "t1", status: "pending", attempts: 1 }];
    await expect(async () => {
      const href = await page.evaluate(
        () => document.querySelector('link[rel="icon"]').href,
      );
      const svg = decodeURIComponent(href);
      expect(svg).toContain("#9f1239"); // red
    }).toPass();
  });

  test("Logs (logs.html) favicon status reflects task status", async ({
    page,
  }) => {
    let taskStatus = "in_progress";
    const attempts = 1;

    await page.route("**/api/tasks/t1", async (route) => {
      await route.fulfill({
        contentType: "application/json",
        json: {
          id: "t1",
          status: taskStatus,
          attempts: attempts,
          description: "Task 1",
        },
      });
    });
    await page.route("**/api/tasks/t1/log**", async (route) => {
      await route.fulfill({
        contentType: "application/json",
        json: { log: "test log" },
      });
    });
    await page.route("http://localhost:8000/tasks/t1/log", async (route) => {
      await route.fulfill({
        contentType: "text/html",
        body: fs.readFileSync(logsHtmlPath, "utf8"),
      });
    });

    await page.goto("http://localhost:8000/tasks/t1/log");
    await page.waitForFunction(() => window.ManchaApp !== undefined);
    await page.evaluate(async () => await window.ManchaApp);

    await expect(async () => {
      const href = await page.evaluate(
        () => document.querySelector('link[rel="icon"]').href,
      );
      const svg = decodeURIComponent(href);
      expect(svg).toContain("circle"); // running
    }).toPass();

    // Success
    taskStatus = "completed";
    await expect(async () => {
      const href = await page.evaluate(
        () => document.querySelector('link[rel="icon"]').href,
      );
      const svg = decodeURIComponent(href);
      expect(svg).toContain("#065f46"); // green
    }).toPass();

    // Mark as seen
    await page.evaluate(() => {
      Object.defineProperty(document, "visibilityState", {
        value: "visible",
        writable: true,
      });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    const lastSeen = await page.evaluate(() =>
      localStorage.getItem("lemming_last_seen_state"),
    );
    expect(lastSeen).toBe(JSON.stringify("success"));
  });
});
