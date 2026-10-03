// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { BrowserProfileCleanupAction } from "../browser-profile-cleanup-action";

vi.mock("@/lib/paraglide/messages", () => ({
  m: new Proxy(
    {},
    {
      get: (_target, property) => () => String(property),
    },
  ),
}));

describe("BrowserProfileCleanupAction", () => {
  it("confirma a limpeza e encaminha o perfil e os escopos selecionados", async () => {
    const clearProfileData = vi.fn().mockResolvedValue(undefined);
    render(
      <BrowserProfileCleanupAction
        profileId="profile-1"
        sessionKey="workspace:thread"
        clearProfileData={clearProfileData}
      />,
    );

    fireEvent.click(screen.getByTestId("browser-clear-profile-btn"));
    fireEvent.click(
      screen
        .getAllByRole("button", {
          name: "workbench_browser_clear_profile_data",
        })
        .at(-1)!,
    );

    await waitFor(() =>
      expect(clearProfileData).toHaveBeenCalledWith("profile-1", {
        storage: true,
        cache: true,
        credentials: false,
      }),
    );
  });

  it("mantém erro acessível quando a bridge rejeita a limpeza", async () => {
    const clearProfileData = vi.fn().mockRejectedValue(new Error("failed"));
    render(
      <BrowserProfileCleanupAction
        profileId="profile-1"
        sessionKey="workspace:thread"
        clearProfileData={clearProfileData}
      />,
    );

    fireEvent.click(screen.getByTestId("browser-clear-profile-btn"));
    fireEvent.click(
      screen
        .getAllByRole("button", {
          name: "workbench_browser_clear_profile_data",
        })
        .at(-1)!,
    );

    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
  });
});
