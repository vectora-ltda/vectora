// @vitest-environment jsdom

import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";

const pageSpy = vi.fn();

vi.mock("../workbench-settings-page", () => ({
  WorkbenchSettingsPage: (props: unknown) => {
    pageSpy(props);
    return <div data-testid="workbench-settings-page" />;
  },
}));

vi.mock("@/lib/stores/active-workbench-context-store", () => ({
  useActiveWorkbenchContextStore: (selector: (state: unknown) => unknown) =>
    selector({
      threadId: "thread-1",
      workspaceId: "workspace-1",
      browserProfileId: "session-profile",
    }),
}));

vi.mock("../workbench-settings-registry", () => ({
  ALL_WORKBENCH_SETTINGS: [{ id: "browser-settings" }],
}));

import { WorkbenchesSettings } from "../workbenches-settings-category";

describe("WorkbenchesSettings", () => {
  it("passa o contexto ativo e força a apresentação global", () => {
    render(<WorkbenchesSettings />);
    expect(pageSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        descriptors: [{ id: "browser-settings" }],
        context: {
          threadId: "thread-1",
          workspaceId: "workspace-1",
          browserProfileId: "session-profile",
        },
      }),
    );
  });
});
