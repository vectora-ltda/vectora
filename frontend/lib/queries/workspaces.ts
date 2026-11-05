/**
 * TanStack Query hooks para workspaces.
 *
 * Complementa o useWorkspacesStore (Zustand) para casos onde o cache reativo
 * do React Query é preferível a subscrever o store manualmente.
 * Mutations (create, trust, setActive) continuam via useWorkspacesStore.
 */

import {
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";
import type { WorkspaceInfo } from "@/lib/stores/workspaces-store";

export { type WorkspaceInfo } from "@/lib/stores/workspaces-store";

interface ListWorkspacesResponse {
  workspaces: WorkspaceInfo[];
  active_id: string | null;
}

export const workspacesQueryKey = ["workspaces"] as const;

async function fetchWorkspaces(): Promise<ListWorkspacesResponse> {
  const res = await fetch("/workspaces", { credentials: "include" });
  if (!res.ok) throw new Error(`workspaces: ${res.status}`);
  return res.json() as Promise<ListWorkspacesResponse>;
}

/** Lista todos os workspaces do usuário. */
export function useWorkspacesQuery(): UseQueryResult<
  ListWorkspacesResponse,
  Error
> {
  return useQuery({
    queryKey: workspacesQueryKey,
    queryFn: fetchWorkspaces,
    staleTime: 60_000,
    refetchOnWindowFocus: true,
  });
}

/** Workspace ativo (primeiro da lista quando nenhum está selecionado). */
export function useActiveWorkspace(): WorkspaceInfo | null {
  const { data } = useWorkspacesQuery();
  if (!data) return null;
  const { workspaces, active_id } = data;
  if (active_id)
    return workspaces.find((w) => w.id === active_id) ?? workspaces[0] ?? null;
  return workspaces[0] ?? null;
}

/** Invalida o cache de workspaces — útil após mutations do store. */
export function useInvalidateWorkspaces(): () => Promise<void> {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: workspacesQueryKey });
}
