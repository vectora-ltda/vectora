// @vitest-environment jsdom
/**
 * BrowserDevtoolsPanel — painel de observabilidade da sessão de browser do
 * AGENTE (Playwright conectado ao Chromium do Electron via CDP), consumindo os endpoints REST espelhados de
 * backend/tools/browser_devtools.py. Cobre: estado sem sessão, listagem de
 * console/network, limpar console, e execução de script via aba Elements.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

import { BrowserDevtoolsPanel } from "../browser-devtools-panel";

vi.mock("@/lib/paraglide/messages", () => ({
  m: new Proxy(
    {},
    {
      get:
        (_t, prop) =>
        (...args: unknown[]) =>
          args.length
            ? `${String(prop)}(${JSON.stringify(args[0])})`
            : String(prop),
    },
  ),
}));

afterEach(cleanup);

function mockFetchSequence(handlers: Record<string, () => unknown>) {
  global.fetch = vi.fn((url: string, init?: RequestInit) => {
    const path = url.split("?")[0];
    const method = init?.method ?? "GET";
    const key = `${method} ${path}`;
    const handler = handlers[key];
    if (!handler) {
      return Promise.resolve({ ok: false, json: async () => ({}) } as Response);
    }
    return Promise.resolve({
      ok: true,
      json: async () => handler(),
    } as Response);
  }) as unknown as typeof fetch;
}

describe("BrowserDevtoolsPanel", () => {
  it("mostra estado sem sessão quando o agente nunca abriu página", async () => {
    mockFetchSequence({
      "GET /workspaces/ws1/browser/devtools/tabs": () => ({ tabs: [] }),
    });

    render(<BrowserDevtoolsPanel wsId="ws1" onClose={() => {}} />);

    expect(
      await screen.findByText("workbench_browser_devtools_no_session"),
    ).toBeInTheDocument();
  });

  it("lista mensagens de console da aba ativa", async () => {
    mockFetchSequence({
      "GET /workspaces/ws1/browser/devtools/tabs": () => ({
        tabs: [{ tab_id: "t1", url: "http://localhost:3000", active: true }],
      }),
      "GET /workspaces/ws1/browser/devtools/console": () => ({
        messages: [{ type: "log", text: "hello from agent" }],
      }),
    });

    render(<BrowserDevtoolsPanel wsId="ws1" onClose={() => {}} />);

    expect(await screen.findByText(/hello from agent/)).toBeInTheDocument();
  });

  it("limpa o console ao clicar em limpar", async () => {
    mockFetchSequence({
      "GET /workspaces/ws1/browser/devtools/tabs": () => ({
        tabs: [{ tab_id: "t1", url: "http://x", active: true }],
      }),
      "GET /workspaces/ws1/browser/devtools/console": () => ({
        messages: [{ type: "log", text: "before clear" }],
      }),
      "DELETE /workspaces/ws1/browser/devtools/console": () => ({
        status: "ok",
      }),
    });

    render(<BrowserDevtoolsPanel wsId="ws1" onClose={() => {}} />);
    await screen.findByText(/before clear/);

    fireEvent.click(
      screen.getByTitle("workbench_browser_devtools_console_clear"),
    );

    await waitFor(() => {
      expect(
        screen.getByText("workbench_browser_devtools_console_empty"),
      ).toBeInTheDocument();
    });
  });

  it("mostra requisições de rede na sub-aba Network", async () => {
    mockFetchSequence({
      "GET /workspaces/ws1/browser/devtools/tabs": () => ({
        tabs: [{ tab_id: "t1", url: "http://x", active: true }],
      }),
      "GET /workspaces/ws1/browser/devtools/console": () => ({ messages: [] }),
      "GET /workspaces/ws1/browser/devtools/network": () => ({
        requests: [
          {
            request_id: "r1",
            url: "http://x/api/data",
            method: "GET",
            resource_type: "xhr",
            status: 200,
          },
        ],
      }),
    });

    render(<BrowserDevtoolsPanel wsId="ws1" onClose={() => {}} />);
    await screen.findByText("workbench_browser_devtools_console_empty");

    fireEvent.click(screen.getByTestId("devtools-subtab-network"));

    expect(await screen.findByText("http://x/api/data")).toBeInTheDocument();
  });

  it("executa script na sub-aba Elements e mostra o resultado", async () => {
    mockFetchSequence({
      "GET /workspaces/ws1/browser/devtools/tabs": () => ({
        tabs: [{ tab_id: "t1", url: "http://x", active: true }],
      }),
      "GET /workspaces/ws1/browser/devtools/console": () => ({ messages: [] }),
      "POST /workspaces/ws1/browser/devtools/evaluate": () => ({
        status: "ok",
        result: "My Page Title",
      }),
    });

    render(<BrowserDevtoolsPanel wsId="ws1" onClose={() => {}} />);
    await screen.findByText("workbench_browser_devtools_console_empty");

    fireEvent.click(screen.getByTestId("devtools-subtab-elements"));
    const input = screen.getByPlaceholderText(
      "workbench_browser_devtools_elements_placeholder",
    );
    fireEvent.change(input, { target: { value: "document.title" } });
    fireEvent.click(
      screen.getByText("workbench_browser_devtools_elements_run"),
    );

    await waitFor(() => {
      expect(screen.getByTestId("devtools-eval-result")).toHaveTextContent(
        "My Page Title",
      );
    });
  });

  it("erro de script fica visível sem quebrar o painel", async () => {
    mockFetchSequence({
      "GET /workspaces/ws1/browser/devtools/tabs": () => ({
        tabs: [{ tab_id: "t1", url: "http://x", active: true }],
      }),
      "GET /workspaces/ws1/browser/devtools/console": () => ({ messages: [] }),
      "POST /workspaces/ws1/browser/devtools/evaluate": () => ({
        status: "error",
        error: "ReferenceError: foo is not defined",
      }),
    });

    render(<BrowserDevtoolsPanel wsId="ws1" onClose={() => {}} />);
    await screen.findByText("workbench_browser_devtools_console_empty");

    fireEvent.click(screen.getByTestId("devtools-subtab-elements"));
    const input = screen.getByPlaceholderText(
      "workbench_browser_devtools_elements_placeholder",
    );
    fireEvent.change(input, { target: { value: "foo" } });
    fireEvent.click(
      screen.getByText("workbench_browser_devtools_elements_run"),
    );

    expect(
      await screen.findByText(/ReferenceError: foo is not defined/),
    ).toBeInTheDocument();
  });

  it("fecha o painel ao clicar no X", async () => {
    mockFetchSequence({
      "GET /workspaces/ws1/browser/devtools/tabs": () => ({ tabs: [] }),
    });
    const onClose = vi.fn();

    render(<BrowserDevtoolsPanel wsId="ws1" onClose={onClose} />);
    await screen.findByText("workbench_browser_devtools_no_session");

    fireEvent.click(screen.getByTitle("workbench_browser_devtools_close"));

    expect(onClose).toHaveBeenCalledOnce();
  });
});
