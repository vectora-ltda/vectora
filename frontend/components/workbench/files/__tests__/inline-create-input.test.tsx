// @vitest-environment jsdom
/**
 * InlineCreateInput — input de criação de arquivo/pasta na árvore.
 * Validação de nome vazio e confirmação/cancelamento via teclado e blur.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";

import { InlineCreateInput } from "../inline-create-input";

afterEach(() => {
  cleanup();
});

function setup(onConfirm = vi.fn(), onCancel = vi.fn()) {
  render(
    <InlineCreateInput
      placeholder="novo arquivo"
      onConfirm={onConfirm}
      onCancel={onCancel}
      depth={1}
    />,
  );
  return { onConfirm, onCancel };
}

describe("InlineCreateInput", () => {
  it("renderiza o input vazio com o placeholder recebido", () => {
    setup();
    const input = screen.getByPlaceholderText(
      "novo arquivo",
    ) as HTMLInputElement;
    expect(input.value).toBe("");
  });

  it("Enter com nome preenchido chama onConfirm com o nome trimado", () => {
    const { onConfirm } = setup();
    const input = screen.getByPlaceholderText("novo arquivo");
    fireEvent.change(input, { target: { value: "  novo.ts  " } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onConfirm).toHaveBeenCalledWith("novo.ts");
  });

  it("Enter com nome vazio (ou só espaços) não chama onConfirm — validação de borda", () => {
    const { onConfirm } = setup();
    const input = screen.getByPlaceholderText("novo arquivo");
    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("Escape chama onCancel e não onConfirm", () => {
    const { onConfirm, onCancel } = setup();
    const input = screen.getByPlaceholderText("novo arquivo");
    fireEvent.change(input, { target: { value: "algo.ts" } });
    fireEvent.keyDown(input, { key: "Escape" });
    expect(onCancel).toHaveBeenCalledOnce();
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("perder o foco (blur) chama onCancel", () => {
    const { onCancel } = setup();
    const input = screen.getByPlaceholderText("novo arquivo");
    fireEvent.blur(input);
    expect(onCancel).toHaveBeenCalledOnce();
  });

  it("aplica indentação (paddingLeft) proporcional a depth", () => {
    setup();
    const input = screen.getByPlaceholderText("novo arquivo");
    const wrapper = input.parentElement as HTMLElement;
    expect(wrapper.style.paddingLeft).toBe("20px");
  });
});
