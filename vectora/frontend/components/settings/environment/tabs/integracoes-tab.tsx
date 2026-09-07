"use client";

/**
 * IntegracoesTab — tela única de credenciais (integrações conhecidas +
 * variáveis customizadas). Qualquer chave/valor livre entra aqui como uma
 * variável "Customizada", ao lado dos providers do catálogo.
 *
 * O1 — API key: usuário insere chave manualmente (inclui Slack — Socket
 * Mode exige xoxb-/xapp-, e o app-level token não sai de um OAuth padrão).
 * OAuth: os apps de GitHub, GitLab e Google são registrados pela Vectora LTDA
 * no Worker services. O usuário apenas autoriza o acesso; tokens manuais
 * continuam disponíveis quando o provider declarar suporte. `kind` do backend
 * decide tudo aqui — nada de lista de ids hardcoded no frontend.
 * Custom: chave+valor livre via /auth/envs, para credenciais sem entrada
 * dedicada no catálogo (MCP servers, providers não listados, etc).
 * Callbacks OAuth e URLs de túnel são internos do ecossistema e nunca são
 * exibidos como configuração para o usuário final.
 */

import {
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Eye,
  EyeOff,
  ExternalLink,
  GitBranch,
  KeyRound,
  Link2,
  Loader2,
  Plus,
  Trash2,
  XCircle,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { m } from "@/lib/paraglide/messages";

// ---------------------------------------------------------------------------
// Tipos
// ---------------------------------------------------------------------------

interface Integration {
  id: string;
  name: string;
  env_var: string;
  kind: "apikey" | "oauth" | "hybrid";
  description: string;
  docs_url: string;
  /** Linha curta de "como obter esta credencial", vinda do catálogo do
   * backend — só aparece quando a integração declara uma. */
  setup_hint?: string;
  icon: string;
  oauth_scopes?: string[];
  parent?: string;
  connected: boolean;
  /** Diferente de `connected`: só true quando o valor setado veio do fluxo
   * OAuth de verdade (callback em oauth.py), não de um token colado
   * manualmente num provider `hybrid` como GitHub. A seção OAuth usa isto
   * pra decidir entre "Conectar via OAuth" e "Conexão ativa (OAuth)" —
   * usar `connected` aqui faria a UI mentir sobre a origem da conexão. */
  oauth_connected: boolean;
  env_var_aliases?: string[];
  extra_vars?: string[];
  /** true quando o broker da Vectora anuncia o provider ou quando esta
   * instalação legada tem CLIENT_ID + CLIENT_SECRET locais configurados; a
   * interface usa o valor para habilitar "Conectar via OAuth". */
  oauth_configured: boolean;
}

type VerifyState = "idle" | "loading" | "ok" | "error";

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

async function fetchIntegrations(): Promise<Integration[]> {
  const res = await fetch("/integrations", { credentials: "include" });
  if (!res.ok) {
    console.error(`fetchIntegrations: ${res.status} ${res.statusText}`);
    return [];
  }
  const data = (await res.json()) as { integrations?: Integration[] };
  return data.integrations ?? [];
}

type GatewayState = "never_connected" | "error" | "connected";

interface GatewayStatus {
  connected: boolean;
  state: GatewayState;
  detail: string | null;
}

const GATEWAY_STATUS_FALLBACK: GatewayStatus = {
  connected: false,
  state: "never_connected",
  detail: null,
};

async function fetchGatewayStatus(): Promise<GatewayStatus> {
  try {
    const res = await fetch("/gateway/status", { credentials: "include" });
    if (!res.ok) return GATEWAY_STATUS_FALLBACK;
    return (await res.json()) as GatewayStatus;
  } catch {
    return GATEWAY_STATUS_FALLBACK;
  }
}

interface EnvsResponse {
  envs: Record<string, string>; // valores mascarados ("••••••••")
  keys: string[];
}

async function fetchEnvs(): Promise<EnvsResponse> {
  const res = await fetch("/auth/envs", { credentials: "include" });
  if (!res.ok) throw new Error(`Erro ${res.status}`);
  return res.json();
}

async function saveApiKey(envVar: string, value: string): Promise<void> {
  const res = await fetch("/auth/envs", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key: envVar, value }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: "" }));
    throw new Error(body.detail || `Erro ${res.status}`);
  }
}

