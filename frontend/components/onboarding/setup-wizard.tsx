"use client";

/**
 * Passos compartilhados do wizard de primeiro acesso, portados de um Dialog
 * próprio (SetupWizard) pra continuação do PreAuthWizard (tela cheia) —
 * StepToken → StepMode → StepApiKeys → StepWorkspace → StepWorkspaceSelect →
 * StepMemory → StepCapabilities → StepDone.
 */

import { useState, useCallback, useEffect, useRef } from "react";
import {
  Loader2,
  CheckCircle2,
  XCircle,
  Check,
  FolderGit2,
  FolderOpen,
  FolderPlus,
  Plus,
  Lock,
} from "lucide-react";
import { useWorkspacesStore } from "@/lib/stores/workspaces-store";
import { useLicenseStatus } from "@/lib/hooks/use-license-status";
import { WorkspaceTrustDialog } from "@/components/sidebar/workspace-trust-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { m } from "@/lib/paraglide/messages";

/** Props comuns a todos os passos. */
export interface StepProps {
  onValidityChange?: (valid: boolean) => void;
  onWorkspaceSelect?: (id: string | null) => void;
}

// ---------------------------------------------------------------------------
// Step: Token de licença (VECTORA_TOKEN)
// ---------------------------------------------------------------------------

interface ConfigSummary {
  vectora_token_configured: boolean;
  vectora_token_masked: string;
}

/** Resultado de POST /license/validate ou /license/connect. */
interface LicenseResult {
  valid: boolean;
  tier?: string;
  status?: string;
  days_remaining?: number;
  error?: string;
}

/** Badge compacto com o resultado da validação da licença. */
function LicenseResultBadge({ result }: { result: LicenseResult }) {
  if (result.valid) {
    return (
      <p className="flex items-center gap-1.5 text-xs text-green-600 dark:text-green-400">
        <CheckCircle2 className="w-3.5 h-3.5 shrink-0" />
        {m.onboarding_token_valid()}
        {result.tier ? ` — ${result.tier}` : ""}
        {result.status === "trial" && result.days_remaining
          ? ` (trial, ${result.days_remaining}d)`
          : ""}
      </p>
    );
  }
  return (
    <p className="flex items-center gap-1.5 text-xs text-destructive">
      <XCircle className="w-3.5 h-3.5 shrink-0" />
      {result.error || m.onboarding_token_invalid()}
    </p>
  );
}

type OAuthState = "idle" | "pending" | "success" | "error";

