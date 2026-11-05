/**
 * environment-dialog-store — controla a abertura do painel "Ambiente"
 * (Integrações, Provider Routing) e a aba ativa.
 *
 * Permite deep-link a partir de qualquer lugar (ex.: o menu "+" abrindo
 * direto em Conectores) sem prop drilling — o dialog é renderizado uma
 * única vez (no SettingsMenu) e lê seu estado daqui.
 *
 * "Preferências" (Conta/Preferências/Memória) e "Administração" (root/admin)
 * são painéis próprios — ver `preferencias-dialog-store` e
 * `administracao-dialog-store`.
 */

import { create } from "zustand";
import {
  useSettingsOverlayStore,
  type SettingsCategoryId,
} from "./settings-overlay-store";

export type EnvironmentTab = "provider_routing" | "integracoes" | "connect";

const CATEGORY_BY_TAB: Record<EnvironmentTab, SettingsCategoryId> = {
  integracoes: "integracoes",
  provider_routing: "provider_routing",
  connect: "connect",
};

interface EnvironmentDialogState {
  open: boolean;
  tab: EnvironmentTab;
  openAt: (tab?: EnvironmentTab) => void;
  setOpen: (v: boolean) => void;
  setTab: (tab: EnvironmentTab) => void;
}

export const useEnvironmentDialogStore = create<EnvironmentDialogState>(
  (set) => ({
    open: false,
    tab: "integracoes",
    openAt: (tab = "integracoes") => {
      set({ open: true, tab });
      useSettingsOverlayStore.getState().openCategory(CATEGORY_BY_TAB[tab]);
    },
    setOpen: (v) => set({ open: v }),
    setTab: (tab) => set({ tab }),
  }),
);
