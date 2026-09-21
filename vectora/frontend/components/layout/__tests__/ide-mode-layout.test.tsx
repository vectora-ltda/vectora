// @vitest-environment jsdom
/**
 * IdeModeLayout — no estado wide os quatro painéis do modo IDE
 * (nav-bar, workbench, editor, chat) renderizam lado a lado sem mudança de
 * comportamento. No estado mobile, só o painel selecionado fica montado, e a
 * faixa de abas no topo troca qual está visível.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen, within, fireEvent } from "@testing-library/react";
import { useState } from "react";

import { IdeModeLayout } from "@/components/layout/ide-mode-layout";

function renderLayout(layoutState: "wide" | "mobile") {
  return render(
    <IdeModeLayout
      layoutState={layoutState}
      header={<div data-testid="panel-header">Header</div>}
      navBar={<div data-testid="panel-navbar">NavBar</div>}
      workbenchContent={<div data-testid="panel-workbench">Workbench</div>}
      editor={<div data-testid="panel-editor">Editor</div>}
      chat={<div data-testid="panel-chat">Chat</div>}
    />,
  );
}

describe("IdeModeLayout", () => {
  it("viewport larga: os quatro painéis renderizam lado a lado (regressão do layout atual)", () => {
    renderLayout("wide");

    expect(screen.getByTestId("panel-navbar")).toBeInTheDocument();
    expect(screen.getByTestId("panel-workbench")).toBeInTheDocument();
    expect(screen.getByTestId("panel-editor")).toBeInTheDocument();
    expect(screen.getByTestId("panel-chat")).toBeInTheDocument();
    // Sem faixa de abas — não existe no layout largo.
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
  });

  it.each(["ltr", "rtl"] as const)(
    "inclui a rail fixa na largura da coluna do Workbench (%s)",
    (direction) => {
      render(
        <IdeModeLayout
          layoutState="wide"
          direction={direction}
          workbenchWidth={268}
          workbenchMinWidth={268}
          workbenchMaxWidth={528}
          header={<div />}
          navBar={<div data-testid="panel-navbar" />}
          workbenchContent={<div data-testid="panel-workbench" />}
          editor={<div data-testid="panel-editor" />}
          chat={<div data-testid="panel-chat" />}
        />,
      );

      expect(
        screen.getByRole("complementary", { name: "Workbench" }),
      ).toHaveStyle({ width: "268px", minWidth: "268px" });
    },
  );

  it("viewport estreita: só o painel ativo aparece no DOM; trocar de aba muda qual está visível", () => {
    renderLayout("mobile");

    // Default: editor.
    expect(screen.getByTestId("panel-editor")).toBeInTheDocument();
    expect(screen.queryByTestId("panel-chat")).not.toBeInTheDocument();
    expect(screen.queryByTestId("panel-workbench")).not.toBeInTheDocument();
    expect(screen.queryByTestId("panel-navbar")).not.toBeInTheDocument();

    const tablist = screen.getByRole("tablist");
    fireEvent.click(within(tablist).getByTestId("ide-mobile-tab-chat"));

    expect(screen.getByTestId("panel-chat")).toBeInTheDocument();
    expect(screen.queryByTestId("panel-editor")).not.toBeInTheDocument();
    expect(screen.queryByTestId("panel-workbench")).not.toBeInTheDocument();

    fireEvent.click(within(tablist).getByTestId("ide-mobile-tab-workbench"));

    expect(screen.getByTestId("panel-workbench")).toBeInTheDocument();
    expect(screen.getByTestId("panel-navbar")).toBeInTheDocument();
    expect(screen.queryByTestId("panel-chat")).not.toBeInTheDocument();
    expect(screen.queryByTestId("panel-editor")).not.toBeInTheDocument();
  });

  it("borda: viewport estreita sem workbenchContent (painel fechado) não quebra ao selecionar a aba workbench", () => {
    render(
      <IdeModeLayout
        layoutState="mobile"
        header={<div data-testid="panel-header">Header</div>}
        navBar={<div data-testid="panel-navbar">NavBar</div>}
        workbenchContent={null}
        editor={<div data-testid="panel-editor">Editor</div>}
        chat={<div data-testid="panel-chat">Chat</div>}
      />,
    );

    fireEvent.click(screen.getByTestId("ide-mobile-tab-workbench"));

    expect(screen.getByTestId("panel-navbar")).toBeInTheDocument();
    expect(screen.queryByTestId("panel-workbench")).not.toBeInTheDocument();
    expect(() => screen.getByTestId("panel-workbench")).toThrow();
  });

  it("viewport larga: header ocupa o slot físico e editor permanece na coluna central", () => {
    renderLayout("wide");

    const header = screen.getByTestId("panel-header");
    const chat = screen.getByTestId("panel-chat");
    const headerSlot = header.parentElement!;
    const centerColumn = screen.getByRole("main");
    expect(headerSlot).not.toContainElement(screen.getByTestId("panel-editor"));
    expect(centerColumn).toContainElement(screen.getByTestId("panel-editor"));
    expect(centerColumn).not.toContainElement(
      screen.getByTestId("panel-navbar"),
    );
    expect(centerColumn).not.toContainElement(chat);
  });

  it("viewport estreita: header aparece acima da faixa de abas, independente de qual painel está ativo", () => {
    renderLayout("mobile");

    expect(screen.getByTestId("panel-header")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("ide-mobile-tab-chat"));
    expect(screen.getByTestId("panel-header")).toBeInTheDocument();
  });

  it("viewport estreita: fechar a workbench troca automaticamente para o editor", () => {
    const { rerender } = render(
      <IdeModeLayout
        layoutState="mobile"
        workbenchOpen
        header={<div data-testid="panel-header">Header</div>}
        navBar={<div data-testid="panel-navbar">NavBar</div>}
        workbenchContent={<div data-testid="panel-workbench">Workbench</div>}
        editor={<div data-testid="panel-editor">Editor</div>}
        chat={<div data-testid="panel-chat">Chat</div>}
      />,
    );
    fireEvent.click(screen.getByTestId("ide-mobile-tab-workbench"));
    expect(screen.getByTestId("panel-workbench")).toBeInTheDocument();

    rerender(
      <IdeModeLayout
        layoutState="mobile"
        workbenchOpen={false}
        header={<div data-testid="panel-header">Header</div>}
        navBar={<div data-testid="panel-navbar">NavBar</div>}
        workbenchContent={null}
        editor={<div data-testid="panel-editor">Editor</div>}
        chat={<div data-testid="panel-chat">Chat</div>}
      />,
    );
    expect(screen.getByTestId("panel-editor")).toBeInTheDocument();
    expect(screen.queryByTestId("panel-workbench")).not.toBeInTheDocument();
  });

  it("viewport estreita: a aba Workbench reabre um painel fechado", () => {
    const onOpenWorkbench = vi.fn();
    function ReopenHarness() {
      const [workbenchOpen, setWorkbenchOpen] = useState(false);
      return (
        <IdeModeLayout
          layoutState="mobile"
          workbenchOpen={workbenchOpen}
          onOpenWorkbench={() => {
            onOpenWorkbench();
            setWorkbenchOpen(true);
          }}
          header={<div data-testid="panel-header">Header</div>}
          navBar={<div data-testid="panel-navbar">NavBar</div>}
          workbenchContent={<div data-testid="panel-workbench">Workbench</div>}
          editor={<div data-testid="panel-editor">Editor</div>}
          chat={<div data-testid="panel-chat">Chat</div>}
        />
      );
    }

    render(<ReopenHarness />);

    fireEvent.click(screen.getByTestId("ide-mobile-tab-workbench"));

    expect(onOpenWorkbench).toHaveBeenCalledOnce();
    expect(screen.getByTestId("panel-workbench")).toBeInTheDocument();
  });
});
