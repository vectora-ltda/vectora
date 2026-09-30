// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { WorkbenchDialog } from "@/components/workbench/workbench-dialog";
import { WorkbenchSettingsContent } from "../workbench-settings-content";
import { WorkbenchSettingsPage } from "../workbench-settings-page";
import { WORKBENCH_SETTINGS } from "../workbench-settings-registry";
import type { WorkbenchSettingsDescriptor } from "@/lib/types/workbench-settings";
import type { WorkbenchId } from "@/lib/types/workbench-settings";
import type { WorkbenchTab } from "@/lib/stores/workbench-store";

afterEach(cleanup);

function Icon() {
  return <span aria-hidden="true" />;
}

const context = { threadId: "thread-1", workspaceId: "workspace-1" };

// Compile-time guard: descriptors can only target real workbench tabs.
const WORKBENCH_ID_IS_TAB: Record<WorkbenchId, WorkbenchTab> = {
  context_graph: "context_graph",
  storage: "storage",
  tasks: "tasks",
  browser: "browser",
  diff: "diff",
  terminal: "terminal",
  files: "files",
};

function descriptor(
  overrides: Partial<WorkbenchSettingsDescriptor> = {},
): WorkbenchSettingsDescriptor {
  return {
    id: "test-settings",
    workbench: "terminal",
    title: () => "Terminal settings",
    description: () => "Configure the terminal",
    icon: Icon,
    scope: "user",
    Component: () => <p>settings form</p>,
    surface: { workbench: "form", settings: "form" },
    ...overrides,
  };
}

describe("workbench settings contract", () => {
  it("keeps the descriptor ids within the workbench tab contract", () => {
    expect(Object.values(WORKBENCH_ID_IS_TAB)).toContain("browser");
  });

  it("starts with a registry that has unique descriptor ids", () => {
    const ids = WORKBENCH_SETTINGS.map((item) => item.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("renders a responsive modal shell and closes through Radix Escape", () => {
    const onOpenChange = vi.fn();
    render(
      <WorkbenchDialog
        open
        onOpenChange={onOpenChange}
        title="Terminal settings"
        description="Configure the terminal"
      >
        <p>body</p>
      </WorkbenchDialog>,
    );

    expect(screen.getByRole("dialog")).toHaveClass("overflow-hidden");
    expect(screen.getByText("body")).toBeInTheDocument();
    expect(screen.getByText("Configure the terminal")).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("shows explicit empty states for missing scoped context", () => {
    render(
      <WorkbenchSettingsContent
        descriptor={descriptor({ scope: "workspace" })}
        context={{
          threadId: null,
          workspaceId: null,
          presentation: "workbench",
        }}
      />,
    );
    expect(screen.getByText(/workspace/i)).toBeInTheDocument();
  });

  it("renders grouped settings with stable anchors", () => {
    render(
      <WorkbenchSettingsPage
        descriptors={[
          descriptor({ id: "terminal-settings", workbench: "terminal" }),
          descriptor({
            id: "git-settings",
            workbench: "diff",
            title: () => "Git settings",
          }),
        ]}
        context={context}
      />,
    );
    expect(screen.getByRole("link", { name: "Terminal" })).toHaveAttribute(
      "href",
      "#workbench-settings-terminal",
    );
    expect(
      document.getElementById("workbench-settings-diff"),
    ).toBeInTheDocument();
    expect(screen.getByText("Git settings")).toBeInTheDocument();
  });
});
