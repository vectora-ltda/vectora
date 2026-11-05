"use client";

/**
 * useGlobalShortcuts — registry central de atalhos de teclado.
 *
 * Substitui addEventListener/keydown espalhados nos componentes por um único
 * registro declarativo. O hook registra um listener global no `document` e
 * despacha para os handlers ativos.
 *
 * Atalhos registrados (alimentam o cheatsheet em C.30):
 *   Ctrl+T    — nova thread
 *   Ctrl+L    — limpar mensagens
 *   Ctrl+\    — abrir/fechar workbench
 *   Ctrl+,    — abrir settings
 *   Ctrl+K    — command palette (C.30)
 *   Ctrl+?    — cheatsheet de atalhos
 *   Shift+E   — focar input de mensagem
 *   Shift+G   — ir ao fim da lista de mensagens
 *
 * Uso:
 *   useGlobalShortcuts({
 *     "ctrl+t": () => void createThread(),
 *     "ctrl+backslash": () => toggleWorkbench(),
 *   });
 *
 * Múltiplos componentes podem chamar o hook; os handlers são compostos
 * (todos ativos ao mesmo tempo). Para inibir propagação, retorne `true`
 * no handler (sinaliza "consumed").
 */

import { useEffect, useRef } from "react";

type ShortcutHandler = (e: KeyboardEvent) => boolean | void;

export interface ShortcutMap {
  [shortcut: string]: ShortcutHandler;
}

/**
 * Normaliza um KeyboardEvent para a forma `ctrl+shift+key` (lowercase).
 * Exemplos:
 *   Ctrl+T  → "ctrl+t"
 *   Ctrl+Shift+E → "ctrl+shift+e"
 *   Ctrl+, → "ctrl+,"
 *   Ctrl+\ → "ctrl+backslash"
 *   Ctrl+? → "ctrl+?"
 */
function normalizeKey(e: KeyboardEvent): string {
  const parts: string[] = [];
  if (e.ctrlKey || e.metaKey) parts.push("ctrl");
  if (e.altKey) parts.push("alt");
  if (e.shiftKey) parts.push("shift");
  // Normalize common special keys.
  let key = e.key.toLowerCase();
  if (key === "\\") key = "backslash";
  if (key === " ") key = "space";
  parts.push(key);
  return parts.join("+");
}

/** Global registry shared across all instances. */
const handlers = new Map<string, Set<ShortcutHandler>>();

let listenerAttached = false;

function globalKeyDown(e: KeyboardEvent): void {
  // Ignore events from input/textarea unless it's a non-typing shortcut.
  const target = e.target as HTMLElement;
  const inInput =
    target.tagName === "INPUT" ||
    target.tagName === "TEXTAREA" ||
    target.isContentEditable;

  const norm = normalizeKey(e);
  const group = handlers.get(norm);
  if (!group) return;

  // For inputs, only fire if Ctrl/Meta is held (functional key, not typing).
  if (inInput && !e.ctrlKey && !e.metaKey) return;

  for (const handler of group) {
    const consumed = handler(e);
    if (consumed) {
      e.preventDefault();
      e.stopPropagation();
      break;
    }
  }
}

function ensureListener() {
  if (listenerAttached) return;
  document.addEventListener("keydown", globalKeyDown, true);
  listenerAttached = true;
}

/**
 * Registra um mapa de atalhos enquanto o componente está montado.
 * Limpa automaticamente no unmount.
 */
export function useGlobalShortcuts(shortcutMap: ShortcutMap): void {
  // Ref to current map so closures inside don't go stale.
  const mapRef = useRef(shortcutMap);
  mapRef.current = shortcutMap;

  useEffect(() => {
    ensureListener();

    // Stable handler wrapper per shortcut so we can remove exactly it.
    const registered: { key: string; fn: ShortcutHandler }[] = [];

    for (const [shortcut, handler] of Object.entries(shortcutMap)) {
      const stable: ShortcutHandler = (e) => mapRef.current[shortcut]?.(e);
      if (!handlers.has(shortcut)) handlers.set(shortcut, new Set());
      handlers.get(shortcut)!.add(stable);
      registered.push({ key: shortcut, fn: stable });
    }

    return () => {
      for (const { key, fn } of registered) {
        handlers.get(key)?.delete(fn);
      }
    };
    // shortcutMap identity intentionally not in deps — we want mount-time
    // registration only; updates are handled via mapRef.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}

/**
 * Lista de todos os atalhos registrados ativos (para o cheatsheet C.30).
 * Retorna as chaves normalizadas (ex.: "ctrl+t", "ctrl+backslash").
 */
export function getRegisteredShortcuts(): string[] {
  return [...handlers.keys()].filter((k) => (handlers.get(k)?.size ?? 0) > 0);
}
