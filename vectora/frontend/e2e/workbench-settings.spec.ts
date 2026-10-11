import { expect, test, type Page } from "@playwright/test";

const VIEWPORTS = [360, 480, 640, 768] as const;

async function openWorkbenchesSettings(page: Page): Promise<void> {
  await page.goto("/");
  await page.getByTestId("plus-menu-trigger").click();
  await page.getByTestId("plus-menu-connectors").click();

  const railButton = page.getByRole("button", {
    name: "Workbenches",
    exact: true,
  });
  if (await railButton.count()) {
    await railButton.click();
    return;
  }

  const categorySelect = page.locator("select").first();
  await categorySelect.selectOption("workbenches");
}

test.describe("settings das workbenches", () => {
  for (const width of VIEWPORTS) {
    test(`mantém o layout sem overflow horizontal em ${width}px`, async ({
      page,
    }) => {
      await page.setViewportSize({ width, height: 900 });
      await openWorkbenchesSettings(page);

      const dialog = page.getByRole("dialog");
      await expect(dialog).toBeVisible({ timeout: 15_000 });
      await expect(
        page.getByText(/^(Browser|Navegador)$/, { exact: true }),
      ).toBeVisible({
        timeout: 15_000,
      });

      const overflow = await dialog.evaluate((element) => ({
        scrollWidth: element.scrollWidth,
        clientWidth: element.clientWidth,
      }));
      expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.clientWidth);
    });
  }

  test("fecha por Escape e retorna o foco ao acionador", async ({ page }) => {
    await openWorkbenchesSettings(page);
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible({ timeout: 15_000 });
    await page.keyboard.press("Escape");
    await expect(dialog).not.toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId("plus-menu-trigger")).toBeFocused();
  });

  test("registra todas as workbenches na ordem do navegador", async ({
    page,
  }) => {
    await openWorkbenchesSettings(page);
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible({ timeout: 15_000 });
    const sections = dialog.locator('details[id^="workbench-settings-"]');
    await expect(sections).toHaveCount(9);
    expect(
      await sections.evaluateAll((items) =>
        items.map((item) => item.id.replace("workbench-settings-", "")),
      ),
    ).toEqual([
      "files",
      "git",
      "plan",
      "tasks",
      "browser",
      "storage",
      "context_graph",
      "library",
      "terminal",
    ]);
  });

  test("abre e fecha grupos sem produzir overflow horizontal", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 360, height: 900 });
    await openWorkbenchesSettings(page);
    const dialog = page.getByRole("dialog");
    const sections = dialog.locator('details[id^="workbench-settings-"]');
    for (const section of await sections.all()) {
      await section.locator("summary").click();
      const overflow = await dialog.evaluate((element) => ({
        scrollWidth: element.scrollWidth,
        clientWidth: element.clientWidth,
      }));
      expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.clientWidth);
    }
  });

  test("abre as quatro superfícies Vectora e sinaliza Browser indisponível na web", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 360, height: 900 });
    await openWorkbenchesSettings(page);
    const dialog = page.getByRole("dialog");
    for (const id of ["context_graph", "storage", "terminal", "git"]) {
      const section = dialog.locator(`#workbench-settings-${id}`);
      await section.locator("summary").click();
      await expect(section.locator('[data-surface-mode="form"]')).toBeVisible();
    }
    const browser = dialog.locator("#workbench-settings-browser");
    await browser.locator("summary").click();
    await expect(
      browser.locator('[data-surface-mode="unavailable"]'),
    ).toBeVisible();
  });
});
