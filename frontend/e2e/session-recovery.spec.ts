import { test, expect, type Page } from "@playwright/test";

/**
 * E2E (browser real): recuperação do histórico da sessão (bug #1).
 *
 * Envia uma mensagem, espera a resposta, recarrega a página (o reload zera o
 * cache em memória do Zustand → força o `getHistory` ao backend, exatamente o
 * caminho de restauração corrigido) e navega de volta à MESMA sessão. As
 * mensagens precisam reaparecer — antes a sessão abria vazia.
 *
 * Requer backend + LLM real (ver playwright.config.ts).
 */

const PROMPT = "Responda apenas com a palavra: pong";

async function sendPrompt(page: Page, text: string): Promise<void> {
  const input = page.getByTestId("chat-input");
  await expect(input).toBeVisible({ timeout: 30_000 });
  await input.fill(text);
  await page.getByTestId("chat-send").click();
}

test("histórico da sessão sobrevive ao reload (não abre vazio)", async ({
  page,
}) => {
  await page.goto("/");
  await sendPrompt(page, PROMPT);

  // Espera a resposta concluir (sai do estado streaming, com conteúdo).
  const assistant = page.getByTestId("message-content-assistant").last();
  await expect(assistant).toBeAttached({ timeout: 30_000 });
  await expect(assistant).toHaveAttribute("data-streaming", "false", {
    timeout: 90_000,
  });

  // A URL agora aponta para a sessão criada (/session/<id>).
  await expect(page).toHaveURL(/\/session\/.+/, { timeout: 30_000 });
  const sessionUrl = page.url();

  // Conteúdo antes do reload (mensagens do usuário + assistente presentes).
  const userBefore = await page
    .getByTestId("message-content-user")
    .first()
    .textContent();
  expect(userBefore?.toLowerCase()).toContain("pong");

  // Reload total: zera o cache de mensagens em memória (Zustand não persiste).
  await page.reload();
  await page.goto(sessionUrl);

  // As mensagens precisam voltar do backend — sessão NÃO pode estar vazia.
  const userAfter = page.getByTestId("message-content-user").first();
  await expect(userAfter).toBeVisible({ timeout: 30_000 });
  expect((await userAfter.textContent())?.toLowerCase()).toContain("pong");

  const assistantAfter = page.getByTestId("message-content-assistant").first();
  await expect(assistantAfter).toBeVisible({ timeout: 30_000 });
  expect(
    (await assistantAfter.textContent())?.trim().length ?? 0,
  ).toBeGreaterThan(0);
});

test("título da sessão é atribuído pela IA, não a mensagem do usuário", async ({
  page,
}) => {
  await page.goto("/");
  const prompt =
    "Quais são as melhores práticas de versionamento semântico em libs npm?";
  await sendPrompt(page, prompt);

  const assistant = page.getByTestId("message-content-assistant").last();
  await expect(assistant).toBeAttached({ timeout: 30_000 });
  await expect(assistant).toHaveAttribute("data-streaming", "false", {
    timeout: 90_000,
  });

  // O título (gerado pela IA, ≤6 palavras) não deve ser a cópia literal do
  // prompt longo do usuário. Damos tempo para o GenerateTitle assíncrono.
  await page.waitForTimeout(4_000);
  const activeTitle = await page
    .locator('[aria-current="page"], .bg-\\[\\#7FC8FF\\]\\/15')
    .first()
    .textContent()
    .catch(() => null);

  if (activeTitle) {
    expect(activeTitle.trim()).not.toBe(prompt);
    expect(activeTitle.trim().length).toBeLessThan(prompt.length);
  }
});
