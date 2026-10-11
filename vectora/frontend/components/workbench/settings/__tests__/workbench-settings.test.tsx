// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { lazy, type ReactElement } from "react";
import { WorkbenchDialog } from "@/components/workbench/workbench-dialog";
import {
  WorkbenchSettingsContent,
  getWorkbenchSettingsScopeKey,
  resolveWorkbenchSettingsSurfaceMode,
} from "../workbench-settings-content";
import { WorkbenchSettingsPage } from "../workbench-settings-page";
import {
  ALL_WORKBENCH_SETTINGS,
  contextGraphSettings,
  gitSettings,
  WORKBENCH_SETTINGS,
} from "../workbench-settings-registry";
import type { WorkbenchSettingsDescriptor } from "@/lib/types/workbench-settings";
import type { WorkbenchId } from "@/lib/types/workbench-settings";
import type { WorkbenchTab } from "@/lib/stores/workbench-store";
import { WORKBENCH_TABS } from "@/lib/stores/workbench-store";

afterEach(() => {
  cleanup();
  Reflect.deleteProperty(window, "vectora");
});

function Icon() {
  return <span aria-hidden="true" />;
}

function Broken(): ReactElement {
  throw new Error("settings failed");
}

const context = {
  threadId: "thread-1",
  workspaceId: "workspace-1",
  presentation: "settings" as const,
};

