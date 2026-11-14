// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { SandboxTab } from "../sandbox-tab";
import type { SandboxPolicy, SandboxStatus } from "@/lib/api/sandbox";

const api = vi.hoisted(() => ({
  getSandboxStatus: vi.fn(),
  getSandboxPolicy: vi.fn(),
  putSandboxPolicy: vi.fn(),
  getSandboxAudit: vi.fn(),
  getSandboxDryRun: vi.fn(),
}));
vi.mock("@/lib/api/sandbox", () => api);

const workspaceState = {
  workspaces: [{ id: "ws-1", name: "Projeto", cwd: "/p", trusted: true }],
  active_id: "ws-1" as string | null,
};
vi.mock("@/lib/stores/workspaces-store", () => ({
  useWorkspacesStore: (selector: (state: typeof workspaceState) => unknown) =>
    selector(workspaceState),
}));

const { text } = vi.hoisted(() => ({
  text:
    (value: string) =>
    (args?: Record<string, string>): string =>
      args
        ? Object.entries(args).reduce(
            (out, [key, v]) => out.replace(`{${key}}`, v),
            value,
          )
        : value,
}));

vi.mock("@/lib/paraglide/messages", () => {
  return {
    m: new Proxy(
      {},
      {
        get: (_target, name: string) => {
          const fixed: Record<string, string> = {
            sandbox_level_effective: "Isolamento efetivo",
            sandbox_level_partial: "Isolamento parcial",
            sandbox_level_soft: "Isolamento fraco",
            sandbox_level_off: "Sem isolamento",
            sandbox_state_effective: "Efetivo",
            sandbox_state_partial: "Parcial",
            sandbox_state_off: "Desligado",
            sandbox_state_unknown: "Não verificado",
            sandbox_dim_seccomp: "Filtro de syscalls",
            sandbox_dim_network: "Rede",
            sandbox_live: "Medido no jail em execução.",
            sandbox_not_live: "Estimado: o jail ainda não iniciou.",
            sandbox_disabled: "O sandbox está desligado.",
            sandbox_ignored_title: "O repositório pediu mais acesso",
            sandbox_ignored_description: "Ignorado: {keys}.",
            sandbox_untrusted: "Confie nesta pasta.",
            sandbox_save: "Salvar política",
            sandbox_saved: "Política salva.",
            sandbox_save_error: "Não foi possível salvar: {error}",
            sandbox_field_allow_hosts: "Destinos permitidos",
            sandbox_field_rw_paths: "Pastas graváveis",
            sandbox_field_network: "Rede do jail",
            sandbox_network_filtered: "Filtrada",
            sandbox_network_off: "Sem rede",
            sandbox_network_open: "Aberta",
            sandbox_field_enabled: "Sandbox ligado",
            sandbox_no_workspace: "Abra um workspace",
            sandbox_error: "Erro ao carregar",
            sandbox_loading: "Carregando",
            sandbox_audit_empty: "Nenhum evento",
            sandbox_audit_chain_ok: "Cadeia íntegra ({count} registros)",
            sandbox_audit_chain_broken: "Cadeia quebrada no registro {line}",
            sandbox_event_worker_started: "Jail iniciado ({network})",
            sandbox_event_egress_allowed: "Saída permitida: {host}",
            sandbox_event_egress_blocked: "Saída bloqueada: {host}",
            sandbox_dryrun_show: "Mostrar o comando",
            sandbox_dryrun_hide: "Ocultar o comando",
          };
          return text(fixed[name] ?? name);
        },
      },
    ),
  };
});

const status = (overrides: Partial<SandboxStatus> = {}): SandboxStatus => ({
  enabled: true,
  diagnostic: null,
  ignored_repo_keys: [],
  isolation: {
    level: "effective",
    live: true,
    dimensions: [
      { name: "seccomp", state: "effective", detail: "" },
      {
        name: "network",
        state: "effective",
        detail: "só os destinos permitidos",
      },
    ],
  },
  ...overrides,
});

