import { test, expect, type Page } from "@playwright/test";

/**
 * E2E (browser real): fluxo Git ponta a ponta via UI.
 *
 * Cria um arquivo real pela aba Arquivos (workspace confiado com git),
 * confirma que a mudança aparece como "untracked" na aba Git (Mudanças),
 * dá stage + commit reais pela UI, e confirma que a lista de mudanças
 * esvazia depois do commit.
 *
 * Requer backend real (ver playwright.config.ts). Não depende de LLM —
 * é fluxo de UI/filesystem/git puro.
 */

const WORKSPACE_FOLDER_NAME = `vectora-e2e-git-${Date.now()}`;
const NEW_FILE_NAME = `e2e-${Date.now()}.txt`;

async function ensureWorkspace(page: Page): Promise<void> {
  await page.goto("/");

  await page.getByTestId("plus-menu-trigger").click();
  await page.getByTestId("plus-menu-add-folder").click();

  const pathInput = page.getByTestId("workspace-path-input");
  await expect(pathInput).toBeVisible({ timeout: 15_000 });

  // Cria uma pasta nova dentro do diretório inicial listado — não depende
  // de conhecer um caminho pré-existente no disco do backend.
  await page.getByTestId("workspace-new-folder-btn").click();
  const newFolderInput = page.getByTestId("workspace-new-folder-input");
  await expect(newFolderInput).toBeVisible({ timeout: 10_000 });
  await newFolderInput.fill(WORKSPACE_FOLDER_NAME);
  await page.getByTestId("workspace-new-folder-create-btn").click();

  // A criação recarrega o listing dentro da nova pasta (vazia) — navega
  // para dentro dela antes de confiar, para o workspace apontar pra lá.
  await page.getByText(WORKSPACE_FOLDER_NAME).first().click();

  const gitInit = page.getByTestId("workspace-git-init-checkbox");
  await expect(gitInit).toBeChecked({ timeout: 10_000 });

  await page.getByTestId("workspace-trust-confirm-btn").click();
  await expect(pathInput).not.toBeVisible({ timeout: 15_000 });
}

test.describe("fluxo Git via UI (workspace real)", () => {
  test.beforeEach(async ({ page }) => {
    await ensureWorkspace(page);
  });

  test("criar um arquivo novo pela aba Arquivos aparece como untracked na aba Git", async ({
    page,
  }) => {
    await page.getByTestId("workbench-nav-files").click();
    await expect(page.getByTestId("workbench-tab-content")).toHaveAttribute(
      "data-tab",
      "files",
    );

    await page.getByTestId("files-new-file-btn").click();
    const createInput = page.getByTestId("files-inline-create-input");
    await expect(createInput).toBeVisible({ timeout: 10_000 });
    await createInput.fill(NEW_FILE_NAME);
    await createInput.press("Enter");

    // Arquivo aparece na árvore.
    await expect(page.getByText(NEW_FILE_NAME).first()).toBeVisible({
      timeout: 10_000,
    });

    // Troca para a aba Git — a mudança precisa aparecer (untracked).
    await page.getByTestId("workbench-nav-git").click();
    await expect(page.getByTestId("workbench-tab-content")).toHaveAttribute(
      "data-tab",
      "git",
    );
    await expect(page.getByText(NEW_FILE_NAME).first()).toBeVisible({
      timeout: 30_000,
    });
  });

  test("stage + commit reais pela UI esvaziam a lista de mudanças", async ({
    page,
  }) => {
    await page.getByTestId("workbench-nav-files").click();
    await page.getByTestId("files-new-file-btn").click();
    const createInput = page.getByTestId("files-inline-create-input");
    await createInput.fill(NEW_FILE_NAME);
    await createInput.press("Enter");
    await expect(page.getByText(NEW_FILE_NAME).first()).toBeVisible({
      timeout: 10_000,
    });

    await page.getByTestId("workbench-nav-git").click();
    const fileRow = page.getByText(NEW_FILE_NAME).first();
    await expect(fileRow).toBeVisible({ timeout: 30_000 });

    // Botão de stage só fica visível em hover (opacity-0 group-hover) —
    // hover explícito na linha antes de clicar.
    await fileRow.hover();
    await page.getByTestId("git-stage-file-btn").first().click();

    await page
      .getByTestId("git-commit-message")
      .fill("e2e: commit real via playwright");
    await page.getByTestId("git-commit-btn").click();

    // Depois do commit a lista de mudanças esvazia — o arquivo commitado
    // não aparece mais como pendente.
    await expect(page.getByText(NEW_FILE_NAME)).not.toBeVisible({
      timeout: 30_000,
    });
  });

  test("commitar sem mensagem não é permitido (botão desabilitado)", async ({
    page,
  }) => {
    await page.getByTestId("workbench-nav-files").click();
    await page.getByTestId("files-new-file-btn").click();
    const createInput = page.getByTestId("files-inline-create-input");
    await createInput.fill(NEW_FILE_NAME);
    await createInput.press("Enter");

    await page.getByTestId("workbench-nav-git").click();
    await expect(page.getByText(NEW_FILE_NAME).first()).toBeVisible({
      timeout: 30_000,
    });

    // Sem digitar mensagem de commit, o botão fica desabilitado — mesmo com
    // mudanças pendentes, um commit vazio de mensagem não deve ser possível.
    await expect(page.getByTestId("git-commit-btn")).toBeDisabled();
  });
});
