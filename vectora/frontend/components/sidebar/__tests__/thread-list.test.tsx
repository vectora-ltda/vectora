// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { ThreadList } from "../thread-list";

vi.mock("@/lib/paraglide/messages", () => ({
  m: {
    sidebar_pull_to_refresh: () => "Puxe para atualizar",
    sidebar_refreshing: () => "Atualizando…",
    sidebar_no_results: () => "Sem resultados",
    sidebar_no_results_hint: () => "Tente outra busca",
    sidebar_no_conversations: () => "Sem conversas",
    sidebar_no_conversations_hint: () => "Crie uma conversa",
    sidebar_group_other_conversations: () => "Outras",
    sidebar_group_today: () => "Hoje",
    sidebar_group_yesterday: () => "Ontem",
    sidebar_group_last_7_days: () => "Últimos 7 dias",
    sidebar_group_older: () => "Mais antigas",
  },
}));
vi.mock("../thread-list-skeleton", () => ({
  ThreadListSkeleton: () => <div />,
}));
vi.mock("../thread-group", () => ({ ThreadGroup: () => <div /> }));
vi.mock("../workspace-group", () => ({ WorkspaceGroup: () => <div /> }));

afterEach(cleanup);

function renderList(onRefresh: () => Promise<unknown>) {
  return render(
    <ThreadList
      isLoading={false}
      searchQuery=""
      filteredThreads={[{ thread_id: "t1" } as never]}
      workspaceGroups={[]}
      orphans={[]}
      grouped={{ today: [], yesterday: [], last7Days: [], older: [] }}
      currentThreadId=""
      collapsedWorkspaces={new Set()}
      isSearching={false}
      onSelectThread={vi.fn()}
      onDeleteThread={vi.fn()}
      onRenameThread={vi.fn()}
      onTogglePinThread={vi.fn()}
      onToggleWorkspace={vi.fn()}
      onRefresh={onRefresh}
    />,
  );
}

function pull(nav: HTMLElement, distance: number): void {
  fireEvent.touchStart(nav, { touches: [{ clientY: 0 }] });
  fireEvent.touchMove(nav, { touches: [{ clientY: distance }] });
  fireEvent.touchEnd(nav);
}

describe("ThreadList pull-to-refresh", () => {
  it("só atualiza ao atingir 56px e começar no topo", async () => {
    const refresh = vi.fn().mockResolvedValue(undefined);
    const { container } = renderList(refresh);
    const nav = container.querySelector("nav")!;
    pull(nav, 55);
    expect(refresh).not.toHaveBeenCalled();
    pull(nav, 56);
    await waitFor(() => expect(refresh).toHaveBeenCalledOnce());
  });

  it("cancela gesto para cima e em touchcancel sem atualizar", () => {
    const refresh = vi.fn().mockResolvedValue(undefined);
    const { container } = renderList(refresh);
    const nav = container.querySelector("nav")!;
    fireEvent.touchStart(nav, { touches: [{ clientY: 30 }] });
    fireEvent.touchMove(nav, { touches: [{ clientY: 10 }] });
    fireEvent.touchEnd(nav);
    fireEvent.touchStart(nav, { touches: [{ clientY: 0 }] });
    fireEvent.touchMove(nav, { touches: [{ clientY: 70 }] });
    fireEvent.touchCancel(nav);
    expect(refresh).not.toHaveBeenCalled();
  });

  it("impede refresh concorrente e mantém indicador durante a promise", async () => {
    let resolveRefresh!: () => void;
    const refresh = vi.fn(
      () => new Promise<void>((resolve) => (resolveRefresh = resolve)),
    );
    const { container } = renderList(refresh);
    const nav = container.querySelector("nav")!;
    pull(nav, 60);
    pull(nav, 60);
    await waitFor(() => expect(refresh).toHaveBeenCalledOnce());
    expect(nav).toHaveAttribute("aria-busy", "true");
    expect(screen.getByRole("status")).toHaveTextContent("Atualizando");
    resolveRefresh();
    await waitFor(() => expect(nav).toHaveAttribute("aria-busy", "false"));
  });
});
