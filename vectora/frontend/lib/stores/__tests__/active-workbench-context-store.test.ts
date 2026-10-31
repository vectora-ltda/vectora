import { beforeEach, describe, expect, it } from "vitest";
import { useActiveWorkbenchContextStore } from "../active-workbench-context-store";

describe("active workbench context store", () => {
  beforeEach(() => {
    useActiveWorkbenchContextStore.getState().clear();
  });

  it("stores only the transient active route context", () => {
    useActiveWorkbenchContextStore.getState().setContext({
      threadId: "thread-1",
      workspaceId: "workspace-1",
    });

    expect(useActiveWorkbenchContextStore.getState()).toMatchObject({
      threadId: "thread-1",
      workspaceId: "workspace-1",
    });
  });

  it("clears context when the session route unmounts", () => {
    useActiveWorkbenchContextStore.getState().setContext({
      threadId: "thread-1",
      workspaceId: "workspace-1",
    });
    useActiveWorkbenchContextStore.getState().clear();

    expect(useActiveWorkbenchContextStore.getState()).toMatchObject({
      threadId: null,
      workspaceId: null,
    });
  });
});
