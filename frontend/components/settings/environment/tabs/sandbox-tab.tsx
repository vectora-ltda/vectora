"use client";

import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { m } from "@/lib/paraglide/messages";
import { useWorkspacesStore } from "@/lib/stores/workspaces-store";
import {
  getSandboxAudit,
  getSandboxDryRun,
  getSandboxPolicy,
  getSandboxStatus,
  putSandboxPolicy,
  type IsolationDimension,
  type IsolationLevel,
  type IsolationState,
  type SandboxAuditEvent,
  type SandboxNetwork,
  type SandboxPolicy,
  type SandboxPolicyInput,
} from "@/lib/api/sandbox";

const LEVEL_LABEL: Record<IsolationLevel, () => string> = {
  effective: () => m.sandbox_level_effective(),
  partial: () => m.sandbox_level_partial(),
  soft: () => m.sandbox_level_soft(),
  off: () => m.sandbox_level_off(),
};

const LEVEL_STYLE: Record<IsolationLevel, string> = {
  effective: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  partial: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  soft: "bg-orange-500/15 text-orange-700 dark:text-orange-300",
  off: "bg-red-500/15 text-red-700 dark:text-red-300",
};

const STATE_LABEL: Record<IsolationState, () => string> = {
  effective: () => m.sandbox_state_effective(),
  partial: () => m.sandbox_state_partial(),
  off: () => m.sandbox_state_off(),
  unknown: () => m.sandbox_state_unknown(),
};

const DIMENSION_LABEL: Record<string, () => string> = {
  filesystem: () => m.sandbox_dim_filesystem(),
  network: () => m.sandbox_dim_network(),
  environment: () => m.sandbox_dim_environment(),
  seccomp: () => m.sandbox_dim_seccomp(),
  landlock: () => m.sandbox_dim_landlock(),
  no_new_privs: () => m.sandbox_dim_no_new_privs(),
  resources: () => m.sandbox_dim_resources(),
  git: () => m.sandbox_dim_git(),
};

const lines = (text: string): string[] =>
  text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

interface Draft {
  enabled: boolean;
  network: SandboxNetwork;
  allowHosts: string;
  registryHosts: boolean;
  rwPaths: string;
  roPaths: string;
  envPass: string;
  gitWritable: boolean;
  lockdown: boolean;
  requireFull: boolean;
}

function toDraft(policy: SandboxPolicy): Draft {
  return {
    enabled: policy.enabled,
    network: policy.network,
    allowHosts: policy.allow_hosts.join("\n"),
    registryHosts: policy.registry_hosts,
    rwPaths: policy.rw_paths.join("\n"),
    roPaths: policy.ro_paths.join("\n"),
    envPass: policy.env_pass.join("\n"),
    gitWritable: policy.git_writable,
    lockdown: policy.lockdown,
    requireFull: policy.require_full_isolation,
  };
}

function toInput(draft: Draft, current: SandboxPolicy): SandboxPolicyInput {
  return {
    enabled: draft.enabled,
    rw_paths: lines(draft.rwPaths),
    ro_paths: lines(draft.roPaths),
    mask: current.mask,
    no_gpu: current.no_gpu,
    lockdown: draft.lockdown,
    allow_tcp_ports: current.allow_tcp_ports,
    env_pass: lines(draft.envPass),
    git_writable: draft.gitWritable,
    require_full_isolation: draft.requireFull,
    network: draft.network,
    allow_hosts: lines(draft.allowHosts),
    registry_hosts: draft.registryHosts,
  };
}

function describeEvent(event: SandboxAuditEvent): string {
  const data = event.data;
  switch (event.event) {
    case "worker_started":
      return m.sandbox_event_worker_started({ network: String(data.network) });
    case "worker_refused":
      return m.sandbox_event_worker_refused({ reason: String(data.reason) });
    case "egress":
      return data.allowed
        ? m.sandbox_event_egress_allowed({ host: String(data.host) })
        : m.sandbox_event_egress_blocked({ host: String(data.host) });
    case "policy_changed":
      return m.sandbox_event_policy_changed();
    default:
      return event.event;
  }
}

function DimensionRow({ dimension }: { dimension: IsolationDimension }) {
  const label = DIMENSION_LABEL[dimension.name]?.() ?? dimension.name;
  return (
    <li className="flex items-baseline justify-between gap-3 py-1.5 text-sm">
      <span className="font-medium">{label}</span>
      <span className="text-right text-xs text-muted-foreground">
        {STATE_LABEL[dimension.state]()}
        {dimension.detail ? ` — ${dimension.detail}` : ""}
      </span>
    </li>
  );
}