const policy = (overrides: Partial<SandboxPolicy> = {}): SandboxPolicy => ({
  enabled: true,
  rw_paths: [],
  ro_paths: [],
  mask: [".env"],
  no_gpu: true,
  lockdown: false,
  allow_tcp_ports: [],
  env_pass: [],
  git_writable: false,
  require_full_isolation: false,
  network: "filtered",
  allow_hosts: ["api.exemplo.com"],
  registry_hosts: true,
  ignored_repo_keys: [],
  user_policy: true,
  ...overrides,
});

function renderTab() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <SandboxTab />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  workspaceState.workspaces = [
    { id: "ws-1", name: "Projeto", cwd: "/p", trusted: true },
  ];
  workspaceState.active_id = "ws-1";
  api.getSandboxStatus.mockResolvedValue(status());
  api.getSandboxPolicy.mockResolvedValue(policy());
  api.getSandboxAudit.mockResolvedValue({
    events: [],
    chain_ok: true,
    records: 0,
    broken_at: null,
  });
  api.putSandboxPolicy.mockResolvedValue(policy());
  api.getSandboxDryRun.mockResolvedValue(["bwrap", "--clearenv", "<comando>"]);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("SandboxTab", () => {
  it("sem workspace ativo pede para abrir um", () => {
    workspaceState.active_id = null;

    renderTab();

    expect(screen.getByText("Abra um workspace")).toBeTruthy();
    expect(api.getSandboxStatus).not.toHaveBeenCalled();
  });

  it("mostra o nível e cada dimensão do isolamento", async () => {
    renderTab();

    expect((await screen.findByTestId("sandbox-level")).textContent).toBe(
      "Isolamento efetivo",
    );
    expect(screen.getByText("Medido no jail em execução.")).toBeTruthy();
    expect(screen.getByText("Filtro de syscalls")).toBeTruthy();
    expect(screen.getByText(/só os destinos permitidos/)).toBeTruthy();
  });

  it("isolamento parcial aparece como parcial, nunca como efetivo", async () => {
    api.getSandboxStatus.mockResolvedValue(
      status({
        isolation: {
          level: "partial",
          live: true,
          dimensions: [
            { name: "seccomp", state: "partial", detail: "libseccomp ausente" },
          ],
        },
      }),
    );

    renderTab();

    expect((await screen.findByTestId("sandbox-level")).textContent).toBe(
      "Isolamento parcial",
    );
    expect(screen.getByText(/libseccomp ausente/)).toBeTruthy();
  });

  it("avisa quando o estado é estimado e o jail ainda não iniciou", async () => {
    api.getSandboxStatus.mockResolvedValue(
      status({
        isolation: { level: "effective", live: false, dimensions: [] },
      }),
    );

    renderTab();

    expect(
      await screen.findByText("Estimado: o jail ainda não iniciou."),
    ).toBeTruthy();
  });

  it("sandbox desligado explica e não mostra nível", async () => {
    api.getSandboxStatus.mockResolvedValue(
      status({ enabled: false, isolation: null }),
    );

    renderTab();

    expect(await screen.findByText("O sandbox está desligado.")).toBeTruthy();
    expect(screen.queryByTestId("sandbox-level")).toBeNull();
  });

  it("lista o que o repositório pediu e foi ignorado", async () => {
    api.getSandboxStatus.mockResolvedValue(
      status({ ignored_repo_keys: ["rw_paths", "network"] }),
    );

    renderTab();

    const alert = await screen.findByRole("alert");
    expect(
      within(alert).getByText("O repositório pediu mais acesso"),
    ).toBeTruthy();
    expect(
      within(alert).getByText("Ignorado: rw_paths, network."),
    ).toBeTruthy();
  });

  it("salva a política editada pelas linhas dos campos de lista", async () => {
    renderTab();
    const hosts = (await screen.findByLabelText(
      "Destinos permitidos",
    )) as HTMLTextAreaElement;
    expect(hosts.value).toBe("api.exemplo.com");

    fireEvent.change(hosts, {
      target: { value: "api.exemplo.com\n  *.cdn.exemplo.com  \n\n" },
    });
    fireEvent.change(screen.getByLabelText("Pastas graváveis"), {
      target: { value: "/dados" },
    });
    fireEvent.change(screen.getByLabelText("Rede do jail"), {
      target: { value: "off" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Salvar política" }));

    await waitFor(() => expect(api.putSandboxPolicy).toHaveBeenCalledTimes(1));
    const [workspaceId, body] = api.putSandboxPolicy.mock.calls[0];
    expect(workspaceId).toBe("ws-1");
    expect(body.allow_hosts).toEqual(["api.exemplo.com", "*.cdn.exemplo.com"]);
    expect(body.rw_paths).toEqual(["/dados"]);
    expect(body.network).toBe("off");
    // O que a tela não edita segue como o servidor devolveu.
    expect(body.mask).toEqual([".env"]);
    expect(await screen.findByText("Política salva.")).toBeTruthy();
  });

  it("mostra o motivo quando o servidor recusa a política", async () => {
    api.putSandboxPolicy.mockRejectedValue(
      new Error("Os caminhos do sandbox precisam ser absolutos."),
    );
    renderTab();
    await screen.findByLabelText("Destinos permitidos");

    fireEvent.click(screen.getByRole("button", { name: "Salvar política" }));

    expect(
      await screen.findByText(
        "Não foi possível salvar: Os caminhos do sandbox precisam ser absolutos.",
      ),
    ).toBeTruthy();
  });

  it("workspace não confiado bloqueia a edição e explica", async () => {
    workspaceState.workspaces = [
      { id: "ws-1", name: "Projeto", cwd: "/p", trusted: false },
    ];

    renderTab();

    expect(await screen.findByText("Confie nesta pasta.")).toBeTruthy();
    // O `fieldset` desabilitado desabilita os controles de dentro (`:disabled`).
    expect(
      screen.getByLabelText("Destinos permitidos").matches(":disabled"),
    ).toBe(true);
    expect(
      screen
        .getByRole("button", { name: "Salvar política" })
        .matches(":disabled"),
    ).toBe(true);
  });

  it("mostra a auditoria e a integridade da cadeia", async () => {
    api.getSandboxAudit.mockResolvedValue({
      events: [
        {
          seq: 3,
          ts: "2026-11-14T10:00:00.000+00:00",
          event: "egress",
          data: { host: "evil.example.org", allowed: false },
        },
        {
          seq: 2,
          ts: "2026-11-14T09:59:00.000+00:00",
          event: "egress",
          data: { host: "pypi.org", allowed: true },
        },
        {
          seq: 1,
          ts: "2026-11-14T09:58:00.000+00:00",
          event: "worker_started",
          data: { network: "filtered" },
        },
      ],
      chain_ok: true,
      records: 3,
      broken_at: null,
    });

    renderTab();

    expect(
      await screen.findByText("Saída bloqueada: evil.example.org"),
    ).toBeTruthy();
    expect(screen.getByText("Saída permitida: pypi.org")).toBeTruthy();
    expect(screen.getByText("Jail iniciado (filtered)")).toBeTruthy();
    expect(screen.getByText("Cadeia íntegra (3 registros)")).toBeTruthy();
  });

  it("acusa a cadeia quebrada", async () => {
    api.getSandboxAudit.mockResolvedValue({
      events: [],
      chain_ok: false,
      records: 4,
      broken_at: 2,
    });

    renderTab();

    expect(
      await screen.findByText("Cadeia quebrada no registro 2"),
    ).toBeTruthy();
  });

  it("sem eventos mostra o estado vazio", async () => {
    renderTab();

    expect(await screen.findByText("Nenhum evento")).toBeTruthy();
  });

  it("a prévia do comando só é buscada ao pedir e pode ser ocultada", async () => {
    renderTab();
    const toggle = await screen.findByRole("button", {
      name: "Mostrar o comando",
    });
    expect(api.getSandboxDryRun).not.toHaveBeenCalled();

    fireEvent.click(toggle);

    expect((await screen.findByTestId("sandbox-command")).textContent).toBe(
      "bwrap --clearenv <comando>",
    );
    fireEvent.click(screen.getByRole("button", { name: "Ocultar o comando" }));
    expect(screen.queryByTestId("sandbox-command")).toBeNull();
  });

  it("falha ao carregar mostra o erro", async () => {
    api.getSandboxStatus.mockRejectedValue(new Error("HTTP 500"));

    renderTab();

    expect(await screen.findByText("Erro ao carregar")).toBeTruthy();
  });
});
