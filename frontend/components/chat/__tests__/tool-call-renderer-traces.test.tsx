// @vitest-environment jsdom
/**
 * Traces colapsáveis
 *
 * Tool calls exibem nome + elapsed + colapsável "N ações".
 */

import { describe, expect, it, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { ToolCallRenderer } from "../tool-call-renderer";
import type { ToolCall } from "@/lib/types";

afterEach(cleanup);

function makeTool(over: Partial<ToolCall> = {}): ToolCall {
  return {
    id: "tc-1",
    name: "file_edit",
    args: { path: "auth.py" },
    renderHint: "json",
    ...over,
  };
}

describe("ToolCallRenderer — traces", () => {
  it("renders tool name in header", () => {
    render(<ToolCallRenderer tool={makeTool()} isStreaming={false} />);
    expect(screen.getByText("file_edit")).toBeDefined();
  });

  it("shows elapsed time when elapsedMs is provided", () => {
    const tool = makeTool({ output: { ok: true }, elapsedMs: 1500 });
    render(<ToolCallRenderer tool={tool} isStreaming={false} />);
    const text =
      document.querySelector("[data-testid='tool-elapsed']")?.textContent ?? "";
    expect(text).toMatch(/1[.,]5\s*s|1500\s*ms/i);
  });

  it("does not show elapsed when elapsedMs is absent", () => {
    const tool = makeTool({ output: { ok: true } });
    render(<ToolCallRenderer tool={tool} isStreaming={false} />);
    expect(document.querySelector("[data-testid='tool-elapsed']")).toBeNull();
  });

  it("shows loading spinner while streaming and no output", () => {
    render(<ToolCallRenderer tool={makeTool()} isStreaming={true} />);
    // The Loader2 spinner should be present (as SVG)
    const svgs = document.querySelectorAll("svg");
    expect(svgs.length).toBeGreaterThan(0);
  });

  it("hides skeleton when output is present", () => {
    const tool = makeTool({ output: { ok: true } });
    render(<ToolCallRenderer tool={tool} isStreaming={false} />);
    // The skeleton divs with animate-pulse should not be present
    const pulse = document.querySelectorAll(".animate-pulse");
    expect(pulse.length).toBe(0);
  });
});
