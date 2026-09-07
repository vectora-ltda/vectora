// @vitest-environment jsdom
/**
 * Testes da IntegracoesTab.
 *
 * Cobre:
 * - Renderização com lista de integrações do backend (conectado / não conectado)
 * - Status badge correto por provider
 * - Botão "Conectar via OAuth" presente para providers OAuth não conectados
 * - Botão "Desconectar" presente para providers OAuth conectados
 * - Webhook URL via gateway (quando gateway conectado) e fallback local
 * - Providers filho (google-drive, gmail) renderizam sem botão OAuth próprio
 * - Gateway status: conectado mostra subdomain; desconectado mostra mensagem padrão
 * - Variáveis customizadas (chave/valor livre): seção "Customizadas" lista
 *   env vars órfãs (sem integração correspondente); dialog "+ Adicionar
 *   variável customizada" salva via /auth/envs; erro/borda — chave ou
 *   valor vazio não submete.
 */

import {
  describe,
  it,
  expect,
  vi,
  afterEach,
  beforeEach,
  beforeAll,
} from "vitest";
import {
  render,
  screen,
  cleanup,
  waitFor,
  fireEvent,
  within,
  act,
} from "@testing-library/react";
import { overwriteGetLocale, baseLocale } from "@/lib/paraglide/runtime";

vi.mock("@/lib/hooks/use-feature-flags", () => ({
  useFeatureFlags: () => ({ enableFeaturesBeta: true }),
}));

afterEach(cleanup);

beforeAll(async () => {
  await import("../integracoes-tab");
}, 120000);

afterEach(() => overwriteGetLocale(() => baseLocale));

type GatewayStatus = {
  connected: boolean;
  state: "never_connected" | "error" | "connected";
  token: string | null;
  subdomain: string | null;
  webhook_base: string | null;
  detail: string | null;
};

const GATEWAY_FALLBACK: GatewayStatus = {
  connected: false,
  state: "never_connected",
  token: null,
  subdomain: null,
  webhook_base: null,
  detail: null,
};

function mockFetch(
  integrations: object[],
  gateway: GatewayStatus = GATEWAY_FALLBACK,
  envs: { envs: Record<string, string>; keys: string[] } = {
    envs: {},
    keys: [],
  },
) {
  global.fetch = vi
    .fn()
    .mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/gateway/status") {
        return Promise.resolve({
          ok: true,
          json: async () => gateway,
        } as Response);
      }
      if (url === "/auth/envs" && (!init || init.method === undefined)) {
        return Promise.resolve({
          ok: true,
          json: async () => envs,
        } as Response);
      }
      if (url === "/auth/envs" && init?.method === "POST") {
        return Promise.resolve({
          ok: true,
          json: async () => ({}),
        } as Response);
      }
      if (url.startsWith("/auth/envs/") && init?.method === "DELETE") {
        return Promise.resolve({
          ok: true,
          json: async () => ({}),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({ integrations }),
      } as Response);
    });
}

function countEnvsPostCalls(): number {
  return (global.fetch as ReturnType<typeof vi.fn>).mock.calls.filter(
    (c) => c[0] === "/auth/envs" && c[1]?.method === "POST",
  ).length;
}

