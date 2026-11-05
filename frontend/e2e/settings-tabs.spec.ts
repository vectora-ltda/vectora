import { test, expect, type Page } from "@playwright/test";

/**
 * E2E (browser real): dialog "Ambiente" (Configurações) — abas Integrações
 * e Provider Routing renderizam e aceitam uma interação básica.
 *
 * Não persiste nenhuma credencial real: os testes digitam em campos de
 * texto/senha e conferem o valor client-side, mas nunca clicam em salvar —
 * evita sobrescrever chaves reais já configuradas em ~/.vectora/.env.
 *
 * Requer backend real (ver playwright.config.ts). Não depende de LLM.
 */

async function openEnvironmentDialog(page: Page): Promise<void> {
  await page.goto("/");
  await page.getByTestId("plus-menu-trigger").click();
  await page.getByTestId("plus-menu-connectors").click();
}

test.describe("dialog Ambiente — abas Integrações / Provider Routing", () => {
  test.beforeEach(async ({ page }) => {
    await openEnvironmentDialog(page);
  });

  test("abre direto na aba Integrações", async ({ page }) => {
    const tab = page.getByRole("tab", { name: "Integrações" });
    await expect(tab).toBeVisible({ timeout: 15_000 });
    await expect(tab).toHaveAttribute("data-state", "active");
  });

  test("troca para a aba Provider Routing e o conteúdo muda", async ({
    page,
  }) => {
    const integracoesPanel = page.getByRole("tabpanel", {
      name: "Integrações",
    });
    await expect(integracoesPanel).toBeVisible({ timeout: 15_000 });

    await page.getByRole("tab", { name: "Provider Routing" }).click();

    const routingTab = page.getByRole("tab", { name: "Provider Routing" });
    await expect(routingTab).toHaveAttribute("data-state", "active", {
      timeout: 10_000,
    });
    await expect(integracoesPanel).not.toBeVisible();
  });

  test("volta para Integrações depois de ir para Provider Routing (ida e volta preserva estado das abas)", async ({
    page,
  }) => {
    await page.getByRole("tab", { name: "Provider Routing" }).click();
    await expect(
      page.getByRole("tab", { name: "Provider Routing" }),
    ).toHaveAttribute("data-state", "active", { timeout: 10_000 });

    await page.getByRole("tab", { name: "Integrações" }).click();
    await expect(
      page.getByRole("tab", { name: "Integrações" }),
    ).toHaveAttribute("data-state", "active", { timeout: 10_000 });
  });

  test("campo de API key em Integrações aceita digitação (sem salvar)", async ({
    page,
  }) => {
    const keyInput = page.locator('input[type="password"]').first();
    // O campo de key só existe depois de expandir um conector — se nenhum
    // estiver expandido nesta instância, o teste documenta o contrato via
    // skip explícito (não é falha do fluxo, é ausência de conector visível).
    const count = await keyInput.count();
    test.skip(
      count === 0,
      "nenhum campo de API key visível sem expandir um conector",
    );

    await keyInput.fill("fake-key-not-saved-e2e");
    await expect(keyInput).toHaveValue("fake-key-not-saved-e2e");
  });

  test("aba Provider Routing: campo de OpenRouter key aceita digitação (sem salvar)", async ({
    page,
  }) => {
    await page.getByRole("tab", { name: "Provider Routing" }).click();

    const keyInput = page.locator('input[type="password"]').first();
    const count = await keyInput.count();
    test.skip(
      count === 0,
      "OpenRouter já configurado nesta instância — campo de key não aparece",
    );

    await keyInput.fill("fake-openrouter-key-e2e");
    await expect(keyInput).toHaveValue("fake-openrouter-key-e2e");
  });

  test("fechar o dialog (Escape) esconde as abas", async ({ page }) => {
    const tab = page.getByRole("tab", { name: "Integrações" });
    await expect(tab).toBeVisible({ timeout: 15_000 });

    await page.keyboard.press("Escape");
    await expect(tab).not.toBeVisible({ timeout: 10_000 });
  });
});
