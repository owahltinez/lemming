import * as fs from "node:fs";
import * as path from "node:path";
import { expect, test } from "@playwright/test";

const indexHtmlPath = path.resolve(process.cwd(), "src/lemming/web/index.html");
const logsHtmlPath = path.resolve(process.cwd(), "src/lemming/web/logs.html");
const manchaJsPath = path.resolve(process.cwd(), "src/lemming/web/mancha.js");
const indexJsPath = path.resolve(process.cwd(), "src/lemming/web/index.js");
const screenshotsDir = path.resolve(process.cwd(), "docs/screenshots");

// Realistic mock data with anonymized content
const mockTasks = [
  {
    id: "a1b2c3",
    description:
      "Set up CI/CD pipeline with GitHub Actions for automated testing and deployment",
    status: "completed",
    attempts: 1,
    has_log: true,
    pid: null,
    agent: null,
    parent: null,
    completed_at: 1711324800,
    run_time: 142.3,
    started_at: null,
    outcomes: [
      "Created .github/workflows/ci.yml with build, test, and deploy stages",
      "Added caching for node_modules to speed up builds",
    ],
  },
  {
    id: "d4e5f6",
    description:
      "Implement user authentication with JWT tokens and refresh token rotation",
    status: "completed",
    attempts: 2,
    has_log: true,
    pid: null,
    agent: null,
    parent: null,
    completed_at: 1711328400,
    run_time: 287.6,
    started_at: null,
    outcomes: [
      "Added /auth/login and /auth/refresh endpoints",
      "Tokens expire after 15 minutes, refresh tokens after 7 days",
    ],
  },
  {
    id: "g7h8i9",
    description: "Migrate database schema to support multi-tenant architecture",
    status: "in_progress",
    attempts: 0,
    has_log: true,
    pid: 48291,
    agent: null,
    parent: null,
    completed_at: null,
    run_time: 45.2,
    started_at: Date.now() / 1000 - 63,
    outcomes: [],
  },
  {
    id: "j0k1l2",
    description:
      "Add rate limiting middleware with Redis-backed sliding window",
    status: "pending",
    attempts: 0,
    has_log: false,
    pid: null,
    agent: null,
    parent: null,
    completed_at: null,
    run_time: null,
    started_at: null,
    outcomes: [],
  },
  {
    id: "m3n4o5",
    description: "Write integration tests for the payment processing module",
    status: "pending",
    attempts: 2,
    has_log: true,
    pid: null,
    agent: null,
    parent: null,
    completed_at: null,
    run_time: 98.1,
    started_at: null,
    outcomes: [
      "Stripe webhook signature verification failing in test environment",
    ],
  },
  {
    id: "p6q7r8",
    description:
      "Refactor API response serialization to use a shared schema layer",
    status: "pending",
    attempts: 0,
    has_log: false,
    pid: null,
    agent: "aider",
    parent: "d4e5f6",
    completed_at: null,
    run_time: null,
    started_at: null,
    outcomes: [],
  },
];

const mockLogContent = `> Analyzing database schema for multi-tenant support...

Reading current schema from src/db/schema.prisma
Found 12 models, 4 need tenant_id column

Modifying model: User
  + adding column: tenant_id (String, required)
  + adding index: @@index([tenant_id])

Modifying model: Project
  + adding column: tenant_id (String, required)
  + adding index: @@index([tenant_id])
  + updating relation: User -> adding tenant scope

Modifying model: ApiKey
  + adding column: tenant_id (String, required)
  + adding index: @@index([tenant_id])

Modifying model: AuditLog
  + adding column: tenant_id (String, required)
  + adding index: @@index([tenant_id, created_at])

> Running prisma migrate dev --name add-tenant-id...

Prisma schema loaded from src/db/schema.prisma
Datasource "db": PostgreSQL database

Applying migration \`20240324_add_tenant_id\`

Migration applied successfully.

> Updating repository layer with tenant filtering...

Modified: src/repositories/user.repository.ts
  - findAll() now requires tenantId parameter
  - findById() adds tenant scope to query
  + added findByTenant(tenantId) method

Modified: src/repositories/project.repository.ts
  - All queries now scoped to tenant
  + added migration helper for existing data

Modified: src/repositories/api-key.repository.ts
  - Generation includes tenant binding
  + validation checks tenant ownership

> Generating seed data for tenant isolation tests...

Created tenant: acme-corp (id: tenant_abc123)
Created tenant: globex (id: tenant_def456)
Seeded 15 users across 2 tenants
Seeded 8 projects across 2 tenants

> Running test suite...

  PASS  src/tests/tenant-isolation.test.ts (8 tests)
  PASS  src/tests/user.repository.test.ts (12 tests)
  PASS  src/tests/project.repository.test.ts (9 tests)

All 29 tests passed.

> Recording outcome: Schema migration complete with tenant isolation verified.`;

