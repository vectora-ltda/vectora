// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { BrowserSettingsContent } from "../browser-settings-content";

const state = {
  browserAllowPopups: false,
  browserZoomPercent: 100,
  browserPermissionMode: "deny" as const,
  browserSearchEngine: "duckduckgo" as const,
  browserOriginPermissions: {} as Record<string, "allow" | "deny">,
  setBrowserAllowPopups: vi.fn(),
  setBrowserZoomPercent: vi.fn(),
  setBrowserPermissionMode: vi.fn(),
  setBrowserSearchEngine: vi.fn(),
  setBrowserOriginPermission: vi.fn(),
  removeBrowserOriginPermission: vi.fn(),
};

vi.mock("@/lib/stores/settings-store", () => ({
  useSettingsStore: () => state,
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  Reflect.deleteProperty(window, "vectora");
  state.browserOriginPermissions = {};
});

function installBridge() {
  const bridge = {
    listCredentials: vi.fn(async () => [
      {
        id: "credential-1",
        origin: "https://example.com",
        username: "bruno",
        createdAt: "2026-01-01",
        updatedAt: "2026-01-01",
      },
    ]),
    saveCredential: vi.fn(
      async (input: {
        profileId: string;
        origin: string;
        username: string;
        password: string;
      }) => ({
        id: "credential-2",
        origin: input.origin,
        username: input.username,
        createdAt: "2026-01-01",
        updatedAt: "2026-01-01",
      }),
    ),
    deleteCredential: vi.fn(async () => undefined),
    listCookies: vi.fn(async () => [
      {
        name: "session",
        domain: "example.com",
        path: "/",
        secure: true,
        httpOnly: true,
      },
    ]),
    removeCookie: vi.fn(async () => undefined),
    clearProfileData: vi.fn(async () => undefined),
    onDownload: vi.fn(() => () => undefined),
  };
  Object.defineProperty(window, "vectora", {
    configurable: true,
    value: { browserView: bridge },
  });
  return bridge;
}

const context = {
  threadId: "thread-1",
  workspaceId: "workspace-1",
  browserProfileId: "profile-1",
  presentation: "workbench" as const,
  open: true,
};

describe("BrowserSettingsContent", () => {
  it("renders the Vectora-owned form without navigating to chrome settings", async () => {
    const bridge = installBridge();
    render(<BrowserSettingsContent {...context} />);

    expect(
      screen.getByText(
        "Browser settings are managed here by Vectora for this profile.",
      ),
    ).toBeTruthy();
    await waitFor(() =>
      expect(bridge.listCredentials).toHaveBeenCalledWith("profile-1"),
    );
    expect(screen.getByText("https://example.com · bruno")).toBeTruthy();
    expect(screen.getByText("session · example.com")).toBeTruthy();
  });

  it("persists browser preferences and origin overrides", async () => {
    const bridge = installBridge();
    render(<BrowserSettingsContent {...context} />);
    await waitFor(() => expect(bridge.listCredentials).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("checkbox"));
    expect(state.setBrowserAllowPopups).toHaveBeenCalledWith(true);
    fireEvent.change(screen.getAllByPlaceholderText("https://example.com")[0], {
      target: { value: "https://login.example.com/" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(state.setBrowserOriginPermission).toHaveBeenCalledWith(
      "https://login.example.com",
      "allow",
    );
  });

  it("saves and removes credentials through the profile bridge", async () => {
    const bridge = installBridge();
    render(<BrowserSettingsContent {...context} />);
    await waitFor(() => expect(bridge.listCredentials).toHaveBeenCalled());
    fireEvent.change(screen.getAllByPlaceholderText("https://example.com")[1], {
      target: { value: "https://new.example.com" },
    });
    fireEvent.change(screen.getByPlaceholderText("Username"), {
      target: { value: "alice" },
    });
    fireEvent.change(screen.getByPlaceholderText("Password"), {
      target: { value: "secret" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save credential" }));
    await waitFor(() => expect(bridge.saveCredential).toHaveBeenCalled());
    fireEvent.click(screen.getAllByRole("button", { name: "Remove" })[0]);
    await waitFor(() =>
      expect(bridge.deleteCredential).toHaveBeenCalledWith({
        profileId: "profile-1",
        id: "credential-1",
      }),
    );
  });

  it("shows the web limitation instead of claiming native profile support", () => {
    render(<BrowserSettingsContent {...context} browserProfileId={null} />);
    expect(screen.getByText(/Cookies, downloads, permissions/)).toBeTruthy();
  });

  it("clears profile data through the bridge", async () => {
    const bridge = installBridge();
    render(<BrowserSettingsContent {...context} />);
    await waitFor(() => expect(bridge.listCredentials).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "Clear browser data" }));
    await waitFor(() =>
      expect(bridge.clearProfileData).toHaveBeenCalledWith("profile-1", {
        storage: true,
        cache: true,
        credentials: true,
      }),
    );
  });
});
