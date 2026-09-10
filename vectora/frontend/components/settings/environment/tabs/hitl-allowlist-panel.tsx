"use client";

/** Settings view for the workspace-scoped smart approval allowlist. */

import { useEffect, useMemo, useState } from "react";
import { Loader2, ShieldCheck } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { m } from "@/lib/paraglide/messages";
import { useWorkspacesStore } from "@/lib/stores/workspaces-store";

interface AllowlistResponse {
  allowlist?: AllowlistItem[];
}

interface AllowlistItem {
  id: string;
  label: string;
}

/**
 * Displays opaque, server-masked rules and keeps destructive revocation behind
 * an explicit confirmation. No signature is logged or expanded in the UI.
 */
export function HitlAllowlistPanel() {
  const workspaces = useWorkspacesStore((state) => state.workspaces);
  const activeId = useWorkspacesStore((state) => state.active_id);
  const workspaceId = activeId ?? workspaces[0]?.id ?? "";
  const [selectedWorkspace, setSelectedWorkspace] = useState(workspaceId);
  const [rules, setRules] = useState<AllowlistItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingRevoke, setPendingRevoke] = useState<{
    workspaceId: string;
    ruleId: string;
  } | null>(null);
  const [revoking, setRevoking] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (workspaceId && !selectedWorkspace) setSelectedWorkspace(workspaceId);
  }, [workspaceId, selectedWorkspace]);

  useEffect(() => {
    if (!selectedWorkspace) {
      setRules([]);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setNotice(null);
    void fetch(
      `/smart-approval/allowlist?workspace_id=${encodeURIComponent(selectedWorkspace)}`,
      { credentials: "include", signal: controller.signal },
    )
      .then(async (response) => {
        if (!response.ok) throw new Error("load");
        const data = (await response.json()) as AllowlistResponse;
        setRules(Array.isArray(data.allowlist) ? data.allowlist : []);
      })
      .catch((reason: unknown) => {
        if ((reason as { name?: string })?.name !== "AbortError") {
          setError(m.hitl_allowlist_error());
          setRules([]);
        }
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [selectedWorkspace]);

  const workspaceName = useMemo(
    () =>
      workspaces.find((workspace) => workspace.id === selectedWorkspace)?.name,
    [selectedWorkspace, workspaces],
  );

  async function revokeRule(): Promise<void> {
    if (!pendingRevoke) return;
    setRevoking(true);
    setError(null);
    try {
      const response = await fetch("/smart-approval/allowlist", {
        method: "DELETE",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          workspace_id: pendingRevoke.workspaceId,
          rule_id: pendingRevoke.ruleId,
        }),
      });
      if (!response.ok) throw new Error("revoke");
      const reload = await fetch(
        `/smart-approval/allowlist?workspace_id=${encodeURIComponent(pendingRevoke.workspaceId)}`,
        { credentials: "include" },
      );
      if (!reload.ok) throw new Error("revoke");
      const data = (await reload.json()) as AllowlistResponse;
      if (pendingRevoke.workspaceId === selectedWorkspace) {
        setRules(Array.isArray(data.allowlist) ? data.allowlist : []);
      }
      setNotice(m.hitl_allowlist_revoked());
      setPendingRevoke(null);
    } catch {
      setError(m.hitl_allowlist_revoke_error());
    } finally {
      setRevoking(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="space-y-1">
        <p className="text-sm font-medium inline-flex items-center gap-2">
          <ShieldCheck className="h-4 w-4" aria-hidden="true" />
          {m.hitl_allowlist_title()}
        </p>
        <p className="text-xs text-muted-foreground">
          {m.hitl_allowlist_subtitle()}
        </p>
      </div>

      <label className="block space-y-1 text-xs">
        <span className="text-muted-foreground">
          {m.hitl_allowlist_workspace()}
        </span>
        <select
          aria-label={m.hitl_allowlist_workspace()}
          value={selectedWorkspace}
          onChange={(event) => setSelectedWorkspace(event.target.value)}
          className="w-full h-8 rounded-md border border-border bg-background px-2"
        >
          {workspaces.map((workspace) => (
            <option key={workspace.id} value={workspace.id}>
              {workspace.name}
            </option>
          ))}
        </select>
      </label>

      {workspaceName && (
        <p className="text-[11px] text-muted-foreground">{workspaceName}</p>
      )}
      {loading && (
        <div className="flex items-center gap-2 py-6 text-xs text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          {m.hitl_allowlist_loading()}
        </div>
      )}
      {!loading && error && <p className="text-xs text-destructive">{error}</p>}
      {!loading && !error && rules.length === 0 && (
        <p className="rounded-lg border border-dashed p-4 text-xs text-muted-foreground">
          {m.hitl_allowlist_empty()}
        </p>
      )}
      {!loading && rules.length > 0 && (
        <div className="rounded-lg border bg-card/50 divide-y divide-border/60">
          {rules.map((rule) => (
            <div
              key={rule.id}
              className="flex items-center justify-between gap-3 px-3 py-2"
            >
              <span
                className="truncate text-xs font-mono"
                title={m.hitl_allowlist_title()}
              >
                {rule.label}
              </span>
              <Button
                size="sm"
                variant="outline"
                className="h-7 shrink-0 text-xs"
                onClick={() =>
                  setPendingRevoke({
                    workspaceId: selectedWorkspace,
                    ruleId: rule.id,
                  })
                }
              >
                {m.hitl_allowlist_revoke()}
              </Button>
            </div>
          ))}
        </div>
      )}
      {notice && <p className="text-xs text-green-600">{notice}</p>}
      <ConfirmDialog
        open={pendingRevoke !== null}
        title={m.hitl_allowlist_revoke_title()}
        description={m.hitl_allowlist_revoke_desc()}
        confirmLabel={m.hitl_allowlist_confirm()}
        cancelLabel={m.hitl_allowlist_cancel()}
        variant="destructive"
        onConfirm={revokeRule}
        onCancel={() => !revoking && setPendingRevoke(null)}
      />
    </div>
  );
}
