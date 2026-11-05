// @vitest-environment jsdom
/**
 * NewChatDialog — seleção de workspace para nova conversa.
 *
 * Testa: render padrão, botão "Adicionar pasta", seleção de workspace
 * existente, cancelar, edge case lista vazia.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  act,
  cleanup,
} from "@testing-library/react";

const { mockHydrate, mockGetState } = vi.hoisted(() => ({
  mockHydrate: vi.fn(),
  mockGetState: vi.fn(() => ({ active_id: null as string | null })),
}));

vi.mock("@/lib/paraglide/messages", () => ({
  m: new Proxy(
    {},
    {
      get:
        (_t, prop) =>
        (..._args: unknown[]) =>
          String(prop),
    },
  ),
}));

vi.mock("@/lib/stores/workspaces-store", () => {
  // The mock factory is intentionally local because Vitest hoists vi.mock.
  // oxlint-disable-next-line unicorn/consistent-function-scoping
  const store = (sel: (s: unknown) => unknown) =>
    sel({
      workspaces: mockWorkspaces,
      active_id: null,
      status: "idle",
      hydrate: mockHydrate,
    });
  store.getState = mockGetState;
  return { useWorkspacesStore: store };
});

vi.mock("@/components/sidebar/workspace-trust-dialog", () => ({
  WorkspaceTrustDialog: ({
    open,
    onOpenChange,
  }: {
    open: boolean;
    onOpenChange: (v: boolean) => void;
  }) =>
    open ? (
      <div data-testid="trust-dialog">
        <button onClick={() => onOpenChange(false)}>fechar-trust</button>
      </div>
    ) : null,
}));

let mockWorkspaces: {
  id: string;
  name: string;
  cwd: string;
  is_git_repo: boolean;
}[] = [];

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  mockWorkspaces = [];
});

import { NewChatDialog } from "@/components/sidebar/new-chat-dialog";

describe("NewChatDialog", () => {
  describe("render padrão", () => {
    it("exibe opção 'criar workspace' sempre", () => {
      render(<NewChatDialog open onOpenChange={vi.fn()} onConfirm={vi.fn()} />);
      expect(screen.getByText("new_chat_create_new")).toBeTruthy();
    });

    it("exibe botão 'Adicionar pasta'", () => {
      render(<NewChatDialog open onOpenChange={vi.fn()} onConfirm={vi.fn()} />);
      expect(screen.getByText("workspace_add_folder")).toBeTruthy();
    });

    it("não renderiza conteúdo quando open=false", () => {
      render(
        <NewChatDialog
          open={false}
          onOpenChange={vi.fn()}
          onConfirm={vi.fn()}
        />,
      );
      expect(screen.queryByText("new_chat_create_new")).toBeNull();
    });
  });

  describe("lista de workspaces existentes", () => {
    beforeEach(() => {
      mockWorkspaces = [
        {
          id: "ws1",
          name: "projeto-a",
          cwd: "/home/user/projeto-a",
          is_git_repo: true,
        },
        {
          id: "ws2",
          name: "projeto-b",
          cwd: "/home/user/projeto-b",
          is_git_repo: false,
        },
      ];
    });

    it("exibe label 'WORKSPACES EXISTENTES' quando há workspaces", () => {
      render(<NewChatDialog open onOpenChange={vi.fn()} onConfirm={vi.fn()} />);
      expect(screen.getByText("new_chat_existing_label")).toBeTruthy();
    });

    it("exibe os nomes dos workspaces existentes", () => {
      render(<NewChatDialog open onOpenChange={vi.fn()} onConfirm={vi.fn()} />);
      expect(screen.getByText("projeto-a")).toBeTruthy();
      expect(screen.getByText("projeto-b")).toBeTruthy();
    });

    it("selecionar workspace existente registra o clique", async () => {
      render(<NewChatDialog open onOpenChange={vi.fn()} onConfirm={vi.fn()} />);
      const btn = screen.getByText("projeto-a").closest("button");
      expect(btn).toBeTruthy();
      await act(async () => {
        fireEvent.click(btn!);
      });
      expect(screen.getByText("projeto-a")).toBeTruthy();
    });
  });

  describe("ações", () => {
    it("cancelar chama onOpenChange(false)", async () => {
      const onOpenChange = vi.fn();
      render(
        <NewChatDialog open onOpenChange={onOpenChange} onConfirm={vi.fn()} />,
      );
      await act(async () => {
        fireEvent.click(screen.getByText("new_chat_cancel"));
      });
      expect(onOpenChange).toHaveBeenCalledWith(false);
    });

    it("confirmar chama onConfirm com null quando 'criar novo' está selecionado", async () => {
      const onConfirm = vi.fn();
      const onOpenChange = vi.fn();
      render(
        <NewChatDialog
          open
          onOpenChange={onOpenChange}
          onConfirm={onConfirm}
        />,
      );
      await act(async () => {
        fireEvent.click(screen.getByText("new_chat_confirm"));
      });
      expect(onConfirm).toHaveBeenCalledWith(null);
      expect(onOpenChange).toHaveBeenCalledWith(false);
    });

    it("confirmar chama onConfirm com workspaceId quando workspace existente selecionado", async () => {
      mockWorkspaces = [
        {
          id: "ws1",
          name: "meu-projeto",
          cwd: "/code/meu-projeto",
          is_git_repo: false,
        },
      ];
      const onConfirm = vi.fn();
      const onOpenChange = vi.fn();
      render(
        <NewChatDialog
          open
          onOpenChange={onOpenChange}
          onConfirm={onConfirm}
        />,
      );
      await act(async () => {
        fireEvent.click(screen.getByText("meu-projeto").closest("button")!);
      });
      await act(async () => {
        fireEvent.click(screen.getByText("new_chat_confirm"));
      });
      expect(onConfirm).toHaveBeenCalledWith("ws1");
    });
  });

  describe("botão Adicionar pasta", () => {
    it("clicar abre WorkspaceTrustDialog", async () => {
      render(<NewChatDialog open onOpenChange={vi.fn()} onConfirm={vi.fn()} />);
      expect(screen.queryByTestId("trust-dialog")).toBeNull();
      await act(async () => {
        fireEvent.click(screen.getByText("workspace_add_folder"));
      });
      expect(screen.getByTestId("trust-dialog")).toBeTruthy();
    });

    it("fechar WorkspaceTrustDialog sem criar workspace fecha o dialog", async () => {
      mockGetState.mockReturnValue({ active_id: null });
      render(<NewChatDialog open onOpenChange={vi.fn()} onConfirm={vi.fn()} />);
      await act(async () => {
        fireEvent.click(screen.getByText("workspace_add_folder"));
      });
      await act(async () => {
        fireEvent.click(screen.getByText("fechar-trust"));
      });
      expect(screen.queryByTestId("trust-dialog")).toBeNull();
    });

    it("erro: fechar WorkspaceTrustDialog não chama onConfirm automaticamente", async () => {
      const onConfirm = vi.fn();
      mockGetState.mockReturnValue({ active_id: null });
      render(
        <NewChatDialog open onOpenChange={vi.fn()} onConfirm={onConfirm} />,
      );
      await act(async () => {
        fireEvent.click(screen.getByText("workspace_add_folder"));
      });
      await act(async () => {
        fireEvent.click(screen.getByText("fechar-trust"));
      });
      expect(onConfirm).not.toHaveBeenCalled();
    });
  });

  describe("edge: lista vazia", () => {
    it("não exibe label 'WORKSPACES EXISTENTES' quando lista está vazia", () => {
      render(<NewChatDialog open onOpenChange={vi.fn()} onConfirm={vi.fn()} />);
      expect(screen.queryByText("new_chat_existing_label")).toBeNull();
    });

    it("exibe 'Adicionar pasta' mesmo sem workspaces", () => {
      render(<NewChatDialog open onOpenChange={vi.fn()} onConfirm={vi.fn()} />);
      expect(screen.getByText("workspace_add_folder")).toBeTruthy();
    });
  });
});
