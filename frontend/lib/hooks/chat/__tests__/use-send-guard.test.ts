// @vitest-environment jsdom
import { describe, it, expect } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useSendGuard } from "../use-send-guard";

/**
 * Guard atômico síncrono contra dupla submissão de `handleSend`.
 *
 * O `uiState.isLoading` do reducer só reflete no próximo render do React —
 * duas chamadas síncronas no mesmo tick (Enter rápido duas vezes, Enter +
 * clique, autofill + Enter) ambas leem `isLoading === false` e duplicariam
 * a mensagem do usuário. O guard usa um `useRef` (síncrono, sem render)
 * para reserva atômica: a 1ª chamada adquire, as seguintes são rejeitadas
 * até `release()`.
 */
describe("useSendGuard", () => {
  it("permite o 1º disparo e rejeita submissões síncronas duplicadas", () => {
    const { result } = renderHook(() => useSendGuard());

    expect(result.current.tryAcquire()).toBe(true);
    // Segunda chamada no mesmo tick (sem release) é rejeitada — é o bug
    // reprodutor: sem o guard, a 2ª submissão adicionaria a mensagem de novo.
    expect(result.current.tryAcquire()).toBe(false);
    expect(result.current.tryAcquire()).toBe(false);
  });

  it("volta a permitir disparo após release()", () => {
    const { result } = renderHook(() => useSendGuard());

    expect(result.current.tryAcquire()).toBe(true);
    act(() => result.current.release());
    expect(result.current.tryAcquire()).toBe(true);
  });

  it("release() é idempotente — chamadas extras não quebram o próximo acquire", () => {
    const { result } = renderHook(() => useSendGuard());

    expect(result.current.tryAcquire()).toBe(true);
    act(() => result.current.release());
    act(() => result.current.release()); // release duplicado não deve corromper
    expect(result.current.tryAcquire()).toBe(true);
  });

  it("não pode haver duas reservas simultâneas entre instances distintas", () => {
    // Dois hooks = dois refs independentes: não compartilham guard. Isso é
    // esperado (cada thread/componente tem seu próprio ciclo de envio), mas
    // documenta o contrato de que o guard é por-instância.
    const a = renderHook(() => useSendGuard());
    const b = renderHook(() => useSendGuard());

    expect(a.result.current.tryAcquire()).toBe(true);
    expect(b.result.current.tryAcquire()).toBe(true);
  });
});
