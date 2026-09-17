// @vitest-environment jsdom

import {
  cleanup,
  render as rtlRender,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Thread } from "@/lib/hooks/threads";
import { Sidebar } from "../sidebar";

let chatMode = false;

function render(ui: React.ReactElement) {
  return rtlRender(
    <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>,
  );
}

vi.mock("@/lib/stores/workspaces-store", () => ({
  useWorkspacesStore: (selector: (state: unknown) => unknown) =>
    selector({ workspaces: [], active_id: "workspace-1" }),
}));
vi.mock("@/lib/stores/settings-store", () => ({
  useSettingsStore: (selector: (state: unknown) => unknown) =>
    selector({ chatMode }),
}));
vi.mock("@/lib/stores/rag-jobs-store", () => ({
  useRagJobsStore: (selector: (state: unknown) => unknown) =>
    selector({ jobs: {}, applyEvent: () => {} }),
}));
vi.mock("@/lib/hooks/use-webhook-events", () => ({
  useWebhookEvents: () => {},
}));
vi.mock("@/lib/hooks/use-network-status", () => ({
  useNetworkStatus: () => ({ offline: false }),
}));
vi.mock("@/lib/queries/threads", () => ({
  useUpdateThread: () => ({ mutate: vi.fn() }),
}));
vi.mock("../sidebar-utils", () => ({
  groupThreads: () => [],
  groupThreadsByWorkspace: () => ({ groups: [], orphans: [] }),
}));
vi.mock("../sidebar-header", () => ({ SidebarHeader: () => null }));
vi.mock("../sidebar-mode-toggle", () => ({ SidebarModeToggle: () => null }));
vi.mock("../sidebar-footer", () => ({ SidebarFooter: () => null }));
vi.mock("../new-chat-button", () => ({ NewChatButton: () => null }));
vi.mock("../session-search", () => ({ SessionSearch: () => null }));
vi.mock("@/components/ui/confirm-dialog", () => ({
  ConfirmDialog: () => null,
}));
vi.mock("@/lib/paraglide/messages", () => ({
  m: {
    session_delete_confirm_title: () => "Apagar sessão?",
    session_delete_confirm_desc: () => "Essa ação não pode ser desfeita.",
    session_delete_confirm_rag_warning: () => "RAG em andamento.",
    session_delete_confirm: () => "Apagar",
    session_delete_cancel: () => "Cancelar",
  },
}));
vi.mock("../thread-list", () => ({
  ThreadList: ({ filteredThreads }: { filteredThreads: Thread[] }) => (
    <div data-testid="expanded-threads">
      {filteredThreads.map((thread) => (
        <span key={thread.thread_id}>{thread.thread_id}</span>
      ))}
    </div>
  ),
}));
vi.mock("../collapsed-sidebar", () => ({
  CollapsedSidebar: ({ threads }: { threads: Thread[] }) => (
    <div data-testid="collapsed-threads">
      {threads.map((thread) => (
        <span key={thread.thread_id}>{thread.thread_id}</span>
      ))}
    </div>
  ),
}));

afterEach(() => {
  chatMode = false;
  cleanup();
  vi.clearAllMocks();
});

const noop = vi.fn();

describe("Sidebar — navegação da sessão nova", () => {
  it("mostra a sessão sintética nos estados expandido e recolhido", async () => {
    const props = {
      threads: [] as Thread[],
      currentThreadId: "draft-thread",
      isNewSession: true,
      onToggle: noop,
      onSelectThread: noop,
      onDeleteThread: noop,
    };

    const { rerender } = render(<Sidebar {...props} isCollapsed={false} />);
    expect(screen.getByTestId("expanded-threads")).toHaveTextContent(
      "draft-thread",
    );

    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <Sidebar {...props} isCollapsed />
      </QueryClientProvider>,
    );
    await waitFor(() => {
      expect(screen.getByTestId("collapsed-threads")).toHaveTextContent(
        "draft-thread",
      );
    });
  });

  it("troca o pool Chat/Code sem perder a sessão sintética", () => {
    chatMode = true;
    const props = {
      threads: [
        { thread_id: "code-thread", mode: "code", metadata: {} },
        { thread_id: "chat-thread", mode: "chat", metadata: {} },
      ] as Thread[],
      currentThreadId: "draft-chat",
      isNewSession: true,
      isCollapsed: true,
      onToggle: noop,
      onSelectThread: noop,
      onDeleteThread: noop,
    };

    render(<Sidebar {...props} />);
    const visible = screen.getByTestId("collapsed-threads");
    expect(visible).toHaveTextContent("chat-thread");
    expect(visible).toHaveTextContent("draft-chat");
    expect(visible).not.toHaveTextContent("code-thread");
  });

  it("substitui a sessão sintética pela persistida sem duplicar o id", () => {
    const props = {
      currentThreadId: "draft-thread",
      isNewSession: true,
      isCollapsed: false,
      onToggle: noop,
      onSelectThread: noop,
      onDeleteThread: noop,
    };
    const { rerender } = render(<Sidebar {...props} threads={[]} />);
    rerender(
      <Sidebar
        {...props}
        threads={[
          { thread_id: "draft-thread", mode: "code", metadata: {} } as Thread,
        ]}
      />,
    );
    expect(screen.getByTestId("expanded-threads").textContent).toBe(
      "draft-thread",
    );
  });
});
