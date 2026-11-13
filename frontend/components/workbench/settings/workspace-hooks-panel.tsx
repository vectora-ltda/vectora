"use client";

import { useEffect, useState } from "react";
import { m } from "@/lib/paraglide/messages";

interface HookRule {
  id: string;
  event: string;
  mode: string;
  tools: string[];
  timeout_s: number;
  command: string[];
}

interface WorkspaceHooks {
  workspace_id: string;
  approval: "no_rules" | "pending" | "approved";
  rules: HookRule[];
}

async function fetchHooks(workspaceId: string): Promise<WorkspaceHooks> {
  const res = await fetch(
    `/workspaces/${encodeURIComponent(workspaceId)}/hooks`,
  );
  if (!res.ok) throw new Error(String(res.status));
  return (await res.json()) as WorkspaceHooks;
}

/** Regras `[[hooks.rules]]` do workspace, para revisar e aprovar. A aprovação
 * vale só para as regras exibidas: se o `vectora.toml` mudar, volta a pendente. */
export function WorkspaceHooksPanel({ workspaceId }: { workspaceId: string }) {
  const [hooks, setHooks] = useState<WorkspaceHooks | null>(null);
  const [failed, setFailed] = useState(false);
  const [approving, setApproving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchHooks(workspaceId)
      .then((data) => {
        if (cancelled) return;
        setHooks(data);
        setFailed(false);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceId]);

  const approve = async () => {
    setApproving(true);
    try {
      const res = await fetch("/workspaces/approve-hooks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workspace_id: workspaceId }),
      });
      if (!res.ok) throw new Error(String(res.status));
      setHooks(await fetchHooks(workspaceId));
      setFailed(false);
    } catch {
      setFailed(true);
    } finally {
      setApproving(false);
    }
  };

  return (
    <section className="mt-6 space-y-3 max-w-xl">
      <div>
        <h3 className="text-sm font-semibold">{m.workspace_hooks_title()}</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          {m.workspace_hooks_description()}
        </p>
      </div>
      {failed && (
        <p role="alert" className="text-xs text-destructive">
          {m.workspace_hooks_error()}
        </p>
      )}
      {hooks?.approval === "no_rules" && (
        <p className="text-xs text-muted-foreground">
          {m.workspace_hooks_empty()}
        </p>
      )}
      {hooks && hooks.rules.length > 0 && (
        <>
          <ul className="space-y-2">
            {hooks.rules.map((rule) => (
              <li
                key={rule.id}
                className="rounded-md border border-border/60 p-3 text-xs"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{rule.id}</span>
                  <code>{rule.event}</code>
                  <code>{rule.mode}</code>
                  <span className="text-muted-foreground">
                    {`${rule.timeout_s}s`}
                  </span>
                </div>
                {rule.tools.length > 0 && (
                  <div className="mt-1 text-muted-foreground">
                    {rule.tools.join(", ")}
                  </div>
                )}
                <code className="mt-2 block break-all">
                  {rule.command.join(" ")}
                </code>
              </li>
            ))}
          </ul>
          {hooks.approval === "approved" ? (
            <p className="text-xs text-muted-foreground">
              {m.workspace_hooks_approved()}
            </p>
          ) : (
            <div className="space-y-2">
              <p className="text-xs">{m.workspace_hooks_pending()}</p>
              <button
                type="button"
                disabled={approving}
                onClick={() => void approve()}
                className="rounded-md border border-border px-3 py-1.5 text-xs font-medium disabled:opacity-60"
              >
                {m.workspace_hooks_approve()}
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}