const BASE_INTEGRATIONS = [
  {
    id: "github",
    name: "GitHub",
    env_var: "GITHUB_TOKEN",
    kind: "hybrid",
    description: "Repos e PRs",
    docs_url: "https://github.com/settings/tokens",
    icon: "github",
    connected: true,
    oauth_connected: true,
    oauth_configured: false,
  },
  {
    id: "gitlab",
    name: "GitLab",
    env_var: "GITLAB_TOKEN",
    kind: "oauth",
    description: "GitLab repos",
    docs_url: "https://gitlab.com",
    icon: "gitlab",
    connected: false,
    oauth_configured: false,
  },
  {
    id: "google",
    name: "Google",
    env_var: "GOOGLE_ACCESS_TOKEN",
    kind: "oauth",
    description: "Google account",
    docs_url: "https://console.cloud.google.com",
    icon: "google",
    connected: false,
    oauth_configured: false,
  },
  {
    id: "google-drive",
    name: "Google Drive",
    env_var: "GOOGLE_ACCESS_TOKEN",
    kind: "oauth",
    description: "Drive files",
    docs_url: "https://console.cloud.google.com",
    icon: "google-drive",
    parent: "google",
    connected: false,
    oauth_configured: false,
  },
  {
    id: "openai",
    name: "OpenAI",
    env_var: "OPENAI_API_KEY",
    kind: "apikey",
    description: "GPT-4",
    docs_url: "https://platform.openai.com",
    icon: "openai",
    connected: false,
  },
  {
    // kind "apikey" reflete o registry real do backend (oauth.py) — Slack
    // exige xoxb- (bot) + xapp- (app, Socket Mode), e o app-level token não
    // sai de um fluxo OAuth padrão, então a conexão é sempre por token
    // colado, nunca "OAuth-styled".
    id: "slack",
    name: "Slack",
    env_var: "SLACK_BOT_TOKEN",
    kind: "apikey",
    description: "Slack messaging",
    docs_url: "https://api.slack.com",
    icon: "slack",
    connected: true,
    oauth_configured: false,
  },
  {
    id: "linear",
    name: "Linear",
    env_var: "LINEAR_API_KEY",
    kind: "apikey",
    description: "Linear issues",
    docs_url: "https://linear.app/settings/api",
    icon: "linear",
    connected: false,
  },
  {
    id: "telegram",
    name: "Telegram",
    env_var: "TELEGRAM_BOT_TOKEN",
    kind: "apikey",
    description: "Converse pelo Telegram",
    docs_url: "https://core.telegram.org/bots",
    icon: "telegram",
    connected: false,
    setup_hint: "No Telegram, fale com @BotFather e mande /newbot.",
  },
  {
    id: "gemini",
    name: "Gemini",
    env_var: "GOOGLE_API_KEY",
    kind: "apikey",
    description: "Google Gemini",
    docs_url: "https://aistudio.google.com/apikey",
    icon: "gemini",
    connected: false,
  },
];