const cleanLog = mockLogContent;

const viewports = {
  desktop: { width: 1280, height: 900 },
  mobile: { width: 390, height: 844 },
};

test.describe("Screenshot Generation", () => {
  test.beforeAll(async () => {
    if (!fs.existsSync(screenshotsDir)) {
      fs.mkdirSync(screenshotsDir, { recursive: true });
    }
  });

  for (const [device, viewport] of Object.entries(viewports)) {
    test.describe(`${device} (${viewport.width}x${viewport.height})`, () => {
      test("dashboard screenshot", async ({ browser }) => {
        const context = await browser.newContext({ viewport });
        const page = await context.newPage();

        // Serve static files
        await page.route("http://localhost:8000/", async (route) => {
          await route.fulfill({
            contentType: "text/html",
            body: fs.readFileSync(indexHtmlPath, "utf8"),
          });
        });
        await page.route(
          "http://localhost:8000/static/mancha.js",
          async (route) => {
            await route.fulfill({
              contentType: "application/javascript",
              body: fs.readFileSync(manchaJsPath, "utf8"),
            });
          },
        );
        await page.route(
          "http://localhost:8000/static/index.js",
          async (route) => {
            await route.fulfill({
              contentType: "application/javascript",
              body: fs.readFileSync(indexJsPath, "utf8"),
              headers: { "Access-Control-Allow-Origin": "*" },
            });
          },
        );

        // Mock API endpoints
        await page.route("**/api/data", async (route) => {
          await route.fulfill({
            contentType: "application/json",
            json: {
              cwd: "/home/dev/projects/acme-saas-platform",
              loop_running: true,
              tasks: mockTasks,
              context:
                "Use TypeScript with strict mode. Follow REST API conventions. Write tests for all new endpoints. Use Prisma as the ORM with PostgreSQL.",
            },
          });
        });
        await page.route("**/api/agents", async (route) => {
          await route.fulfill({
            contentType: "application/json",
            json: ["gemini", "aider", "claude", "codex"],
          });
        });

        await page.goto("http://localhost:8000/");
        await page.evaluate(async () => {
          while (!window.ManchaApp) await new Promise((r) => setTimeout(r, 50));
          await window.ManchaApp;
        });
        await page.waitForLoadState("networkidle");

        // Expand the in-progress task to show details
        const inProgressTask = page
          .locator('[role="listitem"]')
          .filter({ hasText: "Migrate database" });
        await inProgressTask
          .getByRole("button", { name: "Show details" })
          .click();
        await page.waitForTimeout(300);

        await page.screenshot({
          path: path.join(screenshotsDir, `dashboard-${device}.png`),
          fullPage: true,
        });

        await context.close();
      });

      test("task log screenshot", async ({ browser }) => {
        const context = await browser.newContext({ viewport });
        const page = await context.newPage();

        const taskId = "g7h8i9";

        // Serve the logs HTML
        await page.route(
          `http://localhost:8000/tasks/${taskId}/log`,
          async (route) => {
            await route.fulfill({
              contentType: "text/html",
              body: fs.readFileSync(logsHtmlPath, "utf8"),
            });
          },
        );
        await page.route(
          "http://localhost:8000/static/mancha.js",
          async (route) => {
            await route.fulfill({
              contentType: "application/javascript",
              body: fs.readFileSync(manchaJsPath, "utf8"),
            });
          },
        );

        // Mock task detail and log APIs
        await page.route(`**/api/tasks/${taskId}`, async (route) => {
          await route.fulfill({
            contentType: "application/json",
            json: mockTasks.find((t) => t.id === taskId),
          });
        });
        await page.route(`**/api/tasks/${taskId}/log`, async (route) => {
          await route.fulfill({
            contentType: "application/json",
            json: { log: cleanLog },
          });
        });

        await page.goto(`http://localhost:8000/tasks/${taskId}/log`);
        await page.evaluate(async () => {
          while (!window.ManchaApp) await new Promise((r) => setTimeout(r, 50));
          await window.ManchaApp;
        });
        await page.waitForLoadState("networkidle");
        await page.waitForTimeout(500);

        await page.screenshot({
          path: path.join(screenshotsDir, `task-log-${device}.png`),
          fullPage: false,
        });

        await context.close();
      });
    });
  }
});
