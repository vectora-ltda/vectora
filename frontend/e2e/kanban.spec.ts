import { test, expect, type Page } from "@playwright/test";

/**
 * E2E (browser real): o board do Kanban não pode produzir scrollbar própria.
 *
 * Regressão relatada com screenshots: board VAZIO exibindo scrollbar vertical
 * E horizontal. Causa: um único container empilhava formulário + filtros +
 * dropzone + a linha de colunas com `h-full` (altura sempre estourando), e as
 * colunas eram `w-60` fixas e sempre renderizadas (~1250px mínimos).
 *
 * Medir `scrollWidth`/`scrollHeight` contra `clientWidth`/`clientHeight` é a
 * única prova objetiva disso — teste de componente em jsdom não tem layout.
 *
 * Requer backend + LLM real (ver playwright.config.ts).
 */

const PROMPT = "Responda apenas com a palavra: pronto";

async function startSession(page: Page): Promise<void> {
  await page.goto("/");
  // A tela inicial exige escolher Chat ou Code session antes do input
  // aparecer — o Kanban só existe numa Code session (o workspace dedicado
  // é criado automaticamente pelo backend ao confirmar sem selecionar nada).
  await page.getByRole("button", { name: /Code session/ }).click();
  await page.getByRole("button", { name: "Start conversation" }).click();
  const input = page.getByTestId("chat-input");
  await expect(input).toBeVisible({ timeout: 30_000 });
  await input.fill(PROMPT);
  await page.getByTestId("chat-send").click();
  const assistant = page.getByTestId("message-content-assistant").last();
  await expect(assistant).toBeAttached({ timeout: 30_000 });
  await expect(assistant).toHaveAttribute("data-streaming", "false", {
    timeout: 90_000,
  });
}

async function openKanban(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Kanban", exact: true }).click();
  await expect(page.getByTestId("kanban-col-todo")).toBeVisible({
    timeout: 10_000,
  });
}

test.describe("Kanban — layout", () => {
  test.beforeEach(async ({ page }) => {
    await startSession(page);
    await openKanban(page);
  });

  test("board vazio não gera scrollbar vertical nem horizontal", async ({
    page,
  }) => {
    // Sobe do container da coluna até a raiz do board e mede cada ancestral:
    // a scrollbar pode nascer em qualquer nível, não só no elemento medido.
    const overflow = await page
      .getByTestId("kanban-col-todo")
      .evaluate((col) => {
        const offenders: { cls: string; x: number; y: number }[] = [];
        let el: HTMLElement | null = col.parentElement;
        for (let i = 0; i < 6 && el; i++, el = el.parentElement) {
          const x = el.scrollWidth - el.clientWidth;
          const y = el.scrollHeight - el.clientHeight;
          if (x > 1 || y > 1) {
            offenders.push({ cls: el.className.slice(0, 60), x, y });
          }
        }
        return offenders;
      });

    expect(overflow).toEqual([]);
  });

  test("as lanes de todos os status do backend existem, incluindo triage/scheduled/review", async ({
    page,
  }) => {
    // Regressão: só 5 lanes existiam e tarefas nos demais status eram
    // descartadas silenciosamente pelo filtro de visibilidade.
    for (const status of [
      "triage",
      "todo",
      "scheduled",
      "ready",
      "running",
      "blocked",
      "review",
      "done",
    ]) {
      await expect(page.getByTestId(`kanban-col-${status}`)).toBeAttached();
    }
  });
});
