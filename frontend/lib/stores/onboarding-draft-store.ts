/**
 * onboarding-draft-store.ts — rascunho do wizard de primeiro acesso.
 *
 * Trocar o idioma dentro do PreAuthWizard força um reload completo da
 * página (Paraglide, `setLocale` sem `{ reload: false }` — comportamento
 * intencional em todo o resto do app, ver settings-store.ts::setLanguage).
 * `name`/`username`/`company` viviam em `useState` local e eram perdidos
 * nesse reload. sessionStorage sobrevive ao reload forçado dentro da mesma
 * aba, mas não vaza pra sessões futuras depois que o onboarding termina.
 */

import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

interface OnboardingDraftState {
  name: string;
  username: string;
  company: string;
  step: string;
  setName: (v: string) => void;
  setUsername: (v: string) => void;
  setCompany: (v: string) => void;
  setStep: (v: string) => void;
  reset: () => void;
}

const DEFAULTS = { name: "", username: "", company: "", step: "identity" };

export const useOnboardingDraftStore = create<OnboardingDraftState>()(
  persist(
    (set) => ({
      ...DEFAULTS,
      setName: (v) => set({ name: v }),
      setUsername: (v) => set({ username: v }),
      setCompany: (v) => set({ company: v }),
      setStep: (v) => set({ step: v }),
      reset: () => set({ ...DEFAULTS }),
    }),
    {
      name: "vectora:onboarding-draft",
      storage: createJSONStorage(() =>
        typeof sessionStorage !== "undefined"
          ? sessionStorage
          : {
              getItem: () => null,
              setItem: () => void 0,
              removeItem: () => void 0,
            },
      ),
    },
  ),
);
