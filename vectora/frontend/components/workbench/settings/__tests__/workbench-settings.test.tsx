// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { lazy, type ReactElement } from "react";
import { WorkbenchDialog } from "@/components/workbench/workbench-dialog";
import {
  WorkbenchSettingsContent,
  resolveWorkbenchSettingsCapabilities,
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
    capabilities: [{ id: "general", status: "available" }],
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

  it("resolves every browser capability as unavailable on the web runtime", () => {
    const browser = WORKBENCH_SETTINGS.find(
      (item) => item.workbench === "browser",
    )!;
    expect(
      resolveWorkbenchSettingsCapabilities(browser, false).every(
        (capability) => capability.status === "unavailable",
      ),
    ).toBe(true);
  });

  it("keeps browser capabilities available on the desktop runtime", () => {
    const browser = WORKBENCH_SETTINGS.find(
      (item) => item.workbench === "browser",
    )!;
    expect(
      resolveWorkbenchSettingsCapabilities(browser, true).every(
        (capability) => capability.status === "available",
      ),
    ).toBe(true);
  });

  it("keeps settings in the same order as the navigation contract", () => {
    expect(ALL_WORKBENCH_SETTINGS.map((item) => item.workbench)).toEqual(
      WORKBENCH_TABS,
    );
  });

  it("does not advertise capabilities that have no implementation", () => {
    expect(
      WORKBENCH_SETTINGS.flatMap((item) => item.capabilities).every(
        (capability) => capability.status === "available",
      ),
    ).toBe(true);
  });

  it("ships the formerly planned services as available capabilities", () => {
    const capabilities = new Map(
      ALL_WORKBENCH_SETTINGS.flatMap((item) =>
        item.capabilities.map(
          (itemCapability) =>
            [itemCapability.id, itemCapability.status] as const,
        ),
      ),
    );

    expect(capabilities.get("password-manager-ui")).toBe("available");
    expect(capabilities.get("formatter-service")).toBe("available");
    expect(capabilities.get("linter-service")).toBe("available");
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
});