export function SandboxTab() {
  const workspace = useWorkspacesStore((state) =>
    state.workspaces.find((w) => w.id === state.active_id),
  );
  const workspaceId = workspace?.id ?? null;
  const queryClient = useQueryClient();

  const status = useQuery({
    queryKey: ["sandbox", workspaceId, "status"],
    queryFn: () => getSandboxStatus(workspaceId as string),
    enabled: workspaceId !== null,
  });
  const policy = useQuery({
    queryKey: ["sandbox", workspaceId, "policy"],
    queryFn: () => getSandboxPolicy(workspaceId as string),
    enabled: workspaceId !== null,
  });
  const audit = useQuery({
    queryKey: ["sandbox", workspaceId, "audit"],
    queryFn: () => getSandboxAudit(workspaceId as string),
    enabled: workspaceId !== null,
  });

  const [draft, setDraft] = useState<Draft | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(
    null,
  );
  const [saving, setSaving] = useState(false);
  const [showCommand, setShowCommand] = useState(false);
  const [command, setCommand] = useState<string[] | null>(null);

  // A tela edita um rascunho; ele reinicia quando a política do servidor muda
  // (outro workspace, ou depois de salvar). Derivado durante a renderização,
  // não num efeito, para não renderizar duas vezes.
  const policyData = policy.data;
  const [seenPolicy, setSeenPolicy] = useState<SandboxPolicy | undefined>();
  if (policyData !== seenPolicy) {
    setSeenPolicy(policyData);
    setDraft(policyData ? toDraft(policyData) : null);
  }

  const toggleCommand = async () => {
    if (!showCommand && workspaceId && command === null) {
      try {
        setCommand(await getSandboxDryRun(workspaceId));
      } catch {
        setCommand([]);
      }
    }
    setShowCommand((visible) => !visible);
  };

  const save = async () => {
    if (!workspaceId || !draft || !policyData) return;
    setSaving(true);
    setMessage(null);
    try {
      await putSandboxPolicy(workspaceId, toInput(draft, policyData));
      setMessage({ ok: true, text: m.sandbox_saved() });
      setCommand(null);
      setShowCommand(false);
      await queryClient.invalidateQueries({
        queryKey: ["sandbox", workspaceId],
      });
    } catch (error) {
      setMessage({
        ok: false,
        text: m.sandbox_save_error({
          error: error instanceof Error ? error.message : String(error),
        }),
      });
    } finally {
      setSaving(false);
    }
  };

  const ignored = useMemo(
    () => status.data?.ignored_repo_keys ?? policyData?.ignored_repo_keys ?? [],
    [status.data, policyData],
  );

  if (!workspace) {
    return (
      <p className="text-sm text-muted-foreground">
        {m.sandbox_no_workspace()}
      </p>
    );
  }
  if (status.isError || policy.isError) {
    return <p className="text-sm text-red-600">{m.sandbox_error()}</p>;
  }
  if (status.isLoading || policy.isLoading || !draft || !policyData) {
    return (
      <p className="text-sm text-muted-foreground">{m.sandbox_loading()}</p>
    );
  }

  const isolation = status.data?.isolation ?? null;
  const set = <K extends keyof Draft>(key: K, value: Draft[K]) =>
    setDraft((current) => (current ? { ...current, [key]: value } : current));

  return (
    <div className="max-w-2xl space-y-6">
      <div>
        <h2 className="text-base font-semibold">{m.sandbox_title()}</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          {m.sandbox_subtitle()}
        </p>
        <p className="mt-1 text-xs text-muted-foreground">{workspace.name}</p>
      </div>

      {ignored.length > 0 && (
        <div
          role="alert"
          className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm"
        >
          <p className="font-medium">{m.sandbox_ignored_title()}</p>
          <p className="mt-1 text-xs text-muted-foreground">
            {m.sandbox_ignored_description({ keys: ignored.join(", ") })}
          </p>
        </div>
      )}

      <section aria-labelledby="sandbox-state">
        {isolation ? (
          <>
            <div className="flex items-center gap-3">
              <span
                id="sandbox-state"
                data-testid="sandbox-level"
                className={`rounded-full px-3 py-1 text-sm font-medium ${LEVEL_STYLE[isolation.level]}`}
              >
                {LEVEL_LABEL[isolation.level]()}
              </span>
              <span className="text-xs text-muted-foreground">
                {isolation.live ? m.sandbox_live() : m.sandbox_not_live()}
              </span>
            </div>
            <ul className="mt-3 divide-y divide-border/60">
              {isolation.dimensions.map((dimension) => (
                <DimensionRow key={dimension.name} dimension={dimension} />
              ))}
            </ul>
          </>
        ) : (
          <p id="sandbox-state" className="text-sm text-muted-foreground">
            {m.sandbox_disabled()}
          </p>
        )}
      </section>

      <section className="space-y-3" aria-labelledby="sandbox-policy">
        <div>
          <h3 id="sandbox-policy" className="text-sm font-semibold">
            {m.sandbox_policy_title()}
          </h3>
          <p className="text-xs text-muted-foreground">
            {m.sandbox_policy_hint()}
          </p>
        </div>

        {!workspace.trusted && (
          <p className="text-xs text-amber-700 dark:text-amber-300">
            {m.sandbox_untrusted()}
          </p>
        )}

        <fieldset
          disabled={!workspace.trusted || saving}
          className="space-y-3 disabled:opacity-70"
        >
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={draft.enabled}
              onChange={(e) => set("enabled", e.target.checked)}
            />
            {m.sandbox_field_enabled()}
          </label>

          <label className="block text-sm">
            <span className="mb-1 block font-medium">
              {m.sandbox_field_network()}
            </span>
            <select
              className="w-full rounded-md border border-border bg-background p-2 text-sm"
              value={draft.network}
              onChange={(e) => set("network", e.target.value as SandboxNetwork)}
            >
              <option value="filtered">{m.sandbox_network_filtered()}</option>
              <option value="off">{m.sandbox_network_off()}</option>
              <option value="open">{m.sandbox_network_open()}</option>
            </select>
          </label>

          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={draft.registryHosts}
              onChange={(e) => set("registryHosts", e.target.checked)}
            />
            {m.sandbox_field_registry_hosts()}
          </label>

          <label className="block text-sm">
            <span className="mb-1 block font-medium">
              {m.sandbox_field_allow_hosts()}
            </span>
            <textarea
              rows={3}
              className="w-full rounded-md border border-border bg-background p-2 font-mono text-xs"
              placeholder={m.sandbox_field_allow_hosts_hint()}
              value={draft.allowHosts}
              onChange={(e) => set("allowHosts", e.target.value)}
            />
          </label>

          <label className="block text-sm">
            <span className="mb-1 block font-medium">
              {m.sandbox_field_rw_paths()}
            </span>
            <textarea
              rows={2}
              className="w-full rounded-md border border-border bg-background p-2 font-mono text-xs"
              value={draft.rwPaths}
              onChange={(e) => set("rwPaths", e.target.value)}
            />
          </label>

          <label className="block text-sm">
            <span className="mb-1 block font-medium">
              {m.sandbox_field_ro_paths()}
            </span>
            <textarea
              rows={2}
              className="w-full rounded-md border border-border bg-background p-2 font-mono text-xs"
              value={draft.roPaths}
              onChange={(e) => set("roPaths", e.target.value)}
            />
          </label>

          <label className="block text-sm">
            <span className="mb-1 block font-medium">
              {m.sandbox_field_env_pass()}
            </span>
            <textarea
              rows={2}
              className="w-full rounded-md border border-border bg-background p-2 font-mono text-xs"
              value={draft.envPass}
              onChange={(e) => set("envPass", e.target.value)}
            />
          </label>

          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={draft.gitWritable}
              onChange={(e) => set("gitWritable", e.target.checked)}
            />
            {m.sandbox_field_git_writable()}
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={draft.lockdown}
              onChange={(e) => set("lockdown", e.target.checked)}
            />
            {m.sandbox_field_lockdown()}
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={draft.requireFull}
              onChange={(e) => set("requireFull", e.target.checked)}
            />
            {m.sandbox_field_require_full()}
          </label>

          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => void save()}
              className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground disabled:opacity-60"
            >
              {m.sandbox_save()}
            </button>
            {message && (
              <span
                role="status"
                className={`text-xs ${message.ok ? "text-emerald-600" : "text-red-600"}`}
              >
                {message.text}
              </span>
            )}
          </div>
        </fieldset>
      </section>

      <section className="space-y-2" aria-labelledby="sandbox-command">
        <h3 id="sandbox-command" className="text-sm font-semibold">
          {m.sandbox_dryrun_title()}
        </h3>
        <button
          type="button"
          onClick={() => void toggleCommand()}
          className="text-xs text-primary underline"
        >
          {showCommand ? m.sandbox_dryrun_hide() : m.sandbox_dryrun_show()}
        </button>
        {showCommand && command && (
          <pre
            data-testid="sandbox-command"
            className="max-h-64 overflow-auto rounded-md bg-muted p-3 text-xs whitespace-pre-wrap"
          >
            {command.join(" ")}
          </pre>
        )}
      </section>

      <section className="space-y-2" aria-labelledby="sandbox-audit">
        <h3 id="sandbox-audit" className="text-sm font-semibold">
          {m.sandbox_audit_title()}
        </h3>
        {audit.data && (
          <p
            role="status"
            className={`text-xs ${audit.data.chain_ok ? "text-emerald-600" : "text-red-600"}`}
          >
            {audit.data.chain_ok
              ? m.sandbox_audit_chain_ok({
                  count: String(audit.data.records),
                })
              : m.sandbox_audit_chain_broken({
                  line: String(audit.data.broken_at ?? 0),
                })}
          </p>
        )}
        {audit.data && audit.data.events.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            {m.sandbox_audit_empty()}
          </p>
        ) : (
          <ul className="divide-y divide-border/60">
            {(audit.data?.events ?? []).map((event) => (
              <li
                key={event.seq}
                className="flex items-baseline justify-between gap-3 py-1.5 text-xs"
              >
                <span>{describeEvent(event)}</span>
                <time className="text-muted-foreground" dateTime={event.ts}>
                  {new Date(event.ts).toLocaleString()}
                </time>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
