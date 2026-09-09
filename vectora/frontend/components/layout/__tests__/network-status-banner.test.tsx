// @vitest-environment jsdom
/**
 * Tests para NetworkStatusBanner: oculto quando online, banner vermelho
 * offline e banner âmbar "reconectando" quando o SSE caiu.
 */

import { describe, expect, it, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, act } from "@testing-library/react";
import { NetworkStatusBanner } from "../network-status-banner";
import { useNetworkStore } from "@/lib/hooks/use-network-status";

beforeEach(() => useNetworkStore.setState({ sseStatus: "idle" }));
afterEach(cleanup);

describe("NetworkStatusBanner", () => {
  it("não renderiza nada quando online e sem queda de SSE", () => {
    const { container } = render(<NetworkStatusBanner />);
    expect(container).toBeEmptyDOMElement();
  });

  it("mostra alerta vermelho quando offline", () => {
    render(<NetworkStatusBanner />);
    act(() => {
      window.dispatchEvent(new Event("offline"));
    });
    expect(screen.getByRole("alert")).toHaveClass("safe-area-top-banner");
  });

  it("mostra status 'reconectando' quando o SSE está reconnecting", () => {
    render(<NetworkStatusBanner />);
    act(() => useNetworkStore.getState().setSSEStatus("reconnecting"));
    expect(screen.getByRole("status")).toHaveClass("safe-area-top-banner");
  });
});
