"use client";

/**
 * Zustand store de autenticação.
 *
 * Armazena apenas dados do usuário (sem tokens — tokens ficam em cookies httpOnly).
 * Persiste o user em sessionStorage para evitar flash de "carregando" no header.
 *
 * Uso:
 *   const { user, isAuthenticated } = useAuthStore();
 *   const { logout } = useAuthStore();
 */

import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

import type { AuthUser } from "@/lib/types/auth";

interface AuthState {
  user: AuthUser | null;
  isAuthenticated: boolean;

  /** Define o usuário após login bem-sucedido. */
  setUser: (user: AuthUser) => void;

  /** Limpa o usuário (logout local). */
  clearUser: () => void;

  /**
   * Hidrata o store a partir de GET /auth/me.
   * Chamado no layout root — garante que o estado está sincronizado com
   * o servidor sem depender do sessionStorage (que pode estar desatualizado).
   */
  hydrate: () => Promise<void>;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      user: null,
      isAuthenticated: false,

      setUser: (user) => set({ user, isAuthenticated: true }),

      clearUser: () => set({ user: null, isAuthenticated: false }),

      hydrate: async () => {
        // `credentials: "include"` envia os cookies httpOnly `vectora_access`
        // e `vectora_refresh` para o backend; sem isso o middleware nunca
        // resolve o usuário e o sessionStorage falsifica `isAuthenticated`.
        try {
          const res = await fetch("/auth/me", { credentials: "include" });
          if (res.ok) {
            const user: AuthUser = await res.json();
            set({ user, isAuthenticated: true });
            return;
          }
          if (res.status === 401) {
            // Tenta refresh silencioso antes de invalidar a sessão local.
            try {
              const refresh = await fetch("/auth/refresh", {
                method: "POST",
                credentials: "include",
                headers: { "Content-Type": "application/json" },
                body: "{}",
              });
              if (refresh.ok) {
                const me = await fetch("/auth/me", {
                  credentials: "include",
                });
                if (me.ok) {
                  set({
                    user: (await me.json()) as AuthUser,
                    isAuthenticated: true,
                  });
                  return;
                }
              }
            } catch {
              /* cai no clear abaixo */
            }
          }
          set({ user: null, isAuthenticated: false });
        } catch {
          // Backend offline — não muda nada (cache anterior continua válido
          // até o backend responder).
        }
      },
    }),
    {
      name: "vectora-auth",
      storage: createJSONStorage(() =>
        typeof window !== "undefined"
          ? sessionStorage
          : {
              getItem: () => null,
              setItem: () => {},
              removeItem: () => {},
            },
      ),
      // Só persiste user e isAuthenticated — nunca tokens
      partialize: (state) => ({
        user: state.user,
        isAuthenticated: state.isAuthenticated,
      }),
    },
  ),
);
