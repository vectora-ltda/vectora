// @vitest-environment jsdom
/**
 * useSystemOpenRequests — arquivos, pastas e deep-links entregues pelo sistema.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, waitFor } from "@testing-library/react";
import type { VectoraOpenedPath } from "@/lib/types/vectora-bridge";
import { useSystemOpenRequests } from "../use-system-open-requests";

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  openSystemFiles: vi.fn(),
  signal: vi.fn(),
  workspaces: {
    active_id: null as string | null,
    create: vi.fn(),
  },
  toastError: vi.fn(),
  add: vi.fn(),
}));

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => mocks.navigate,
}));
vi.mock("@/lib/utils/open-system-files", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/utils/open-system-files")>()),
  openSystemFiles: mocks.openSystemFiles,
}));
vi.mock("@/lib/stores/new-session-signal", () => ({
  signalWorkspaceChoiceForNewSession: mocks.signal,
}));
vi.mock("@/lib/stores/workspaces-store", () => ({
  useWorkspacesStore: { getState: () => mocks.workspaces },
}));
vi.mock("@/lib/stores/toast-store", () => ({
  useToastStore: { getState: () => ({ error: mocks.toastError }) },
}));
vi.mock("@/lib/stores/opened-files-store", () => ({
  useOpenedFilesStore: { getState: () => ({ add: mocks.add }) },
}));

function Harness() {
  useSystemOpenRequests();
  return null;
}

const folder: VectoraOpenedPath = {
  kind: "directory",
  path: "/w/p",
  name: "p",
};

interface Bridge {
  takeOpenFiles: ReturnType<typeof vi.fn>;
  takeDeepLink: ReturnType<typeof vi.fn>;
  onOpenFiles: ReturnType<typeof vi.fn>;
  onDeepLink: ReturnType<typeof vi.fn>;
  acknowledgeDeepLink: ReturnType<typeof vi.fn>;
  stopFiles: ReturnType<typeof vi.fn>;
  stopLinks: ReturnType<typeof vi.fn>;
}

function installBridge(
  pending: VectoraOpenedPath[] = [],
  pendingLink: string | null = null,
): Bridge {
  const bridge: Bridge = {
    takeOpenFiles: vi.fn().mockResolvedValue(pending),
    takeDeepLink: vi.fn().mockResolvedValue(pendingLink),
    onOpenFiles: vi.fn(),
    onDeepLink: vi.fn(),
    acknowledgeDeepLink: vi.fn(),
    stopFiles: vi.fn(),
    stopLinks: vi.fn(),
  };
  bridge.onOpenFiles.mockReturnValue(bridge.stopFiles);
  bridge.onDeepLink.mockReturnValue(bridge.stopLinks);
  Object.defineProperty(window, "vectora", {
    configurable: true,
    value: bridge,
  });
  return bridge;
}

beforeEach(() => {
  Object.values(mocks).forEach((value) => {
    if (typeof value === "function") value.mockReset();
  });
  mocks.workspaces.active_id = null;
  mocks.workspaces.create.mockReset();
});
afterEach(() => {
  cleanup();
  Reflect.deleteProperty(window, "vectora");
});

describe("useSystemOpenRequests", () => {
  it("sem a bridge do desktop (modo web), não faz nada", () => {
    expect(() => render(<Harness />)).not.toThrow();
    expect(mocks.openSystemFiles).not.toHaveBeenCalled();
  });

  it("abre o que chegou antes de a janela montar", async () => {
    installBridge([folder]);

    render(<Harness />);

    await waitFor(() => expect(mocks.openSystemFiles).toHaveBeenCalledTimes(1));
    expect(mocks.openSystemFiles.mock.calls[0]![0]).toEqual([folder]);
  });

  it("não chama a abertura quando não havia nada pendente", async () => {
    const bridge = installBridge([]);

    render(<Harness />);
    await waitFor(() => expect(bridge.takeOpenFiles).toHaveBeenCalled());

    expect(mocks.openSystemFiles).not.toHaveBeenCalled();
  });

  it("abre o que chega depois, por push", async () => {
    const bridge = installBridge();
    render(<Harness />);
    await waitFor(() => expect(bridge.onOpenFiles).toHaveBeenCalled());

    const push = bridge.onOpenFiles.mock.calls[0]![0] as (
      files: VectoraOpenedPath[],
    ) => void;
    push([folder]);

    expect(mocks.openSystemFiles).toHaveBeenCalledTimes(1);
  });

  it("a conversa nova usa o workspace informado, ou o ativo, e navega para /session/new", async () => {
    installBridge([folder]);
    mocks.workspaces.active_id = "ativo";
    render(<Harness />);
    await waitFor(() => expect(mocks.openSystemFiles).toHaveBeenCalled());
    const deps = mocks.openSystemFiles.mock.calls[0]![1] as {
      openNewSession: (id: string | null) => void;
    };

    deps.openNewSession("ws-9");
    expect(mocks.signal).toHaveBeenLastCalledWith("ws-9");
    deps.openNewSession(null);
    expect(mocks.signal).toHaveBeenLastCalledWith("ativo");
    expect(mocks.navigate).toHaveBeenLastCalledWith({
      to: "/session/$threadId",
      params: { threadId: "new" },
    });
  });

  it("sem workspace nenhum, a conversa nova abre sem pré-escolha", async () => {
    installBridge([folder]);
    render(<Harness />);
    await waitFor(() => expect(mocks.openSystemFiles).toHaveBeenCalled());
    const deps = mocks.openSystemFiles.mock.calls[0]![1] as {
      openNewSession: (id: string | null) => void;
    };

    deps.openNewSession(null);

    expect(mocks.signal).not.toHaveBeenCalled();
    expect(mocks.navigate).toHaveBeenCalled();
  });

  it("criar o workspace devolve o id, ou null quando falha", async () => {
    installBridge([folder]);
    render(<Harness />);
    await waitFor(() => expect(mocks.openSystemFiles).toHaveBeenCalled());
    const deps = mocks.openSystemFiles.mock.calls[0]![1] as {
      createWorkspace: (path: string) => Promise<string | null>;
    };

    mocks.workspaces.create.mockResolvedValueOnce({
      ok: true,
      data: { id: "ws-1" },
    });
    expect(await deps.createWorkspace("/w/p")).toBe("ws-1");
    mocks.workspaces.create.mockResolvedValueOnce({ ok: false, error: "x" });
    expect(await deps.createWorkspace("/w/q")).toBeNull();
    expect(mocks.workspaces.create).toHaveBeenCalledWith("/w/p");
  });

  it("deep-link vectora://session/<id> abre a conversa e confirma o recebimento", async () => {
    const bridge = installBridge([], "vectora://session/abc123");

    render(<Harness />);

    await waitFor(() =>
      expect(mocks.navigate).toHaveBeenCalledWith({
        to: "/session/$threadId",
        params: { threadId: "abc123" },
      }),
    );
    expect(bridge.acknowledgeDeepLink).toHaveBeenCalledWith(
      "vectora://session/abc123",
    );
  });

  it("deep-link de destino desconhecido é confirmado mas não navega", async () => {
    const bridge = installBridge();
    render(<Harness />);
    await waitFor(() => expect(bridge.onDeepLink).toHaveBeenCalled());

    (bridge.onDeepLink.mock.calls[0]![0] as (url: string) => void)(
      "vectora://settings/x",
    );

    expect(bridge.acknowledgeDeepLink).toHaveBeenCalledWith(
      "vectora://settings/x",
    );
    expect(mocks.navigate).not.toHaveBeenCalled();
  });

  it("ao desmontar, cancela as duas assinaturas", async () => {
    const bridge = installBridge();
    const { unmount } = render(<Harness />);
    await waitFor(() => expect(bridge.onOpenFiles).toHaveBeenCalled());

    unmount();

    expect(bridge.stopFiles).toHaveBeenCalledTimes(1);
    expect(bridge.stopLinks).toHaveBeenCalledTimes(1);
  });

  it("o que a busca devolve depois de desmontar é descartado", async () => {
    let resolve!: (files: VectoraOpenedPath[]) => void;
    const bridge = installBridge();
    bridge.takeOpenFiles.mockReturnValue(
      new Promise<VectoraOpenedPath[]>((r) => {
        resolve = r;
      }),
    );
    const { unmount } = render(<Harness />);
    unmount();

    resolve([folder]);
    await Promise.resolve();

    expect(mocks.openSystemFiles).not.toHaveBeenCalled();
  });
});
