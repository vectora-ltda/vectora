"use client";

/**
 * use-webhook-workbench — consome eventos de webhook do GitHub e reflete o
 * estado de CI no workbench em tempo real (sem F5).
 *
 * - `workflow_run` / `check_run` / `check_suite` → atualiza o ci-store e, ao
 *   completar, dispara um toast ("build passou/falhou").
 *
 * Montado UMA vez por session (na rota da session) para que o toast apareça
 * mesmo com o painel de Git fechado.
 */

import { useCallback } from "react";

import { useWebhookEvents } from "./use-webhook-events";
import { useCIStore } from "@/lib/stores/ci-store";
import { useToastStore } from "@/lib/stores/toast-store";
import { m } from "@/lib/paraglide/messages";

interface WebhookEvent {
  provider: string;
  event_type: string;
  data: Record<string, unknown>;
}

const CI_EVENTS = new Set(["workflow_run", "check_run", "check_suite"]);

function toStr(v: unknown): string {
  return typeof v === "string" ? v : v == null ? "" : String(v);
}

export function useWebhookWorkbench(): void {
  const handler = useCallback((evt: WebhookEvent) => {
    if (evt.provider !== "github") return;
    const base = evt.event_type.split(".")[0];
    if (!CI_EVENTS.has(base)) return;

    const d = evt.data;
    const name = toStr(d.name) || "CI";
    const status = toStr(d.status);
    const conclusion = toStr(d.conclusion);

    useCIStore.getState().setRun({
      repo: toStr(d.repo),
      name,
      status,
      conclusion: conclusion || null,
      htmlUrl: toStr(d.html_url),
      at: Date.now(),
    });

    if (status !== "completed" || !conclusion) return;

    const toast = useToastStore.getState();
    if (conclusion === "success") {
      toast.success(m.ci_build_passed({ name }));
    } else {
      toast.error(m.ci_build_failed({ name }));
    }
  }, []);

  useWebhookEvents(handler);
}
