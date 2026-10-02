import { expect, test, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";

async function openRepository(page: Page) {
  await page.goto("/");
  await page
    .getByLabel("Start with a public GitHub repository")
    .fill(`https://github.com/example/repo-${randomUUID()}`);
  await page.getByRole("button", { name: "Open workspace" }).click();
  await expect(page.getByText("Saved commit", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Start conversation", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Start conversation", exact: true })
    .click();
  await expect(
    page.getByRole("textbox", { name: "Ask a question about this repository" }),
  ).toBeEnabled();
}
async function ask(page: Page) {
  await page
    .getByRole("textbox", { name: "Ask a question about this repository" })
    .fill("How does authenticate work?");
  await page.getByRole("button", { name: "Send question" }).click();
}

test("submit, index, answer, inspect cited source, and restore conversation", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await openRepository(page);
  await page.getByText("Index coverage", { exact: true }).click();
  await expect(
    page.getByText("ignored directory", { exact: false }),
  ).toBeVisible();
  await page.getByText("Index coverage", { exact: true }).click();
  await ask(page);
  await expect(
    page.getByText("Authentication returns the token.", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("What remains uncertain", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: /Open auth.py, lines/ })
    .first()
    .click();
  await expect(page.locator(".code-line.highlighted")).not.toHaveCount(0);
  await expect(page.getByRole("region", { name: "Source code" })).toContainText(
    "def authenticate",
  );
  const address = page.url();
  await page.reload();
  await expect(
    page.getByText("Authentication returns the token.", { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".message.user")).toHaveCount(1);
  await expect(page).toHaveURL(address);
  await page
    .getByRole("button", { name: /Open auth.py, lines/ })
    .first()
    .click();
  await expect(page.locator(".code-line.highlighted")).not.toHaveCount(0);
  await page.screenshot({
    path: "test-results/workspace-desktop.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});

test("reload while running resumes without a second submission", async ({
  page,
}) => {
  await openRepository(page);
  let submissions = 0;
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().endsWith("/messages"))
      submissions++;
  });
  await ask(page);
  await expect(
    page.getByRole("button", { name: "Cancel answer", exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByText("Authentication returns the token.", { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".message.user")).toHaveCount(1);
  expect(submissions).toBe(1);
});

test("an ambiguous delivery retry uses the original idempotency key", async ({
  page,
}) => {
  await openRepository(page);
  const keys: string[] = [];
  let lost = false;
  await page.route("**/api/conversations/*/messages", async (route) => {
    keys.push(route.request().headers()["idempotency-key"]);
    if (!lost) {
      lost = true;
      await route.fetch();
      await route.abort("failed");
    } else await route.continue();
  });
  await ask(page);
  await expect(
    page.getByRole("button", { name: "Retry question", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Retry question", exact: true })
    .click();
  await expect(
    page.getByText("Authentication returns the token.", { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".message.user")).toHaveCount(1);
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
});

test("cancellation is terminal and the conversation remains usable", async ({
  page,
}) => {
  await openRepository(page);
  await ask(page);
  await page
    .getByRole("button", { name: "Cancel answer", exact: true })
    .click();
  await expect(page.getByText("Answer cancelled.").first()).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Cancel answer", exact: true }),
  ).not.toBeVisible();
  await expect(
    page.getByRole("textbox", { name: "Ask a question about this repository" }),
  ).toBeEnabled();
  await page.reload();
  await expect(page.getByText("Answer cancelled.").first()).toBeVisible();
});

test("new snapshots require an explicit new conversation and retain old history", async ({
  page,
}) => {
  await openRepository(page);
  await ask(page);
  await expect(
    page.getByText("Authentication returns the token.", { exact: true }),
  ).toBeVisible();
  const oldURL = page.url();
  await page.getByRole("button", { name: "Check for updates" }).click();
  await expect(
    page.getByRole("button", { name: "Start chat on latest" }),
  ).toBeVisible();
  await expect(page.locator(".commit-label code")).toHaveText("aaaaaaa");
  await expect(
    page.getByText("Authentication returns the token.", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Start chat on latest" }).click();
  await expect(page.locator(".commit-label code")).toHaveText("bbbbbbb");
  await expect(page.locator(".message")).toHaveCount(0);
  await page.goto(oldURL);
  await expect(page.locator(".commit-label code")).toHaveText("aaaaaaa");
  await expect(
    page.getByText("Authentication returns the token.", { exact: true }),
  ).toBeVisible();
});

test("file browsing and citations work on a narrow screen", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openRepository(page);
  await page.getByRole("tab", { name: "Files", exact: true }).click();
  await page.getByRole("button", { name: "src", exact: true }).click();
  await page.getByRole("button", { name: "routes.py", exact: true }).click();
  await expect(page.getByRole("region", { name: "Source code" })).toContainText(
    "from auth import authenticate",
  );
  await page.getByRole("tab", { name: "Chat", exact: true }).click();
  await ask(page);
  await expect(
    page.getByText("Authentication returns the token.", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: /Open auth.py, lines/ })
    .first()
    .click();
  await expect(
    page.getByRole("tab", { name: "Source", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await expect(page.locator(".code-line.highlighted")).not.toHaveCount(0);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "test-results/workspace-mobile.png",
    fullPage: true,
  });
});

test("service errors are actionable and unsafe markup stays text", async ({
  page,
}) => {
  await page.goto("/");
  await page.route("**/api/repositories", (route) =>
    route.fulfill({
      status: 503,
      json: {
        error: {
          message:
            "Configure the embedding provider before submitting indexing jobs.",
        },
      },
    }),
  );
  await page
    .getByLabel("Start with a public GitHub repository")
    .fill("https://github.com/example/failure");
  await page.getByRole("button", { name: "Open workspace" }).click();
  await expect(
    page
      .getByRole("alert")
      .filter({ hasText: "Configure the embedding provider" }),
  ).toContainText("Configure the embedding provider");
  await page.unroute("**/api/repositories");
  await openRepository(page);
  await page
    .getByRole("textbox", { name: "Ask a question about this repository" })
    .fill('<img src=x onerror="alert(1)"> authenticate');
  await page.getByRole("button", { name: "Send question" }).click();
  await expect(page.locator(".message.user")).toContainText(
    '<img src=x onerror="alert(1)">',
  );
  await expect(page.locator(".message.user img")).toHaveCount(0);
});

test("keyboard question submission enforces the backend byte limit", async ({
  page,
}) => {
  await openRepository(page);
  const question = page.getByRole("textbox", {
    name: "Ask a question about this repository",
  });
  await question.fill("🌍".repeat(130));
  await expect(
    page.getByRole("button", { name: "Send question" }),
  ).toBeDisabled();
  await expect(
    page.getByText("Shorten your question to 512 UTF-8 bytes."),
  ).toBeVisible();
  await question.fill("authenticate");
  await question.press("Control+Enter");
  await expect(
    page.getByText("Authentication returns the token.", { exact: true }),
  ).toBeVisible();
});

test("investigation activity remains inspectable after reload", async ({
  page,
}) => {
  await openRepository(page);
  await page
    .getByRole("combobox", { name: "Answer mode" })
    .selectOption("agent");
  await ask(page);
  await expect(
    page.getByText("Authentication returns the token.", { exact: true }),
  ).toBeVisible();
  await page.reload();
  await page.locator(".saved-activity summary").click();
  await expect(page.locator(".saved-activity")).toContainText("read file");
  await expect(page.locator(".saved-activity")).toContainText("completed");
  await page.locator(".saved-activity summary").click();
  await page.locator(".saved-activity summary").click();
  await expect(page.locator(".saved-activity li")).toHaveCount(2);
});

test("mobile panels support keyboard navigation", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openRepository(page);
  await page.getByRole("tab", { name: "Chat", exact: true }).focus();
  await page.keyboard.press("ArrowLeft");
  await expect(
    page.getByRole("tab", { name: "Source", exact: true }),
  ).toBeFocused();
  await expect(
    page.getByRole("tab", { name: "Source", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Home");
  await expect(
    page.getByRole("tab", { name: "Files", exact: true }),
  ).toBeFocused();
  await expect(
    page.getByRole("navigation", { name: "Repository files" }),
  ).toBeVisible();
});

test("the proxy rejects unrelated endpoints and cross-origin writes", async ({
  request,
}) => {
  const missing = await request.get("/api/unknown-host/secrets");
  expect(missing.status()).toBe(404);
  const crossOrigin = await request.post("/api/repositories", {
    headers: { Origin: "https://unrelated.example" },
    data: { url: "https://github.com/example/repository" },
  });
  expect(crossOrigin.status()).toBe(403);
});

test("workspace has no automated WCAG A/AA violations", async ({ page }) => {
  const { default: AxeBuilder } = await import("@axe-core/playwright");
  await page.goto("/");
  const landing = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect
    .soft(
      landing.violations.map((v) => ({
        id: v.id,
        nodes: v.nodes.map((n) => ({
          target: n.target,
          issue: n.failureSummary,
        })),
      })),
    )
    .toEqual([]);
  await openRepository(page);
  await ask(page);
  await expect(
    page.getByText("Authentication returns the token.", { exact: true }),
  ).toBeVisible();
  const workspace = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect
    .soft(
      workspace.violations.map((v) => ({
        id: v.id,
        nodes: v.nodes.map((n) => ({
          target: n.target,
          issue: n.failureSummary,
        })),
      })),
    )
    .toEqual([]);
});

test("homepage has a right-hand link form, dynamic preview, and clear instructions", async ({
  page,
}) => {
  await page.goto("/");
  const heading = page.getByRole("heading", {
    name: "Every repository has a story. Find your way in.",
  });
  await expect(heading).toBeVisible();
  const story = await page.locator(".home-story").boundingBox();
  const form = await page.locator(".start-card").boundingBox();
  expect(form!.x).toBeGreaterThan(story!.x + story!.width);
  await expect(
    page.getByRole("heading", { name: "From link to understanding." }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Preview step 2: Ask what matters" })
    .click();
  await expect(
    page.getByRole("button", { name: "Preview step 2: Ask what matters" }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(
    page.getByRole("button", { name: "Play illustration" }),
  ).toBeVisible();
  await page.screenshot({
    path: "test-results/homepage-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await expect(
    page.getByLabel("Start with a public GitHub repository"),
  ).toBeVisible();
  await page.screenshot({
    path: "test-results/homepage-mobile.png",
    fullPage: true,
  });
});

test("queued retries and expired worker leases do not look like active parsing", async ({
  page,
}) => {
  await openRepository(page);
  const address = new URL(page.url());
  const jobId = randomUUID();
  let status = "queued";
  await page.route(`**/index-jobs/${jobId}`, async (route) => {
    await route.fulfill({
      json: {
        id: jobId,
        repository_id: address.searchParams.get("repository"),
        snapshot_id: address.searchParams.get("snapshot"),
        status,
        stage: "parsing",
        progress: 40,
        attempts: 2,
        lease_expires_at: "2000-01-01T00:00:00Z",
        error: null,
      },
    });
  });
  address.searchParams.set("job", jobId);
  await page.goto(address.toString());
  await expect(
    page.getByText("Waiting to retry indexing", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText(
      "You can leave this page. Indexing continues in the background.",
      { exact: true },
    ),
  ).toHaveCount(0);
  status = "running";
  await expect(
    page.getByText("Indexing worker stopped responding", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Progress shows the last completed stage.", {
      exact: false,
    }),
  ).toBeVisible();
});
