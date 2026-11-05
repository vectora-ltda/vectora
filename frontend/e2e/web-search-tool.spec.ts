import { test, expect, type Page } from "@playwright/test";

/**
 * E2E (browser real): a tool `web_search` é chamada de verdade (Tavily
 * real, configurado em ~/.vectora/.env) quando o prompt exige informação
 * atual que o modelo não tem de memória.
 *
 * Sem mock: o agente decide sozinho chamar a tool, o Tavily real responde,
 * e a resposta final referencia o conteúdo da busca. Pode ser lento (LLM +
 * tool call real) — timeouts generosos, no padrão de `streaming.spec.ts`.
 *
 * Requer backend + LLM real + TAVILY_API_KEY (ver playwright.config.ts e
 * e2e/README.md).
 */

const SEARCH_PROMPT =
  "Busque na web a versão estável mais recente do FastAPI (Python) hoje e me diga o número da versão.";

async function sendPrompt(page: Page, text: string): Promise<void> {
  const input = page.getByTestId("chat-input");
  await expect(input).toBeVisible({ timeout: 30_000 });
  await input.fill(text);
  await page.getByTestId("chat-send").click();
}

test("prompt que exige busca atual dispara a tool web_search real e a resposta chega", async ({
  page,
}) => {
  await page.goto("/");
  await sendPrompt(page, SEARCH_PROMPT);

  // A tool call de busca web aparece renderizada no chat antes da resposta
  // final — tempo generoso: o agente primeiro decide chamar a tool, espera
  // o Tavily real responder, só depois retoma o streaming da resposta.
  const searchTool = page
    .getByTestId("tool-call")
    .filter({ has: page.getByTestId("tool-call-name") })
    .filter({ hasText: /search/i })
    .first();
  await expect(searchTool).toBeVisible({ timeout: 60_000 });

  const assistant = page.getByTestId("message-content-assistant").last();
  await expect(assistant).toBeAttached({ timeout: 60_000 });
  await expect(assistant).toHaveAttribute("data-streaming", "false", {
    timeout: 120_000,
  });

  const text = (await assistant.textContent())?.trim() ?? "";
  expect(text.length, "resposta final não pode ficar vazia").toBeGreaterThan(0);
  // A resposta cita um número de versão (padrão semver) — prova que o
  // conteúdo da busca real foi incorporado, não é uma alucinação genérica
  // sem tool call ("não sei", "não tenho acesso à internet").
  expect(text).toMatch(/\d+\.\d+(\.\d+)?/);
  expect(text.toLowerCase()).not.toMatch(
    /não tenho acesso à internet|i don't have access to the internet/,
  );
});

test("erro de tool web_search não vira JSON cru na resposta", async ({
  page,
}) => {
  // Documenta o contrato de erro (como em streaming.spec.ts): se a tool
  // falhar (rede, quota do Tavily), a bolha de resposta precisa continuar
  // com texto legível — nunca o traceback/JSON cru da exceção da tool.
  await page.goto("/");
  await sendPrompt(page, SEARCH_PROMPT);

  const assistant = page.getByTestId("message-content-assistant").last();
  await expect(assistant).toBeAttached({ timeout: 60_000 });
  await expect(assistant).toHaveAttribute("data-streaming", "false", {
    timeout: 120_000,
  });

  const text = (await assistant.textContent()) ?? "";
  expect(text).not.toContain("Traceback (most recent call last)");
  expect(text).not.toMatch(/"error":\s*"/);
});

test("segundo prompt de busca numa sessão nova também completa (não é fluke de cache)", async ({
  page,
}) => {
  await page.goto("/");
  await sendPrompt(
    page,
    "Pesquise na web e diga: qual é a licença open-source do projeto FastAPI?",
  );

  const assistant = page.getByTestId("message-content-assistant").last();
  await expect(assistant).toBeAttached({ timeout: 60_000 });
  await expect(assistant).toHaveAttribute("data-streaming", "false", {
    timeout: 120_000,
  });

  const text = (await assistant.textContent())?.trim() ?? "";
  expect(text.length).toBeGreaterThan(0);
  expect(text.toLowerCase()).toMatch(/mit|apache|licen/);
});