export function StepToken(_props: StepProps) {
  const [config, setConfig] = useState<ConfigSummary | null>(null);
  const [result, setResult] = useState<LicenseResult | null>(null);

  // Modo OAuth
  const [oauthState, setOAuthState] = useState<OAuthState>("idle");
  const [authUrl, setAuthUrl] = useState<string | null>(null);
  const [oauthStateKey, setOAuthStateKey] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Modo token manual
  const [tokenInput, setTokenInput] = useState("");
  const [showToken, setShowToken] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    fetch("/admin/config", { credentials: "include" })
      .then((r) => r.json())
      .then(setConfig)
      .catch(() => void 0);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const startOAuth = async () => {
    setOAuthState("pending");
    setResult(null);
    try {
      const init = (await fetch("/license/oauth/init", {
        method: "POST",
        credentials: "include",
      }).then((r) => r.json())) as { state: string; auth_url: string };
      setOAuthStateKey(init.state);
      setAuthUrl(init.auth_url);
      window.open(init.auth_url, "_blank", "noopener,noreferrer");
      startPolling(init.state);
    } catch {
      setOAuthState("error");
    }
  };

  const startPolling = (state: string) => {
    if (pollRef.current) clearInterval(pollRef.current);
    let attempts = 0;
    const MAX_ATTEMPTS = 150; // 5 min a 2s
    pollRef.current = setInterval(async () => {
      attempts++;
      if (attempts > MAX_ATTEMPTS) {
        clearInterval(pollRef.current!);
        setOAuthState("error");
        return;
      }
      try {
        const data = (await fetch(
          `/license/oauth/poll?state=${encodeURIComponent(state)}`,
          { credentials: "include" },
        ).then((r) => r.json())) as {
          pending?: boolean;
          ok?: boolean;
          valid?: boolean;
        };
        if (data.ok) {
          clearInterval(pollRef.current!);
          setOAuthState("success");
          if (data.valid) {
            setResult({ valid: true });
          }
        } else if (!data.pending) {
          clearInterval(pollRef.current!);
          setOAuthState("error");
        }
      } catch {
        // ignora falhas transientes de network
      }
    }, 2000);
  };

  const handleSave = async () => {
    const value = tokenInput.trim();
    if (!value) return;
    setSaving(true);
    setResult(null);
    try {
      await fetch("/admin/config", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ vectora_token: value }),
      });
      const fresh = await fetch("/admin/config", {
        credentials: "include",
      }).then((r) => r.json());
      setConfig(fresh);
      setTokenInput("");
      const validation = (await fetch("/license/validate", {
        method: "POST",
        credentials: "include",
      }).then((r) => r.json())) as LicenseResult;
      setResult(validation);
    } catch {
      setResult({ valid: false, error: m.onboarding_token_invalid() });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-3 py-2">
      <p className="text-sm text-muted-foreground">
        {m.onboarding_token_body()}
      </p>
      <p className="text-xs text-muted-foreground">
        {m.onboarding_token_free_note()}
      </p>

      {config?.vectora_token_configured && (
        <p className="text-xs text-muted-foreground font-mono">
          {m.onboarding_token_configured()}: {config.vectora_token_masked}
        </p>
      )}

      <>
        <div className="flex gap-1.5">
          <Input
            type={showToken ? "text" : "password"}
            value={tokenInput}
            onChange={(e) => setTokenInput(e.target.value)}
            placeholder="vct_…"
            className="h-8 text-xs font-mono flex-1"
            autoComplete="new-password"
          />
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-8 px-2"
            onClick={() => setShowToken((v) => !v)}
          >
            {showToken ? m.onboarding_token_hide() : m.onboarding_token_show()}
          </Button>
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={handleSave}
            disabled={saving || !tokenInput.trim()}
          >
            {saving ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin mr-1.5" />
            ) : null}
            {m.onboarding_token_save()}
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          {m.onboarding_token_hint()}{" "}
          <a
            href="https://vectora.company/dashboard"
            target="_blank"
            rel="noopener noreferrer"
            className="text-primary hover:underline"
          >
            vectora.company/dashboard
          </a>
        </p>
      </>

      {result && <LicenseResultBadge result={result} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step: Modo de armazenamento (Lite vs Completo)
// ---------------------------------------------------------------------------

interface StorageTestResult {
  ok: boolean;
  error?: string;
  latency_ms?: number;
  started?: boolean;
}

interface ServiceFieldConfig {
  service: "postgres" | "redis" | "qdrant";
  title: string;
  fieldKey: string;
  testKey: string;
  placeholder: string;
  type?: string;
  /** Serviço expõe campo de API key (Qdrant). */
  hasApiKey?: boolean;
}

/** Config default por serviço (vem de GET /admin/storage/defaults). */
interface ServiceDefaults {
  url?: string;
  api_key?: string;
  start_command?: string;
}

const SERVICE_FIELDS: ServiceFieldConfig[] = [
  {
    service: "postgres",
    title: "PostgreSQL",
    fieldKey: "postgres_dsn",
    testKey: "dsn",
    placeholder: "postgresql+asyncpg://user:pass@host:5432/vectora",
  },
  {
    service: "redis",
    title: "Redis",
    fieldKey: "redis_url",
    testKey: "url",
    placeholder: "redis://localhost:6379/0",
  },
  {
    service: "qdrant",
    title: "Qdrant",
    fieldKey: "qdrant_url",
    testKey: "url",
    placeholder: "http://localhost:6333",
    hasApiKey: true,
  },
];

function ServiceConnectionCard({
  config,
  defaults,
  onConnectedChange,
}: {
  config: ServiceFieldConfig;
  defaults?: ServiceDefaults;
  onConnectedChange?: (service: string, ok: boolean) => void;
}) {
  const [value, setValue] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [selfHosted, setSelfHosted] = useState(false);
  const [startCommand, setStartCommand] = useState("");
  const [testResult, setTestResult] = useState<StorageTestResult | null>(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  // Pré-preenche com os defaults reais (mesma config que `docker compose up`
  // cria) — URL, API key e o comando self-hosted, já com o toggle ligado. O
  // usuário só clica em Testar/Salvar; nada de digitar credencial conhecida.
  useEffect(() => {
    if (!defaults) return;
    // `defaults` chega assíncrono (fetch no componente pai) — sincroniza o
    // preenchimento inicial assim que os valores reais ficam disponíveis,
    // não é cópia de prop sem necessidade.
    // oxlint-disable-next-line react/set-state-in-effect
    if (defaults.url) setValue(defaults.url);
    if (defaults.api_key) setApiKey(defaults.api_key);
    if (defaults.start_command) {
      setSelfHosted(true);
      setStartCommand(defaults.start_command);
    }
  }, [defaults]);

  const handleTest = async () => {
    const v = value.trim();
    if (!v) return;
    setTesting(true);
    setTestResult(null);
    onConnectedChange?.(config.service, false);
    try {
      const body: Record<string, unknown> = {
        backend: config.service,
        [config.testKey]: v,
      };
      if (config.hasApiKey && apiKey.trim()) {
        body.api_key = apiKey.trim();
      }
      if (selfHosted && startCommand.trim()) {
        body.self_hosted = true;
        body.start_command = startCommand.trim();
      }
      const res = await fetch("/admin/storage/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify(body),
      });
      const result = (await res.json()) as StorageTestResult;
      setTestResult(result);
      onConnectedChange?.(config.service, result.ok);
    } finally {
      setTesting(false);
    }
  };

  const handleSave = async () => {
    const v = value.trim();
    if (!v) return;
    setSaving(true);
    try {
      const body: Record<string, unknown> = { [config.fieldKey]: v };
      if (config.hasApiKey && apiKey.trim()) {
        body.qdrant_api_key = apiKey.trim();
      }
      if (selfHosted || startCommand.trim()) {
        body.services = {
          [config.service]: {
            self_hosted: selfHosted,
            start_command: startCommand.trim() || null,
          },
        };
      }
      await fetch("/admin/storage", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify(body),
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-1.5 rounded border px-2.5 py-2">
      <p className="text-xs font-medium">{config.title}</p>
      <Input
        type={config.type ?? "text"}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        autoComplete={config.type === "password" ? "new-password" : "off"}
        placeholder={config.placeholder}
        className="h-7 text-xs font-mono"
      />
      {config.hasApiKey && (
        <Input
          type="password"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          autoComplete="new-password"
          placeholder={m.onboarding_mode_api_key_placeholder()}
          className="h-7 text-xs font-mono"
        />
      )}
      <label className="flex items-center gap-2 text-xs text-muted-foreground">
        <Switch checked={selfHosted} onCheckedChange={setSelfHosted} />
        {m.onboarding_mode_self_hosted()}
      </label>
      {selfHosted && (
        <Input
          value={startCommand}
          onChange={(e) => setStartCommand(e.target.value)}
          autoComplete="off"
          placeholder={m.onboarding_mode_start_command_placeholder()}
          className="h-7 text-xs font-mono"
        />
      )}
      <div className="flex items-center gap-2">
        <Button
          size="sm"
          variant="outline"
          className="h-7 text-xs"
          onClick={handleTest}
          disabled={testing || !value.trim()}
        >
          {testing ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            m.onboarding_mode_test()
          )}
        </Button>
        <Button
          size="sm"
          className="h-7 text-xs"
          onClick={handleSave}
          disabled={saving || !value.trim()}
        >
          {saving ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : saved ? (
            m.onboarding_token_saved()
          ) : (
            m.onboarding_mode_save()
          )}
        </Button>
      </div>
      {testResult && (
        <div
          className={`text-xs flex items-center gap-1 ${testResult.ok ? "text-green-600" : "text-destructive"}`}
        >
          {testResult.ok ? (
            <>
              <CheckCircle2 className="w-3.5 h-3.5" />
              OK
              {testResult.latency_ms !== undefined && (
                <span className="text-muted-foreground">
                  ({testResult.latency_ms}ms)
                </span>
              )}
            </>
          ) : (
            <>
              <XCircle className="w-3.5 h-3.5" />
              {testResult.error ?? "Falha na conexão"}
            </>
          )}
        </div>
      )}
    </div>
  );
}

export function StepMode({ onValidityChange }: StepProps) {
  // `configured=false` (sem VECTORA_TOKEN) é o estado Free — mesma fonte de
  // admin-tab.tsx (GET /license/status). O modo "Completo" (Postgres+Qdrant+
  // Redis) é recurso exclusivo do Pro; Free fica preso ao Lite (SQLite +
  // LanceDB + NATS embutido).
  const { status: license } = useLicenseStatus();
  const isFree = !license?.configured || license?.tier !== "pro";
  const [mode, setMode] = useState<"lite" | "complete">("lite");
  const [saving, setSaving] = useState(false);
  const [connected, setConnected] = useState<Record<string, boolean>>({});
  // Serviços já configurados via env (ex: docker-compose com Postgres/Redis/
  // Qdrant embutidos) — o wizard não pede para configurá-los novamente.
  const [preconfigured, setPreconfigured] = useState<Record<string, boolean>>(
    {},
  );
  // Resultado do teste automático dos serviços pré-configurados (via env).
  const [preconfiguredTests, setPreconfiguredTests] = useState<
    Record<string, StorageTestResult | null>
  >({});
  // Config default por serviço (URL, API key, comando self-hosted) para
  // pré-preencher os cards de conexão manual — fonte única no backend.
  const [defaults, setDefaults] = useState<Record<string, ServiceDefaults>>({});

  const handleConnectedChange = useCallback((service: string, ok: boolean) => {
    setConnected((prev) => ({ ...prev, [service]: ok }));
  }, []);

  useEffect(() => {
    fetch("/admin/storage/defaults", { credentials: "include" })
      .then((r) => (r.ok ? r.json() : {}))
      .then((data: Record<string, ServiceDefaults>) => setDefaults(data ?? {}))
      .catch(() => void 0);
  }, []);

  useEffect(() => {
    if (mode === "lite") {
      onValidityChange?.(true);
      return;
    }
    onValidityChange?.(
      SERVICE_FIELDS.every(
        (f) => preconfigured[f.service] || connected[f.service],
      ),
    );
  }, [mode, connected, preconfigured, onValidityChange]);

  useEffect(() => {
    fetch("/admin/storage", { credentials: "include" })
      .then((r) => r.json())
      .then((data) => {
        const cfg = data?.config ?? {};
        setMode(cfg.storage_mode === "complete" ? "complete" : "lite");
        const pre: Record<string, boolean> = {
          postgres: !!cfg.postgres_configured,
          redis: !!cfg.redis_configured,
          qdrant: !!cfg.qdrant_configured,
        };
        setPreconfigured(pre);

        // Testa em background cada serviço já configurado via env.
        Object.entries(pre)
          .filter(([, ok]) => ok)
          .forEach(([service]) => {
            fetch("/admin/storage/test", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              credentials: "include",
              body: JSON.stringify({ backend: service }),
            })
              .then((r) => r.json())
              .then((result: StorageTestResult) => {
                setPreconfiguredTests((prev) => ({
                  ...prev,
                  [service]: result,
                }));
                if (!result.ok) {
                  // Teste falhou: remove da lista de pré-configurados para
                  // exibir o card de configuração manual.
                  setPreconfigured((prev) => ({ ...prev, [service]: false }));
                } else {
                  setConnected((prev) => ({ ...prev, [service]: true }));
                }
              })
              .catch(() => {
                setPreconfiguredTests((prev) => ({
                  ...prev,
                  [service]: { ok: false, error: "Erro ao testar conexão" },
                }));
                setPreconfigured((prev) => ({ ...prev, [service]: false }));
              });
          });
      })
      .catch(() => void 0);
  }, []);

  const handleSelect = async (next: "lite" | "complete") => {
    if (next === "complete" && isFree) return;
    setMode(next);
    setSaving(true);
    try {
      await fetch("/admin/storage", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ storage_mode: next }),
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-3 py-2">
      <p className="text-sm text-muted-foreground">
        {m.onboarding_mode_body()}
      </p>
      <div className="grid grid-cols-2 gap-2">
        <button
          onClick={() => handleSelect("lite")}
          className={`text-left p-3 rounded-md border transition-colors ${
            mode === "lite"
              ? "border-primary bg-primary/10"
              : "border-border text-muted-foreground hover:text-foreground"
          }`}
        >
          <p className="text-xs font-medium">
            {m.onboarding_mode_lite_title()}
          </p>
          <p className="text-[11px] mt-1">{m.onboarding_mode_lite_desc()}</p>
        </button>
        <button
          onClick={() => handleSelect("complete")}
          disabled={isFree}
          aria-disabled={isFree}
          className={`text-left p-3 rounded-md border transition-colors ${
            mode === "complete"
              ? "border-primary bg-primary/10"
              : "border-border text-muted-foreground hover:text-foreground"
          } ${isFree ? "opacity-60 cursor-not-allowed hover:text-muted-foreground" : ""}`}
        >
          <div className="flex items-center justify-between gap-2">
            <p className="text-xs font-medium">
              {m.onboarding_mode_complete_title()}
            </p>
            {isFree && (
              <span className="flex items-center gap-1 text-[10px] font-medium text-muted-foreground">
                <Lock className="w-3 h-3" />
                {m.onboarding_mode_complete_locked()}
              </span>
            )}
          </div>
          <p className="text-[11px] mt-1">
            {m.onboarding_mode_complete_desc()}
          </p>
        </button>
      </div>
      {saving && (
        <Loader2 className="w-3.5 h-3.5 animate-spin text-muted-foreground" />
      )}
      {mode === "complete" && (
        <div className="space-y-2 pt-2 border-t max-h-64 overflow-y-auto pr-1">
          {SERVICE_FIELDS.map((cfg) =>
            preconfigured[cfg.service] ? (
              <div
                key={cfg.service}
                className="flex items-center justify-between gap-2 rounded border px-2.5 py-2"
              >
                <p className="text-xs font-medium">{cfg.title}</p>
                {preconfiguredTests[cfg.service] === undefined ? (
                  <span className="flex items-center gap-1 text-xs text-muted-foreground">
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    {m.onboarding_mode_testing()}
                  </span>
                ) : preconfiguredTests[cfg.service]?.ok ? (
                  <span className="flex items-center gap-1 text-xs text-green-600 dark:text-green-400">
                    <CheckCircle2 className="w-3.5 h-3.5" />
                    {m.onboarding_mode_already_configured()}
                    {preconfiguredTests[cfg.service]?.latency_ms !==
                      undefined && (
                      <span className="text-muted-foreground">
                        ({preconfiguredTests[cfg.service]?.latency_ms}ms)
                      </span>
                    )}
                  </span>
                ) : null}
              </div>
            ) : (
              <ServiceConnectionCard
                key={cfg.service}
                config={cfg}
                defaults={defaults[cfg.service]}
                onConnectedChange={handleConnectedChange}
              />
            ),
          )}
        </div>
      )}
      {mode === "complete" &&
        !SERVICE_FIELDS.every(
          (f) => preconfigured[f.service] || connected[f.service],
        ) && (
          <p className="text-xs text-amber-600 dark:text-amber-400">
            {m.onboarding_mode_validation_warning()}
          </p>
        )}
    </div>
  );
}

export function StepWorkspace(_props: StepProps) {
  return (
    <div className="space-y-3 py-2 text-sm text-muted-foreground">
      <p>{m.onboarding_workspace_body()}</p>
      <ul className="list-disc list-inside space-y-1 text-xs">
        <li>{m.onboarding_workspace_bullet_1()}</li>
        <li>{m.onboarding_workspace_bullet_2()}</li>
        <li>{m.onboarding_workspace_bullet_3()}</li>
        <li>{m.onboarding_workspace_bullet_sandbox()}</li>
      </ul>
    </div>
  );
}

export function StepWorkspaceSelect({ onWorkspaceSelect }: StepProps) {
  const workspaces = useWorkspacesStore((s) => s.workspaces);
  const activeId = useWorkspacesStore((s) => s.active_id);
  const hydrate = useWorkspacesStore((s) => s.hydrate);
  const [selected, setSelected] = useState<string | null>(null);
  const [trustOpen, setTrustOpen] = useState(false);

  useEffect(() => {
    void hydrate();
    // Sincroniza com o workspace ativo do store (sistema externo,
    // persistido) — não deriva de outro state local.
    // oxlint-disable-next-line react/set-state-in-effect
    setSelected(activeId ?? null);
  }, [hydrate, activeId]);

  function handleSelect(id: string | null) {
    setSelected(id);
    onWorkspaceSelect?.(id);
  }

  function handleTrustOpenChange(open: boolean) {
    setTrustOpen(open);
    if (!open) {
      // WorkspaceTrustDialog chama store.create() que seta active_id
      const newId = useWorkspacesStore.getState().active_id;
      if (newId) handleSelect(newId);
    }
  }

  return (
    <>
      {/* Div nativa em vez de ScrollArea (Radix): o wrapper interno do
          Viewport vira `display: table` e mede a largura pelo conteúdo mais
          largo — um path do Windows sem espaços/barras pra quebrar linha
          força a coluna inteira a crescer, vazando pra fora do modal. */}
      <div className="max-h-60 overflow-y-auto overflow-x-hidden">
        <div className="space-y-1 pr-3 py-2 w-full min-w-0">
          <button
            type="button"
            onClick={() => handleSelect(null)}
            className={`w-full flex items-start gap-3 rounded-md border px-3 py-2.5 text-left transition-colors ${
              selected === null
                ? "border-border/80 bg-muted/60"
                : "border-border hover:bg-muted/50"
            }`}
          >
            <FolderPlus className="w-4 h-4 mt-0.5 shrink-0 text-muted-foreground" />
            <span className="min-w-0 flex-1">
              <span className="block text-sm font-medium text-foreground">
                {m.new_chat_create_new()}
              </span>
              <span className="block text-xs text-muted-foreground mt-0.5">
                {m.new_chat_create_new_desc()}
              </span>
            </span>
            {selected === null && (
              <Check className="w-4 h-4 mt-0.5 shrink-0 text-foreground" />
            )}
          </button>

          {workspaces.length > 0 && (
            <p className="px-1 pt-2 pb-1 text-xs font-medium text-muted-foreground uppercase tracking-wide">
              {m.new_chat_existing_label()}
            </p>
          )}

          {workspaces.map((ws) => (
            <button
              key={ws.id}
              type="button"
              onClick={() => handleSelect(ws.id)}
              className={`w-full flex items-start gap-3 rounded-md border px-3 py-2.5 text-left transition-colors ${
                selected === ws.id
                  ? "border-border/80 bg-muted/60"
                  : "border-border hover:bg-muted/50"
              }`}
            >
              {ws.is_git_repo ? (
                <FolderGit2 className="w-4 h-4 mt-0.5 shrink-0 text-muted-foreground" />
              ) : (
                <FolderOpen className="w-4 h-4 mt-0.5 shrink-0 text-muted-foreground" />
              )}
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-medium text-foreground truncate">
                  {ws.name}
                </span>
                <span className="block text-xs text-muted-foreground truncate mt-0.5">
                  {ws.cwd}
                </span>
              </span>
              {selected === ws.id && (
                <Check className="w-4 h-4 mt-0.5 shrink-0 text-foreground" />
              )}
            </button>
          ))}

          <button
            type="button"
            onClick={() => setTrustOpen(true)}
            className="w-full flex items-center gap-2 px-3 py-2 text-sm text-foreground/80 hover:text-foreground hover:bg-muted/50 rounded-md transition-colors text-left mt-1"
          >
            <Plus className="w-4 h-4 shrink-0 text-muted-foreground" />
            {m.workspace_add_folder()}
          </button>
        </div>
      </div>

      <WorkspaceTrustDialog
        open={trustOpen}
        onOpenChange={handleTrustOpenChange}
      />
    </>
  );
}

// ---------------------------------------------------------------------------
// StepApiKeys — configuração de chaves Google, Cohere e Tavily
// ---------------------------------------------------------------------------

type KeyStatus = "idle" | "testing" | "ok" | "fail";

interface ApiKeyState {
  value: string;
  masked: string;
  configured: boolean;
  status: KeyStatus;
  error: string;
}

type ApiKeyProvider = "google" | "cohere" | "tavily";

const PROVIDERS: {
  id: ApiKeyProvider;
  label: () => string;
  desc: () => string;
  url: () => string;
  placeholder: string;
}[] = [
  {
    id: "google",
    label: m.onboarding_api_keys_google_label,
    desc: m.onboarding_api_keys_google_desc,
    url: m.onboarding_api_keys_google_url,
    placeholder: "AIza…",
  },
  {
    id: "cohere",
    label: m.onboarding_api_keys_cohere_label,
    desc: m.onboarding_api_keys_cohere_desc,
    url: m.onboarding_api_keys_cohere_url,
    placeholder: "…",
  },
  {
    id: "tavily",
    label: m.onboarding_api_keys_tavily_label,
    desc: m.onboarding_api_keys_tavily_desc,
    url: m.onboarding_api_keys_tavily_url,
    placeholder: "tvly-…",
  },
];

async function saveKey(id: ApiKeyProvider, value: string): Promise<void> {
  if (!value.trim()) return;
  try {
    await fetch("/admin/api-keys", {
      method: "PATCH",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ [`${id}_api_key`]: value.trim() }),
    });
  } catch {}
}

