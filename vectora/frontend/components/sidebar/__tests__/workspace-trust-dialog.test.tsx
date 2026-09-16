// @vitest-environment jsdom
/**
 * WorkspaceTrustDialog: botão de reload recarrega o diretório listado, e o
 * fluxo de "nova pasta" cria a subpasta via POST e relista (par feliz),
 * mostrando erro localizado em conflito de nome (par de erro).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  render,
  screen,
  cleanup,
  fireEvent,
  waitFor,
} from "@testing-library/react";

import { WorkspaceTrustDialog } from "../workspace-trust-dialog";
import { useWorkspacesStore } from "@/lib/stores/workspaces-store";

const FETCH = vi.fn();

function jsonRes(body: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

const listing = {
  path: "C:\\Users\\Machi\\Documents\\vectora",
  parent: "C:\\Users\\Machi\\Documents",
  entries: [
    {
      name: "projeto-a",
      path: "C:\\...\\projeto-a",
      is_dir: true,
      kind: "dir",
    },
  ],
  safe_root_id: "docs-vectora",
  at_drives_root: false,
};

beforeEach(() => {
  Object.defineProperty(window.navigator, "onLine", {
    value: true,
    writable: true,
    configurable: true,
  });
  FETCH.mockReset();
  FETCH.mockImplementation((url: string) => {
    if (url.startsWith("/workspaces/browse/mkdir")) {
      return jsonRes({ error: "unhandled mkdir call" }, 500);
    }
    if (url.startsWith("/workspaces/browse")) {
      return jsonRes(listing);
    }
    return jsonRes({}, 404);
  });
  vi.stubGlobal("fetch", FETCH);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("WorkspaceTrustDialog — reload e nova pasta", () => {
  it("botão de reload refaz o fetch do diretório atualmente listado", async () => {
    render(<WorkspaceTrustDialog open onOpenChange={() => {}} />);

    await waitFor(() => screen.getByText("projeto-a"));
    const callsBeforeReload = FETCH.mock.calls.length;

    fireEvent.click(screen.getByTitle("Reload"));

    await waitFor(() =>
      expect(FETCH.mock.calls.length).toBeGreaterThan(callsBeforeReload),
    );
    const lastCall = FETCH.mock.calls.at(-1)?.[0] as string;
    expect(lastCall).toContain("/workspaces/browse");
    expect(lastCall).toContain(encodeURIComponent(listing.path));
  });

  it("cria uma pasta nova e relista", async () => {
    let createdPath: string | null = null;
    const originalCreate = useWorkspacesStore.getState().create;
    const createSpy = vi.fn().mockResolvedValue({ ok: true, data: null });
    useWorkspacesStore.setState({ create: createSpy });
    FETCH.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/workspaces/browse/mkdir" && init?.method === "POST") {
        const body = JSON.parse(init.body as string) as {
          path: string;
          name: string;
        };
        if (body.name === "ja-existe") {
          return jsonRes({ detail: "conflict" }, 409);
        }
        createdPath = `${body.path}\\${body.name}`;
        return jsonRes({
          ...listing,
          created_path: createdPath,
          entries: [
            ...listing.entries,
            {
              name: body.name,
              path: `${body.path}\\${body.name}`,
              is_dir: true,
              kind: "dir",
            },
          ],
        });
      }
      if (url.startsWith("/workspaces/browse")) {
        if (createdPath && decodeURIComponent(url).includes(createdPath)) {
          return jsonRes({
            ...listing,
            path: createdPath,
            parent: listing.path,
            entries: [],
          });
        }
        return jsonRes(listing);
      }
      return jsonRes({}, 404);
    });

    render(<WorkspaceTrustDialog open onOpenChange={() => {}} />);
    await waitFor(() => screen.getByText("projeto-a"));

    fireEvent.click(screen.getByTitle("New folder"));
    const input = await screen.findByPlaceholderText("Folder name");
    fireEvent.change(input, { target: { value: "minha-pasta" } });
    fireEvent.click(screen.getByText("Create"));

    await waitFor(() =>
      expect(screen.getByDisplayValue(createdPath as string)).toBeTruthy(),
    );
    // Formulário fecha após sucesso.
    expect(screen.queryByPlaceholderText("Folder name")).toBeNull();
    fireEvent.click(screen.getByTestId("workspace-trust-confirm-btn"));
    await waitFor(() =>
      expect(createSpy).toHaveBeenCalledWith(createdPath, {
        trust: true,
        git_init: true,
      }),
    );

    useWorkspacesStore.setState({ create: originalCreate });
  });

  it("mantém o formulário aberto quando a pasta entra em conflito", async () => {
    FETCH.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/workspaces/browse/mkdir" && init?.method === "POST") {
        return jsonRes({ detail: "conflict" }, 409);
      }
      if (url.startsWith("/workspaces/browse")) return jsonRes(listing);
      return jsonRes({}, 404);
    });

    render(<WorkspaceTrustDialog open onOpenChange={() => {}} />);
    await waitFor(() => screen.getByText("projeto-a"));
    fireEvent.click(screen.getByTitle("New folder"));
    const input = await screen.findByPlaceholderText("Folder name");
    fireEvent.change(input, { target: { value: "ja-existe" } });
    fireEvent.click(screen.getByText("Create"));

    await waitFor(() =>
      expect(
        screen.getByText("A folder with that name already exists."),
      ).toBeTruthy(),
    );
    expect(screen.getByPlaceholderText("Folder name")).toBeTruthy();
  });

  it("ignora erro de criação que retorna depois de uma nova navegação", async () => {
    const otherListing = {
      ...listing,
      path: "C:\\Users\\Machi\\Documents\\outro",
      entries: [
        {
          name: "novo-diretorio",
          path: "C:\\Users\\Machi\\Documents\\outro\\novo-diretorio",
          is_dir: true,
          kind: "dir",
        },
      ],
    };
    let resolveMkdir!: (response: Response) => void;
    const pendingMkdir = new Promise<Response>((resolve) => {
      resolveMkdir = resolve;
    });
    FETCH.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/workspaces/browse/mkdir" && init?.method === "POST") {
        return pendingMkdir;
      }
      if (url.startsWith("/workspaces/browse")) {
        return url.includes(encodeURIComponent(otherListing.path))
          ? jsonRes(otherListing)
          : jsonRes(listing);
      }
      return jsonRes({}, 404);
    });

    render(<WorkspaceTrustDialog open onOpenChange={() => {}} />);
    await waitFor(() => screen.getByText("projeto-a"));
    fireEvent.click(screen.getByTitle("New folder"));
    fireEvent.change(await screen.findByPlaceholderText("Folder name"), {
      target: { value: "pasta-pendente" },
    });
    fireEvent.click(screen.getByText("Create"));

    const pathInput = screen.getByTestId("workspace-path-input");
    fireEvent.change(pathInput, { target: { value: otherListing.path } });
    fireEvent.click(screen.getByTestId("workspace-go-btn"));
    await waitFor(() => screen.getByText("novo-diretorio"));

    resolveMkdir(
      new Response(JSON.stringify({ detail: "conflict" }), {
        status: 409,
        headers: { "Content-Type": "application/json" },
      }),
    );
    await waitFor(() =>
      expect(
        screen.queryByText("A folder with that name already exists."),
      ).toBeNull(),
    );
    expect(screen.getByText("novo-diretorio")).toBeTruthy();
  });

  it("usa a entrada criada quando o servidor antigo não retorna created_path", async () => {
    const createdPath = `${listing.path}\\legado`;
    FETCH.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/workspaces/browse/mkdir" && init?.method === "POST") {
        return jsonRes({
          ...listing,
          entries: [
            ...listing.entries,
            { name: "legado", path: createdPath, is_dir: true, kind: "dir" },
          ],
        });
      }
      if (url.startsWith("/workspaces/browse")) {
        return url.includes(encodeURIComponent(createdPath))
          ? jsonRes({ ...listing, path: createdPath, entries: [] })
          : jsonRes(listing);
      }
      return jsonRes({}, 404);
    });

    render(<WorkspaceTrustDialog open onOpenChange={() => {}} />);
    await waitFor(() => screen.getByText("projeto-a"));
    fireEvent.click(screen.getByTitle("New folder"));
    fireEvent.change(await screen.findByPlaceholderText("Folder name"), {
      target: { value: "legado" },
    });
    fireEvent.click(screen.getByText("Create"));

    await waitFor(() =>
      expect(screen.getByDisplayValue(createdPath)).toBeTruthy(),
    );
  });
});

describe("WorkspaceTrustDialog — mode=ingest, filtro de formato e bucket", () => {
  beforeEach(() => {
    useWorkspacesStore.getState().setWorkspaces(
      [
        {
          id: "ws-1",
          name: "vectora",
          cwd: listing.path,
          trusted: true,
        } as unknown as ReturnType<
          typeof useWorkspacesStore.getState
        >["workspaces"][number],
      ],
      "ws-1",
    );
  });

  it("digitar extensões customizadas e nome do bucket envia ambos no POST de ingest", async () => {
    FETCH.mockImplementation((url: string, init?: RequestInit) => {
      if (url.startsWith("/workspaces/browse")) return jsonRes(listing);
      if (url === "/workspaces/ws-1/rag/ingest") {
        return jsonRes({
          job_id: "job-1",
          total_files: 0,
          total_chunks: 0,
          status: "indexing",
          bucket_id: "b-1",
        });
      }
      return jsonRes({}, 404);
    });

    render(<WorkspaceTrustDialog open onOpenChange={() => {}} mode="ingest" />);
    await waitFor(() => screen.getByText("projeto-a"));

    fireEvent.change(
      screen.getByPlaceholderText("e.g. xml, tscn, src/components"),
      { target: { value: "xml, tscn" } },
    );
    fireEvent.change(screen.getByPlaceholderText("Bucket name"), {
      target: { value: "Godot Docs" },
    });
    fireEvent.click(screen.getByText("Index this folder"));

    await waitFor(() =>
      expect(
        FETCH.mock.calls.some(
          (call) => call[0] === "/workspaces/ws-1/rag/ingest",
        ),
      ).toBe(true),
    );
    const ingestCall = FETCH.mock.calls.find(
      (call) => call[0] === "/workspaces/ws-1/rag/ingest",
    ) as [string, RequestInit];
    const body = JSON.parse(ingestCall[1].body as string);
    expect(body.file_types).toBe("all");
    expect(body.include_exts).toBe("xml, tscn");
    expect(body.bucket_name).toBe("Godot Docs");
  });

  it("mostra o botão de configurações do RAG e abre o painel sem fechar o modal", async () => {
    FETCH.mockImplementation((url: string) => {
      if (url.startsWith("/workspaces/browse")) return jsonRes(listing);
      if (url === "/rag/settings") {
        return jsonRes({
          reranker_enabled: true,
          reranker_top_k: 5,
          rerank_provider: "auto",
          embed_provider: "auto",
          embed_model: "",
          ingest_file_types: [],
        });
      }
      if (url === "/rag/collections") {
        return jsonRes({ collections: [] });
      }
      return jsonRes({}, 404);
    });

    render(<WorkspaceTrustDialog open onOpenChange={() => {}} mode="ingest" />);
    await waitFor(() => screen.getByText("projeto-a"));

    const settingsBtn = screen.getByTestId("rag-settings-btn");
    fireEvent.click(settingsBtn);

    await waitFor(() => {
      expect(screen.getByTestId("rag-settings-panel")).toBeTruthy();
    });
    // O modal de indexação continua aberto — o painel só se sobrepõe ao
    // conteúdo interno, não substitui o dialog.
    expect(screen.getByText("Index this folder")).toBeTruthy();
  });

  it("sem extensões customizadas, envia o atalho selecionado (regressão)", async () => {
    FETCH.mockImplementation((url: string, init?: RequestInit) => {
      if (url.startsWith("/workspaces/browse")) return jsonRes(listing);
      if (url === "/workspaces/ws-1/rag/ingest") {
        return jsonRes({
          job_id: "job-1",
          total_files: 0,
          total_chunks: 0,
          status: "indexing",
          bucket_id: "b-1",
        });
      }
      return jsonRes({}, 404);
    });

    render(<WorkspaceTrustDialog open onOpenChange={() => {}} mode="ingest" />);
    await waitFor(() => screen.getByText("projeto-a"));

    fireEvent.click(screen.getByText("Index this folder"));

    await waitFor(() =>
      expect(
        FETCH.mock.calls.some(
          (call) => call[0] === "/workspaces/ws-1/rag/ingest",
        ),
      ).toBe(true),
    );
    const ingestCall = FETCH.mock.calls.find(
      (call) => call[0] === "/workspaces/ws-1/rag/ingest",
    ) as [string, RequestInit];
    const body = JSON.parse(ingestCall[1].body as string);
    expect(body.file_types).toBe("all");
    expect(body.include_exts).toBeUndefined();
    expect(body.exclude_exts).toBeUndefined();
    expect(body.bucket_name).toBeUndefined();
  });
});

describe("WorkspaceTrustDialog — preview de arquivos filtrados", () => {
  beforeEach(() => {
    useWorkspacesStore.getState().setWorkspaces(
      [
        {
          id: "ws-1",
          name: "vectora",
          cwd: listing.path,
          trusted: true,
        } as unknown as ReturnType<
          typeof useWorkspacesStore.getState
        >["workspaces"][number],
      ],
      "ws-1",
    );
  });

  it("digitar no filtro de inclusão atualiza a contagem exibida via preview real (feliz); sem match mostra mensagem vazia (bad)", async () => {
    FETCH.mockImplementation((url: string) => {
      if (url.startsWith("/workspaces/browse")) return jsonRes(listing);
      if (url.startsWith("/workspaces/ws-1/rag/ingest/preview")) {
        if (url.includes("include_exts=xml")) {
          return jsonRes({ total: 3, files: ["a.xml", "b.xml", "c.xml"] });
        }
        return jsonRes({ total: 0, files: [] });
      }
      return jsonRes({}, 404);
    });

    render(<WorkspaceTrustDialog open onOpenChange={() => {}} mode="ingest" />);
    await waitFor(() => screen.getByText("projeto-a"));

    // Sem filtro: nenhum match no mock → mensagem de vazio (bad path).
    await waitFor(() => screen.getByText("No files match these filters."), {
      timeout: 2000,
    });

    fireEvent.change(
      screen.getByPlaceholderText("e.g. xml, tscn, src/components"),
      { target: { value: "xml" } },
    );

    await waitFor(() => screen.getByText("3 file(s) found"), {
      timeout: 2000,
    });
    expect(screen.getByText("a.xml")).toBeTruthy();
  });

  it("debounça: digitação rápida em sequência não gera 1 fetch por tecla", async () => {
    FETCH.mockImplementation((url: string) => {
      if (url.startsWith("/workspaces/browse")) return jsonRes(listing);
      if (url.startsWith("/workspaces/ws-1/rag/ingest/preview")) {
        return jsonRes({ total: 1, files: ["x.md"] });
      }
      return jsonRes({}, 404);
    });

    render(<WorkspaceTrustDialog open onOpenChange={() => {}} mode="ingest" />);
    await waitFor(() => screen.getByText("projeto-a"));

    const input = screen.getByPlaceholderText("e.g. xml, tscn, src/components");
    for (const partial of ["m", "md", "md,", "md,t"]) {
      fireEvent.change(input, { target: { value: partial } });
    }

    await waitFor(() => screen.getByText("1 file(s) found"), {
      timeout: 2000,
    });

    const previewCalls = FETCH.mock.calls.filter((call) =>
      String(call[0]).startsWith("/workspaces/ws-1/rag/ingest/preview"),
    );
    expect(previewCalls.length).toBeLessThan(4);
  });
});
