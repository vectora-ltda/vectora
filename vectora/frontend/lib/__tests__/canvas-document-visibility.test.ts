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
  it("exibe preview MCP apenas no mesmo workspace e thread", () => {
    expect(isCanvasDocumentVisible(preview, "workspace-1", "thread-1")).toBe(
      true,
    );
    expect(isCanvasDocumentVisible(preview, "workspace-2", "thread-1")).toBe(
      false,
    );
    expect(isCanvasDocumentVisible(preview, "workspace-1", "thread-2")).toBe(
      false,
    );
  });
});
