"use client";

/**
 * use-switch-mode — alterna entre Chat e Dev abrindo uma sessão NOVA do modo.
 *
 * Chat e Dev são pools de sessão separados: trocar de modo não deve continuar na
 * mesma thread. Ao alternar, seta o modo ativo (`chatMode`) e navega para uma
 * sessão nova/vazia daquele modo (a sidebar passa a mostrar só o histórico do
 * modo). Chat não tem workspace/folders.
 */

import { useCallback } from "react";
import { useNavigate } from "@tanstack/react-router";

import { useSettingsStore } from "@/lib/stores/settings-store";
import { markAsNew } from "@/lib/stores/new-thread-registry";
import { safeRandomUUID } from "@/lib/utils/uuid";

export function useSwitchMode(): (toChat: boolean) => void {
  const navigate = useNavigate();
  const setChatMode = useSettingsStore((s) => s.setChatMode);
  const chatMode = useSettingsStore((s) => s.chatMode);

  return useCallback(
    (toChat: boolean) => {
      setChatMode(toChat);
      // Já está no modo e numa sessão — abrir nova mesmo assim seria ruído.
      // Só força sessão nova quando o modo realmente muda.
      if (toChat === chatMode) return;
      const id = safeRandomUUID();
      markAsNew(id);
      void navigate({
        to: "/session/$threadId",
        params: { threadId: id },
      });
    },
    [navigate, setChatMode, chatMode],
  );
}
