/**
 * settings-overlay-store — controla a abertura do `SettingsOverlay`
 * unificado (shell único, rail lateral + conteúdo) e a categoria ativa.
 *
 * Substitui a antiga arquitetura de 3 `Dialog` Radix independentes
 * (Preferências/Ambiente/Administração), cada um com seu próprio
 * `open`/`setOpen` — trocar de "grupo" fechava um Dialog inteiro e abria
 * outro, produzindo o flicker visível reportado pelo usuário. Agora só
 * este `open` controla o único Dialog renderizado; trocar de categoria é
 * navegação interna (`setActiveCategory`), nunca fecha/reabre nada.
 *
 * Os 3 stores antigos (`preferencias-dialog-store`, `environment-dialog-
 * store`, `administracao-dialog-store`) continuam existindo — ainda
 * guardam qual sub-aba cada domínio deve mostrar (ex.: `AdminTab` lê
 * `subTab` pra deep-link) — mas seus `openAt()` agora também abrem este
 * store, que é quem de fato decide se o Dialog aparece.
 */

import { create } from "zustand";

export type SettingsCategoryId =
  | "geral"
  | "fallbacks"
  | "memoria"
  | "conta"
  | "integracoes"
  | "provider_routing"
  | "connect"
  | "plugins"
  | "skills"
  | "tool_policy"
  | "hitl_allowlist"
  | "admin_users"
  | "admin_tools"
  | "admin_saferoots"
  | "admin_system"
  | "admin_storage"
  | "billing"
  | "about";

interface SettingsOverlayState {
  open: boolean;
  activeCategory: SettingsCategoryId;
  openCategory: (category: SettingsCategoryId) => void;
  setOpen: (v: boolean) => void;
  setActiveCategory: (category: SettingsCategoryId) => void;
}

export const useSettingsOverlayStore = create<SettingsOverlayState>((set) => ({
  open: false,
  activeCategory: "geral",
  openCategory: (category) => set({ open: true, activeCategory: category }),
  setOpen: (v) => set({ open: v }),
  setActiveCategory: (category) => set({ activeCategory: category }),
}));
