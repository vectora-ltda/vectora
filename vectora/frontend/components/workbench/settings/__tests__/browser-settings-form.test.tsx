// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { BrowserSettingsForm } from "../browser-settings-form";
vi.mock("@/lib/paraglide/messages", () => ({
  m: new Proxy({}, { get: (_target, key) => () => String(key) }),
}));
const error = vi.fn();
vi.mock("@/lib/stores/toast-store", () => ({
  useToastStore: { getState: () => ({ error }) },
}));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
  delete window.vectora;
});
describe("browser cookies", () => {
  it.each([true, false])(
    "removes a cookie with secure=%s using the matching scheme",
    async (secure) => {
      const removeCookie = vi.fn().mockResolvedValue(undefined);
      Object.defineProperty(window, "vectora", {
        configurable: true,
        value: {
          browserView: {
            listCookies: async () => [
              { name: "cookie", domain: ".example.com", path: "/", secure },
            ],
            removeCookie,
          },
        },
      });
      render(
        <BrowserSettingsForm
          threadId="t"
          workspaceId="w"
          browserProfileId="default"
          presentation="settings"
        />,
      );
      fireEvent.click(
        await screen.findByText("workbench_browser_cookies_remove"),
      );
      await waitFor(() =>
        expect(
          screen.queryByText("workbench_browser_cookies_remove"),
        ).toBeNull(),
      );
      expect(removeCookie).toHaveBeenCalledWith({
        profileId: "default",
        name: "cookie",
        url: `${secure ? "https" : "http"}://example.com/`,
      });
    },
  );
  it("keeps the cookie visible and reports a failed removal", async () => {
    Object.defineProperty(window, "vectora", {
      configurable: true,
      value: {
        browserView: {
          listCookies: async () => [
            { name: "cookie", domain: "example.com", path: "/", secure: false },
          ],
          removeCookie: vi.fn().mockRejectedValue(new Error("offline")),
        },
      },
    });
    render(
      <BrowserSettingsForm
        threadId="t"
        workspaceId="w"
        browserProfileId="default"
        presentation="settings"
      />,
    );
    fireEvent.click(
      await screen.findByText("workbench_browser_cookies_remove"),
    );
    await waitFor(() => expect(error).toHaveBeenCalled());
    expect(screen.getByText("workbench_browser_cookies_remove")).toBeTruthy();
  });
});