function KeyStatusIcon({ status }: { status: KeyStatus }) {
  if (status === "testing")
    return (
      <Loader2 className="w-3.5 h-3.5 animate-spin text-muted-foreground shrink-0" />
    );
  if (status === "ok")
    return <CheckCircle2 className="w-3.5 h-3.5 text-green-500 shrink-0" />;
  if (status === "fail")
    return <XCircle className="w-3.5 h-3.5 text-destructive shrink-0" />;
  return null;
}

export function StepApiKeys(_props: StepProps) {
  const [keys, setKeys] = useState<Record<ApiKeyProvider, ApiKeyState>>({
    google: {
      value: "",
      masked: "",
      configured: false,
      status: "idle",
      error: "",
    },
    cohere: {
      value: "",
      masked: "",
      configured: false,
      status: "idle",
      error: "",
    },
    tavily: {
      value: "",
      masked: "",
      configured: false,
      status: "idle",
      error: "",
    },
  });
  const [show, setShow] = useState<Record<ApiKeyProvider, boolean>>({
    google: false,
    cohere: false,
    tavily: false,
  });

  // Carrega valores pré-configurados e dispara teste automático para os que já existem.
  useEffect(() => {
    void (async () => {
      try {
        const res = await fetch("/admin/api-keys", { credentials: "include" });
        if (!res.ok) return;
        const data = (await res.json()) as Record<
          ApiKeyProvider,
          { configured: boolean; masked: string }
        >;
        const next = { ...keys };
        const toTest: ApiKeyProvider[] = [];
        for (const id of ["google", "cohere", "tavily"] as ApiKeyProvider[]) {
          if (data[id]?.configured) {
            next[id] = {
              ...next[id],
              // Popula `value` direto com a env real mascarada — o campo
              // mostra a chave já configurada em vez do placeholder
              // genérico (que só faz sentido quando não há nada salvo).
              value: data[id].masked,
              masked: data[id].masked,
              configured: true,
              status: "testing",
            };
            toTest.push(id);
          }
        }
        setKeys(next);
        // Testa as pré-configuradas com a chave real (não mascarada — backend testa com env atual).
        for (const id of toTest) {
          void testKey(id, "");
        }
      } catch {}
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function testKey(id: ApiKeyProvider, value: string): Promise<void> {
    setKeys((prev) => ({
      ...prev,
      [id]: { ...prev[id], status: "testing", error: "" },
    }));
    try {
      const res = await fetch("/admin/api-keys/test", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        // Valor vazio → backend usa a env já configurada (para pré-preenchidos).
        body: JSON.stringify({
          provider: id,
          api_key: value.trim() || "__use_env__",
        }),
      });
      const data = (await res.json()) as { ok: boolean; error?: string };
      setKeys((prev) => ({
        ...prev,
        [id]: {
          ...prev[id],
          status: data.ok ? "ok" : "fail",
          error: data.error ?? "",
        },
      }));
    } catch (err) {
      setKeys((prev) => ({
        ...prev,
        [id]: { ...prev[id], status: "fail", error: String(err) },
      }));
    }
  }

  async function handleBlur(id: ApiKeyProvider, value: string): Promise<void> {
    const trimmed = value.trim();
    if (!trimmed) return;
    // Campo ainda mostra a env mascarada (usuário não editou) — não
    // sobrescreve a chave real salva com o texto mascarado exibido.
    if (trimmed === keys[id].masked) return;
    await saveKey(id, trimmed);
    await testKey(id, trimmed);
  }

  return (
    <div className="space-y-2.5 py-2">
      <p className="text-xs text-muted-foreground">
        {m.onboarding_api_keys_body()}
      </p>
      <p className="text-[11px] text-muted-foreground/80 leading-snug rounded-md border border-border/60 bg-muted/30 px-2 py-1.5">
        {m.onboarding_api_keys_ollama_hint()}
      </p>

      {PROVIDERS.map((prov) => {
        const state = keys[prov.id];
        const isVisible = show[prov.id];
        return (
          <div key={prov.id} className="space-y-0.5">
            <div className="flex items-center justify-between gap-2">
              <label className="text-xs font-medium text-foreground">
                {prov.label()}
              </label>
              <div className="flex items-center gap-1.5">
                <KeyStatusIcon status={state.status} />
                {state.status === "ok" && (
                  <span className="text-xs text-green-500">
                    {m.onboarding_api_keys_ok()}
                  </span>
                )}
                {state.status === "fail" && (
                  <span className="text-xs text-destructive">
                    {m.onboarding_api_keys_fail()}
                  </span>
                )}
                {state.status === "testing" && (
                  <span className="text-xs text-muted-foreground">
                    {m.onboarding_api_keys_testing()}
                  </span>
                )}
                <a
                  href={prov.url()}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-xs text-primary hover:underline"
                >
                  {m.onboarding_api_keys_get_key()}
                </a>
              </div>
            </div>
            <p className="text-[11px] text-muted-foreground leading-snug">
              {prov.desc()}
            </p>
            <div className="flex gap-1.5">
              <Input
                type={isVisible ? "text" : "password"}
                value={state.value}
                placeholder={prov.placeholder}
                className="h-7 text-xs font-mono flex-1"
                autoComplete="off"
                onChange={(e) =>
                  setKeys((prev) => ({
                    ...prev,
                    [prov.id]: {
                      ...prev[prov.id],
                      value: e.target.value,
                      status: "idle",
                    },
                  }))
                }
                onBlur={(e) => void handleBlur(prov.id, e.target.value)}
              />
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="h-7 px-2"
                onClick={() =>
                  setShow((prev) => ({ ...prev, [prov.id]: !prev[prov.id] }))
                }
              >
                {isVisible
                  ? m.onboarding_token_hide()
                  : m.onboarding_token_show()}
              </Button>
            </div>
            {state.status === "fail" && state.error && (
              <p className="text-xs text-destructive line-clamp-2">
                {state.error}
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}

export function StepMemory(_props: StepProps) {
  return (
    <div className="space-y-4 py-2 text-sm text-muted-foreground">
      <p>{m.onboarding_memory_intro()}</p>
      <div className="space-y-3">
        <div>
          <p className="text-xs font-medium text-foreground">
            {m.onboarding_memory_conversation_title()}
          </p>
          <p className="text-xs">{m.onboarding_memory_conversation_body()}</p>
        </div>
        <div>
          <p className="text-xs font-medium text-foreground">
            {m.onboarding_memory_remember_title()}
          </p>
          <p className="text-xs">{m.onboarding_memory_remember_body()}</p>
        </div>
        <div>
          <p className="text-xs font-medium text-foreground">
            {m.onboarding_memory_rag_title()}
          </p>
          <p className="text-xs">{m.onboarding_memory_rag_body()}</p>
        </div>
      </div>
    </div>
  );
}

export function StepCapabilities(_props: StepProps) {
  return (
    <div className="space-y-3 py-2 text-sm text-muted-foreground">
      <p>{m.onboarding_capabilities_intro()}</p>
      <ul className="list-disc list-inside space-y-1 text-xs">
        <li>{m.onboarding_capabilities_bullet_files()}</li>
        <li>{m.onboarding_capabilities_bullet_git()}</li>
        <li>{m.onboarding_capabilities_bullet_browser()}</li>
        <li>{m.onboarding_capabilities_bullet_library()}</li>
        <li>{m.onboarding_capabilities_bullet_schedule()}</li>
      </ul>
    </div>
  );
}

export function StepDone(_props: StepProps) {
  return (
    <div className="flex flex-col items-center gap-3 py-4 text-center">
      <span className="text-4xl">🎉</span>
      <p className="text-sm text-muted-foreground max-w-xs">
        {m.onboarding_done_body()}
      </p>
    </div>
  );
}

/** Passos compartilhados (Local e VPS) após o gate de identidade/modo do
 * PreAuthWizard — StepWelcome/StepLanguage não entram aqui: já cobertos
 * pelo passo "identity" do PreAuthWizard. */
export const ONBOARDING_CONTINUATION_STEPS = [
  StepToken,
  StepMode,
  StepApiKeys,
  StepWorkspace,
  StepWorkspaceSelect,
  StepMemory,
  StepCapabilities,
  StepDone,
];

export const ONBOARDING_CONTINUATION_TITLE_KEYS = [
  "onboarding.step3_title",
  "onboarding.step4_title",
  "onboarding.step4b_title",
  "onboarding.step5_title",
  "onboarding.workspace_select_title",
  "onboarding.step6_title",
  "onboarding.step_capabilities_title",
  "onboarding.step7_title",
] as const;

// ===========================================================================
// Step indicator — bolinhas de progresso conectadas por "ganchos"
// ===========================================================================

export function StepIndicator({
  step,
  total,
}: {
  step: number;
  total: number;
}) {
  return (
    <div className="flex items-center justify-center gap-1.5 py-1">
      {Array.from({ length: total }).map((_, i) => (
        <div key={i} className="flex items-center">
          <div
            className={`h-2 rounded-full transition-all duration-300 ${
              i === step
                ? "w-5 bg-primary"
                : i < step
                  ? "w-2 bg-primary/50"
                  : "w-2 bg-border"
            }`}
          />
          {i < total - 1 && (
            <div
              className={`h-px w-5 transition-colors duration-300 ${
                i < step ? "bg-primary/50" : "bg-border"
              }`}
            />
          )}
        </div>
      ))}
    </div>
  );
}
