import { describe, it, expect } from "vitest";
import {
  getAllowedModels,
  getDefaultModel,
  isModelAllowed,
  getModelDisplayName,
  getModelProvider,
  getContextWindow,
} from "@/lib/config/deployment-config";
import type { ModelOption } from "@/lib/config/deployment-config";

describe("modelos permitidos", () => {
  it("getAllowedModels retorna ids 'provider:model' não vazios", () => {
    const models = getAllowedModels();
    expect(models.length).toBeGreaterThan(0);
    for (const id of models) expect(id).toContain(":");
  });

  it("o modelo default está entre os permitidos", () => {
    expect(getAllowedModels()).toContain(getDefaultModel());
  });

  it("isModelAllowed: default true, inexistente false", () => {
    expect(isModelAllowed(getDefaultModel())).toBe(true);
    expect(isModelAllowed("fake:model" as ModelOption)).toBe(false);
  });
});

describe("getModelDisplayName", () => {
  it("modelo conhecido tem nome amigável não vazio", () => {
    expect(getModelDisplayName(getDefaultModel()).length).toBeGreaterThan(0);
  });

  it("modelo desconhecido cai no próprio id", () => {
    expect(getModelDisplayName("xyz:abc" as ModelOption)).toBe("xyz:abc");
  });

  it("modelo ollama dinâmico mostra a tag crua, sem o prefixo", () => {
    expect(getModelDisplayName("ollama:qwen3:8b")).toBe("qwen3:8b");
  });

  it("modelo openrouter dinâmico mostra o id cru, sem o prefixo", () => {
    expect(getModelDisplayName("openrouter:openai/gpt-4o")).toBe(
      "openai/gpt-4o",
    );
  });
});

describe("getModelProvider", () => {
  it("infere o provider de um id conhecido", () => {
    expect(getModelProvider("cohere:command-a-03-2025" as ModelOption)).toBe(
      "cohere",
    );
  });

  it("id desconhecido cai no provider default google-genai", () => {
    expect(getModelProvider("xyz:abc" as ModelOption)).toBe("google-genai");
  });

  it("id ollama:* infere provider ollama", () => {
    expect(getModelProvider("ollama:qwen3:8b")).toBe("ollama");
  });

  it("id openrouter:* infere provider openrouter", () => {
    expect(getModelProvider("openrouter:openai/gpt-4o")).toBe("openrouter");
  });
});

describe("getContextWindow", () => {
  it("usa a tabela explícita quando há entrada", () => {
    expect(getContextWindow("cohere:command-a-03-2025" as ModelOption)).toBe(
      256_000,
    );
    expect(getContextWindow("anthropic:claude-opus-4-7" as ModelOption)).toBe(
      200_000,
    );
  });

  it("fallback por prefixo de provider", () => {
    expect(getContextWindow("google-genai:qualquer-coisa" as ModelOption)).toBe(
      1_000_000,
    );
    expect(getContextWindow("openai:algo" as ModelOption)).toBe(200_000);
    expect(getContextWindow("cohere:command-r-novo" as ModelOption)).toBe(
      128_000,
    );
  });

  it("provider totalmente desconhecido → 128k", () => {
    expect(getContextWindow("zzz:modelo" as ModelOption)).toBe(128_000);
  });
});
