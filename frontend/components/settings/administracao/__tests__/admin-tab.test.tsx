// @vitest-environment jsdom
/**
 * Testes dos painéis de Administração — cada um exportado direto (não mais
 * atrás de um wrapper `AdminTab` com tab bar própria; ver
 * `settings-categories.test.tsx` pro filtro de tier de "Usuários", que
 * migrou pra `buildSettingsCategoryGroups`).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  render,
  screen,
  cleanup,
  act,
  fireEvent,
  waitFor,
} from "@testing-library/react";
import { overwriteGetLocale, baseLocale } from "@/lib/paraglide/runtime";

vi.mock("@/lib/hooks/use-license-status", () => ({
  useLicenseStatus: () => ({
    status: { configured: true, tier: "pro" },
    loading: false,
    refetch: vi.fn(),
  }),
}));

const { SystemPanel, SafeRootsPanel, UsersPanel } =
  await import("../admin-tab");

/** Formato mínimo válido pra cada endpoint que os painéis buscam no mount —
 * sem isso `SystemPanel`/`ConfigSection` quebram em campos undefined
 * (ex.: `info.python_version.split(...)`) quando o act() flush deste
 * teste deixa o efeito de fetch assentar de verdade. */
function mockAdminFetch(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const body: Record<string, unknown> = url.includes("/admin/system")
        ? {
            version: "0.1.0",
            python_version: "3.13.0",
            platform: "test",
            services: {},
            recent_spans_count: 0,
          }
        : url.includes("/admin/config")
          ? {
              default_model: "",
              max_recursion: 50,
              allow_public_signup: false,
              db_dsn: "",
              vectora_token_masked: "",
              vectora_token_configured: false,
            }
          : {};
      return new Response(JSON.stringify(body), { status: 200 });
    }),
  );
}

function renderSafeRoots(): void {
  mockAdminFetch();
  render(<SafeRootsPanel />);
}

beforeEach(() => {
  overwriteGetLocale(() => "pt");
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  overwriteGetLocale(() => baseLocale);
});

describe("SystemPanel — Configuração", () => {
  it("não expõe mais modelo padrão nem limite de recursão", async () => {
    // Modelo se escolhe no seletor do chat; recursão é detalhe interno do
    // grafo. Ambos saíram da tela e do payload do PATCH — o teste trava as
    // duas pontas, porque tirar só da tela deixaria o campo sendo enviado.
    mockAdminFetch();

    render(<SystemPanel />);
    await act(async () => {});

    expect(screen.queryByText(/modelo padrão/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/recursão/i)).not.toBeInTheDocument();

    const calls = (global.fetch as unknown as ReturnType<typeof vi.fn>).mock
      .calls as [string, RequestInit | undefined][];
    const patchCall = calls.find(([, init]) => init?.method === "PATCH");
    // Erro/borda: se algum PATCH saiu no mount, ele não pode carregar os
    // campos removidos.
    if (patchCall) {
      const body = JSON.parse(String(patchCall[1]?.body ?? "{}"));
      expect(body).not.toHaveProperty("default_model");
      expect(body).not.toHaveProperty("max_recursion");
    }
  });
});

