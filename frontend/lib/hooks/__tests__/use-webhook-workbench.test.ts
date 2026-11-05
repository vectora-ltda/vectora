// @vitest-environment jsdom
/**
 * Tests para useWebhookWorkbench — eventos de CI do GitHub via webhook viram
 * estado no ci-store + toast. Par erro/borda no mesmo teste (CLAUDE.md §18).
 *
 * Os métodos do toast-store são substituídos por `vi.fn()` frescos a cada teste
 * (via setState) — evita acúmulo de chamadas entre testes que um spy global teria.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, cleanup } from "@testing-library/react";

type Handler = (e: {
  provider: string;
  event_type: string;
  data: Record<string, unknown>;
}) => void;

let captured: Handler | undefined;

vi.mock("../use-webhook-events", () => ({
  useWebhookEvents: (h: Handler) => {
    captured = h;
  },
  onWebhookEvent: () => () => {},
}));

import { useWebhookWorkbench } from "../use-webhook-workbench";
import { useCIStore } from "@/lib/stores/ci-store";
import { useToastStore } from "@/lib/stores/toast-store";

let success: ReturnType<typeof vi.fn>;
let error: ReturnType<typeof vi.fn>;

beforeEach(() => {
  useCIStore.getState().clear();
  success = vi.fn();
  error = vi.fn();
  useToastStore.setState({ success, error } as never);
});

afterEach(() => cleanup());

function mount(): Handler {
  captured = undefined;
  renderHook(() => useWebhookWorkbench());
  const handle = captured;
  if (!handle) throw new Error("handler não registrado");
  return handle;
}

describe("useWebhookWorkbench", () => {
  it("workflow_run completed=success → ci-store + toast de sucesso", () => {
    const handle = mount();

    handle({
      provider: "github",
      event_type: "workflow_run.completed",
      data: {
        name: "CI",
        status: "completed",
        conclusion: "success",
        html_url: "https://gh/run/1",
        repo: "me/app",
      },
    });

    const run = useCIStore.getState().lastRun;
    expect(run?.conclusion).toBe("success");
    expect(run?.repo).toBe("me/app");
    expect(success).toHaveBeenCalledTimes(1);
    expect(error).not.toHaveBeenCalled();

    // Erro/borda: provider não-github é ignorado (store intacto).
    handle({
      provider: "slack",
      event_type: "workflow_run.completed",
      data: { name: "X", status: "completed", conclusion: "failure" },
    });
    expect(useCIStore.getState().lastRun?.name).toBe("CI");
    // Erro/borda: evento não-CI (push) é ignorado.
    handle({ provider: "github", event_type: "push", data: { name: "z" } });
    expect(useCIStore.getState().lastRun?.name).toBe("CI");
  });

  it("failure → toast de erro; in_progress → store sem toast", () => {
    const handle = mount();

    handle({
      provider: "github",
      event_type: "check_run.completed",
      data: { name: "lint", status: "completed", conclusion: "failure" },
    });
    expect(error).toHaveBeenCalledTimes(1);
    expect(success).not.toHaveBeenCalled();

    // in_progress: atualiza o badge mas não dispara toast.
    handle({
      provider: "github",
      event_type: "workflow_run.in_progress",
      data: { name: "build", status: "in_progress" },
    });
    const run = useCIStore.getState().lastRun;
    expect(run?.status).toBe("in_progress");
    expect(run?.conclusion).toBeNull();
    expect(error).toHaveBeenCalledTimes(1);
    expect(success).not.toHaveBeenCalled();
  });
});
