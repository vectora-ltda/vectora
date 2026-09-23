// @vitest-environment jsdom

import { describe, expect, it, afterEach, vi } from "vitest";
import { render as rtlRender, cleanup, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Sidebar } from "../sidebar";
import type { Thread } from "@/lib/hooks/threads";

function render(ui: React.ReactElement) {
  const queryClient = new QueryClient();
  return rtlRender(
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>{ui}</TooltipProvider>
    </QueryClientProvider>,
  );
}

vi.mock("@/lib/stores/workspaces-store", () => ({
  useWorkspacesStore: (sel: (s: unknown) => unknown) =>
    sel({
      workspaces: [
        {
          id: "workspace-1",
          name: "Vectora",
          cwd: "/tmp/vectora",
          trusted: true,
          is_git_repo: true,
          git_remote: null,
          git_current_branch: null,
          git_default_branch: null,
        },
      ],
      active_id: "workspace-1",
    }),
}));
vi.mock("@/lib/stores/settings-store", () => ({
  useSettingsStore: (sel: (s: unknown) => unknown) => sel({ chatMode: false }),
}));
vi.mock("@/lib/stores/rag-jobs-store", () => ({
  useRagJobsStore: (sel: (s: unknown) => unknown) =>
    sel({ jobs: {}, applyEvent: () => {} }),
}));
vi.mock("@/lib/hooks/use-webhook-events", () => ({
  useWebhookEvents: () => {},
}));
vi.mock("../sidebar-utils", () => ({
  groupThreads: () => [],
  groupThreadsByWorkspace: (items: Thread[]) => ({
    groups: items.length
      ? [
          {
            workspace: {
              id: "workspace-1",
              name: "Vectora",
              cwd: "/tmp/vectora",
            },
            threads: items,
          },
        ]
      : [],
    orphans: [],
  }),
}));
vi.mock("@/lib/hooks/use-network-status", () => ({
  useNetworkStatus: () => ({ offline: false }),
}));
vi.mock("../sidebar-header", () => ({
  SidebarHeader: ({ compact }: { compact?: boolean }) => (
    <div data-testid={compact ? "sidebar-compact-toggle" : "sidebar-header"} />
  ),
}));
vi.mock("../new-chat-button", () => ({ NewChatButton: () => null }));
vi.mock("../session-search", () => ({ SessionSearch: () => null }));
vi.mock("../sidebar-mode-toggle", () => ({
  SidebarModeToggle: ({ compact }: { compact?: boolean }) => (
    <div data-testid={compact ? "compact-mode-toggle" : "mode-toggle"} />
  ),
}));
vi.mock("../thread-list", () => ({
  ThreadList: () => <div data-testid="thread-list" />,
}));
vi.mock("../sidebar-footer", () => ({ SidebarFooter: () => null }));
vi.mock("@/components/ui/confirm-dialog", () => ({
  ConfirmDialog: () => null,
}));
vi.mock("@/lib/paraglide/messages", () => ({
  m: {
    sidebar_expand: () => "Expandir",
    sidebar_new_chat: () => "Nova conversa",
    network_disabled_offline: () => "Offline",
    sidebar_untitled_chat: () => "Sem título",
    session_delete_confirm_title: () => "Apagar sessão?",
    session_delete_confirm_desc: () => "Essa ação não pode ser desfeita.",
    session_delete_confirm_rag_warning: () => "RAG em andamento.",
    session_delete_confirm: () => "Apagar",
    session_delete_cancel: () => "Cancelar",
    sidebar_refreshing: () => "Atualizando",
    sidebar_pull_to_refresh: () => "Puxe para atualizar",
    sidebar_no_results: () => "Nenhum resultado",
    sidebar_no_results_hint: () => "Tente outra busca",
    sidebar_no_conversations: () => "Nenhuma conversa",
    sidebar_no_conversations_hint: () => "Comece uma conversa",
    sidebar_group_other_conversations: () => "Outras conversas",
    sidebar_group_today: () => "Hoje",
    sidebar_group_yesterday: () => "Ontem",
    sidebar_group_last_7_days: () => "Últimos 7 dias",
    sidebar_group_older: () => "Mais antigas",
    sidebar_workspace_collapse: () => "Recolher workspace",
    sidebar_workspace_expand: () => "Expandir workspace",
    sidebar_workspace_thread_count: ({ n }: { n: number }) => `${n} sessões`,
  },
}));

afterEach(cleanup);

const noop = vi.fn();
const threads: Thread[] = [
  {
    thread_id: "thread-1",
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    metadata: { user_id: "local" },
    workspace_id: "workspace-1",
    mode: "code",
  },
];

describe("Sidebar — wrapper de animação não quebra o preenchimento de altura", () => {
  it("recolhida: o <aside> fica direto sob um wrapper 'contents' (herda a altura do pai)", () => {
    const { container } = render(
      <Sidebar
        isCollapsed
        onToggle={noop}
        threads={threads}
        currentThreadId=""
        onSelectThread={noop}
        onDeleteThread={noop}
      />,
    );
    const aside = container.querySelector("aside")!;
    expect(aside).toBeTruthy();
    const wrapper = aside.parentElement!;
    expect(wrapper.className).toContain("contents");
  });

  it("expandida: o <aside> também fica direto sob um wrapper 'contents'", () => {
    const { container } = render(
      <Sidebar
        isCollapsed={false}
        onToggle={noop}
        threads={threads}
        currentThreadId=""
        onSelectThread={noop}
        onDeleteThread={noop}
      />,
    );
    const aside = container.querySelector("aside")!;
    expect(aside).toBeTruthy();
    const wrapper = aside.parentElement!;
    expect(wrapper.className).toContain("contents");
  });

  it("permite ocultar o título local quando o header do app vive na coluna central", () => {
    render(
      <Sidebar
        isCollapsed={false}
        showHeader={false}
        onToggle={noop}
        threads={threads}
        currentThreadId=""
        onSelectThread={noop}
        onDeleteThread={noop}
      />,
    );

    expect(screen.queryByTestId("sidebar-header")).not.toBeInTheDocument();
    expect(screen.getByTestId("sidebar-compact-toggle")).toBeInTheDocument();
    expect(screen.getByTestId("compact-mode-toggle")).toBeInTheDocument();
  });

  it("não renderiza pastas duplicadas e mantém a lista de sessões expandida", () => {
    render(
      <Sidebar
        isCollapsed={false}
        onToggle={noop}
        threads={threads}
        currentThreadId=""
        onSelectThread={noop}
        onDeleteThread={noop}
      />,
    );

    expect(screen.queryByTestId("sidebar-folders")).not.toBeInTheDocument();
    expect(screen.getByTestId("thread-list")).toBeInTheDocument();
  });

  it("mantém o toggle de modo separado do botão de recolher no modo compacto", () => {
    render(
      <Sidebar
        isCollapsed={false}
        showHeader={false}
        onToggle={noop}
        threads={threads}
        currentThreadId=""
        onSelectThread={noop}
        onDeleteThread={noop}
      />,
    );

    expect(screen.getByTestId("sidebar-compact-toggle")).toBeInTheDocument();
    expect(screen.getByTestId("compact-mode-toggle")).toBeInTheDocument();
  });
});
