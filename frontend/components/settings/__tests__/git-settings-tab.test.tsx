// @vitest-environment jsdom

import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const setters = {
  hooks: vi.fn(),
  signoff: vi.fn(),
  bypass: vi.fn(),
};

vi.mock("@/lib/paraglide/messages", () => ({
  m: new Proxy({}, { get: (_target, key) => () => String(key) }),
}));

vi.mock("@/lib/stores/settings-store", () => ({
  useSettingsStore: (selector: (state: object) => unknown) =>
    selector({
      gitHooksEnabled: false,
      gitSignoffEnabled: false,
      gitBypassEnabled: false,
      setGitHooksEnabled: setters.hooks,
      setGitSignoffEnabled: setters.signoff,
      setGitBypassEnabled: setters.bypass,
    }),
}));

import { GitSettingsTab } from "../git-settings-tab";

afterEach(() => {
  vi.clearAllMocks();
});

describe("GitSettingsTab", () => {
  it("renderiza as três preferências e persiste cada alteração", () => {
    render(<GitSettingsTab />);
    const checkboxes = screen.getAllByRole("checkbox");
    expect(checkboxes).toHaveLength(3);

    fireEvent.click(checkboxes[0]);
    fireEvent.click(checkboxes[1]);
    fireEvent.click(checkboxes[2]);

    expect(setters.hooks).toHaveBeenCalledWith(true);
    expect(setters.signoff).toHaveBeenCalledWith(true);
    expect(setters.bypass).toHaveBeenCalledWith(true);
    expect(screen.getByText("settings_git_bypass")).toBeTruthy();
  });
});
