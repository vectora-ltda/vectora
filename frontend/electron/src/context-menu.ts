/**
 * Menu de clique com o botão direito da janela principal.
 *
 * O Electron não desenha nenhum menu sozinho: sem isto, o clique direito não faz
 * nada, e não há como recortar, copiar, colar, selecionar tudo ou corrigir uma
 * palavra no campo de mensagem. A montagem é uma função pura (parâmetros do clique
 * → itens), para poder ser testada sem o Electron em execução.
 */

import type { MenuItemConstructorOptions } from "electron";

interface EditFlags {
  canUndo: boolean;
  canRedo: boolean;
  canCut: boolean;
  canCopy: boolean;
  canPaste: boolean;
  canSelectAll: boolean;
}

/** O que o evento `context-menu` do Electron informa sobre o clique. */
export interface ContextMenuParams {
  isEditable: boolean;
  selectionText: string;
  linkURL: string;
  misspelledWord: string;
  dictionarySuggestions: string[];
  editFlags: EditFlags;
}

export interface ContextMenuLabels {
  undo: string;
  redo: string;
  cut: string;
  copy: string;
  paste: string;
  selectAll: string;
  copyLink: string;
  addToDictionary: string;
  noSuggestions: string;
}

export interface ContextMenuActions {
  replaceMisspelling: (suggestion: string) => void;
  addToDictionary: (word: string) => void;
  copyText: (text: string) => void;
}

const LABELS: Record<string, ContextMenuLabels> = {
  pt: {
    undo: "Desfazer",
    redo: "Refazer",
    cut: "Recortar",
    copy: "Copiar",
    paste: "Colar",
    selectAll: "Selecionar tudo",
    copyLink: "Copiar endereço do link",
    addToDictionary: "Adicionar ao dicionário",
    noSuggestions: "Sem sugestões",
  },
  en: {
    undo: "Undo",
    redo: "Redo",
    cut: "Cut",
    copy: "Copy",
    paste: "Paste",
    selectAll: "Select all",
    copyLink: "Copy link address",
    addToDictionary: "Add to dictionary",
    noSuggestions: "No suggestions",
  },
  es: {
    undo: "Deshacer",
    redo: "Rehacer",
    cut: "Cortar",
    copy: "Copiar",
    paste: "Pegar",
    selectAll: "Seleccionar todo",
    copyLink: "Copiar dirección del enlace",
    addToDictionary: "Añadir al diccionario",
    noSuggestions: "Sin sugerencias",
  },
};

/** Textos do menu no idioma do sistema (`pt-BR`, `es-MX`, `en-US`...); inglês como padrão. */
export function contextMenuLabels(locale: string): ContextMenuLabels {
  const language = locale.toLowerCase().split(/[-_]/)[0] ?? "en";
  return LABELS[language] ?? LABELS.en!;
}

/** Quantas correções ortográficas o menu oferece. */
const MAX_SUGGESTIONS = 5;

/**
 * Itens do menu para o clique. Devolve lista vazia quando não há o que oferecer
 * (clique numa área sem texto selecionado, sem campo e sem link): nesse caso nenhum
 * menu deve abrir.
 */
export function buildContextMenuTemplate(
  params: ContextMenuParams,
  labels: ContextMenuLabels,
  actions: ContextMenuActions,
): MenuItemConstructorOptions[] {
  const items: MenuItemConstructorOptions[] = [];
  const separator = (): void => {
    if (items.length > 0 && items.at(-1)?.type !== "separator") {
      items.push({ type: "separator" });
    }
  };

  if (params.isEditable && params.misspelledWord) {
    const suggestions = params.dictionarySuggestions.slice(0, MAX_SUGGESTIONS);
    if (suggestions.length === 0) {
      items.push({ label: labels.noSuggestions, enabled: false });
    }
    for (const suggestion of suggestions) {
      items.push({
        label: suggestion,
        click: () => actions.replaceMisspelling(suggestion),
      });
    }
    items.push({
      label: labels.addToDictionary,
      click: () => actions.addToDictionary(params.misspelledWord),
    });
  }

  if (params.isEditable) {
    separator();
    const { editFlags } = params;
    items.push(
      { role: "undo", label: labels.undo, enabled: editFlags.canUndo },
      { role: "redo", label: labels.redo, enabled: editFlags.canRedo },
      { type: "separator" },
      { role: "cut", label: labels.cut, enabled: editFlags.canCut },
      { role: "copy", label: labels.copy, enabled: editFlags.canCopy },
      { role: "paste", label: labels.paste, enabled: editFlags.canPaste },
      { type: "separator" },
      {
        role: "selectAll",
        label: labels.selectAll,
        enabled: editFlags.canSelectAll,
      },
    );
  } else if (params.selectionText) {
    separator();
    items.push(
      { role: "copy", label: labels.copy },
      { type: "separator" },
      { role: "selectAll", label: labels.selectAll },
    );
  }

  if (params.linkURL) {
    separator();
    items.push({
      label: labels.copyLink,
      click: () => actions.copyText(params.linkURL),
    });
  }

  return items;
}
