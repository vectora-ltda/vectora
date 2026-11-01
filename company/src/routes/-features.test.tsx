// @vitest-environment jsdom
/**
 * /features — cada feature listada precisa de título, resumo E descrição
 * completa reais (nenhum vazio) e id único, senão o accordion quebra
 * silenciosamente (duas entradas com o mesmo `value` do Radix Accordion
 * colidem) ou mostra uma linha em branco pro usuário.
 */
import { describe, it, expect, vi } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

// Mensagens reais do catálogo pt (fonte de verdade), não um Proxy que ecoa a
// chave — senão as asserções de "não vazio" abaixo passam sempre, mesmo com
// uma mensagem genuinamente vazia/ausente no JSON.
const ptMessages: Record<string, string> = JSON.parse(
  readFileSync(join(import.meta.dirname, "../../messages/pt.json"), "utf-8"),
);

vi.mock("#/paraglide/messages", () => ({
  m: new Proxy(
    {},
    {
      get:
        (_t, prop: string) =>
        (..._args: unknown[]) => {
          const value = ptMessages[prop];
          if (typeof value !== "string") {
            throw new Error(`mensagem "${prop}" ausente em messages/pt.json`);
          }
          return value;
        },
    },
  ),
}));

import { getFeatureCategories } from "./features";

describe("getFeatureCategories", () => {
  it("tem pelo menos 4 categorias, cada uma com pelo menos 1 feature", () => {
    const categories = getFeatureCategories();
    expect(categories.length).toBeGreaterThanOrEqual(4);
    for (const category of categories) {
      expect(category.items.length).toBeGreaterThan(0);
    }
  });

  it("erro/borda: nenhuma feature tem título, resumo ou descrição vazios", () => {
    const categories = getFeatureCategories();
    const allItems = categories.flatMap((c) => c.items);
    expect(allItems.length).toBeGreaterThan(0);
    for (const item of allItems) {
      expect(item.title.trim().length).toBeGreaterThan(0);
      expect(item.summary.trim().length).toBeGreaterThan(0);
      expect(item.description.trim().length).toBeGreaterThan(0);
    }
  });

  it("lista as capacidades da linha 0.3 nas categorias de agente e navegação", () => {
    const categories = getFeatureCategories();
    const idsOf = (categoryId: string) =>
      categories.find((c) => c.id === categoryId)?.items.map((i) => i.id) ?? [];

    expect(idsOf("agente")).toEqual(
      expect.arrayContaining([
        "agent-run",
        "plan-mode",
        "decision-models",
        "verification-lab",
      ]),
    );
    const navegador = idsOf("navegador");
    expect(navegador.indexOf("egress-policies")).toBe(
      navegador.indexOf("sandbox") + 1,
    );

    // Erro/borda: nenhuma categoria paralela de governança, e cada item novo
    // tem ícone próprio, distinto de todos os outros itens do catálogo.
    expect(categories.map((c) => c.id)).not.toContain("governanca");
    const items = categories.flatMap((c) => c.items);
    for (const id of [
      "agent-run",
      "plan-mode",
      "decision-models",
      "verification-lab",
      "egress-policies",
    ]) {
      const item = items.find((i) => i.id === id);
      expect(item, id).toBeDefined();
      const sharing = items.filter((i) => i.Icon === item?.Icon);
      expect(sharing.map((i) => i.id)).toEqual([id]);
    }

    // Erro/borda: em todos os idiomas, Landlock ABI V4 exige kernel 6.7+ e o
    // Evaluation Lab oculta (não "redige") os dados sensíveis.
    const hidden: Record<string, string> = {
      pt: "ocultados",
      es: "ocultos",
      fr: "masquées",
      ru: "скрытыми",
    };
    for (const locale of ["en", "pt", "es", "de", "fr", "it", "ru"]) {
      const messages = JSON.parse(
        readFileSync(
          join(import.meta.dirname, `../../messages/${locale}.json`),
          "utf-8",
        ),
      ) as Record<string, string>;
      const egress = messages.features_egress_policies_desc;
      expect(egress, locale).toContain("6.7+");
      expect(egress, locale).not.toContain("6.5+");
      const lab = messages.features_verification_lab_desc;
      expect(lab, locale).not.toMatch(/redigid|rédaction|редактир|redactad/);
      if (hidden[locale]) expect(lab, locale).toContain(hidden[locale]);
    }
  });

  it("erro/borda: todos os ids de feature são únicos (accordion Radix colide em duplicata)", () => {
    const categories = getFeatureCategories();
    const ids = categories.flatMap((c) => c.items.map((i) => i.id));
    expect(new Set(ids).size).toBe(ids.length);
  });
});