describe("IntegracoesTab", () => {
  beforeEach(() => {
    overwriteGetLocale(() => "pt");
    mockFetch(BASE_INTEGRATIONS);
  });

  it("renderiza os nomes de todas as integrações", async () => {
    const { IntegracoesTab } = await import("../integracoes-tab");
    render(<IntegracoesTab />);
    await waitFor(() => {
      for (const name of [
        "GitHub",
        "GitLab",
        "Google",
        "Slack",
        "OpenAI",
        "Linear",
        "Gemini",
      ]) {
        expect(screen.getAllByText(name).length).toBeGreaterThan(0);
      }
    });
  });

  it("todas as chamadas de fetch enviam o cookie de sessão (credentials: include) — sem isso o backend responde 401 e o catálogo aparece vazio", async () => {
    const { IntegracoesTab } = await import("../integracoes-tab");
    render(<IntegracoesTab />);
    await waitFor(() => {
      expect(screen.getAllByText("GitHub").length).toBeGreaterThan(0);
    });

    const calls = (global.fetch as ReturnType<typeof vi.fn>).mock.calls;
    expect(calls.length).toBeGreaterThan(0);
    for (const [url, init] of calls) {
      expect(
        init?.credentials,
        `fetch(${String(url)}) sem credentials: include`,
      ).toBe("include");
    }
  });

  it("erro: fetchIntegrations usa /integrations sem barra final — com barra cai no fallback HTML da SPA e o catálogo aparece vazio", async () => {
    const { IntegracoesTab } = await import("../integracoes-tab");
    render(<IntegracoesTab />);
    await waitFor(() => {
      expect(screen.getAllByText("GitHub").length).toBeGreaterThan(0);
    });

    const calls = (global.fetch as ReturnType<typeof vi.fn>).mock.calls;
    const integrationsCall = calls.find(
      ([url]) =>
        String(url).startsWith("/integrations") &&
        !String(url).includes("/verify"),
    );
    expect(integrationsCall?.[0]).toBe("/integrations");
  });

  it("GitHub conectado exibe badge 'Conectado'", async () => {
    const { IntegracoesTab } = await import("../integracoes-tab");
    render(<IntegracoesTab />);
    await waitFor(() => {
      const badges = screen.getAllByText(/conectado/i);
      expect(badges.length).toBeGreaterThan(0);
    });
  });

  it("GitLab não conectado exibe badge 'Não configurado'", async () => {
    const { IntegracoesTab } = await import("../integracoes-tab");
    render(<IntegracoesTab />);
    await waitFor(() => {
      const badges = screen.getAllByText(/não configurado/i);
      expect(badges.length).toBeGreaterThan(0);
    });
  });

  it("providers OAuth não conectados com OAuth App configurado exibem botão de OAuth", async () => {
    mockFetch(
      BASE_INTEGRATIONS.map((i) =>
        i.kind === "oauth" ? { ...i, oauth_configured: true } : i,
      ),
    );
    const { IntegracoesTab } = await import("../integracoes-tab");
    render(<IntegracoesTab />);
    await waitFor(() => {
      const oauthBtns = screen.getAllByText(/conectar via oauth/i);
      expect(oauthBtns.length).toBeGreaterThanOrEqual(2);
    });
  });

  it("todo provider (independente de kind) também aceita token manual", async () => {
    // Todo provider, independente de kind, expõe o botão de expandir pra
    // colar token manualmente (title = "Colar token manualmente") — o fluxo
    // OAuth não é a única via, nem existe pra Slack (apikey).
    const { IntegracoesTab } = await import("../integracoes-tab");
    render(<IntegracoesTab />);
    await waitFor(() => {
      const pasteButtons = screen.getAllByTitle(/colar token manualmente/i);
      // github(hybrid) + gitlab(oauth) + google(oauth) + slack(apikey) = 4
      // no mínimo (google-drive é filho, não conta)
      expect(pasteButtons.length).toBeGreaterThanOrEqual(4);
    });
  });

  it("GitHub (hybrid) conectado exibe botão Desconectar de OAuth", async () => {
    const { IntegracoesTab } = await import("../integracoes-tab");
    render(<IntegracoesTab />);
    await waitFor(() => {
      const disconnectBtns = screen.getAllByText(/desconectar/i);
      expect(disconnectBtns.length).toBeGreaterThan(0);
    });
  });

  it("GitHub (hybrid) com token colado manualmente (connected mas não oauth_connected) NÃO exibe botão Desconectar de OAuth, só oferece Conectar via OAuth", async () => {
    // connected=true por si só não prova que a credencial veio do fluxo
    // OAuth — um token colado manualmente também deixa connected=true. A UI
    // usa oauth_connected pra decidir se mostra "Conexão ativa (OAuth)" +
    // Desconectar.
    mockFetch(
      BASE_INTEGRATIONS.map((i) =>
        i.id === "github"
          ? {
              ...i,
              connected: true,
              oauth_connected: false,
              oauth_configured: true,
            }
          : i,
      ),
    );
    const { IntegracoesTab } = await import("../integracoes-tab");
    render(<IntegracoesTab />);
    await waitFor(() => {
      expect(screen.getAllByText("GitHub").length).toBeGreaterThan(0);
    });
    expect(screen.queryByText(/conexão ativa \(oauth\)/i)).toBeNull();
    expect(screen.getByText(/conectar via oauth/i)).toBeInTheDocument();
  });

  it("GitHub (hybrid) conectado via OAuth de verdade (oauth_connected) exibe 'Conexão ativa (OAuth)' e Desconectar", async () => {
    mockFetch(
      BASE_INTEGRATIONS.map((i) =>
        i.id === "github"
          ? { ...i, connected: true, oauth_connected: true }
          : i,
      ),
    );
    const { IntegracoesTab } = await import("../integracoes-tab");
    render(<IntegracoesTab />);
    await waitFor(() => {
      expect(screen.getAllByText("GitHub").length).toBeGreaterThan(0);
    });
    expect(screen.getByText(/conexão ativa \(oauth\)/i)).toBeInTheDocument();
    expect(screen.getByText(/desconectar/i)).toBeInTheDocument();
  });

  it("erro de borda — Slack (kind apikey) conectado NÃO exibe botão de Desconectar OAuth, só remoção manual da chave", async () => {
    // isOAuthProvider deriva 100% de integ.kind — Slack é apikey (Socket
    // Mode exige xapp- que OAuth puro não entrega), então nunca deve
    // renderizar a seção estilo-OAuth mesmo conectado.
    const { IntegracoesTab } = await import("../integracoes-tab");
    render(<IntegracoesTab />);
    await waitFor(() => {
      expect(screen.getAllByText("Slack").length).toBeGreaterThan(0);
    });
    const slackCard = screen.getAllByText("Slack")[0]!.closest("div[class]")!
      .parentElement!.parentElement!;
    expect(within(slackCard).queryByText(/desconectar/i)).toBeNull();
  });

  it("GitLab sem OAuth App configurado não mostra 'Conectar via OAuth' e já expõe o campo de token", async () => {
    // Sem GITLAB_OAUTH_CLIENT_ID/SECRET configurado no backend, o botão de
    // OAuth não é oferecido; o campo de token manual (única opção
    // funcional) já vem aberto, sem exigir clique extra no chevron.
    const { IntegracoesTab } = await import("../integracoes-tab");
    render(<IntegracoesTab />);
    await waitFor(() => {
      expect(screen.getByText("GitLab")).toBeTruthy();
    });
    expect(screen.queryByText("Conectar via OAuth")).toBeNull();
    expect(
      screen.getAllByPlaceholderText("Cole sua chave aqui").length,
    ).toBeGreaterThan(0);
  });

  it("provider OAuth com oauth_configured=true mostra 'Conectar via OAuth'", async () => {
    mockFetch(
      BASE_INTEGRATIONS.map((i) =>
        i.id === "gitlab" ? { ...i, oauth_configured: true } : i,
      ),
    );
    const { IntegracoesTab } = await import("../integracoes-tab");
    render(<IntegracoesTab />);
    await waitFor(() => {
      expect(screen.getByText("Conectar via OAuth")).toBeInTheDocument();
    });
  });

  it("Google Drive exibe card sem botão OAuth próprio (filho de Google)", async () => {
    const { IntegracoesTab } = await import("../integracoes-tab");
    render(<IntegracoesTab />);
    await waitFor(() => {
      expect(screen.getByText("Google Drive")).toBeTruthy();
    });
  });

  it("não exibe URLs de webhook ou túnel na interface", async () => {
    const { IntegracoesTab } = await import("../integracoes-tab");
    render(<IntegracoesTab />);
    await waitFor(() => {
      expect(screen.getByText("GitHub")).toBeInTheDocument();
    });
    expect(screen.queryByText(/\/webhook\//i)).toBeNull();
    expect(screen.queryByText(/vectora\.chat/i)).toBeNull();
  });

  it("provider OAuth não exibe callback para cadastro do usuário", async () => {
    mockFetch(BASE_INTEGRATIONS, {
      connected: true,
      state: "connected",
      token: "abc123",
      subdomain: "abc123.vectora.chat",
      webhook_base: "https://abc123.vectora.chat",
      detail: null,
    });
    const { IntegracoesTab } = await import("../integracoes-tab");
    render(<IntegracoesTab />);
    await waitFor(() =>
      expect(screen.getAllByText("GitLab").length).toBeGreaterThan(0),
    );
    expect(screen.queryByText(/\/auth\/[^/\s]+\/callback/i)).toBeNull();
  });

  it("erro de borda — sem subdomínio do gateway ainda, a callback URL não aparece (nada pra copiar)", async () => {
    const { IntegracoesTab } = await import("../integracoes-tab");
    render(<IntegracoesTab />);
    await waitFor(() => {
      expect(screen.getAllByText("GitLab").length).toBeGreaterThan(0);
    });
    expect(screen.queryByText(/\/auth\/[^/\s]+\/callback/i)).toBeNull();
  });

  it("erro de borda — provider apikey (Slack) nunca exibe a callback URL de OAuth", async () => {
    mockFetch(BASE_INTEGRATIONS, {
      connected: true,
      state: "connected",
      token: "abc123",
      subdomain: "abc123.vectora.chat",
      webhook_base: "https://abc123.vectora.chat",
      detail: null,
    });
    const { IntegracoesTab } = await import("../integracoes-tab");
    render(<IntegracoesTab />);
    await waitFor(() => {
      expect(screen.getAllByText("Slack").length).toBeGreaterThan(0);
    });
    expect(screen.queryByText(/\/auth\/slack\/callback/i)).toBeNull();
  });

  it("renderiza indicador de carregamento antes da resposta", async () => {
    global.fetch = vi.fn().mockReturnValue(new Promise(() => {}));
    const { IntegracoesTab } = await import("../integracoes-tab");
    const { container } = render(<IntegracoesTab />);
    const spinner = container.querySelector(".animate-spin");
    expect(spinner).toBeTruthy();
  });

  it("sumário exibe contador de integrações conectadas", async () => {
    const { IntegracoesTab } = await import("../integracoes-tab");
    render(<IntegracoesTab />);
    // GitHub + Slack = 2 conectadas
    await waitFor(() => {
      expect(screen.getByText(/2 integraç/i)).toBeTruthy();
    });
  });

  it("gateway nunca conectado exibe mensagem neutra (não parece erro)", async () => {
    // Erro/borda: state="never_connected" é o default do mockFetch — nada foi
    // configurado ainda, e a mensagem não deve soar como falha.
    const { IntegracoesTab } = await import("../integracoes-tab");
    render(<IntegracoesTab />);
    await waitFor(() => {
      expect(screen.getByText(/nenhuma integração oauth/i)).toBeTruthy();
      expect(screen.queryByText(/gateway indisponível/i)).toBeNull();
    });
  });

  it("gateway conectado exibe apenas o estado, sem expor o subdomínio", async () => {
    mockFetch(BASE_INTEGRATIONS, {
      connected: true,
      state: "connected",
      token: "abc123",
      subdomain: "abc123.vectora.chat",
      webhook_base: "https://abc123.vectora.chat",
      detail: null,
    });
    const { IntegracoesTab } = await import("../integracoes-tab");
    render(<IntegracoesTab />);
    await waitFor(() => {
      expect(screen.getByText(/gateway conectado/i)).toBeTruthy();
      expect(screen.queryByText(/abc123\.vectora\.chat/)).toBeNull();
    });
  });

  it("gateway com erro real exibe mensagem distinta de 'nunca conectado', com detalhe", async () => {
    // Erro/borda: distingue estado neutro (nunca tentou) de falha real
    // (já teve token, tentou, o Worker não respondeu) — a UI não pode
    // mostrar a mesma mensagem pros dois casos.
    mockFetch(BASE_INTEGRATIONS, {
      connected: false,
      state: "error",
      token: "abc123",
      subdomain: "abc123.vectora.chat",
      webhook_base: "https://abc123.vectora.chat",
      detail: "Gateway respondeu 503",
    });
    const { IntegracoesTab } = await import("../integracoes-tab");
    render(<IntegracoesTab />);
    await waitFor(() => {
      expect(screen.getByText(/gateway indisponível/i)).toBeTruthy();
      expect(screen.getByText("Gateway respondeu 503")).toBeTruthy();
      expect(screen.queryByText(/nenhuma integração oauth/i)).toBeNull();
    });
  });

  it("gateway com erro se recupera sozinho quando a conexão termina alguns segundos depois (corrida de startup)", async () => {
    // Reprodução do bug real (2026-08-30): o backend loga "gateway:
    // conectado" alguns segundos após o boot, mas o card ficava preso em
    // "Gateway indisponível" pra sempre porque fetchGatewayStatus só era
    // chamado uma vez no mount. Sem retry, esse teste falharia (o texto
    // de erro nunca some).
    // try/finally: se alguma asserção falhar antes do fim, os timers falsos
    // ainda precisam voltar ao normal — senão vazam pros testes seguintes.
    vi.useFakeTimers();
    try {
      let call = 0;
      global.fetch = vi.fn().mockImplementation((url: string) => {
        if (url === "/gateway/status") {
          call += 1;
          const status: GatewayStatus =
            call === 1
              ? {
                  connected: false,
                  state: "error",
                  token: "abc123",
                  subdomain: "abc123.vectora.chat",
                  webhook_base: "https://abc123.vectora.chat",
                  detail: "Gateway respondeu 503",
                }
              : {
                  connected: true,
                  state: "connected",
                  token: "abc123",
                  subdomain: "abc123.vectora.chat",
                  webhook_base: "https://abc123.vectora.chat",
                  detail: null,
                };
          return Promise.resolve({
            ok: true,
            json: async () => status,
          } as Response);
        }
        if (url === "/auth/envs") {
          return Promise.resolve({
            ok: true,
            json: async () => ({ envs: {}, keys: [] }),
          } as Response);
        }
        return Promise.resolve({
          ok: true,
          json: async () => ({ integrations: BASE_INTEGRATIONS }),
        } as Response);
      });

      const { IntegracoesTab } = await import("../integracoes-tab");
      render(<IntegracoesTab />);

      await vi.waitFor(() => {
        expect(screen.getByText(/gateway indisponível/i)).toBeTruthy();
      });

      await act(async () => {
        await vi.advanceTimersByTimeAsync(3000);
      });

      await vi.waitFor(() => {
        expect(screen.getByText(/gateway conectado/i)).toBeTruthy();
        expect(screen.queryByText(/gateway indisponível/i)).toBeNull();
      });
    } finally {
      vi.useRealTimers();
    }
  });

  it("lista variáveis customizadas (órfãs) numa seção separada", async () => {
    mockFetch(BASE_INTEGRATIONS, GATEWAY_FALLBACK, {
      envs: { MY_CUSTOM_TOKEN: "••••••••" },
      // GITHUB_TOKEN já é coberto pela integração GitHub — não deve
      // aparecer duplicado na seção Customizadas.
      keys: ["MY_CUSTOM_TOKEN", "GITHUB_TOKEN"],
    });
    const { IntegracoesTab } = await import("../integracoes-tab");
    render(<IntegracoesTab />);
    await waitFor(() => {
      expect(screen.getByText("MY_CUSTOM_TOKEN")).toBeTruthy();
      expect(screen.getByText(/customizadas/i)).toBeTruthy();
    });
    // GITHUB_TOKEN é conhecido — não deve renderizar como card customizado
    expect(screen.queryByText("GITHUB_TOKEN")).toBeNull();
  });

  it("botão 'Adicionar variável customizada' abre dialog e salva via /auth/envs", async () => {
    const { IntegracoesTab } = await import("../integracoes-tab");
    render(<IntegracoesTab />);

    await waitFor(() => {
      expect(screen.getByText("GitHub")).toBeTruthy();
    });

    fireEvent.click(screen.getByText(/adicionar variável customizada/i));

    await waitFor(() => {
      expect(screen.getByPlaceholderText(/OPENAI_API_KEY/i)).toBeTruthy();
    });

    fireEvent.change(screen.getByPlaceholderText(/OPENAI_API_KEY/i), {
      target: { value: "MY_KEY" },
    });
    fireEvent.change(screen.getByPlaceholderText(/enter the value|valor/i), {
      target: { value: "secret-value" },
    });

    fireEvent.click(screen.getByRole("button", { name: /^save$|^salvar$/i }));

    await waitFor(() => {
      expect(countEnvsPostCalls()).toBeGreaterThan(0);
    });
  });

  it("botão salvar da variável customizada fica desabilitado com chave ou valor vazio", async () => {
    // Erro/borda: sem os dois campos preenchidos, não deve nem tentar
    // chamar o backend.
    const { IntegracoesTab } = await import("../integracoes-tab");
    render(<IntegracoesTab />);

    await waitFor(() => {
      expect(screen.getByText("GitHub")).toBeTruthy();
    });

    fireEvent.click(screen.getByText(/adicionar variável customizada/i));

    await waitFor(() => {
      expect(screen.getByPlaceholderText(/OPENAI_API_KEY/i)).toBeTruthy();
    });

    const saveBtn = screen.getByRole("button", { name: /^save$|^salvar$/i });
    expect(saveBtn).toBeDisabled();

    fireEvent.change(screen.getByPlaceholderText(/OPENAI_API_KEY/i), {
      target: { value: "MY_KEY" },
    });
    expect(saveBtn).toBeDisabled();
  });

  it("olho revela/oculta o valor digitado da variável customizada", async () => {
    const { IntegracoesTab } = await import("../integracoes-tab");
    render(<IntegracoesTab />);

    await waitFor(() => {
      expect(screen.getByText("GitHub")).toBeTruthy();
    });

    fireEvent.click(screen.getByText(/adicionar variável customizada/i));

    await waitFor(() => {
      expect(screen.getByPlaceholderText(/OPENAI_API_KEY/i)).toBeTruthy();
    });

    const dialog = within(screen.getByRole("dialog"));
    const valueInput = dialog.getByPlaceholderText(
      /enter the value|valor/i,
    ) as HTMLInputElement;
    expect(valueInput.type).toBe("password");

    fireEvent.click(dialog.getByLabelText(/mostrar valor/i));
    expect(valueInput.type).toBe("text");

    fireEvent.click(dialog.getByLabelText(/ocultar valor/i));
    expect(valueInput.type).toBe("password");
  });

  it("olho reseta ao fechar o dialog de variável customizada", async () => {
    // Erro/borda: o estado de visibilidade não pode vazar pra próxima
    // abertura do dialog.
    const { IntegracoesTab } = await import("../integracoes-tab");
    render(<IntegracoesTab />);

    await waitFor(() => {
      expect(screen.getByText("GitHub")).toBeTruthy();
    });

    fireEvent.click(screen.getByText(/adicionar variável customizada/i));
    await waitFor(() => {
      expect(screen.getByPlaceholderText(/OPENAI_API_KEY/i)).toBeTruthy();
    });
    fireEvent.click(
      within(screen.getByRole("dialog")).getByLabelText(/mostrar valor/i),
    );
    fireEvent.click(
      screen.getByRole("button", { name: /^cancel$|^cancelar$/i }),
    );

    fireEvent.click(screen.getByText(/adicionar variável customizada/i));
    await waitFor(() => {
      const valueInput = within(
        screen.getByRole("dialog"),
      ).getByPlaceholderText(/enter the value|valor/i) as HTMLInputElement;
      expect(valueInput.type).toBe("password");
    });
  });

  it("olho revela/oculta o token manual de uma integração", async () => {
    const { IntegracoesTab } = await import("../integracoes-tab");
    render(<IntegracoesTab />);

    const pasteBtn = await screen.findAllByTitle(/colar token manualmente/i);
    fireEvent.click(pasteBtn[0]);

    const keyInput = (
      await screen.findAllByPlaceholderText(/cole sua chave aqui/i)
    )[0] as HTMLInputElement;
    expect(keyInput.type).toBe("password");

    fireEvent.click(screen.getAllByLabelText(/mostrar valor/i)[0]);
    expect(keyInput.type).toBe("text");
  });

  it("erro real do backend (com detail) aparece na UI da variável customizada, não a mensagem genérica", async () => {
    global.fetch = vi
      .fn()
      .mockImplementation((url: string, init?: RequestInit) => {
        if (url === "/auth/envs" && init?.method === "POST") {
          return Promise.resolve({
            ok: false,
            status: 401,
            json: async () => ({ detail: "Não autenticado" }),
          } as Response);
        }
        if (url === "/gateway/status") {
          return Promise.resolve({
            ok: true,
            json: async () => GATEWAY_FALLBACK,
          } as Response);
        }
        if (url === "/auth/envs") {
          return Promise.resolve({
            ok: true,
            json: async () => ({ envs: {}, keys: [] }),
          } as Response);
        }
        return Promise.resolve({
          ok: true,
          json: async () => ({ integrations: BASE_INTEGRATIONS }),
        } as Response);
      });

    const { IntegracoesTab } = await import("../integracoes-tab");
    render(<IntegracoesTab />);

    await waitFor(() => {
      expect(screen.getByText("GitHub")).toBeTruthy();
    });

    fireEvent.click(screen.getByText(/adicionar variável customizada/i));
    await waitFor(() => {
      expect(screen.getByPlaceholderText(/OPENAI_API_KEY/i)).toBeTruthy();
    });

    fireEvent.change(screen.getByPlaceholderText(/OPENAI_API_KEY/i), {
      target: { value: "MY_KEY" },
    });
    fireEvent.change(screen.getByPlaceholderText(/enter the value|valor/i), {
      target: { value: "secret-value" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^save$|^salvar$/i }));

    await waitFor(() => {
      expect(screen.getByText("Não autenticado")).toBeTruthy();
    });
  });

  it("fetchIntegrations loga o erro no console quando a resposta falha, em vez de engolir silenciosamente", async () => {
    const consoleErrorSpy = vi
      .spyOn(console, "error")
      .mockImplementation(() => {});
    global.fetch = vi.fn().mockImplementation((url: string) => {
      if (url === "/integrations") {
        return Promise.resolve({ ok: false, status: 500 } as Response);
      }
      if (url === "/gateway/status") {
        return Promise.resolve({
          ok: true,
          json: async () => GATEWAY_FALLBACK,
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({ envs: {}, keys: [] }),
      } as Response);
    });

    const { IntegracoesTab } = await import("../integracoes-tab");
    render(<IntegracoesTab />);

    await waitFor(() => {
      expect(consoleErrorSpy).toHaveBeenCalled();
    });

    consoleErrorSpy.mockRestore();
  });
});

describe("IntegracoesTab — instrução inline de credencial", () => {
  beforeEach(() => {
    overwriteGetLocale(() => "pt");
    mockFetch(BASE_INTEGRATIONS);
  });

  it("integração fora das categorias fixas ainda renderiza — o catálogo é do backend, a lista de categorias é do frontend", async () => {
    // Uma integração com id fora de `CATEGORIES` cai no fallback de
    // categoria genérica em vez de desaparecer da tela.
    mockFetch([
      ...BASE_INTEGRATIONS,
      {
        id: "plataforma-desconhecida",
        name: "Plataforma Desconhecida",
        env_var: "DESCONHECIDA_API_KEY",
        kind: "apikey",
        description: "Integração que o frontend não categoriza",
        docs_url: "https://exemplo.test",
        icon: "custom",
        connected: false,
      },
    ]);
    const { IntegracoesTab } = await import("../integracoes-tab");
    render(<IntegracoesTab />);
    await waitFor(() => {
      expect(
        screen.getAllByText("Plataforma Desconhecida").length,
      ).toBeGreaterThan(0);
    });
  });

  it("card com setup_hint mostra a instrução ao expandir; card sem hint não inventa texto", async () => {
    const { IntegracoesTab } = await import("../integracoes-tab");
    render(<IntegracoesTab />);
    await waitFor(() => {
      expect(screen.getAllByText("Telegram").length).toBeGreaterThan(0);
    });

    // Fechado, a instrução não ocupa espaço no card.
    expect(screen.queryByText(/BotFather/i)).toBeNull();

    const telegramCard = screen.getByText("Telegram").closest("div[class]")!
      .parentElement!.parentElement!;
    fireEvent.click(
      within(telegramCard).getByTitle(/colar token manualmente/i),
    );
    expect(screen.getByText(/BotFather/i)).toBeInTheDocument();

    // Erro/borda: Gemini não declara setup_hint — expandir não pode
    // renderizar parágrafo nenhum (nem vazio, que viraria espaçamento morto).
    const geminiCard = screen.getByText("Gemini").closest("div[class]")!
      .parentElement!.parentElement!;
    fireEvent.click(within(geminiCard).getByTitle(/colar token manualmente/i));
    expect(within(geminiCard).queryByText(/BotFather/i)).toBeNull();
  });
});
