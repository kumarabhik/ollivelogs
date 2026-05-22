import { expect, test } from "@playwright/test";

test("send, stream, cancel, resume, and list conversations", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByText("OlliveLogs Conversation Console")).toBeVisible();
  await page.getByRole("button", { name: "New" }).click();

  const composer = page.getByPlaceholder(
    "Ask for a cost summary, a cancellation drill, or a provider comparison.",
  );
  await composer.fill("Walk me through a layered cancellation demo.");
  await page.getByRole("button", { name: "Send" }).click();

  await expect(page.getByText("Streaming...", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Stop" })).toBeVisible();
  await expect(page.getByText("Layered insight", { exact: false })).toBeVisible();

  await page.getByRole("button", { name: "Stop" }).click();
  await expect(
    page.locator('[data-testid="chat-message"]').filter({ hasText: "cancelled" }).last(),
  ).toBeVisible();

  await page.getByRole("button", { name: "New" }).click();
  await composer.fill("Start a second thread so the sidebar has multiple items.");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("The stream is chunked word by word", { exact: false })).toBeVisible();

  const sidebarCards = page.locator('[data-testid^="conversation-item-"]');
  await expect(sidebarCards).toHaveCount(2);

  await sidebarCards.nth(1).click();
  await expect(
    page.locator('[data-testid="chat-message"]').filter({ hasText: "cancelled" }).last(),
  ).toBeVisible();

  await page.reload();
  await expect(sidebarCards).toHaveCount(2);
  await sidebarCards.nth(1).click();
  await expect(
    page.locator('[data-testid="chat-message"]').filter({ hasText: "cancelled" }).last(),
  ).toBeVisible();
});
