// @vitest-environment jsdom
/**
 * Tests para broadcastEvent + useBroadcastSync: sincronização entre abas via
 * BroadcastChannel (invalidate/created/deleted/logout).
 */

import { describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import {
  broadcastEvent,
  useBroadcastSync,
  type BroadcastPayload,
} from "../use-broadcast-sync";

describe("broadcastEvent", () => {
  it("entrega o payload a um listener do mesmo canal", async () => {
    const received = new Promise<BroadcastPayload>((resolve) => {
      const bc = new BroadcastChannel("chan-a");
      bc.addEventListener("message", (e: MessageEvent<BroadcastPayload>) => {
        resolve(e.data);
        bc.close();
      });
    });
    broadcastEvent("chan-a", { type: "invalidate" });
    expect(await received).toEqual({ type: "invalidate" });
  });
});

describe("useBroadcastSync", () => {
  it("chama onMessage quando outra aba publica no canal", async () => {
    const onMessage = vi.fn();
    const { unmount } = renderHook(() => useBroadcastSync("chan-b", onMessage));

    broadcastEvent("chan-b", { type: "deleted", id: "t1" });

    await waitFor(() =>
      expect(onMessage).toHaveBeenCalledWith({ type: "deleted", id: "t1" }),
    );
    unmount();
  });

  it("não escuta quando enabled=false", async () => {
    const onMessage = vi.fn();
    renderHook(() => useBroadcastSync("chan-c", onMessage, false));
    broadcastEvent("chan-c", { type: "invalidate" });
    // Pequena espera para garantir que nada chegou.
    await new Promise((r) => setTimeout(r, 30));
    expect(onMessage).not.toHaveBeenCalled();
  });
});
