// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/paraglide/messages", () => ({
  m: new Proxy({}, { get: (_target, key) => () => String(key) }),
}));

import { WorkspaceHooksPanel } from "../workspace-hooks-panel";

const RULE = {
  id: "sem-rm",
  event: "pre_tool",
  mode: "blocking",
  tools: ["run_*"],
  timeout_s: 10,
  command: ["python", "gate.py"],
};

function respond(body: unknown, ok = true) {
  return { ok, json: async () => body } as Response;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("WorkspaceHooksPanel", () => {
  it("mostra as regras pendentes e aprova com o botão", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        respond({ workspace_id: "w1", approval: "pending", rules: [RULE] }),
      )
      .mockResolvedValueOnce(respond({ status: "ok" }))
      .mockResolvedValueOnce(
        respond({ workspace_id: "w1", approval: "approved", rules: [RULE] }),
      );
    vi.stubGlobal("fetch", fetchMock);

    render(<WorkspaceHooksPanel workspaceId="w1" />);
    expect(await screen.findByText("sem-rm")).toBeTruthy();
    expect(screen.getByText("python gate.py")).toBeTruthy();
    expect(screen.getByText("workspace_hooks_pending")).toBeTruthy();

    fireEvent.click(screen.getByText("workspace_hooks_approve"));
    await waitFor(() =>
      expect(screen.getByText("workspace_hooks_approved")).toBeTruthy(),
    );
    expect(fetchMock.mock.calls[1][0]).toBe("/workspaces/approve-hooks");
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({
      workspace_id: "w1",
    });
    expect(screen.queryByText("workspace_hooks_approve")).toBeNull();
  });

  it("avisa quando o workspace não tem regras", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          respond({ workspace_id: "w1", approval: "no_rules", rules: [] }),
        ),
    );
    render(<WorkspaceHooksPanel workspaceId="w1" />);
    expect(await screen.findByText("workspace_hooks_empty")).toBeTruthy();
  });

  it("mostra o erro quando a consulta falha", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(respond({}, false)));
    render(<WorkspaceHooksPanel workspaceId="w1" />);
    expect(await screen.findByRole("alert")).toBeTruthy();
  });
});
