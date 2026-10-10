/**
 * Setup global do vitest.
 *
 * 1. Estende `expect` com os matchers de DOM do @testing-library/jest-dom
 *    (toBeInTheDocument, toBeEmptyDOMElement, etc.).
 *
 * 2. Instala um localStorage/sessionStorage in-memory. No Node 22+ existe um
 *    `localStorage` nativo experimental que fica `undefined` sem
 *    `--localstorage-file` e sombreia o do jsdom — o que quebra o zustand
 *    persist e o paraglide (`getLocale()` lê localStorage). O polyfill garante
 *    um Storage funcional em ambos os ambientes (node e jsdom).
 */

import { afterEach } from "vitest";
// Import de efeito colateral: registra os matchers de DOM no `expect`.
// eslint-disable-next-line import/no-unassigned-import
import "@testing-library/jest-dom/vitest";

// monaco-editor chama document.queryCommandSupported() no carregamento —
// jsdom não implementa esse método legado, então precisamos de um stub.
if (typeof document !== "undefined" && !document.queryCommandSupported) {
  document.queryCommandSupported = () => false;
}

// Monaco consulta `window.matchMedia` ao inicializar o serviço de temas.
// jsdom não fornece essa API por padrão; o stub mantém o contrato mínimo
// necessário sem impor um modo de cor aos testes.
if (typeof window !== "undefined" && typeof window.matchMedia !== "function") {
  window.matchMedia = (query: string): MediaQueryList => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  });
}

// O serviço de clipboard do Monaco registra um handler que usa
// `navigator.clipboard.write` ao copiar conteúdo. O jsdom não fornece essa
// API; o stub mantém a operação assíncrona inofensiva durante os testes.
if (typeof globalThis.ClipboardItem === "undefined") {
  class ClipboardItemStub {
    constructor(public readonly items: Record<string, unknown>) {}
  }
  globalThis.ClipboardItem =
    ClipboardItemStub as unknown as typeof ClipboardItem;
}

if (typeof navigator !== "undefined" && !navigator.clipboard) {
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: {
      readText: async (): Promise<string> => "",
      write: async (): Promise<void> => {},
      writeText: async (): Promise<void> => {},
    },
  });
}

// Radix UI (Select/Popover/DropdownMenu) chama hasPointerCapture/
// setPointerCapture/releasePointerCapture e scrollIntoView ao abrir —
// jsdom não implementa nenhum dos dois, o que faz qualquer teste que
// clica pra abrir esses componentes lançar. Stubs mínimos, sem
// comportamento real de captura de ponteiro (não precisamos dele em teste).
if (typeof Element !== "undefined") {
  if (!Element.prototype.hasPointerCapture) {
    Element.prototype.hasPointerCapture = () => false;
  }
  if (!Element.prototype.setPointerCapture) {
    Element.prototype.setPointerCapture = () => {};
  }
  if (!Element.prototype.releasePointerCapture) {
    Element.prototype.releasePointerCapture = () => {};
  }
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = () => {};
  }
}

// jsdom não implementa ResizeObserver — qualquer componente que meça a
// própria largura via `useElementWidth` (Header, ModeSwitch) lança
// "ResizeObserver is not defined" ao montar sem esse stub. Não observa de
// verdade (não há layout real em jsdom); só evita o crash — testes que
// precisam de uma largura específica passam `width` como prop ou mockam
// `use-element-width` diretamente.
if (typeof globalThis.ResizeObserver === "undefined") {
  class ResizeObserverStub {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  }
  globalThis.ResizeObserver =
    ResizeObserverStub as unknown as typeof ResizeObserver;
}

class MemoryStorage implements Storage {
  private store = new Map<string, string>();
  get length(): number {
    return this.store.size;
  }
  clear(): void {
    this.store.clear();
  }
  getItem(key: string): string | null {
    return this.store.has(key) ? (this.store.get(key) as string) : null;
  }
  setItem(key: string, value: string): void {
    this.store.set(key, String(value));
  }
  removeItem(key: string): void {
    this.store.delete(key);
  }
  key(index: number): string | null {
    return [...this.store.keys()][index] ?? null;
  }
}

const localStoragePolyfill = new MemoryStorage();
const sessionStoragePolyfill = new MemoryStorage();

for (const [name, value] of [
  ["localStorage", localStoragePolyfill],
  ["sessionStorage", sessionStoragePolyfill],
] as const) {
  Object.defineProperty(globalThis, name, {
    value,
    configurable: true,
    writable: true,
  });
}

// Isola os testes: limpa o storage entre cada um (evita vazamento de chaves
// persistidas por stores zustand entre testes do mesmo arquivo).
afterEach(() => {
  localStoragePolyfill.clear();
  sessionStoragePolyfill.clear();
});
