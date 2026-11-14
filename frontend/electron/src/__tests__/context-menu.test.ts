import { describe, expect, it, vi } from "vitest";
import {
  buildContextMenuTemplate,
  contextMenuLabels,
  type ContextMenuActions,
  type ContextMenuParams,
} from "../context-menu";

const labels = contextMenuLabels("pt-BR");

const everything = {
  canUndo: true,
  canRedo: true,
  canCut: true,
  canCopy: true,
  canPaste: true,
  canSelectAll: true,
};

function params(over: Partial<ContextMenuParams> = {}): ContextMenuParams {
  return {
    isEditable: false,
    selectionText: "",
    linkURL: "",
    misspelledWord: "",
    dictionarySuggestions: [],
    editFlags: { ...everything },
    ...over,
  };
}

function actions(): ContextMenuActions {
  return {
    replaceMisspelling: vi.fn(),
    addToDictionary: vi.fn(),
    copyText: vi.fn(),
  };
}

const names = (items: ReturnType<typeof buildContextMenuTemplate>) =>
  items.map((item) => item.role ?? item.type ?? item.label);

describe("buildContextMenuTemplate — campo de texto", () => {
  it("oferece desfazer, refazer, recortar, copiar, colar e selecionar tudo", () => {
    const items = buildContextMenuTemplate(
      params({ isEditable: true }),
      labels,
      actions(),
    );

    expect(names(items)).toEqual([
      "undo",
      "redo",
      "separator",
      "cut",
      "copy",
      "paste",
      "separator",
      "selectAll",
    ]);
    expect(items.filter((item) => item.role).map((item) => item.label)).toEqual(
      ["Desfazer", "Refazer", "Recortar", "Copiar", "Colar", "Selecionar tudo"],
    );
  });

  it("desabilita o que o campo não pode fazer agora (sem seleção, sem nada para refazer)", () => {
    const items = buildContextMenuTemplate(
      params({
        isEditable: true,
        editFlags: {
          ...everything,
          canRedo: false,
          canCut: false,
          canCopy: false,
          canPaste: false,
          canSelectAll: false,
        },
      }),
      labels,
      actions(),
    );

    const enabled = Object.fromEntries(
      items
        .filter((item) => item.role)
        .map((item) => [item.role, item.enabled]),
    );
    expect(enabled).toEqual({
      undo: true,
      redo: false,
      cut: false,
      copy: false,
      paste: false,
      selectAll: false,
    });
  });

  it("palavra errada: sugestões no topo, no máximo cinco, e adicionar ao dicionário", () => {
    const act = actions();
    const items = buildContextMenuTemplate(
      params({
        isEditable: true,
        misspelledWord: "teste",
        dictionarySuggestions: ["a", "b", "c", "d", "e", "f", "g"],
      }),
      labels,
      act,
    );

    expect(items.slice(0, 7).map((item) => item.label)).toEqual([
      "a",
      "b",
      "c",
      "d",
      "e",
      "Adicionar ao dicionário",
      undefined,
    ]);
    items[1]!.click?.({} as never, undefined, {} as never);
    expect(act.replaceMisspelling).toHaveBeenCalledWith("b");
    items[5]!.click?.({} as never, undefined, {} as never);
    expect(act.addToDictionary).toHaveBeenCalledWith("teste");
    expect(items[6]!.type).toBe("separator");
  });

  it("palavra errada sem sugestão mostra o aviso desabilitado, ainda com o dicionário", () => {
    const items = buildContextMenuTemplate(
      params({ isEditable: true, misspelledWord: "xyzq" }),
      labels,
      actions(),
    );

    expect(items[0]).toMatchObject({ label: "Sem sugestões", enabled: false });
    expect(items[1]!.label).toBe("Adicionar ao dicionário");
  });

  it("palavra errada fora de um campo editável não oferece correção", () => {
    const items = buildContextMenuTemplate(
      params({ misspelledWord: "teste", selectionText: "teste" }),
      labels,
      actions(),
    );

    expect(items.some((item) => item.label === "Adicionar ao dicionário")).toBe(
      false,
    );
  });
});

describe("buildContextMenuTemplate — texto da página", () => {
  it("com texto selecionado, oferece copiar e selecionar tudo", () => {
    const items = buildContextMenuTemplate(
      params({ selectionText: "olá" }),
      labels,
      actions(),
    );

    expect(names(items)).toEqual(["copy", "separator", "selectAll"]);
  });

  it("link ganha copiar endereço, e o clique copia a URL", () => {
    const act = actions();
    const items = buildContextMenuTemplate(
      params({ linkURL: "https://example.com/x" }),
      labels,
      act,
    );

    expect(items.map((item) => item.label)).toEqual([
      "Copiar endereço do link",
    ]);
    items[0]!.click?.({} as never, undefined, {} as never);
    expect(act.copyText).toHaveBeenCalledWith("https://example.com/x");
  });

  it("texto selecionado e link: os dois grupos, separados", () => {
    const items = buildContextMenuTemplate(
      params({ selectionText: "olá", linkURL: "https://example.com" }),
      labels,
      actions(),
    );

    expect(names(items)).toEqual([
      "copy",
      "separator",
      "selectAll",
      "separator",
      "Copiar endereço do link",
    ]);
  });

  it("sem campo, sem seleção e sem link, não há menu", () => {
    expect(buildContextMenuTemplate(params(), labels, actions())).toEqual([]);
  });

  it("nunca começa nem termina com separador", () => {
    const cases = [
      params({ isEditable: true }),
      params({ isEditable: true, misspelledWord: "x" }),
      params({ selectionText: "a", linkURL: "https://e.com" }),
      params({ linkURL: "https://e.com" }),
    ];

    for (const entry of cases) {
      const items = buildContextMenuTemplate(entry, labels, actions());
      expect(items[0]!.type).not.toBe("separator");
      expect(items.at(-1)!.type).not.toBe("separator");
    }
  });
});

describe("contextMenuLabels", () => {
  it("escolhe o idioma pelo código do sistema, com inglês como padrão", () => {
    expect(contextMenuLabels("pt-BR").paste).toBe("Colar");
    expect(contextMenuLabels("es_MX").paste).toBe("Pegar");
    expect(contextMenuLabels("en-US").paste).toBe("Paste");
    expect(contextMenuLabels("fr-FR").paste).toBe("Paste");
    expect(contextMenuLabels("").paste).toBe("Paste");
  });
});
