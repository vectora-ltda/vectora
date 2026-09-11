// @vitest-environment jsdom

import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ToolPolicyPanel } from "../tool-policy-panel";

describe("ToolPolicyPanel", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("não exibe zero enquanto o uso ainda está carregando", async () => {
    let resolveUsage!: (value: Response) => void;
    const usageResponse = new Promise<Response>((resolve) => {
      resolveUsage = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) =>
        url === "/tools/policy"
          ? Promise.resolve(
              new Response(
                JSON.stringify({ available: ["web_search"], disabled: [] }),
                { status: 200 },
              ),
            )
          : usageResponse,
      ),
    );

    render(<ToolPolicyPanel />);

    expect(await screen.findByText("Loading usage…")).toBeInTheDocument();
    expect(screen.queryByText(/Used 0 times/)).not.toBeInTheDocument();

    await act(async () => {
      resolveUsage(
        new Response(JSON.stringify({ usage: { web_search: 3 } }), {
          status: 200,
        }),
      );
    });

    expect(
      await screen.findByText("Used 3 times in the last 7 days"),
    ).toBeInTheDocument();
  });
});
