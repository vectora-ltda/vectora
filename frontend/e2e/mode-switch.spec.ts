import { test, expect, type Page } from "@playwright/test";

/**
 * E2E (browser real): troca de modo de layout (Assistente/IDE/Kanban).
 *
 * Mesma classe de bug já corrigida uma vez em `workbench-panel.tsx` (ver
 * `workbench-tabs.spec.ts`): `AnimatePresence mode="wait"` depende da
 * animação de saída completar (`onExitComplete`) para desmontar o branch
 * antigo — sem esse callback, o conteúdo antigo fica desenhado por cima do
 * novo. Em `$threadId.tsx` isso apareceu como o board do Kanban surgindo
 * com o chat do modo anterior sobreposto. A troca de modo passou a ser
 * unmount/mount instantâneo, e o scroll do chat é preservado por thread
 * em `message-list.tsx` em vez de depender de manter a instância montada.
 * Este spec prova, em browser real, que nunca há dois modos visíveis ao
 * mesmo tempo e que o scroll não volta ao topo na ida e volta.
 *
 * Requer backend + LLM real (ver playwright.config.ts).
 */

const PROMPT = "Responda apenas com a palavra: pronto";

async function sendPrompt(page: Page, text: string): Promise<void> {
  const input = page.getByTestId("chat-input");
  await expect(input).toBeVisible({ timeout: 30_000 });
  await input.fill(text);
  await page.getByTestId("chat-send").click();
}

async function startSession(page: Page): Promise<void> {
  await page.goto("/");
  // A tela inicial exige escolher Chat ou Code session antes do input
  // aparecer — os modos IDE/Kanban só existem numa Code session (o
  // workspace dedicado é criado automaticamente pelo backend ao confirmar
  // sem selecionar nada).
  await page.getByRole("button", { name: /Code session/ }).click();
  await page.getByRole("button", { name: "Start conversation" }).click();
  await sendPrompt(page, PROMPT);
  const assistant = page.getByTestId("message-content-assistant").last();
  await expect(assistant).toBeAttached({ timeout: 30_000 });
  await expect(assistant).toHaveAttribute("data-streaming", "false", {
    timeout: 90_000,
  });
}

/** Rótulo interno (usado nas assinaturas dos testes) → texto real do botão
 * no DOM. O browser do Playwright roda em inglês por padrão — só "Kanban"
 * é igual nos dois idiomas, o que mascarou esse mismatch: os testes que só
 * clicavam "Kanban" passavam, e qualquer um que precisasse voltar pra
 * "Assistente" ficava esperando pra sempre um botão "Assistente" que nunca
 * existe (o real é "Assistant", `messages/en.json::ide_mode_assistente`). */
const MODE_LABEL = {
  Assistente: "Assistant",
  IDE: "IDE",
  Kanban: "Kanban",
} as const;

function modeButton(page: Page, label: "Assistente" | "IDE" | "Kanban") {
  return page.getByRole("button", { name: MODE_LABEL[label], exact: true });
}

test.describe("troca de modo de layout (Assistente/IDE/Kanban)", () => {
  test.beforeEach(async ({ page }) => {
    await startSession(page);
  });

  test("Kanban nunca mostra o chat por trás do board", async ({ page }) => {
    await modeButton(page, "Kanban").click();

    await expect(page.getByTestId("kanban-col-todo")).toBeVisible({
      timeout: 10_000,
    });
    // A guarda explícita (independente de animação) precisa esconder o
    // chat imediatamente — sem esperar nenhum timeout de convergência.
    await expect(page.getByTestId("chat-input")).not.toBeVisible();
  });

  test("Assistente → Kanban → Assistente: o chat volta a aparecer, sem ghost do board", async ({
    page,
  }) => {
    await modeButton(page, "Kanban").click();
    await expect(page.getByTestId("kanban-col-todo")).toBeVisible({
      timeout: 10_000,
    });

    await modeButton(page, "Assistente").click();
    await expect(page.getByTestId("chat-input")).toBeVisible({
      timeout: 10_000,
    });
    await expect(page.getByTestId("kanban-col-todo")).not.toBeVisible();
  });

  test("trocar rapidamente entre os 3 modos nunca deixa dois conteúdos visíveis ao mesmo tempo", async ({
    page,
  }) => {
    const sequence = ["IDE", "Kanban", "Assistente", "Kanban", "IDE"] as const;

    for (const mode of sequence) {
      await modeButton(page, mode).click();
      if (mode === "Kanban") {
        await expect(page.getByTestId("kanban-col-todo")).toBeVisible({
          timeout: 10_000,
        });
        await expect(page.getByTestId("chat-input")).not.toBeVisible();
      } else {
        await expect(page.getByTestId("chat-input")).toBeVisible({
          timeout: 10_000,
        });
        await expect(page.getByTestId("kanban-col-todo")).not.toBeVisible();
      }
    }
  });

  test("o scroll do chat não volta pro topo ao trocar de modo e voltar", async ({
    page,
  }) => {
    const list = page.getByLabel("Messages");
    await expect(list).toBeVisible({ timeout: 10_000 });

    // Garante conteúdo rolável e leva o scroll pro fim.
    await list.evaluate((el) => {
      el.scrollTop = el.scrollHeight;
    });
    const before = await list.evaluate((el) => el.scrollTop);
    test.skip(before === 0, "conversa curta demais para ter scroll");

    await modeButton(page, "Kanban").click();
    await expect(page.getByTestId("kanban-col-todo")).toBeVisible({
      timeout: 10_000,
    });
    await modeButton(page, "Assistente").click();
    await expect(page.getByTestId("chat-input")).toBeVisible({
      timeout: 10_000,
    });

    // Tolerância pequena: o conteúdo pode remedir ao remontar, mas não
    // pode voltar ao topo (o sintoma relatado).
    await expect
      .poll(
        async () =>
          await page.getByLabel("Messages").evaluate((el) => el.scrollTop),
        { timeout: 10_000 },
      )
      .toBeGreaterThan(before * 0.8);
  });
});
