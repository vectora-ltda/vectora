import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  signalWorkspacePreChosen,
  consumeWorkspacePreChosen,
  signalCreateNewWorkspacePreNav,
  consumeCreateNewWorkspacePreNav,
  signalWorkspaceChoiceForNewSession,
} from "../new-session-signal";
import { useWorkspacesStore } from "../workspaces-store";

beforeEach(() => {
  consumeWorkspacePreChosen();
  consumeCreateNewWorkspacePreNav();
});

describe("new-session-signal", () => {
  it("começa sem sinal pendente", () => {
    expect(consumeWorkspacePreChosen()).toBe(false);
  });

  it("signal → consume retorna true", () => {
    signalWorkspacePreChosen();
    expect(consumeWorkspacePreChosen()).toBe(true);
  });

  it("consume é one-shot: segundo consume retorna false", () => {
    signalWorkspacePreChosen();
    consumeWorkspacePreChosen();
    expect(consumeWorkspacePreChosen()).toBe(false);
  });

  it("dois signals consecutivos continuam sendo consumidos uma vez", () => {
    signalWorkspacePreChosen();
    signalWorkspacePreChosen();
    expect(consumeWorkspacePreChosen()).toBe(true);
    expect(consumeWorkspacePreChosen()).toBe(false);
  });

  it("múltiplos cycles funcionam independentemente", () => {
    for (let i = 0; i < 3; i++) {
      expect(consumeWorkspacePreChosen()).toBe(false);
      signalWorkspacePreChosen();
      expect(consumeWorkspacePreChosen()).toBe(true);
      expect(consumeWorkspacePreChosen()).toBe(false);
    }
  });

  it("consume sem signal prévio é idempotente", () => {
    consumeWorkspacePreChosen();
    consumeWorkspacePreChosen();
    expect(consumeWorkspacePreChosen()).toBe(false);
  });
});

describe("new-session-signal — 'criar novo workspace' (sinal independente)", () => {
  it("signal → consume retorna true; segundo consume retorna false (one-shot)", () => {
    signalCreateNewWorkspacePreNav();
    expect(consumeCreateNewWorkspacePreNav()).toBe(true);
    expect(consumeCreateNewWorkspacePreNav()).toBe(false);
  });

  it("não interfere no sinal de workspace pré-escolhido (independentes)", () => {
    signalWorkspacePreChosen();
    signalCreateNewWorkspacePreNav();
    expect(consumeCreateNewWorkspacePreNav()).toBe(true);
    // o outro sinal continua pendente — consumo de um não afeta o outro
    expect(consumeWorkspacePreChosen()).toBe(true);
  });
});

describe("signalWorkspaceChoiceForNewSession — decisão única compartilhada", () => {
  it("workspaceId truthy: ativa o workspace via store e sinaliza pré-escolha, sem pedir workspace novo", () => {
    const setActive = vi.fn().mockResolvedValue(undefined);
    useWorkspacesStore.setState({ setActive });

    signalWorkspaceChoiceForNewSession("ws1");

    expect(setActive).toHaveBeenCalledWith("ws1");
    expect(consumeWorkspacePreChosen()).toBe(true);
    expect(consumeCreateNewWorkspacePreNav()).toBe(false);
  });

  it("workspaceId null ('criar novo'): sinaliza create-new-workspace, sem tocar a store (edge — era o bug real da tela inicial)", () => {
    const setActive = vi.fn().mockResolvedValue(undefined);
    useWorkspacesStore.setState({ setActive });

    signalWorkspaceChoiceForNewSession(null);

    expect(setActive).not.toHaveBeenCalled();
    expect(consumeWorkspacePreChosen()).toBe(true);
    expect(consumeCreateNewWorkspacePreNav()).toBe(true);
  });
});
