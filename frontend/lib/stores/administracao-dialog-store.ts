/**
 * administracao-dialog-store — mantido por compatibilidade com quem ainda
 * chama `openAt(subTab)` de fora (ex.: `license-banner.tsx` →
 * `openAt("system")`) e por `open`/`setOpen` que outros pontos ainda
 * checam. As 5 sub-abas de Administração (Usuários/Ferramentas/Pastas
 * Seguras/Sistema/Storage) NÃO ficam mais atrás de um painel único — cada
 * uma é sua própria categoria de primeiro nível no rail do
 * `SettingsOverlay` (mesmo achatamento já aplicado a Preferências e
 * Ambiente). `openAt` mapeia a sub-aba direto pra essa categoria.
 */

import { create } from "zustand";
import {
  useSettingsOverlayStore,
  type SettingsCategoryId,
} from "./settings-overlay-store";

/** Sub-abas históricas de Administração — hoje cada uma é sua própria
 * categoria (`admin_*`) no SettingsOverlay; este tipo só sobrevive pra
 * quem ainda deep-linka por aqui. */
export type AdminSubTab =
  "users" | "tools" | "system" | "safe-roots" | "storage";

const CATEGORY_BY_SUBTAB: Record<AdminSubTab, SettingsCategoryId> = {
  users: "admin_users",
  tools: "admin_tools",
  "safe-roots": "admin_saferoots",
  system: "admin_system",
  storage: "admin_storage",
};

interface AdminDialogState {
  open: boolean;
  subTab?: AdminSubTab;
  openAt: (subTab?: AdminSubTab) => void;
  setOpen: (v: boolean) => void;
  setSubTab: (subTab?: AdminSubTab) => void;
}

export const useAdministracaoDialogStore = create<AdminDialogState>((set) => ({
  open: false,
  subTab: undefined,
  openAt: (subTab) => {
    set({ open: true, subTab });
    useSettingsOverlayStore
      .getState()
      .openCategory(CATEGORY_BY_SUBTAB[subTab ?? "users"]);
  },
  setOpen: (v) => set({ open: v }),
  setSubTab: (subTab) => set({ subTab }),
}));
