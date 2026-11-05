/**
 * Tests para `selectedModel` do settings-store — regressão do bug em que o
 * modelo escolhido pelo usuário (ex.: Cohere) voltava para o default a cada
 * restart/reload, porque nunca era persistido (vivia só em useState local de
 * `$threadId.tsx`, recriado do zero em cada mount via getDefaultModel()).
 */

import { describe, expect, it, beforeEach } from "vitest";
import { useSettingsStore } from "../settings-store";
import { getDefaultModel } from "@/lib/config/deployment-config";

beforeEach(() => {
  useSettingsStore.getState().resetSettings();
});

describe("settings-store — selectedModel", () => {
  it("valor padrão é o default do deployment-config", () => {
    expect(useSettingsStore.getState().selectedModel).toBe(getDefaultModel());
  });

  it("setSelectedModel atualiza o modelo ativo", () => {
    useSettingsStore.getState().setSelectedModel("cohere:command-a-03-2025");
    expect(useSettingsStore.getState().selectedModel).toBe(
      "cohere:command-a-03-2025",
    );
  });

  it("resetSettings restaura para o default", () => {
    useSettingsStore.getState().setSelectedModel("cohere:command-a-03-2025");
    useSettingsStore.getState().resetSettings();
    expect(useSettingsStore.getState().selectedModel).toBe(getDefaultModel());
  });

  it("selectedModel entra no partialize (é persistido no localStorage)", () => {
    useSettingsStore.getState().setSelectedModel("cohere:command-a-03-2025");
    const partialize = useSettingsStore.persist.getOptions().partialize;
    expect(partialize).toBeDefined();
    const persisted = partialize!(useSettingsStore.getState());
    expect(persisted).toMatchObject({
      selectedModel: "cohere:command-a-03-2025",
    });
  });

  it("valor vazio não é permitido — string vazia ainda é aceita mas onChange do seletor sempre manda um id válido", () => {
    // Guarda de regressão: o setter não deve normalizar/validar (é
    // responsabilidade do ModelSelector só emitir ids válidos) — aqui só
    // confirmamos que o setter não lança para nenhum valor de string.
    expect(() =>
      useSettingsStore.getState().setSelectedModel(""),
    ).not.toThrow();
  });
});