describe("SafeRootsPanel — seletor nativo de pasta", () => {
  it("com bridge do desktop: o botão aparece e a pasta escolhida preenche o campo", async () => {
    const pickDirectory = vi.fn(async () => "/home/teste/projetos");
    vi.stubGlobal("vectora", { pickDirectory });

    renderSafeRoots();
    await act(async () => {});

    const browse = screen.getByRole("button", { name: /escolher pasta/i });
    await act(async () => {
      fireEvent.click(browse);
    });

    expect(pickDirectory).toHaveBeenCalled();
    const pathInput = screen.getByPlaceholderText(
      /caminho|path|ruta/i,
    ) as HTMLInputElement;
    await waitFor(() => expect(pathInput.value).toBe("/home/teste/projetos"));
  });

  it("cancelar o diálogo preserva o que já estava digitado", async () => {
    // Erro/borda: `null` é cancelamento. Tratar como valor escolhido
    // apagaria um caminho que o usuário já tinha digitado à mão.
    const pickDirectory = vi.fn(async () => null);
    vi.stubGlobal("vectora", { pickDirectory });

    renderSafeRoots();
    await act(async () => {});

    const input = screen.getByPlaceholderText(
      /\/home\/|caminho|path/i,
    ) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "/tmp/mantido" } });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /escolher pasta/i }));
    });

    expect(input.value).toBe("/tmp/mantido");
  });

  it("sem bridge (modo web) o botão não renderiza", async () => {
    vi.stubGlobal("vectora", undefined);

    renderSafeRoots();
    await act(async () => {});

    expect(
      screen.queryByRole("button", { name: /escolher pasta/i }),
    ).not.toBeInTheDocument();
  });

  it("mostra falha ao restaurar e não recarrega a lista quando a API responde erro", async () => {
    vi.stubGlobal("vectora", undefined);
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/admin/safe-roots" && !init) {
        return new Response(
          JSON.stringify({
            roots: [
              {
                id: "root-archived",
                path: "/tmp/archived",
                label: "Arquivada",
                builtin: false,
                created_at: "2026-01-01T00:00:00Z",
                created_by: "admin",
                archived_at: "2026-01-02T00:00:00Z",
              },
            ],
          }),
          { status: 200 },
        );
      }
      if (url === "/admin/safe-roots/root-archived/restore") {
        return new Response(JSON.stringify({ detail: "disco indisponível" }), {
          status: 503,
        });
      }
      return new Response("{}", { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<SafeRootsPanel />);
    await screen.findByTestId("safe-root-root-archived");
    fireEvent.click(screen.getByRole("button", { name: /restaurar/i }));

    expect(await screen.findByText("disco indisponível")).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.filter(
        ([url, init]) => url === "/admin/safe-roots" && !init,
      ),
    ).toHaveLength(1);
  });
});

const pending = (hash: string) => ({
  token_hash: hash,
  email: `${hash}@example.com`,
  role: "member",
  created_by: "u1",
  expires_at: "2030-01-02T00:00:00+00:00",
  created_at: "2030-01-01T00:00:00+00:00",
});

const ok = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200 });

/** Responde `/admin/users` e `/admin/invites` conforme o cenário. */
function mockInvites(options: {
  list?: Response | (() => Response);
  revoke?: Response;
  create?: Response;
}) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/admin/users")
      return new Response(JSON.stringify({ users: [] }), { status: 200 });
    if (url === "/admin/invites" && init?.method === "POST")
      return options.create ?? new Response("{}", { status: 500 });
    if (url === "/admin/invites") {
      const list = options.list;
      return typeof list === "function"
        ? list()
        : (list ?? new Response("{}", { status: 500 }));
    }
    if (url.startsWith("/admin/invites/") && init?.method === "DELETE")
      return options.revoke ?? new Response("{}", { status: 500 });
    return new Response("{}", { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("UsersPanel — convites", () => {
  it("lista os convites pendentes com a função e o e-mail", async () => {
    mockInvites({ list: ok({ invites: [pending("a")], total: 1 }) });
    await act(async () => {
      render(<UsersPanel />);
    });

    expect(await screen.findByText(/a@example\.com/)).toBeTruthy();
    expect(screen.getByText("member")).toBeTruthy();
  });

  it("falha ao carregar os convites mostra o erro em vez de esconder", async () => {
    mockInvites({ list: new Response("{}", { status: 500 }) });
    await act(async () => {
      render(<UsersPanel />);
    });

    expect((await screen.findByRole("alert")).textContent).toBeTruthy();
  });

  it("revoga o convite escolhido e o tira da lista", async () => {
    const fetchMock = mockInvites({
      list: ok({ invites: [pending("a"), pending("b")], total: 2 }),
      revoke: ok({ status: "revoked", token_hash: "a" }),
    });
    await act(async () => {
      render(<UsersPanel />);
    });
    await screen.findByText(/a@example\.com/);

    await act(async () => {
      fireEvent.click(screen.getAllByTitle("Revogar")[0]!);
    });

    await waitFor(() =>
      expect(screen.queryByText(/a@example\.com/)).toBeNull(),
    );
    expect(screen.getByText(/b@example\.com/)).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledWith("/admin/invites/a", {
      method: "DELETE",
    });
  });

  it("falha ao revogar mantém o convite e mostra o erro", async () => {
    mockInvites({
      list: ok({ invites: [pending("a")], total: 1 }),
      revoke: new Response("{}", { status: 500 }),
    });
    await act(async () => {
      render(<UsersPanel />);
    });
    await screen.findByText(/a@example\.com/);

    await act(async () => {
      fireEvent.click(screen.getByTitle("Revogar"));
    });

    expect((await screen.findByRole("alert")).textContent).toBeTruthy();
    expect(screen.getByText(/a@example\.com/)).toBeTruthy();
  });
});
