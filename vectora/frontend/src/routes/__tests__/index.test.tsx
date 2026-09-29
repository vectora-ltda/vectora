// @vitest-environment jsdom
/**
 * HomeScreen (rota "/"): confirmar o dialog de nova sessão code delega a
 * decisão de sinal pra signalWorkspaceChoiceForNewSession — função única
 * compartilhada com $threadId.tsx (ver new-session-signal.ts). Antes dessa
 * extração, index.tsx tinha sua própria cópia dessa lógica e nunca chamava
 * signalCreateNewWorkspacePreNav: escolher "criar novo workspace" na tela
 * inicial tinha o mesmo efeito de não escolher nada (bug real).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  render,
  screen,
  cleanup,
  fireEvent,
  waitFor,
} from "@testing-library/react";

const { navigateSpy } = vi.hoisted(() => ({
  navigateSpy: vi.fn(),
}));

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (opts: unknown) => opts,
  useNavigate: () => navigateSpy,
}));

const { sidebarProps, deleteThreadMutateAsync } = vi.hoisted(() => ({
  sidebarProps: {
    current: null as null | { onDeleteThread: (id: string) => void },
  },
  deleteThreadMutateAsync: vi.fn().mockResolvedValue(undefined),
}));
vi.mock("@/components/sidebar/sidebar", () => ({
  Sidebar: (props: { onDeleteThread: (id: string) => void }) => {
    sidebarProps.current = props;
    return (
      <button
        type="button"
        data-testid="delete-thread"
        onClick={() => props.onDeleteThread("deleted-thread")}
      />
    );
  },
}));
vi.mock("@/components/layout/license-banner", () => ({
  LicenseBanner: () => null,
}));
const { headerMock } = vi.hoisted(() => ({ headerMock: vi.fn(() => null) }));
vi.mock("@/components/header/header", () => ({ Header: headerMock }));
vi.mock("@/components/chat/features/empty-state-header", () => ({
  EmptyStateHeader: ({
    onStartCode,
  }: {
    onStartCode: () => void;
    onStartChat: () => void;
  }) => (
    <button type="button" onClick={onStartCode}>
      Sessão de código
    </button>
  ),
}));

const { signalWorkspacePreChosenMock, signalWorkspaceChoiceForNewSessionMock } =
  vi.hoisted(() => ({
    signalWorkspacePreChosenMock: vi.fn(),
    signalWorkspaceChoiceForNewSessionMock: vi.fn(),
  }));

vi.mock("@/lib/stores/new-session-signal", () => ({
  signalWorkspacePreChosen: signalWorkspacePreChosenMock,
  signalWorkspaceChoiceForNewSession: signalWorkspaceChoiceForNewSessionMock,
}));

vi.mock("@/lib/queries/threads", () => ({
  useThreadsQuery: () => ({ data: [], isLoading: false }),
  useDeleteThread: () => ({ mutateAsync: deleteThreadMutateAsync }),
  threadsQueryKey: (limit = 100) => ["threads", limit],
}));

vi.mock("@/lib/api/vectora-client", () => ({
  listThreads: vi.fn().mockResolvedValue([]),
}));

vi.mock("../../router", () => ({
  queryClient: { ensureQueryData: vi.fn() },
}));

import type { ReactElement } from "react";
import { useWorkspacesStore } from "@/lib/stores/workspaces-store";
import * as browserSessionStore from "@/lib/browser-session-store";
import { Route } from "../index";

const HomeScreen = (Route as unknown as { component: () => ReactElement })
  .component;

beforeEach(() => {
  navigateSpy.mockClear();
  headerMock.mockClear();
  signalWorkspacePreChosenMock.mockClear();
  signalWorkspaceChoiceForNewSessionMock.mockClear();
  sidebarProps.current = null;
  vi.spyOn(browserSessionStore, "disposeBrowserThread").mockImplementation(
    () => {},
  );
  // NewChatDialog chama hydrate() no mount — mocka fetch pra não bater na
  // rede de verdade.
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ workspaces: [], active_id: null }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("HomeScreen — excluir thread", () => {
  it("descarta a sessão do navegador depois da exclusão confirmada", async () => {
    deleteThreadMutateAsync.mockClear();
    render(<HomeScreen />);
    await waitFor(() => expect(sidebarProps.current).not.toBeNull());
    fireEvent.click(screen.getByTestId("delete-thread"));
    await waitFor(() =>
      expect(deleteThreadMutateAsync).toHaveBeenCalledWith("deleted-thread"),
    );
    expect(browserSessionStore.disposeBrowserThread).toHaveBeenCalledWith(
      "deleted-thread",
    );
  });
});

describe("HomeScreen — dialog de nova sessão code", () => {
  it("confirmar 'criar novo workspace' (null, default sem active_id) delega null pra signalWorkspaceChoiceForNewSession", async () => {
    useWorkspacesStore.setState({ active_id: null, workspaces: [] });
    render(<HomeScreen />);

    fireEvent.click(screen.getByText("Sessão de código"));

    // "Criar um workspace para esta conversa" já vem selecionado (selected
    // nasce de activeId, que é null aqui) — confirma direto.
    const confirmButton = await screen.findByText("Start conversation", {
      selector: "button",
    });
    fireEvent.click(confirmButton);

    expect(signalWorkspaceChoiceForNewSessionMock).toHaveBeenCalledWith(null);

    // go() navega via setTimeout(LEAVE_DURATION_MS) real — espera de verdade.
    await waitFor(() =>
      expect(navigateSpy).toHaveBeenCalledWith({
        to: "/session/$threadId",
        params: { threadId: "new" },
      }),
    );
  });

  it("confirmar com um workspace existente selecionado delega o id (edge — não null)", async () => {
    const ws1 = {
      id: "ws1",
      name: "vectora",
      cwd: "C:\\Users\\Machi\\Desktop\\vectora",
      trusted: true,
      is_git_repo: true,
      git_remote: null,
      git_current_branch: null,
      git_default_branch: null,
    };
    useWorkspacesStore.setState({ active_id: "ws1", workspaces: [ws1] });
    // hydrate() no mount do NewChatDialog reseta a store a partir da
    // resposta de rede — devolve o mesmo estado pra não perder o "ws1"
    // selecionado antes da interação assíncrona do teste.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ workspaces: [ws1], active_id: "ws1" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    render(<HomeScreen />);

    fireEvent.click(screen.getByText("Sessão de código"));

    // selected já nasce "ws1" (= activeId) — só confirma.
    await screen.findByText("vectora");
    const confirmButton = screen.getByText("Start conversation", {
      selector: "button",
    });
    fireEvent.click(confirmButton);

    await waitFor(() =>
      expect(navigateSpy).toHaveBeenCalledWith({
        to: "/session/$threadId",
        params: { threadId: "new" },
      }),
    );
    expect(signalWorkspaceChoiceForNewSessionMock).toHaveBeenCalledWith("ws1");
  });
});
