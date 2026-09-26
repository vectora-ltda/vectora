import { describe, expect, it } from "vitest";

import { isCanvasDocumentVisible } from "../canvas-document-visibility";
import type { CanvasDocumentDescriptor } from "../stores/windows-store";

const preview: CanvasDocumentDescriptor = {
  id: "mcp:workspace-1:thread-1:filesystem",
  kind: "mcp-preview",
  workspaceId: "workspace-1",
  threadId: "thread-1",
  title: "MCP: Filesystem",
};

describe("isCanvasDocumentVisible", () => {
  it("exibe o preview no mesmo workspace e thread", () => {
    expect(isCanvasDocumentVisible(preview, "workspace-1", "thread-1")).toBe(
      true,
    );
  });

  it("oculta o preview em outro workspace", () => {
    expect(isCanvasDocumentVisible(preview, "workspace-2", "thread-1")).toBe(
      false,
    );
  });

  it("oculta o preview em outro thread", () => {
    expect(isCanvasDocumentVisible(preview, "workspace-1", "thread-2")).toBe(
      false,
    );
  });

  it("exibe um preview sem workspace no mesmo thread", () => {
    expect(
      isCanvasDocumentVisible(
        { ...preview, workspaceId: null },
        null,
        "thread-1",
      ),
    ).toBe(true);
  });

  it("rejeita workspace vazio", () => {
    expect(
      isCanvasDocumentVisible({ ...preview, workspaceId: "" }, "", "thread-1"),
    ).toBe(false);
  });

  it("rejeita thread vazio", () => {
    expect(isCanvasDocumentVisible(preview, "workspace-1", "")).toBe(false);
  });
});
