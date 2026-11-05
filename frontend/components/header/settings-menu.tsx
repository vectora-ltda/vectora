"use client";

import { Settings } from "lucide-react";

import { useAuthStore } from "@/lib/stores/auth-store";
import { useSettingsOverlayStore } from "@/lib/stores/settings-overlay-store";
import { SettingsOverlay } from "@/components/settings/settings-overlay";

export function SettingsMenu() {
  const user = useAuthStore((s) => s.user);
  const openCategory = useSettingsOverlayStore((s) => s.openCategory);

  const displayName = user?.name?.trim() || user?.username || "Vectora";

  return (
    <>
      {/* Botão de configurações (engrenagem) — abre o SettingsOverlay
          direto na categoria "Geral", sem dropdown intermediário. O menu
          antigo (Preferências/Ambiente/Administração) escondia atrás de
          um clique extra algo que já é o destino óbvio de um ícone de
          engrenagem; "Sair" migrou pra dentro de Configurações → Conta. */}
      <button
        onClick={() => openCategory("geral")}
        className="flex items-center justify-center w-8 h-8 rounded-full hover:bg-muted/70 text-muted-foreground hover:text-foreground transition-colors select-none"
        title={displayName}
        aria-label="Configurações"
      >
        <Settings className="w-4 h-4" />
      </button>

      <SettingsOverlay />
    </>
  );
}
