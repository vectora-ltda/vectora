"use client";

/**
 * Canal único de feedback do produto. Qualquer falha de ação ou
 * confirmação importante passa pelo `toast-store`: stores chamam
 * `useToastStore.getState().push({...})` antes de retornar
 * `{ ok: false, error }`.
 *
 * Política:
 *   - success/info → auto-dismiss em 4s (default)
 *   - warning      → auto-dismiss em 6s
 *   - error        → não auto-dismiss (usuário fecha)
 *   - fila máxima de 3 toasts simultâneos (mais antigo é descartado)
 *   - dedup por (level, title) para evitar spam idêntico
 *
 * O componente `<Toaster />` lê o store e renderiza no canto superior-direito.
 */

import { create } from "zustand";

export type ToastLevel = "success" | "error" | "warning" | "info";

export interface ToastAction {
  label: string;
  onClick: () => void | Promise<void>;
}

export interface Toast {
  id: string;
  level: ToastLevel;
  title: string;
  description?: string;
  /** ms para auto-dismiss. `null` = nunca (default para erros). */
  duration: number | null;
  /** Ação opcional (botão à direita). */
  action?: ToastAction;
  /** timestamp de criação (para ordering visual). */
  createdAt: number;
}

interface ToastState {
  toasts: Toast[];
  push: (
    input: Omit<Toast, "id" | "createdAt" | "duration"> & {
      duration?: number | null;
    },
  ) => string;
  success: (
    title: string,
    opts?: Partial<Omit<Toast, "id" | "createdAt" | "title" | "level">>,
  ) => string;
  error: (
    title: string,
    opts?: Partial<Omit<Toast, "id" | "createdAt" | "title" | "level">>,
  ) => string;
  warning: (
    title: string,
    opts?: Partial<Omit<Toast, "id" | "createdAt" | "title" | "level">>,
  ) => string;
  info: (
    title: string,
    opts?: Partial<Omit<Toast, "id" | "createdAt" | "title" | "level">>,
  ) => string;
  dismiss: (id: string) => void;
  clear: () => void;
}

const MAX_TOASTS = 3;

function defaultDuration(level: ToastLevel): number | null {
  switch (level) {
    case "error":
      return null;
    case "warning":
      return 6000;
    case "success":
    case "info":
    default:
      return 4000;
  }
}

function nextId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `toast-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export const useToastStore = create<ToastState>()((set, get) => ({
  toasts: [],

  push: (input) => {
    const { toasts } = get();
    // Dedup por (level, title) — evita spam de erros idênticos repetidos.
    const existing = toasts.find(
      (t) => t.level === input.level && t.title === input.title,
    );
    if (existing) return existing.id;

    const id = nextId();
    const toast: Toast = {
      id,
      level: input.level,
      title: input.title,
      description: input.description,
      action: input.action,
      duration:
        input.duration !== undefined
          ? input.duration
          : defaultDuration(input.level),
      createdAt: Date.now(),
    };

    set((s) => {
      const next = [...s.toasts, toast];
      // Cap em MAX_TOASTS — descarta o mais antigo.
      while (next.length > MAX_TOASTS) next.shift();
      return { toasts: next };
    });

    return id;
  },

  success: (title, opts = {}) =>
    get().push({ ...opts, level: "success", title }),
  error: (title, opts = {}) => get().push({ ...opts, level: "error", title }),
  warning: (title, opts = {}) =>
    get().push({ ...opts, level: "warning", title }),
  info: (title, opts = {}) => get().push({ ...opts, level: "info", title }),

  dismiss: (id) =>
    set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),

  clear: () => set({ toasts: [] }),
}));

// ActionResult moved to lib/types/async-state.ts — reexport for compatibility.
export type { ActionResult } from "@/lib/types/async-state";
