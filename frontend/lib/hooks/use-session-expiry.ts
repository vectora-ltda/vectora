"use client";

/**
 * Aviso de renovação de sessão.
 *
 * O access token (`vectora_access`) é um cookie httpOnly: o JS não consegue
 * lê-lo nem decodificar seu `exp`. Por isso o backend agora repassa esse
 * claim via `UserResponse.token_expires_at` (ver `src/api/schemas.py` e
 * `src/api/middleware/auth.py::_extract_user`) — sem isso este hook não
 * teria como saber quando a sessão expira.
 *
 * Comportamento: agenda um toast de aviso para `exp - 5min`. O toast tem uma
 * ação "Renovar" que chama `POST /auth/refresh` e re-hidrata o usuário (o
 * que também atualiza `token_expires_at` e reagenda o próximo aviso). Note
 * que o cliente já renova silenciosamente em qualquer 401 (ver
 * `vectora-client.ts::tryRefreshToken`) — este aviso é um complemento de UX
 * para o caso em que o usuário ficaria, p.ex., parado numa tela sem disparar
 * nenhuma chamada à API por minutos a fio.
 */

import { useEffect, useRef } from "react";
import { useAuthStore } from "@/lib/stores/auth-store";
import { useToastStore } from "@/lib/stores/toast-store";
import { m } from "@/lib/paraglide/messages";
/** Avisa 5 minutos antes do access token expirar. */
const WARN_BEFORE_MS = 5 * 60 * 1000;
/** Nunca agenda para "já" — evita disparo em loop em re-renders/race. */
const MIN_DELAY_MS = 1000;
/** Nunca agenda além disso — `exp` de instâncias mal configuradas. */
const MAX_DELAY_MS = 24 * 60 * 60 * 1000;

async function renewSession(): Promise<boolean> {
  try {
    const res = await fetch("/auth/refresh", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    if (!res.ok) return false;
    const me = await fetch("/auth/me", { credentials: "include" });
    if (!me.ok) return false;
    useAuthStore.getState().setUser(await me.json());
    return true;
  } catch {
    return false;
  }
}

function showExpiryToast(): void {
  useToastStore.getState().warning(m.auth_session_expiring_title(), {
    description: m.auth_session_expiring_desc(),
    duration: null, // fica até o usuário agir ou a sessão de fato cair
    action: {
      label: m.auth_session_renew_action(),
      onClick: async () => {
        const ok = await renewSession();
        if (ok) {
          useToastStore.getState().success(m.auth_session_renewed());
        } else {
          useToastStore.getState().error(m.auth_session_renew_failed());
        }
      },
    },
  });
}

/**
 * Monta o agendamento do aviso de expiração — chamar uma vez perto da raiz
 * (ex.: `RootComponent` em `__root.tsx`), não por componente de tela.
 */
export function useSessionExpiry(): void {
  const tokenExpiresAt = useAuthStore((s) => s.user?.token_expires_at ?? null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const scheduledForRef = useRef<number | null>(null);

  useEffect(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }

    if (!tokenExpiresAt) {
      scheduledForRef.current = null;
      return;
    }

    // Evita reagendar para o mesmo `exp` (ex.: re-render sem mudança real).
    if (scheduledForRef.current === tokenExpiresAt) return;
    scheduledForRef.current = tokenExpiresAt;

    const fireAt = tokenExpiresAt * 1000 - WARN_BEFORE_MS;
    const delay = Math.min(
      Math.max(fireAt - Date.now(), MIN_DELAY_MS),
      MAX_DELAY_MS,
    );

    timerRef.current = setTimeout(showExpiryToast, delay);

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [tokenExpiresAt]);
}
