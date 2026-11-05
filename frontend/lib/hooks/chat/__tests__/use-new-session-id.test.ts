// @vitest-environment jsdom
/**
 * Regressão: TanStack Router reaproveita a MESMA instância de componente
 * entre /session/<uuid-real> e /session/new (mesmo padrão de rota
 * $threadId). Clicar em "Nova sessão" a partir de uma sessão real navegava
 * de volta pra "new" sem remontar o componente — o id local gerado só na
 * primeira montagem ficava reciclado, fazendo a "nova" sessão continuar
 * silenciosamente a conversa anterior (ou, em outros casos, apontar pra um
 * id client-side que o backend nunca viu → getHistory 404 → tela "Not
 * Found").
 */

import { describe, expect, it } from "vitest";
import { renderHook } from "@testing-library/react";
import { useNewSessionId } from "../use-new-session-id";
import { isNew } from "@/lib/stores/new-thread-registry";
import { signalWorkspacePreChosen } from "@/lib/stores/new-session-signal";
import { isWorkspaceChosen } from "@/lib/stores/workspace-choice-registry";

describe("useNewSessionId", () => {
  it("gera um id em /session/new e string vazia fora dele", () => {
    const { result: newResult } = renderHook(() => useNewSessionId("new"));
    expect(newResult.current).not.toBe("");
    expect(isNew(newResult.current)).toBe(true);

    const { result: realResult } = renderHook(() =>
      useNewSessionId("real-thread-id"),
    );
    expect(realResult.current).toBe("");
  });

  it("regera um id novo ao voltar pra 'new' vindo de uma sessão real (edge — bug real: id antigo era reciclado)", () => {
    const { result, rerender } = renderHook(
      ({ routeParam }) => useNewSessionId(routeParam),
      { initialProps: { routeParam: "new" } },
    );

    const firstId = result.current;
    expect(firstId).not.toBe("");

    // Navega pra uma sessão real (thread já persistida) — mesma instância,
    // sem remontar (comportamento real do TanStack Router pro padrão
    // /session/$threadId).
    rerender({ routeParam: "real-thread-abc" });
    expect(result.current).toBe("");

    // Clica em "Nova sessão" de novo — routeParam volta pra "new".
    rerender({ routeParam: "new" });
    expect(result.current).not.toBe("");
    expect(result.current).not.toBe(firstId);
    expect(isNew(result.current)).toBe(true);
  });

  it("não regera o id enquanto routeParam continuar 'new' (sem transição)", () => {
    const { result, rerender } = renderHook(
      ({ routeParam }) => useNewSessionId(routeParam),
      { initialProps: { routeParam: "new" } },
    );

    const firstId = result.current;
    rerender({ routeParam: "new" });
    expect(result.current).toBe(firstId);
  });

  it("consome a escolha de workspace somente depois do commit", () => {
    signalWorkspacePreChosen();
    const { result } = renderHook(() => useNewSessionId("new"));

    expect(isWorkspaceChosen(result.current)).toBe(true);
  });
});
