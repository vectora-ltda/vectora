import { describe, it, expect } from "vitest";
import { buildOptimisticThread } from "../-thread-cache-helpers";

describe("buildOptimisticThread", () => {
  it("marca mode='chat' quando a conversa nasce em modo Chat", () => {
    const thread = buildOptimisticThread({
      id: "t1",
      title: "Nova conversa",
      workspaceId: "",
      chatMode: true,
      now: "2026-08-26T00:00:00.000Z",
    });
    expect(thread.mode).toBe("chat");
    expect(thread.workspace_id).toBe("");
  });

  it("erro/borda: marca mode='code' quando a conversa nasce em modo Code, nunca deixa mode indefinido", () => {
    const thread = buildOptimisticThread({
      id: "t2",
      title: "Nova sessão",
      workspaceId: "ws-1",
      chatMode: false,
      now: "2026-08-26T00:00:00.000Z",
    });
    expect(thread.mode).toBe("code");
    expect(thread.mode).not.toBeUndefined();
  });
});
