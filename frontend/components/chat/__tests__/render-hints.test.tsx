// @vitest-environment jsdom
/**
 * Render Hints Novos
 *
 * Verifica que ToolCallRenderer despacha para renderers corretos via registry.
 */

import { describe, expect, it, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { ToolCallRenderer } from "../tool-call-renderer";
import type { ToolCall } from "@/lib/types";

afterEach(cleanup);

function makeTool(overrides: Partial<ToolCall> = {}): ToolCall {
  return {
    id: "tc-1",
    name: "test_tool",
    args: {},
    ...overrides,
  };
}

describe("Render Hints — registry dispatch", () => {
  it("renders image_preview hint with an img element", () => {
    const tool = makeTool({
      renderHint: "image_preview",
      output: { url: "https://example.com/img.png", alt: "screenshot" },
    });
    const { container } = render(
      <ToolCallRenderer tool={tool} isStreaming={false} />,
    );
    const img = container.querySelector("img");
    expect(img).not.toBeNull();
  });

  it("renders browser_screenshot hint with an img element", () => {
    const tool = makeTool({
      renderHint: "browser_screenshot",
      output: { url: "https://example.com/screen.png", alt: "page" },
    });
    const { container } = render(
      <ToolCallRenderer tool={tool} isStreaming={false} />,
    );
    const img = container.querySelector("img");
    expect(img).not.toBeNull();
  });

  it("renders thinking_step hint with accordion structure", () => {
    const tool = makeTool({
      renderHint: "thinking_step",
      output: {
        thought: "Analisando o código...",
        thought_number: 1,
        total_thoughts: 3,
        is_final: false,
      },
    });
    render(<ToolCallRenderer tool={tool} isStreaming={false} />);
    expect(screen.getByText(/Analisando o código/i)).toBeDefined();
  });

  it("renders json_tree hint with collapsible JSON", () => {
    const tool = makeTool({
      renderHint: "json_tree",
      output: { key: "value", nested: { a: 1 } },
    });
    const { container } = render(
      <ToolCallRenderer tool={tool} isStreaming={false} />,
    );
    // json_tree usa details/summary — deve ter elemento details
    const details = container.querySelector("details");
    expect(details).not.toBeNull();
  });

  it("renders chart_inline hint with svg element", () => {
    const tool = makeTool({
      renderHint: "chart_inline",
      output: {
        labels: ["Jan", "Feb", "Mar"],
        values: [10, 25, 15],
        title: "Vendas",
      },
    });
    const { container } = render(
      <ToolCallRenderer tool={tool} isStreaming={false} />,
    );
    const svg = container.querySelector("svg");
    expect(svg).not.toBeNull();
  });

  it("renders db_result hint as table", () => {
    const tool = makeTool({
      renderHint: "db_result",
      output: { rows: [{ id: 1, name: "Alice" }], columns: ["id", "name"] },
    });
    const { container } = render(
      <ToolCallRenderer tool={tool} isStreaming={false} />,
    );
    const table = container.querySelector("table");
    expect(table).not.toBeNull();
  });

  it("renders json hint as fallback for unknown output", () => {
    const tool = makeTool({
      renderHint: "json",
      output: { status: "ok" },
    });
    render(<ToolCallRenderer tool={tool} isStreaming={false} />);
    // Json viewer shows "Ver output" summary
    expect(screen.getByText(/Ver output/i)).toBeDefined();
  });

  it("falls back to json renderer for unknown renderHint", () => {
    const tool = makeTool({
      renderHint: "unknown_hint" as any,
      output: { data: 42 },
    });
    render(<ToolCallRenderer tool={tool} isStreaming={false} />);
    expect(screen.getByText(/Ver output/i)).toBeDefined();
  });
});
