/**
 * ci-store — último estado de CI recebido via webhook do GitHub.
 *
 * Alimentado por `use-webhook-workbench` (eventos `workflow_run`/`check_run`/
 * `check_suite`) e consumido pelo badge de CI no git-tab. Volátil (não
 * persistido): reflete o que chegou nesta sessão do app.
 */

import { create } from "zustand";

export interface CIRun {
  /** Repositório no formato "owner/repo". */
  repo: string;
  /** Nome do workflow/check. */
  name: string;
  /** "queued" | "in_progress" | "completed". */
  status: string;
  /** Quando completo: "success" | "failure" | "cancelled" | etc.; senão null. */
  conclusion: string | null;
  /** URL para abrir o run no GitHub. */
  htmlUrl: string;
  /** Timestamp (ms) de quando o evento foi recebido. */
  at: number;
}

interface CIState {
  lastRun: CIRun | null;
  setRun: (run: CIRun) => void;
  clear: () => void;
}

export const useCIStore = create<CIState>((set) => ({
  lastRun: null,
  setRun: (run) => set({ lastRun: run }),
  clear: () => set({ lastRun: null }),
}));
