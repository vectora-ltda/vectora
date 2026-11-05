/**
 * User ID Management Hook
 *
 * Anonymous browser UUID persisted in localStorage.
 */

"use client";

import { useState, useEffect } from "react";
import { safeRandomUUID } from "@/lib/utils/uuid";

// ============================================================================
// Constants
// ============================================================================

const USER_ID_KEY = "vectora-user-id";

// ============================================================================
// Hook Implementation
// ============================================================================

/**
 * Get user ID for thread management.
 *
 * Returns a browser UUID used to filter threads on the backend.
 *
 * @returns The user's unique ID, or null while loading
 *
 * @example
 * const userId = useUserId()
 * if (!userId) return <div>Loading...</div>
 * return <ChatInterface userId={userId} />
 */
export function useUserId(): string | null {
  const [userId, setUserId] = useState<string | null>(null);

  useEffect(() => {
    // Skip during SSR
    if (typeof window === "undefined") return;

    let id = localStorage.getItem(USER_ID_KEY);

    if (!id) {
      // safeRandomUUID: funciona em HTTP + IP da LAN/Tailscale (contexto
      // inseguro) onde `crypto.randomUUID` lança no Firefox/Zen.
      id = `user-${safeRandomUUID()}`;
      localStorage.setItem(USER_ID_KEY, id);
      console.info("Generated new browser user ID:", id);
    } else {
      console.info("Loaded existing browser user ID:", id);
    }

    // Sincroniza com o sistema externo localStorage — leitura só possível no
    // client, por isso fica num efeito.
    // oxlint-disable-next-line react/set-state-in-effect
    setUserId(id);
  }, []);

  return userId;
}
