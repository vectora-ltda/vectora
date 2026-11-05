import { test, expect, type Page } from "@playwright/test";

/**
 * E2E (browser real): pedir um plano no chat gera tarefas reais
 * (write_todos, TodoListMiddleware) visíveis na aba Plano do workbench.
 *
 * Sem mock: o LLM real decide criar a checklist a partir do prompt; a aba
 * Plano lê o estado ao vivo do workbench-store (que reflete os eventos SSE
 * do backend). Pode ser lento — timeouts generosos, no padrão de
 * `streaming.spec.ts`.
 *
 * Requer backend + LLM real (ver playwright.config.ts).
 */

const PLAN_PROMPT =
  "Crie um plano com exatamente 3 tarefas para organizar uma festa de aniversário simples. Não execute nada, só liste o plano.";

async function sendPrompt(page: Page, text: string): Promise<void> {
  const input = page.getByTestId("chat-input");
  await expect(input).toBeVisible({ timeout: 30_000 });
  await input.fill(text);
  await page.getByTestId("chat-send").click();
}

test.describe("plano criado via prompt aparece na aba Plano", () => {
  test("prompt de plano gera tarefas visíveis na aba Plano do workbench", async ({
    page,
  }) => {
    await page.goto("/");
    await sendPrompt(page, PLAN_PROMPT);

    const assistant = page.getByTestId("message-content-assistant").last();
    await expect(assistant).toBeAttached({ timeout: 60_000 });

    // Abre a aba Plano enquanto a resposta ainda pode estar completando —
    // o checklist do write_todos chega via SSE, não precisa esperar o fim
    // do streaming de texto.
    await page.getByTestId("workbench-nav-plan").click();
    await expect(page.getByTestId("workbench-tab-content")).toHaveAttribute(
      "data-tab",
      "plan",
    );

    const todosTrigger = page.getByTestId("plan-todos-trigger");
    await expect(todosTrigger).toBeVisible({ timeout: 90_000 });

    // Garante que a seção de tarefas está expandida para contar os itens.
    const expanded = await todosTrigger.getAttribute("data-state");
    if (expanded !== "open") {
      await todosTrigger.click();
    }

    const items = page.getByTestId("plan-todo-item");
    await expect(items.first()).toBeVisible({ timeout: 15_000 });
    const count = await items.count();
    expect(
      count,
      "o plano deve ter pelo menos 1 tarefa listada",
    ).toBeGreaterThan(0);
  });

  test("as tarefas do plano têm texto (não itens vazios)", async ({ page }) => {
    await page.goto("/");
    await sendPrompt(page, PLAN_PROMPT);

    await page.getByTestId("workbench-nav-plan").click();
    const todosTrigger = page.getByTestId("plan-todos-trigger");
    await expect(todosTrigger).toBeVisible({ timeout: 90_000 });
    if ((await todosTrigger.getAttribute("data-state")) !== "open") {
      await todosTrigger.click();
    }

    const items = page.getByTestId("plan-todo-item");
    await expect(items.first()).toBeVisible({ timeout: 15_000 });
    const texts = await items.allTextContents();
    for (const t of texts) {
      expect(t.trim().length, `item de tarefa vazio: "${t}"`).toBeGreaterThan(
        0,
      );
    }
  });

  test("recarregar a página mantém o plano visível (persistido, não só em memória)", async ({
    page,
  }) => {
    await page.goto("/");
    await sendPrompt(page, PLAN_PROMPT);

    await page.getByTestId("workbench-nav-plan").click();
    await expect(page.getByTestId("plan-todos-trigger")).toBeVisible({
      timeout: 90_000,
    });
    await expect(page).toHaveURL(/\/session\/.+/, { timeout: 30_000 });
    const sessionUrl = page.url();

    await page.reload();
    await page.goto(sessionUrl);

    await page.getByTestId("workbench-nav-plan").click();
    await expect(page.getByTestId("workbench-tab-content")).toHaveAttribute(
      "data-tab",
      "plan",
    );
    // Depois do reload, o plano precisa reaparecer — ele é revalidado do
    // backend (`fetchArtifacts`/thread activity), não só cache em memória.
    await expect(page.getByTestId("plan-todos-trigger")).toBeVisible({
      timeout: 30_000,
    });
  });

  test("prompt sem pedido de plano não cria tarefas (sem falso positivo)", async ({
    page,
  }) => {
    await page.goto("/");
    await sendPrompt(page, "Olá, tudo bem?");

    const assistant = page.getByTestId("message-content-assistant").last();
    await expect(assistant).toHaveAttribute("data-streaming", "false", {
      timeout: 90_000,
    });

    await page.getByTestId("workbench-nav-plan").click();
    await expect(page.getByTestId("plan-todos-trigger")).not.toBeVisible({
      timeout: 10_000,
    });
  });
});
