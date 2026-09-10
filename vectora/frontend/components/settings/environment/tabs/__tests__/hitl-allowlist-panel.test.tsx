// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

import { m } from "@/lib/paraglide/messages";
import { HitlAllowlistPanel } from "../hitl-allowlist-panel";
import { HITLPanel } from "@/components/chat/features/hitl-panel";

vi.mock("@/lib/paraglide/messages", () => ({
  m: {
    hitl_allowlist_title: () => "Regras",
    hitl_allowlist_subtitle: () => "Subtítulo",
    hitl_allowlist_workspace: () => "Workspace",
    hitl_allowlist_loading: () => "Carregando",
    hitl_allowlist_empty: () => "Lista vazia",
    hitl_allowlist_error: () => "Erro ao carregar",
    hitl_allowlist_revoke: () => "Revogar",
    hitl_allowlist_revoke_title: () => "Revogar regra",
    hitl_allowlist_revoke_desc: () => "Confirme",
    hitl_allowlist_confirm: () => "Confirmar",
    hitl_allowlist_cancel: () => "Cancelar",
    hitl_allowlist_revoked: () => "Revogada",
    hitl_allowlist_revoke_error: () => "Erro ao revogar",
    hitl_title: () => "Aprovação",
    hitl_show_args: () => "Mostrar argumentos",
    hitl_hide_args: () => "Ocultar argumentos",
    hitl_approve: () => "Aprovar",
    hitl_edit: () => "Editar",
    hitl_reject: () => "Rejeitar",
    hitl_always_allow: () => "Sempre permitir",
    hitl_always_allow_added: () => "Adicionado",
    hitl_always_allow_error: () => "Não foi possível salvar",
  },
}));

const workspaceState = {
  workspaces: [{ id: "ws-1", name: "Projeto", cwd: "", trusted: true }],
  active_id: "ws-1",
};

vi.mock("@/lib/stores/workspaces-store", () => ({
  useWorkspacesStore: (selector: (state: typeof workspaceState) => unknown) =>
    selector(workspaceState),
}));

afterEach(cleanup);

describe("HitlAllowlistPanel", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("mostra estado vazio após carregar o workspace", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ allowlist: [] }),
    } as Response);

    render(<HitlAllowlistPanel />);

    await waitFor(() =>
      expect(screen.getByText(m.hitl_allowlist_empty())).toBeTruthy(),
    );
    expect(global.fetch).toHaveBeenCalledWith(
      "/smart-approval/allowlist?workspace_id=ws-1",
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("preserva a regra quando a revogação falha", async () => {
    global.fetch = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ allowlist: [{ id: "rule-1", label: "Regra 1" }] }),
      } as Response)
      .mockResolvedValueOnce({ ok: false, json: async () => ({}) } as Response);

    render(<HitlAllowlistPanel />);
    await waitFor(() => expect(screen.getByText("Regra 1")).toBeTruthy());
    fireEvent.click(
      screen.getByRole("button", { name: m.hitl_allowlist_revoke() }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: m.hitl_allowlist_confirm() }),
    );

    await waitFor(() =>
      expect(screen.getByText(m.hitl_allowlist_revoke_error())).toBeTruthy(),
    );
    expect(document.body.textContent).toContain("Regra 1");
  });

  it("mantém Sempre permitir disponível quando o POST falha", async () => {
    global.fetch = vi
      .fn()
      .mockResolvedValue({ ok: false, status: 500 } as Response);
    render(
      <HITLPanel
        messageId="message-1"
        threadId="thread-1"
        pending={{
          toolName: "terminal",
          argsJson: '{"command":"pwd"}',
          interruptId: "interrupt-1",
          workspaceId: "ws-1",
        }}
        onDecision={vi.fn()}
      />,
    );

    const allowButton = screen.getByRole("button", { name: "Sempre permitir" });
    fireEvent.click(allowButton);
    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(allowButton).not.toBeDisabled();
  });
});
