// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { TooltipProvider } from "@/components/ui/tooltip";
import { PlusMenu } from "../plus-menu";

vi.mock("@/lib/stores/environment-dialog-store", () => ({
  useEnvironmentDialogStore: (
    selector: (state: { openAt: () => void }) => unknown,
  ) => selector({ openAt: vi.fn() }),
}));

vi.mock("@/lib/stores/workspaces-store", () => ({
  useWorkspacesStore: (
    selector: (state: { getActive: () => null }) => unknown,
  ) => selector({ getActive: () => null }),
}));

vi.mock("@/components/sidebar/workspace-trust-dialog", () => ({
  WorkspaceTrustDialog: () => null,
}));

vi.mock("@/lib/paraglide/messages", () => ({
  m: {
    tooltip_chat_add_files: () => "Adicionar arquivos",
    plus_add_files: () => "Adicionar arquivos",
    plus_capture_screenshot: () => "Capturar screenshot",
    plus_add_folder: () => "Adicionar pasta",
    plus_ingest_folder: () => "Importar pasta",
    plus_slash_commands: () => "Comandos",
    plus_connectors: () => "Conectores",
  },
}));

afterEach(cleanup);

function renderMenu(onCaptureScreenshot?: () => void) {
  return render(
    <TooltipProvider>
      <PlusMenu
        onAddFiles={() => {}}
        onCaptureScreenshot={onCaptureScreenshot}
      />
    </TooltipProvider>,
  );
}

async function openMenu() {
  fireEvent.click(screen.getByTestId("plus-menu-trigger"));
  return screen.findByTestId("plus-menu-add-folder");
}

describe("PlusMenu — captura de screenshot", () => {
  it("não renderiza a ação sem callback", async () => {
    renderMenu();
    await openMenu();

    expect(screen.queryByTestId("plus-menu-capture-screenshot")).toBeNull();
  });

  it("renderiza a ação quando recebe callback", async () => {
    renderMenu(() => {});
    await openMenu();

    expect(
      screen.getByTestId("plus-menu-capture-screenshot"),
    ).toBeInTheDocument();
  });

  it("chama o callback ao clicar na ação", async () => {
    const onCaptureScreenshot = vi.fn();
    renderMenu(onCaptureScreenshot);
    await openMenu();

    fireEvent.click(screen.getByTestId("plus-menu-capture-screenshot"));
    expect(onCaptureScreenshot).toHaveBeenCalledOnce();
  });
});