async function removeApiKey(envVar: string): Promise<void> {
  const res = await fetch(`/auth/envs/${encodeURIComponent(envVar)}`, {
    method: "DELETE",
    credentials: "include",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: "" }));
    throw new Error(body.detail || `Erro ${res.status}`);
  }
}

async function verifyIntegration(
  id: string,
): Promise<{ ok: boolean; message: string }> {
  const res = await fetch(`/integrations/${id}/verify`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) return { ok: false, message: `Erro ${res.status}` };
  return res.json() as Promise<{ ok: boolean; message: string }>;
}

async function disconnectOAuth(provider: string): Promise<void> {
  await fetch(`/auth/${provider}`, {
    method: "DELETE",
    credentials: "include",
  });
}

function startOAuth(provider: string): void {
  window.location.href = `/auth/${provider}`;
}

// ---------------------------------------------------------------------------
// Subcomponente: card de integração
// ---------------------------------------------------------------------------

function IntegrationCard({
  integ,
  onUpdated,
}: {
  integ: Integration;
  onUpdated: () => void;
}) {
  // O token manual continua sendo uma alternativa explícita e permanece
  // disponível quando o provider declara suporte a credenciais manuais.
  const [expanded, setExpanded] = useState(
    () =>
      (integ.kind === "oauth" || integ.kind === "hybrid") &&
      !integ.oauth_configured &&
      !integ.connected,
  );
  const [keyValue, setKeyValue] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [saving, setSaving] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [verifyState, setVerifyState] = useState<VerifyState>("idle");
  const [verifyMsg, setVerifyMsg] = useState("");
  const [error, setError] = useState<string | null>(null);

  // Deriva 100% do registry do backend (`kind`) — nada hardcoded aqui: uma
  // integração nova ganha o comportamento certo só por declarar seu `kind`
  // no backend, sem precisar tocar nesta lista duplicada no frontend.
  const isOAuthProvider = integ.kind === "oauth" || integ.kind === "hybrid";
  const isChildOAuth = !!integ.parent; // google-drive, gmail dependem de google
  // Todo provider (apikey/hybrid/oauth) aceita token manual, incluindo
  // OAuth-only, como alternativa a registrar o app próprio no provider.
  const allowToken =
    integ.kind === "apikey" ||
    integ.kind === "hybrid" ||
    integ.kind === "oauth";
  const handleSave = async () => {
    if (!keyValue.trim()) return;
    setSaving(true);
    setError(null);
    try {
      await saveApiKey(integ.env_var, keyValue.trim());
      setKeyValue("");
      setExpanded(false);
      onUpdated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erro ao salvar");
    } finally {
      setSaving(false);
    }
  };

  const handleRemove = async () => {
    setRemoving(true);
    setError(null);
    try {
      await removeApiKey(integ.env_var);
      onUpdated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erro ao remover");
    } finally {
      setRemoving(false);
    }
  };

  const handleVerify = async () => {
    setVerifyState("loading");
    setVerifyMsg("");
    try {
      const result = await verifyIntegration(integ.id);
      setVerifyState(result.ok ? "ok" : "error");
      setVerifyMsg(result.message);
    } catch {
      setVerifyState("error");
      setVerifyMsg("Falha na verificação");
    }
  };

  const handleOAuthDisconnect = async () => {
    setRemoving(true);
    try {
      const provider = integ.parent ?? integ.id;
      await disconnectOAuth(provider);
      onUpdated();
    } finally {
      setRemoving(false);
    }
  };

  // Providers filho (google-drive, gmail) herdam conexão do pai — não mostram
  // card próprio com botão de OAuth; apenas mostram status herdado.
  if (isChildOAuth) {
    return (
      <div className="rounded-lg border bg-card/50">
        <div className="flex items-center gap-3 p-3">
          <div className="w-8 h-8 rounded-md bg-muted flex items-center justify-center shrink-0">
            <Link2 className="w-4 h-4 text-muted-foreground" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium">{integ.name}</span>
              <Badge
                variant={integ.connected ? "default" : "secondary"}
                className="text-[10px] h-4 px-1.5"
              >
                {integ.connected
                  ? m.integrations_connected()
                  : m.integrations_disconnected()}
              </Badge>
            </div>
            <p className="text-xs text-muted-foreground truncate">
              {integ.description}
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-lg border bg-card">
      {/* Cabeçalho */}
      <div className="flex items-center gap-3 p-3">
        <div className="w-8 h-8 rounded-md bg-muted flex items-center justify-center shrink-0">
          {isOAuthProvider ? (
            <GitBranch className="w-4 h-4" />
          ) : (
            <KeyRound className="w-4 h-4 text-muted-foreground" />
          )}
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium">{integ.name}</span>
            <Badge
              variant={integ.connected ? "default" : "secondary"}
              className="text-[10px] h-4 px-1.5"
            >
              {integ.connected
                ? m.integrations_connected()
                : m.integrations_disconnected()}
            </Badge>
          </div>
          <p className="text-xs text-muted-foreground truncate">
            {integ.description}
          </p>
        </div>

        <div className="flex items-center gap-1 shrink-0">
          {integ.connected && (
            <Button
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-xs"
              onClick={handleVerify}
              disabled={verifyState === "loading"}
            >
              {verifyState === "loading" ? (
                <Loader2 className="w-3 h-3 animate-spin" />
              ) : verifyState === "ok" ? (
                <CheckCircle2 className="w-3 h-3 text-green-500" />
              ) : verifyState === "error" ? (
                <XCircle className="w-3 h-3 text-destructive" />
              ) : (
                m.integrations_verify()
              )}
            </Button>
          )}

          {allowToken && (
            <Button
              variant="ghost"
              size="sm"
              className="h-7 w-7 p-0"
              onClick={() => {
                setExpanded((v) => !v);
                setShowKey(false);
              }}
              title={m.integrations_paste_token()}
            >
              {expanded ? (
                <ChevronUp className="w-3.5 h-3.5" />
              ) : (
                <ChevronDown className="w-3.5 h-3.5" />
              )}
            </Button>
          )}
        </div>
      </div>

      {/* Mensagem de verificação */}
      {verifyMsg && (
        <div
          className={`mx-3 mb-2 text-xs px-2 py-1 rounded ${
            verifyState === "ok"
              ? "bg-green-500/10 text-green-600 dark:text-green-400"
              : "bg-destructive/10 text-destructive"
          }`}
        >
          {verifyMsg}
        </div>
      )}

      {/* Expansão para API key / token manual */}
      {allowToken && expanded && (
        <div className="px-3 pb-3 space-y-2 border-t pt-3">
          {integ.setup_hint && (
            <p className="text-xs text-muted-foreground leading-relaxed">
              {integ.setup_hint}
            </p>
          )}
          <div className="flex gap-2">
            <div className="relative flex-1">
              <Input
                type={showKey ? "text" : "password"}
                autoComplete="new-password"
                placeholder={m.integrations_api_key_placeholder()}
                value={keyValue}
                onChange={(e) => setKeyValue(e.target.value)}
                className="h-8 pr-8 text-xs font-mono"
                onKeyDown={(e) => {
                  if (e.key === "Enter") void handleSave();
                }}
              />
              <button
                type="button"
                onClick={() => setShowKey((v) => !v)}
                className="absolute inset-y-0 right-0 flex items-center justify-center w-8 text-muted-foreground hover:text-foreground transition-colors"
                aria-label={
                  showKey
                    ? m.integrations_hide_key()
                    : m.integrations_show_key()
                }
              >
                {showKey ? (
                  <EyeOff className="h-3.5 w-3.5" />
                ) : (
                  <Eye className="h-3.5 w-3.5" />
                )}
              </button>
            </div>
            <Button
              size="sm"
              className="h-8 shrink-0"
              onClick={handleSave}
              disabled={saving || !keyValue.trim()}
            >
              {saving ? (
                <Loader2 className="w-3 h-3 animate-spin" />
              ) : (
                m.integrations_save_key()
              )}
            </Button>
          </div>

          <div className="flex items-center justify-between">
            {integ.connected && (
              <Button
                variant="ghost"
                size="sm"
                className="h-6 px-2 text-xs text-muted-foreground hover:text-destructive"
                onClick={handleRemove}
                disabled={removing}
              >
                {removing && <Loader2 className="w-3 h-3 animate-spin mr-1" />}
                {m.integrations_remove_key()}
              </Button>
            )}
            <a
              href={integ.docs_url}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground ml-auto"
            >
              {m.integrations_get_key()}
              <ExternalLink className="w-2.5 h-2.5" />
            </a>
          </div>

          {error && <p className="text-xs text-destructive">{error}</p>}
        </div>
      )}

      {/* OAuth section — os apps são mantidos pela Vectora LTDA no Worker;
          o usuário apenas autoriza o acesso no provider. */}
      {isOAuthProvider && (integ.oauth_configured || integ.oauth_connected) && (
        <div className="px-3 pb-3 border-t pt-3 space-y-2">
          {integ.oauth_connected ? (
            <div className="flex items-center justify-between">
              <span className="text-xs text-muted-foreground">
                Conexão ativa (OAuth)
              </span>
              <Button
                variant="outline"
                size="sm"
                className="h-7 text-xs text-destructive hover:text-destructive"
                onClick={handleOAuthDisconnect}
                disabled={removing}
              >
                {removing && <Loader2 className="w-3 h-3 animate-spin mr-1" />}
                {m.integrations_disconnect()}
              </Button>
            </div>
          ) : (
            <div className="flex items-center justify-between">
              <span className="text-xs text-muted-foreground">
                {integ.description}
              </span>
              <Button
                size="sm"
                className="h-7 text-xs"
                onClick={() => startOAuth(integ.id)}
              >
                <GitBranch className="w-3 h-3 mr-1.5" />
                {m.integrations_connect_oauth()}
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Subcomponente: card de variável customizada (chave/valor livre)
// ---------------------------------------------------------------------------

function CustomVarCard({
  varKey,
  maskedValue,
  onDeleted,
}: {
  varKey: string;
  maskedValue: string;
  onDeleted: () => void;
}) {
  const [deleting, setDeleting] = useState(false);

  const handleDelete = async () => {
    setDeleting(true);
    try {
      await removeApiKey(varKey);
      onDeleted();
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border bg-card px-3 py-2.5">
      <div className="min-w-0 flex-1">
        <div className="text-sm font-mono font-medium truncate">{varKey}</div>
        <div className="text-xs text-muted-foreground font-mono">
          {maskedValue}
        </div>
      </div>
      <Button
        variant="ghost"
        size="sm"
        className="h-7 px-2 text-xs text-muted-foreground hover:text-destructive shrink-0"
        onClick={handleDelete}
        disabled={deleting}
      >
        {deleting ? (
          <Loader2 className="w-3.5 h-3.5 animate-spin" />
        ) : (
          <Trash2 className="w-3.5 h-3.5" />
        )}
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Categorias
// ---------------------------------------------------------------------------

const CATEGORIES: { label: () => string; ids: string[] }[] = [
  { label: m.integrations_category_git, ids: ["github", "gitlab"] },
  {
    label: m.integrations_category_ai,
    ids: ["gemini", "openai", "anthropic", "cohere", "tavily"],
  },
  {
    label: m.integrations_category_google,
    ids: ["google", "google-drive", "gmail"],
  },
  {
    label: m.integrations_category_communication,
    ids: ["slack", "telegram", "discord", "email-connect"],
  },
  {
    label: m.integrations_category_productivity,
    ids: ["linear", "jira", "notion"],
  },
  { label: m.integrations_category_smart_home, ids: ["home-assistant"] },
  {
    label: m.integrations_category_email,
    ids: ["resend", "sendgrid", "mailgun"],
  },
];

/** Ids do catálogo do backend que nenhuma categoria acima reivindica.
 *
 * Sem isto, uma integração nova no backend simplesmente não renderiza —
 * a lista de categorias é fixa e descarta silenciosamente o que não
 * conhece. */
function uncategorizedIds(all: Integration[]): string[] {
  const claimed = new Set(CATEGORIES.flatMap((c) => c.ids));
  return all.filter((i) => !claimed.has(i.id) && !i.parent).map((i) => i.id);
}

// ---------------------------------------------------------------------------
// Componente principal
// ---------------------------------------------------------------------------

export function IntegracoesTab() {
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [customEnvs, setCustomEnvs] = useState<Record<string, string>>({});
  const [customKeys, setCustomKeys] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [gateway, setGateway] = useState<GatewayStatus>(
    GATEWAY_STATUS_FALLBACK,
  );

  const [addCustomOpen, setAddCustomOpen] = useState(false);
  const [newCustomKey, setNewCustomKey] = useState("");
  const [newCustomValue, setNewCustomValue] = useState("");
  const [showCustomValue, setShowCustomValue] = useState(false);
  const [savingCustom, setSavingCustom] = useState(false);
  const [customError, setCustomError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [data, gatewayStatus, envsData] = await Promise.all([
        fetchIntegrations(),
        fetchGatewayStatus(),
        fetchEnvs(),
      ]);
      setIntegrations(data);
      setGateway(gatewayStatus);

      // Variáveis "órfãs": chaves em /auth/envs que não correspondem ao
      // env_var (nem aliases/extra_vars) de nenhuma integração do catálogo —
      // continuam visíveis, agora como seção "Customizadas".
      const knownVars = new Set(
        data.flatMap((i) => [
          i.env_var,
          ...(i.env_var_aliases ?? []),
          ...(i.extra_vars ?? []),
        ]),
      );
      const orphanKeys = (envsData.keys ?? []).filter((k) => !knownVars.has(k));
      setCustomKeys(orphanKeys);
      setCustomEnvs(envsData.envs ?? {});
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Busca integrações/env vars no backend ao montar.
    // oxlint-disable-next-line react/set-state-in-effect
    void load();
  }, [load]);

  useEffect(() => {
    // Corrida conhecida (commit d732f197): o registro do gateway confirma
    // só o handshake HTTP -- o WebSocket pode levar alguns segundos a mais
    // pra ser marcado "conectado" no Durable Object. Sem retry, um load()
    // disparado nessa janela prendia o card em "Gateway indisponível" pra
    // sempre, mesmo o backend conectando logo em seguida.
    if (gateway.state !== "error") return;
    let cancelled = false;
    let attempts = 0;
    const maxAttempts = 5;
    const interval = setInterval(() => {
      attempts += 1;
      // Limite aplicado ANTES de disparar a requisição: se a resposta
      // anterior ainda estiver pendente (mais lenta que os 3s do
      // intervalo), o clearInterval só rodava DEPOIS do .then() — a próxima
      // tentativa já teria disparado, ultrapassando maxAttempts.
      if (attempts >= maxAttempts) {
        clearInterval(interval);
      }
      void fetchGatewayStatus().then((status) => {
        if (cancelled) return;
        setGateway(status);
        if (status.state !== "error") {
          clearInterval(interval);
        }
      });
    }, 3000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [gateway.state]);

  // Detecta oauth_success/oauth_error na URL após callback OAuth
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("oauth_success") ?? params.get("oauth_error")) {
      const url = new URL(window.location.href);
      url.searchParams.delete("oauth_success");
      url.searchParams.delete("oauth_error");
      window.history.replaceState({}, "", url.toString());
      // Recarrega o status das integrações após o redirect do OAuth —
      // sincroniza com o resultado do callback (sistema externo).
      // oxlint-disable-next-line react/set-state-in-effect
      void load();
    }
  }, [load]);

  const handleAddCustom = async () => {
    if (!newCustomKey.trim() || !newCustomValue.trim()) return;
    setSavingCustom(true);
    setCustomError(null);
    try {
      await saveApiKey(newCustomKey.trim(), newCustomValue);
      await load();
      setAddCustomOpen(false);
      setNewCustomKey("");
      setNewCustomValue("");
    } catch (err) {
      setCustomError(err instanceof Error ? err.message : m.envs_error_save());
    } finally {
      setSavingCustom(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const byId = Object.fromEntries(integrations.map((i) => [i.id, i]));
  const connected = integrations.filter((i) => i.connected).length;

  return (
    <div className="space-y-4">
      {/* Sumário */}
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-0.5">
          <p className="text-sm font-medium">
            {connected > 0
              ? `${connected} integração${connected > 1 ? "s" : ""} ativa${connected > 1 ? "s" : ""}`
              : m.integrations_none_active()}
          </p>
          <p className="text-xs text-muted-foreground">
            {m.integrations_keys_private()}
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          className="shrink-0"
          onClick={() => setAddCustomOpen(true)}
        >
          <Plus className="w-3.5 h-3.5 mr-1.5" />
          {m.integrations_add_custom()}
        </Button>
      </div>

      {/* Banner gateway — 3 estados distintos (não só conectado/desconectado):
          nunca conectou (neutro, nada errado — normal antes da 1ª
          integração), erro real (gateway fora do ar/mal configurado, com
          detalhe), e conectado. */}
      <div
        className={`rounded-lg border px-3 py-2 text-xs space-y-0.5 ${
          gateway.state === "connected"
            ? "border-green-500/30 bg-green-500/5"
            : gateway.state === "error"
              ? "border-destructive/30 bg-destructive/5"
              : "border-border bg-muted/30"
        }`}
      >
        <div className="flex items-center justify-between">
          <p
            className={`font-medium ${
              gateway.state === "connected"
                ? "text-green-600 dark:text-green-400"
                : gateway.state === "error"
                  ? "text-destructive"
                  : "text-muted-foreground"
            }`}
          >
            {gateway.state === "connected"
              ? m.gateway_connected()
              : gateway.state === "error"
                ? m.gateway_error()
                : m.gateway_never_connected()}
          </p>
        </div>
        {gateway.state === "error" && (
          <p className="text-destructive/80">
            {gateway.detail ?? m.gateway_error_retry()}
          </p>
        )}
        {gateway.state === "never_connected" && (
          <p className="text-muted-foreground">{m.gateway_no_token()}</p>
        )}
      </div>

      {/* Cards por categoria */}
      {[
        ...CATEGORIES,
        {
          label: m.integrations_category_other,
          ids: uncategorizedIds(integrations),
        },
      ].map((cat) => {
        const items = cat.ids.flatMap((id) => (byId[id] ? [byId[id]] : []));
        if (items.length === 0) return null;
        return (
          <div key={cat.label()} className="space-y-2">
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
              {cat.label()}
            </p>
            {items.map((integ) => (
              <IntegrationCard key={integ.id} integ={integ} onUpdated={load} />
            ))}
          </div>
        );
      })}

      {/* Variáveis customizadas — chave/valor livre, sem entrada no catálogo */}
      {customKeys.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
            {m.integrations_custom_section_title()}
          </p>
          {customKeys.map((key) => (
            <CustomVarCard
              key={key}
              varKey={key}
              maskedValue={customEnvs[key] ?? "••••••••"}
              onDeleted={load}
            />
          ))}
        </div>
      )}

      {/* Dialog — adicionar variável customizada */}
      <Dialog
        open={addCustomOpen}
        onOpenChange={(open) => {
          setAddCustomOpen(open);
          if (!open) {
            setNewCustomKey("");
            setNewCustomValue("");
            setShowCustomValue(false);
            setCustomError(null);
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{m.integrations_custom_title()}</DialogTitle>
            <DialogDescription>
              {m.integrations_custom_desc()}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-1">
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground">
                {m.envs_key_label()}
              </label>
              <Input
                placeholder={m.envs_key_placeholder()}
                value={newCustomKey}
                onChange={(e) => setNewCustomKey(e.target.value)}
                autoComplete="off"
                className="text-sm font-mono"
                autoFocus
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground">
                {m.envs_value_label()}
              </label>
              <div className="relative">
                <Input
                  type={showCustomValue ? "text" : "password"}
                  autoComplete="new-password"
                  placeholder={m.envs_value_placeholder()}
                  value={newCustomValue}
                  onChange={(e) => setNewCustomValue(e.target.value)}
                  className="pr-8 text-sm font-mono"
                />
                <button
                  type="button"
                  onClick={() => setShowCustomValue((v) => !v)}
                  className="absolute inset-y-0 right-0 flex items-center justify-center w-8 text-muted-foreground hover:text-foreground transition-colors"
                  aria-label={
                    showCustomValue
                      ? m.integrations_hide_key()
                      : m.integrations_show_key()
                  }
                >
                  {showCustomValue ? (
                    <EyeOff className="h-3.5 w-3.5" />
                  ) : (
                    <Eye className="h-3.5 w-3.5" />
                  )}
                </button>
              </div>
            </div>
            {customError && (
              <p className="text-xs text-destructive">{customError}</p>
            )}
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setAddCustomOpen(false);
                setNewCustomKey("");
                setNewCustomValue("");
                setShowCustomValue(false);
                setCustomError(null);
              }}
              disabled={savingCustom}
            >
              {m.envs_cancel()}
            </Button>
            <Button
              onClick={handleAddCustom}
              disabled={
                savingCustom || !newCustomKey.trim() || !newCustomValue.trim()
              }
            >
              {savingCustom && (
                <Loader2 className="w-4 h-4 animate-spin mr-2" />
              )}
              {m.envs_save()}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
