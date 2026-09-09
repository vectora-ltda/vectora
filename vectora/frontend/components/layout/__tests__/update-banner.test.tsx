// @vitest-environment jsdom

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { UpdateBanner } from "../update-banner";

describe("UpdateBanner", () => {
  it("mantém os metadados da atualização e permite tentar novamente após erro", () => {
    const downloadUpdate = vi.fn();
    const onUpdateStatus = vi.fn((callback) => {
      callback({
        state: "available",
        message: "1.2.3",
        changelog: "Correções",
      });
      callback({ state: "error", message: "falha de rede" });
      return () => undefined;
    });
    Object.defineProperty(window, "vectora", {
      configurable: true,
      value: { onUpdateStatus, downloadUpdate },
    });

    render(<UpdateBanner />);

    expect(screen.getByText(/Update failed/)).toBeTruthy();
    expect(screen.getByText("Correções")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Download now/i }));
    expect(downloadUpdate).toHaveBeenCalledOnce();
  });

  it("remove o banner ao concluir uma checagem sem atualização", () => {
    const onUpdateStatus = vi.fn((callback) => {
      callback({ state: "available", message: "1.2.3" });
      callback({ state: "checking" });
      callback({ state: "not-available" });
      return () => undefined;
    });
    Object.defineProperty(window, "vectora", {
      configurable: true,
      value: { onUpdateStatus },
    });

    render(<UpdateBanner />);

    expect(
      screen.queryByText(/Update available|Update failed|Downloading/),
    ).toBeNull();
  });
});
