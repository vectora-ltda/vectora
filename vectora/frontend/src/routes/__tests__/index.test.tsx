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
const { headerMock } = vi.hoisted(() => ({ headerMock: vi.fn(() => null) }));
vi.mock("@/components/header/header", () => ({ Header: headerMock }));
vi.mock("@/components/chat/features/empty-state-header", () => ({
  EmptyStateHeader: () => <div data-testid="empty-state-header" />,
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
import * as browserSessionStore from "@/lib/browser-session-store";
import { Route } from "../index";

const HomeScreen = (Route as unknown as { component: () => ReactElement })
  .component;

beforeEach(() => {
  navigateSpy.mockClear();
  headerMock.mockClear();
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
});