// Compile-time guard: descriptors can only target real workbench tabs.
const WORKBENCH_ID_IS_TAB: Record<WorkbenchId, WorkbenchTab> = {
  context_graph: "context_graph",
  storage: "storage",
  tasks: "tasks",
  browser: "browser",
  git: "git",
  terminal: "terminal",
  files: "files",
  plan: "plan",
  library: "library",
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
    sections: [{ id: "general", title: () => "General" }],
    capabilities: [],
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

  it("registers every workbench in the canonical navigation order", () => {
    expect(WORKBENCH_SETTINGS.map((item) => item.workbench)).toEqual([
      ...WORKBENCH_TABS,
    ]);
  });

  it("registers a reusable settings surface for every workbench", () => {
    expect(ALL_WORKBENCH_SETTINGS).toHaveLength(WORKBENCH_TABS.length);
    for (const item of ALL_WORKBENCH_SETTINGS) {
      expect(item.Component).toBeDefined();
      expect(item.surface.workbench).toBeDefined();
      expect(item.surface.settings).toBeDefined();
      expect(item.sections.length).toBeGreaterThan(0);
    }
    expect(
      WORKBENCH_SETTINGS.find((item) => item.workbench === "browser")?.surface,
    ).toEqual({
      workbench: "form",
      settings: "form",
    });
  });

  it("keeps owner scopes explicit for session, instance, and workspace data", () => {
    expect(
      WORKBENCH_SETTINGS.find((item) => item.workbench === "browser")?.scope,
    ).toBe("session");
    expect(
      WORKBENCH_SETTINGS.find((item) => item.workbench === "storage")?.scope,
    ).toBe("instance");
    expect(
      WORKBENCH_SETTINGS.find((item) => item.workbench === "terminal")
        ?.sections,
    ).toEqual([
      expect.objectContaining({ id: "display", scope: "user" }),
      expect.objectContaining({ id: "sandbox", scope: "workspace" }),
    ]);
  });

  it("resolves native surfaces to unavailable and Vectora Browser to a form", () => {
    const native = descriptor({
      id: "native-settings",
      surface: { workbench: "native-view", settings: "form" },
    });
    expect(
      resolveWorkbenchSettingsSurfaceMode(native, {
        ...context,
        presentation: "workbench",
      }),
    ).toBe("unavailable");
    expect(
      resolveWorkbenchSettingsSurfaceMode(
        WORKBENCH_SETTINGS.find((item) => item.id === "browser-settings")!,
        { ...context, presentation: "workbench" },
      ),
    ).toBe("form");
  });

  it("keeps settings in the same order as the navigation contract", () => {
    expect(ALL_WORKBENCH_SETTINGS.map((item) => item.workbench)).toEqual(
      WORKBENCH_TABS,
    );
  });

  it("does not expose a capability panel or capability metadata", () => {
    for (const item of ALL_WORKBENCH_SETTINGS) {
      expect("capabilities" in item).toBe(false);
    }
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

    expect(screen.getByRole("dialog")).toHaveClass(
      "overflow-hidden",
      "h-[min(85vh,52rem)]",
      "w-[min(92vw,78rem)]",
    );
    expect(screen.getByTestId("workbench-dialog-body")).toHaveClass(
      "min-h-0",
      "overflow-y-auto",
    );
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

  it("resolves isolated owner keys for user, workspace, session, and instance", () => {
    expect(getWorkbenchSettingsScopeKey("user", context)).toBe("user");
    expect(getWorkbenchSettingsScopeKey("instance", context)).toBe("instance");
    expect(getWorkbenchSettingsScopeKey("workspace", context)).toBe(
      "workspace-1",
    );
    expect(getWorkbenchSettingsScopeKey("session", context)).toBe(
      "workspace-1:thread-1",
    );
    expect(
      getWorkbenchSettingsScopeKey("session", {
        threadId: null,
        workspaceId: "workspace-1",
      }),
    ).toBeNull();
  });

  it("publishes the resolved scope on the shared host", () => {
    render(
      <WorkbenchSettingsContent
        descriptor={descriptor({ scope: "session" })}
        context={context}
      />,
    );
    expect(screen.getByTestId("workbench-settings-content")).toHaveAttribute(
      "data-settings-scope",
      "session",
    );
    expect(screen.getByTestId("workbench-settings-content")).toHaveAttribute(
      "data-settings-scope-key",
      "workspace-1:thread-1",
    );
  });

  it("renders translated loading and error fallbacks", async () => {
    const pending = lazy(
      () => new Promise<{ default: () => ReactElement }>(() => undefined),
    );
    render(
      <WorkbenchSettingsContent
        descriptor={descriptor({ Component: pending })}
        context={context}
      />,
    );
    expect(screen.getByText(/loading/i)).toBeInTheDocument();

    cleanup();
    const errorSpy = vi
      .spyOn(console, "error")
      .mockImplementation(() => undefined);
    render(
      <WorkbenchSettingsContent
        descriptor={descriptor({ Component: Broken })}
        context={context}
      />,
    );
    expect(
      await screen.findByText(/could not load|não foi possível|no se pudo/i),
    ).toBeInTheDocument();
    errorSpy.mockRestore();
  });

  it("renders grouped settings as an accordion with stable anchors", () => {
    render(
      <WorkbenchSettingsPage
        descriptors={[
          descriptor({ id: "terminal-settings", workbench: "terminal" }),
          descriptor({
            id: "git-settings",
            workbench: "git",
            title: () => "Git settings",
          }),
        ]}
        context={context}
      />,
    );
    expect(
      screen.getByText("Terminal", { selector: "summary span" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Git", { selector: "summary span" }),
    ).toBeInTheDocument();
    expect(
      document.querySelector("details#workbench-settings-git"),
    ).toHaveAttribute("open");
    expect(
      document.getElementById("workbench-settings-git"),
    ).toBeInTheDocument();
    expect(screen.getAllByText("settings form")).toHaveLength(2);
    expect(screen.getByRole("navigation")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Git" })).toHaveAttribute(
      "href",
      "#workbench-settings-git",
    );
  });

  it("renders the real Git and Context Graph forms through the shared page", () => {
    render(
      <WorkbenchSettingsPage
        descriptors={[contextGraphSettings, gitSettings]}
        context={context}
      />,
    );

    expect(screen.getByText(/file types/i)).toBeInTheDocument();
    expect(screen.getByText(/run hooks before commit/i)).toBeInTheDocument();
    expect(screen.getByRole("navigation")).toHaveTextContent("Context Graph");
    expect(screen.getByRole("navigation")).toHaveTextContent("Git");
  });

  it("does not render contract capability metadata", () => {
    render(
      <WorkbenchSettingsContent descriptor={descriptor()} context={context} />,
    );
    expect(screen.queryByText("general")).not.toBeInTheDocument();
    expect(screen.queryByText(/capabilit/i)).not.toBeInTheDocument();
  });

  it("passes the active context to the global workbench settings page", async () => {
    const pageSpy = vi.fn();
    vi.doMock("../workbench-settings-page", () => ({
      WorkbenchSettingsPage: (props: unknown) => {
        pageSpy(props);
        return <div data-testid="workbench-settings-page" />;
      },
    }));
    vi.doMock("../workbench-settings-registry", () => ({
      ALL_WORKBENCH_SETTINGS: [{ id: "browser-settings" }],
    }));
    vi.doMock("@/lib/stores/active-workbench-context-store", () => ({
      useActiveWorkbenchContextStore: (selector: (state: unknown) => unknown) =>
        selector({
          threadId: "thread-1",
          workspaceId: "workspace-1",
          browserProfileId: "session-profile",
        }),
    }));

    const { WorkbenchesSettings } =
      await import("@/components/settings/workbenches-settings");
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
    vi.doUnmock("../workbench-settings-page");
    vi.doUnmock("../workbench-settings-registry");
    vi.doUnmock("@/lib/stores/active-workbench-context-store");
  });
});
