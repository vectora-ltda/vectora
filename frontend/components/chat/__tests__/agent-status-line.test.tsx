// @vitest-environment jsdom
/**
 * AgentStatusLine
 *
 * Componente que mostra a tool em execução durante o streaming do agente.
 */

import { describe, expect, it, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { AgentStatusLine } from "../agent-status-line";

afterEach(cleanup);

describe("AgentStatusLine", () => {
  it("renders nothing when activeTool is null", () => {
    const { container } = render(<AgentStatusLine activeTool={null} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when activeTool is undefined", () => {
    const { container } = render(<AgentStatusLine activeTool={undefined} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders tool name when activeTool is set", () => {
    render(
      <AgentStatusLine
        activeTool={{ name: "file_edit", argsPreview: "auth.py" }}
      />,
    );
    expect(screen.getByText(/file_edit/i)).toBeDefined();
  });

  it("renders args preview when provided", () => {
    render(
      <AgentStatusLine
        activeTool={{ name: "web_search", argsPreview: "python async tips" }}
      />,
    );
    expect(screen.getByText(/python async tips/i)).toBeDefined();
  });

  it("renders elapsed time when elapsedMs is provided", () => {
    render(
      <AgentStatusLine
        activeTool={{
          name: "file_edit",
          argsPreview: "main.py",
          elapsedMs: 1234,
        }}
      />,
    );
    // Should show something like "1.2s" or "1234ms"
    const text = screen.getByTestId("agent-status-line").textContent ?? "";
    expect(text).toMatch(/1[.,]2\s*s|1234\s*ms/i);
  });

  it("does not render elapsed time when elapsedMs is absent", () => {
    render(
      <AgentStatusLine
        activeTool={{ name: "file_edit", argsPreview: "auth.py" }}
      />,
    );
    const text = screen.getByTestId("agent-status-line").textContent ?? "";
    expect(text).not.toMatch(/\d+\s*ms|\d+\.\d+\s*s/);
  });

  it("is accessible — has a status role or aria-live", () => {
    render(
      <AgentStatusLine
        activeTool={{ name: "file_edit", argsPreview: "auth.py" }}
      />,
    );
    const el = screen.getByTestId("agent-status-line");
    const role = el.getAttribute("role");
    const ariaLive = el.getAttribute("aria-live");
    expect(role === "status" || ariaLive !== null).toBe(true);
  });
});
