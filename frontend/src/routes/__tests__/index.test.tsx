// @vitest-environment jsdom
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
const { headerMock, narrowViewport } = vi.hoisted(() => ({
  headerMock: vi.fn((_props: unknown) => null),
  narrowViewport: { current: false },
}));
vi.mock("@/components/header/header", () => ({ Header: headerMock }));
vi.mock("@/lib/hooks/use-media-query", () => ({
  useIsNarrowViewport: () => narrowViewport.current,
}));
vi.mock("@/components/chat/features/empty-state-header", () => ({
  EmptyStateHeader: () => <div data-testid="empty-state-header" />,
}));

vi.mock("@/lib/queries/threads", () => ({
  useThreadsQuery: () => ({ data: [], isLoading: false }),
  useThreadsPaging: () => ({
    hasMore: false,
    remaining: 0,
    isLoadingMore: false,
    loadMore: async () => {},
  }),
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
import * as browserSessionStore from "@/lib/browser-session-store";
import { Route } from "../index";

const HomeScreen = (Route as unknown as { component: () => ReactElement })
  .component;

beforeEach(() => {
  navigateSpy.mockClear();
  headerMock.mockClear();
  narrowViewport.current = false;
  sidebarProps.current = null;
  vi.spyOn(browserSessionStore, "disposeBrowserThread").mockImplementation(
    () => {},
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

  it("oferece reabertura da lista de sessões quando a sidebar desktop some", () => {
    narrowViewport.current = true;
    render(<HomeScreen />);

    const props = headerMock.mock.calls.at(-1)?.[0] as
      { compactNavigation?: { onOpenSessions: () => void } } | undefined;
    expect(props?.compactNavigation?.onOpenSessions).toBeTypeOf("function");
    props?.compactNavigation?.onOpenSessions();
    expect(sidebarProps.current).not.toBeNull();
  });
});
