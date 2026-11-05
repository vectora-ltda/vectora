import { test, expect, type Page } from "@playwright/test";

/**
 * E2E (browser real): troca de aba do workbench.
 *
 * Regressão do bug real corrigido em `workbench-panel.tsx` — um
 * `AnimatePresence mode="wait"` na troca de aba dependia da animação de
 * saída completar; quando o framer-motion nunca chamava `onExitComplete`, o
 * conteúdo renderizado ficava travado na primeira aba montada (Plan) para
 * sempre, enquanto só o header (`mDyn`) seguia trocando. O fix trocou por
 * unmount/mount instantâneo (só a entrada anima).
 *
 * Este spec clica em cada aba da NavBar (`data-testid="workbench-nav-<tab>"`)
 * e confirma que o header (`data-testid="workbench-header-title"`,
 * `data-active-tab`) E o wrapper de conteúdo
 * (`data-testid="workbench-tab-content"`, `data-tab`) **sempre** apontam
 * para a mesma aba — inclusive trocando rapidamente entre 3+ abas seguidas,
 * o cenário que expôs o bug ao vivo.
 *
 * Requer backend + LLM real (ver playwright.config.ts).
 */

const PROMPT = "Responda apenas com a palavra: pronto";

// Abas sempre disponíveis (sem depender do flag enableFeaturesBeta).
const CORE_TABS = [
  "files",
  "git",
  "plan",
  "tasks",
  "browser",
  "storage",
  "context_graph",
  "terminal",
] as const;

async function sendPrompt(page: Page, text: string): Promise<void> {
  const input = page.getByTestId("chat-input");
  await expect(input).toBeVisible({ timeout: 30_000 });
  await input.fill(text);
  await page.getByTestId("chat-send").click();
}

async function startSession(page: Page): Promise<void> {
  await page.goto("/");
  await sendPrompt(page, PROMPT);
  const assistant = page.getByTestId("message-content-assistant").last();
  await expect(assistant).toBeAttached({ timeout: 30_000 });
  await expect(assistant).toHaveAttribute("data-streaming", "false", {
    timeout: 90_000,
  });
}

/** Clica na aba e afirma que header + conteúdo convergem para ela. */
async function clickTabAndAssert(page: Page, tab: string): Promise<void> {
  await page.getByTestId(`workbench-nav-${tab}`).click();

  const header = page.getByTestId("workbench-header-title");
  await expect(header).toHaveAttribute("data-active-tab", tab, {
    timeout: 10_000,
  });

  const content = page.getByTestId("workbench-tab-content");
  await expect(content).toHaveAttribute("data-tab", tab, { timeout: 10_000 });
}

test.describe("troca de aba do workbench", () => {
  test.beforeEach(async ({ page }) => {
    await startSession(page);
  });

  for (const tab of CORE_TABS) {
    test(`clicar em "${tab}" mostra o header e o conteúdo da própria aba`, async ({
      page,
    }) => {
      await clickTabAndAssert(page, tab);
    });
  }

  test("trocar rapidamente entre 3+ abas mantém header e conteúdo sempre sincronizados", async ({
    page,
  }) => {
    // Sequência que expôs o bug ao vivo: Arquivos → Git → Plano,
    // repetida com mais abas para não depender de timing específico.
    const sequence = [
      "files",
      "git",
      "plan",
      "tasks",
      "git",
      "files",
      "storage",
      "context_graph",
      "plan",
    ] as const;

    for (const tab of sequence) {
      await clickTabAndAssert(page, tab);
    }
  });

  test("conteúdo de uma aba nunca sobrevive à troca para outra aba (verificação sem sleep)", async ({
    page,
  }) => {
    // Abre Arquivos, depois troca para Plano sem esperar — o conteúdo
    // antigo (Arquivos) não pode continuar montado sob o novo header.
    await page.getByTestId("workbench-nav-files").click();
    await expect(page.getByTestId("workbench-tab-content")).toHaveAttribute(
      "data-tab",
      "files",
    );

    await page.getByTestId("workbench-nav-plan").click();

    // A dupla (header, conteúdo) tem que trocar junto — nunca um sem o outro.
    await expect(page.getByTestId("workbench-header-title")).toHaveAttribute(
      "data-active-tab",
      "plan",
    );
    await expect(page.getByTestId("workbench-tab-content")).toHaveAttribute(
      "data-tab",
      "plan",
    );
  });

  test("clicar na aba já ativa colapsa o painel; clicar de novo reabre na mesma aba", async ({
    page,
  }) => {
    await clickTabAndAssert(page, "files");

    // Clicar de novo na aba ativa colapsa o painel (comportamento
    // documentado em WorkbenchNavBar) — o conteúdo deixa de estar montado.
    await page.getByTestId("workbench-nav-files").click();
    await expect(page.getByTestId("workbench-tab-content")).not.toBeVisible({
      timeout: 10_000,
    });

    // Reabrir mostra a MESMA aba (files), não a primeira aba montada.
    await page.getByTestId("workbench-nav-files").click();
    await expect(page.getByTestId("workbench-tab-content")).toHaveAttribute(
      "data-tab",
      "files",
    );
  });

  test("aba Library (beta) só é clicável quando o flag de features beta está ativo", async ({
    page,
  }) => {
    const libraryBtn = page.getByTestId("workbench-nav-library");
    const isDisabled = await libraryBtn
      .getAttribute("aria-disabled")
      .catch(() => null);

    if (isDisabled === "true") {
      // Flag desligado: botão "coming soon", não navega.
      await expect(libraryBtn).toHaveAttribute("aria-disabled", "true");
      return;
    }

    // Flag ligado: comporta-se como qualquer outra aba.
    await clickTabAndAssert(page, "library");
  });
});
