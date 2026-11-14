// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { Dialog, DialogTitle } from "@/components/ui/dialog";
import { ResizableDialogContent } from "../resizable-dialog";
import { useDialogSizeStore } from "@/lib/stores/dialog-size-store";

vi.mock("@/lib/paraglide/messages", () => ({
  m: {
    dialog_close: () => "Fechar",
    dialog_resize: () => "Redimensionar diálogo",
  },
}));

function renderDialog(storageKey?: string) {
  return render(
    <Dialog open>
      <ResizableDialogContent
        storageKey={storageKey}
        defaultWidth={500}
        defaultHeight={400}
        minWidth={300}
        minHeight={200}
        aria-describedby={undefined}
      >
        <DialogTitle>Título</DialogTitle>
      </ResizableDialogContent>
    </Dialog>,
  );
}

const content = () => screen.getByRole("dialog");

/** Arrasta a alça: jsdom não implementa pointer capture. */
function drag(dx: number, dy: number) {
  const handle = screen.getByRole("separator", {
    name: "Redimensionar diálogo",
  });
  Object.assign(handle, {
    setPointerCapture: vi.fn(),
    releasePointerCapture: vi.fn(),
  });
  fireEvent.pointerDown(handle, { clientX: 100, clientY: 100, pointerId: 1 });
  fireEvent.pointerMove(handle, {
    clientX: 100 + dx,
    clientY: 100 + dy,
    pointerId: 1,
  });
  fireEvent.pointerUp(handle, { pointerId: 1 });
}

beforeEach(() => {
  useDialogSizeStore.setState({ sizes: {} });
  window.localStorage.clear();
});
afterEach(cleanup);

describe("ResizableDialogContent", () => {
  it("abre no tamanho padrão", () => {
    renderDialog();

    expect(content().style.width).toBe("500px");
    expect(content().style.height).toBe("400px");
  });

  it("arrastar a alça muda o tamanho", () => {
    renderDialog();

    drag(120, 80);

    expect(content().style.width).toBe("620px");
    expect(content().style.height).toBe("480px");
  });

  it("nunca encolhe abaixo do mínimo", () => {
    renderDialog();

    drag(-1000, -1000);

    expect(content().style.width).toBe("300px");
    expect(content().style.height).toBe("200px");
  });

  it("com storageKey o tamanho fica salvo e volta na próxima abertura", () => {
    const first = renderDialog("meu-dialogo");
    drag(100, 50);
    expect(useDialogSizeStore.getState().sizes["meu-dialogo"]).toEqual({
      w: 600,
      h: 450,
    });
    first.unmount();

    renderDialog("meu-dialogo");

    expect(content().style.width).toBe("600px");
    expect(content().style.height).toBe("450px");
  });

  it("sem storageKey nada é salvo", () => {
    renderDialog();
    drag(100, 50);

    expect(useDialogSizeStore.getState().sizes).toEqual({});
  });

  it("chaves diferentes guardam tamanhos independentes", () => {
    const a = renderDialog("a");
    drag(100, 0);
    a.unmount();
    const b = renderDialog("b");

    expect(content().style.width).toBe("500px");
    b.unmount();
    expect(useDialogSizeStore.getState().sizes.a?.w).toBe(600);
  });

  it("o botão de fechar tem nome acessível traduzido", () => {
    renderDialog();

    expect(screen.getByText("Fechar").closest("button")).toBeTruthy();
  });
});
