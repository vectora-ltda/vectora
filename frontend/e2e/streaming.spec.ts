import { test, expect, type Page } from "@playwright/test";

/**
 * E2E (browser real): streaming token-a-token renderizado incrementalmente.
 *
 * Prova o invariante do bug #2: a resposta aparece CRESCENDO ao longo do tempo,
 * não de uma vez só no final. Amostramos o comprimento do texto da mensagem do
 * assistente em alta frequência enquanto `data-streaming="true"` e exigimos
 * vários comprimentos crescentes distintos — impossível se a saída fosse
 * bufferizada (RunnableRetry), onde o texto saltaria de vazio → completo entre
 * duas amostras (só 2 comprimentos distintos).
 *
 * Requer backend + LLM real (ver playwright.config.ts).
 */

const PROMPT = "Conte de 1 até 20, um número por linha, sem texto extra.";

async function sendPrompt(page: Page, text: string): Promise<void> {
  const input = page.getByTestId("chat-input");
  await expect(input).toBeVisible({ timeout: 30_000 });
  await input.fill(text);
  await page.getByTestId("chat-send").click();
}

test("a resposta do assistente é renderizada incrementalmente (streaming)", async ({
  page,
}) => {
  await page.goto("/");
  await sendPrompt(page, PROMPT);

  const content = page.getByTestId("message-content-assistant").last();
  // O bubble do assistente aparece (cursor/1º token) rápido.
  await expect(content).toBeAttached({ timeout: 30_000 });

  // Amostragem de alta frequência do comprimento do texto enquanto streama.
  const samples: { t: number; len: number }[] = [];
  const start = Date.now();
  const DEADLINE_MS = 90_000;
  while (Date.now() - start < DEADLINE_MS) {
    const len = (await content.textContent())?.length ?? 0;
    samples.push({ t: Date.now() - start, len });
    const streaming = await content.getAttribute("data-streaming");
    if (streaming === "false" && len > 0) break;
    await page.waitForTimeout(40);
  }

  const finalLen = samples.at(-1)?.len ?? 0;
  expect(finalLen, "resposta final deve ter conteúdo").toBeGreaterThan(0);

  // Comprimentos crescentes distintos observados ANTES do fim.
  const distinctGrowing = [...new Set(samples.map((s) => s.len))]
    .filter((n) => n > 0)
    .sort((a, b) => a - b);
  expect(
    distinctGrowing.length,
    `esperado crescimento incremental; amostras=${JSON.stringify(samples)}`,
  ).toBeGreaterThanOrEqual(3);

  // O 1º conteúdo não-vazio chega bem antes da amostra final (TTFT << total).
  const firstNonEmpty = samples.find((s) => s.len > 0);
  const last = samples.at(-1);
  expect(firstNonEmpty).toBeTruthy();
  expect(last!.t).toBeGreaterThan(firstNonEmpty!.t);
});

test("erro de provider não vira a 'resposta' crua da IA", async ({ page }) => {
  // Não forçamos 429 aqui (depende de quota); este teste documenta o contrato:
  // se um erro de stream ocorrer, a bolha entra em estado de erro
  // (data-error="true") com texto limpo — nunca um JSON cru do provider.
  await page.goto("/");
  await sendPrompt(page, "Olá!");

  const content = page.getByTestId("message-content-assistant").last();
  await expect(content).toBeAttached({ timeout: 30_000 });
  await expect(content).toHaveAttribute("data-streaming", "false", {
    timeout: 90_000,
  });

  const text = (await content.textContent()) ?? "";
  // Em nenhuma hipótese o texto exibido contém o ruído cru de erro do provider.
  expect(text).not.toContain("RESOURCE_EXHAUSTED");
  expect(text).not.toMatch(/Erro no stream:/i);
});
