/**
 * Cliente do sandbox do workspace: estado do isolamento, política do usuário,
 * auditoria e prévia do comando do jail (`/workspaces/{id}/sandbox/*`).
 */

export type IsolationState = "effective" | "partial" | "off" | "unknown";
export type IsolationLevel = "effective" | "partial" | "soft" | "off";
export type SandboxNetwork = "off" | "filtered" | "open";

export interface IsolationDimension {
  name: string;
  state: IsolationState;
  detail: string;
}

export interface SandboxStatus {
  enabled: boolean;
  diagnostic: string | null;
  isolation: {
    level: IsolationLevel;
    live: boolean;
    dimensions: IsolationDimension[];
  } | null;
  ignored_repo_keys: string[];
}

export interface SandboxPolicy {
  enabled: boolean;
  rw_paths: string[];
  ro_paths: string[];
  mask: string[];
  no_gpu: boolean;
  lockdown: boolean;
  allow_tcp_ports: number[];
  env_pass: string[];
  git_writable: boolean;
  require_full_isolation: boolean;
  network: SandboxNetwork;
  allow_hosts: string[];
  registry_hosts: boolean;
  ignored_repo_keys: string[];
  user_policy: boolean;
}

/** O que a tela edita (o servidor devolve a política efetiva inteira). */
export type SandboxPolicyInput = Omit<
  SandboxPolicy,
  "ignored_repo_keys" | "user_policy"
>;

export interface SandboxAuditEvent {
  seq: number;
  ts: string;
  event: string;
  data: Record<string, unknown>;
}

export interface SandboxAudit {
  events: SandboxAuditEvent[];
  chain_ok: boolean;
  records: number;
  broken_at: number | null;
}

function base(workspaceId: string): string {
  return `/workspaces/${encodeURIComponent(workspaceId)}/sandbox`;
}

async function failure(res: Response): Promise<Error> {
  const body = (await res.json().catch(() => null)) as {
    detail?: unknown;
  } | null;
  return new Error(
    typeof body?.detail === "string" ? body.detail : `HTTP ${res.status}`,
  );
}

export async function getSandboxStatus(
  workspaceId: string,
): Promise<SandboxStatus> {
  const res = await fetch(`${base(workspaceId)}/status`);
  if (!res.ok) throw await failure(res);
  return res.json() as Promise<SandboxStatus>;
}

export async function getSandboxPolicy(
  workspaceId: string,
): Promise<SandboxPolicy> {
  const res = await fetch(`${base(workspaceId)}/policy`);
  if (!res.ok) throw await failure(res);
  return res.json() as Promise<SandboxPolicy>;
}

/** Grava a política do usuário; lança com a mensagem do servidor ao recusar. */
export async function putSandboxPolicy(
  workspaceId: string,
  policy: SandboxPolicyInput,
): Promise<SandboxPolicy> {
  const res = await fetch(`${base(workspaceId)}/policy`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(policy),
  });
  if (!res.ok) throw await failure(res);
  return res.json() as Promise<SandboxPolicy>;
}

export async function getSandboxAudit(
  workspaceId: string,
  limit = 50,
): Promise<SandboxAudit> {
  const res = await fetch(`${base(workspaceId)}/audit?limit=${limit}`);
  if (!res.ok) throw await failure(res);
  return res.json() as Promise<SandboxAudit>;
}

export async function getSandboxDryRun(workspaceId: string): Promise<string[]> {
  const res = await fetch(`${base(workspaceId)}/dry-run`);
  if (!res.ok) throw await failure(res);
  return ((await res.json()) as { argv: string[] }).argv;
}
