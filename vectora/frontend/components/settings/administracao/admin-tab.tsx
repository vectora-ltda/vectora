"use client";

/**
 * Painéis de administração (root/admin only), um componente exportado por
 * categoria do SettingsOverlay — ver o topo do arquivo, seção "As 5
 * sub-abas de Administração", pra por que não há mais um wrapper único.
 *
 * - UsersPanel: lista usuários, muda role, deleta, convida
 * - ToolsPanel: habilita/desabilita tools globalmente
 * - SafeRootsPanel: pastas seguras (SafeRoot)
 * - SystemPanel: versão, serviços, métricas + configuração do servidor
 * - StoragePanel: modo lite/completo, status/config dos backends
 */

import {
  Check,
  CheckCircle2,
  Copy,
  Database,
  FolderLock,
  FolderOpen,
  HardDrive,
  Loader2,
  Pencil,
  RefreshCw,
  RotateCcw,
  Trash2,
  UserPlus,
  Wrench,
  XCircle,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";

const DASHBOARD_URL = "https://vectora.company/dashboard";
const TOKEN_ENV_NAME = "VECTORA_TOKEN";
const LATENCY_UNIT = "ms";
const BASIC_ROLES = ["admin", "member", "viewer"] as const;
const ALL_ROLES = ["root", "admin", "member", "viewer"] as const;

import { useLicenseStatus } from "@/lib/hooks/use-license-status";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { m } from "@/lib/paraglide/messages";

// ---------------------------------------------------------------------------
// Tipos
// ---------------------------------------------------------------------------

interface AdminUser {
  id: string;
  email: string;
  role: string;
  created_at: string;
  last_login_at: string | null;
}

interface PendingInvite {
  token_hash: string;
  email: string | null;
  role: string;
  created_by: string | null;
  expires_at: string;
  created_at: string;
}

interface AdminTool {
  name: string;
  description: string;
  category: string;
  destructive: boolean;
  enabled: boolean;
}

interface SystemInfo {
  version: string;
  python_version: string;
  platform: string;
  services: Record<string, string>;
  recent_spans_count: number;
}

interface ServerConfig {
  allow_public_signup: boolean;
  db_dsn: string;
  vectora_token_masked: string;
  vectora_token_configured: boolean;
}

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

const api = {
  users: {
    list: () => fetch("/admin/users").then((r) => r.json()),
    updateRole: (id: string, role: string) =>
      fetch(`/admin/users/${id}/role`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role }),
      }),
    delete: (id: string) => fetch(`/admin/users/${id}`, { method: "DELETE" }),
  },
  tools: {
    list: () => fetch("/admin/tools").then((r) => r.json()),
    toggle: (name: string, enabled: boolean) =>
      fetch(`/admin/tools/${name}/toggle`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      }),
  },
  invites: {
    list: () => fetch("/admin/invites").then((r) => r.json()),
    create: (body: { role: string; email?: string; ttl_hours: number }) =>
      fetch("/admin/invites", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }).then((r) => r.json()),
    revoke: (tokenHash: string) =>
      fetch(`/admin/invites/${tokenHash}`, { method: "DELETE" }),
  },
  system: () => fetch("/admin/system").then((r) => r.json()),
  config: {
    get: () => fetch("/admin/config").then((r) => r.json()),
    patch: (body: Partial<ServerConfig> & { vectora_token?: string }) =>
      fetch("/admin/config", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),
  },
};

// ---------------------------------------------------------------------------
// Sub-aba: Usuários
// ---------------------------------------------------------------------------

function InvitesSection() {
  const [invites, setInvites] = useState<PendingInvite[]>([]);
  const [open, setOpen] = useState(false);
  const [role, setRole] = useState("member");
  const [email, setEmail] = useState("");
  const [ttl, setTtl] = useState(24);
  const [creating, setCreating] = useState(false);
  const [link, setLink] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await api.invites.list();
      setInvites(data.invites ?? []);
    } catch {
      // silencioso
    }
  }, []);

  useEffect(() => {
    // Busca a lista de convites no backend ao montar.
    // oxlint-disable-next-line react/set-state-in-effect
    void load();
  }, [load]);

  const handleCreate = async () => {
    setCreating(true);
    setError(null);
    setLink(null);
    try {
      const data = await api.invites.create({
        role,
        email: email.trim() || undefined,
        ttl_hours: ttl,
      });
      if (data?.url) {
        setLink(data.url);
        await load();
      } else {
        setError(m.invite_error_create());
      }
    } catch {
      setError(m.invite_error_create());
    } finally {
      setCreating(false);
    }
  };

  const handleCopy = async () => {
    if (!link) return;
    await navigator.clipboard.writeText(link);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleRevoke = async (tokenHash: string) => {
    await api.invites.revoke(tokenHash);
    setInvites((prev) => prev.filter((i) => i.token_hash !== tokenHash));
  };

  return (
    <div className="rounded-lg border bg-card/50 p-3 space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium flex items-center gap-1.5">
          <UserPlus className="w-3.5 h-3.5 text-muted-foreground" />
          {m.invite_title()}
        </span>
        <Button
          variant="ghost"
          size="sm"
          className="h-7 text-xs"
          onClick={() => {
            setOpen((o) => !o);
            setLink(null);
            setError(null);
          }}
        >
          {m.invite_title()}
        </Button>
      </div>

      {open && (
        <div className="space-y-2 border-t pt-3">
          <div className="grid grid-cols-3 gap-2">
            <div className="space-y-1">
              <label className="text-[10px] text-muted-foreground">
                {m.invite_role_label()}
              </label>
              <Select value={role} onValueChange={setRole}>
                <SelectTrigger className="h-7 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {BASIC_ROLES.map((role) => (
                    <SelectItem key={role} value={role}>
                      {role}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1 col-span-1">
              <label className="text-[10px] text-muted-foreground">
                {m.invite_ttl_label()}
              </label>
              <Input
                type="number"
                value={ttl}
                onChange={(e) => setTtl(parseInt(e.target.value) || 24)}
                autoComplete="off"
                className="h-7 text-xs"
                min={1}
                max={720}
              />
            </div>
            <div className="space-y-1">
              <label className="text-[10px] text-muted-foreground">
                {m.invite_email_label()}
              </label>
              <Input
                type="email"
                autoComplete="off"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="h-7 text-xs"
                placeholder="—"
              />
            </div>
          </div>

          <Button
            size="sm"
            className="h-7 text-xs"
            onClick={handleCreate}
            disabled={creating}
          >
            {creating ? (
              <Loader2 className="w-3 h-3 animate-spin mr-1.5" />
            ) : null}
            {m.invite_create()}
          </Button>

          {error && <p className="text-xs text-destructive">{error}</p>}

          {link && (
            <div className="flex items-center gap-1.5 rounded-md border bg-background p-1.5">
              <span className="text-[10px] font-mono truncate flex-1">
                {link}
              </span>
              <Button
                variant="ghost"
                size="sm"
                className="h-6 w-6 p-0 shrink-0"
                onClick={handleCopy}
              >
                {copied ? (
                  <Check className="w-3.5 h-3.5 text-green-500" />
                ) : (
                  <Copy className="w-3.5 h-3.5" />
                )}
              </Button>
            </div>
          )}
        </div>
      )}

      {/* Convites pendentes */}
      <div className="space-y-1">
        <p className="text-[10px] text-muted-foreground uppercase tracking-wide">
          {m.invite_pending()}
        </p>
        {invites.length === 0 ? (
          <p className="text-xs text-muted-foreground">{m.invite_none()}</p>
        ) : (
          invites.map((inv) => (
            <div
              key={inv.token_hash}
              className="flex items-center gap-2 px-2 py-1.5 rounded-md border text-xs"
            >
              <Badge variant="secondary" className="text-[9px] h-4">
                {inv.role}
              </Badge>
              <span className="flex-1 truncate text-muted-foreground">
                {inv.email || "—"} · {m.invite_expires()}{" "}
                {new Date(inv.expires_at).toLocaleString("pt-BR")}
              </span>
              <Button
                variant="ghost"
                size="sm"
                className="h-6 w-6 p-0 text-muted-foreground hover:text-destructive"
                onClick={() => void handleRevoke(inv.token_hash)}
                title={m.invite_revoke()}
              >
                <Trash2 className="w-3.5 h-3.5" />
              </Button>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function UserToolsRow({ userId }: { userId: string }) {
  const [available, setAvailable] = useState<string[]>([]);
  const [disabled, setDisabled] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch(`/admin/users/${userId}/tools`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (cancelled || !d) return;
        setAvailable(d.available ?? []);
        setDisabled(new Set(d.disabled ?? []));
      })
      .finally(() => setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [userId]);

  const toggle = (name: string) => {
    setDisabled((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
    setSaved(false);
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await fetch(`/admin/users/${userId}/tools`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ disabled: [...disabled] }),
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="px-3 py-2">
        <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="rounded-lg border bg-card/50 -mt-1 p-3 space-y-2">
      <div className="max-h-48 overflow-y-auto divide-y divide-border/60 rounded-md border">
        {available.map((name) => (
          <div
            key={name}
            className="flex items-center justify-between px-2.5 py-1.5"
          >
            <span className="text-[11px] font-mono">{name}</span>
            <Switch
              checked={!disabled.has(name)}
              onCheckedChange={() => toggle(name)}
            />
          </div>
        ))}
      </div>
      <div className="flex items-center justify-end gap-2">
        {saved && (
          <span className="text-[10px] text-green-500">
            {m.toolpolicy_saved()}
          </span>
        )}
        <Button
          size="sm"
          className="h-7 text-xs"
          onClick={handleSave}
          disabled={saving}
        >
          {saving && <Loader2 className="w-3 h-3 animate-spin mr-1.5" />}
          {m.toolpolicy_save()}
        </Button>
      </div>
    </div>
  );
}

export function UsersPanel() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [updating, setUpdating] = useState<string | null>(null);
  const [openTools, setOpenTools] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.users.list();
      setUsers(data.users ?? []);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Busca a lista de usuários no backend ao montar.
    // oxlint-disable-next-line react/set-state-in-effect
    void load();
  }, [load]);

  const handleRoleChange = async (userId: string, role: string) => {
    setUpdating(userId);
    try {
      await api.users.updateRole(userId, role);
      setUsers((prev) =>
        prev.map((u) => (u.id === userId ? { ...u, role } : u)),
      );
    } finally {
      setUpdating(null);
    }
  };

  const handleDelete = async (userId: string) => {
    if (!confirm(m.admin_users_confirm_delete())) return;
    await api.users.delete(userId);
    setUsers((prev) => prev.filter((u) => u.id !== userId));
  };

  if (loading) {
    return (
      <div className="flex justify-center py-8">
        <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <InvitesSection />
      <p className="text-xs text-muted-foreground mb-3">
        {m.admin_users_total({ count: users.length })}
      </p>
      {users.map((u) => (
        <div key={u.id} className="space-y-1">
          <div className="flex items-center gap-3 p-2.5 rounded-lg border bg-card">
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium truncate">{u.email}</p>
              <p className="text-[10px] text-muted-foreground">
                {m.admin_users_since()}{" "}
                {new Date(u.created_at).toLocaleDateString("pt-BR")}
                {u.last_login_at &&
                  ` · ${m.admin_users_last_login()} ${new Date(u.last_login_at).toLocaleDateString("pt-BR")}`}
              </p>
            </div>

            <Select
              value={u.role}
              onValueChange={(role) => void handleRoleChange(u.id, role)}
              disabled={updating === u.id}
            >
              <SelectTrigger className="h-7 w-24 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ALL_ROLES.map((role) => (
                  <SelectItem key={role} value={role}>
                    {role}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Button
              variant="ghost"
              size="sm"
              className="h-7 w-7 p-0 text-muted-foreground hover:text-foreground"
              onClick={() => setOpenTools(openTools === u.id ? null : u.id)}
              title={m.admin_tab_tools()}
            >
              <Wrench className="w-3.5 h-3.5" />
            </Button>

            <Button
              variant="ghost"
              size="sm"
              className="h-7 w-7 p-0 text-muted-foreground hover:text-destructive"
              onClick={() => void handleDelete(u.id)}
            >
              <Trash2 className="w-3.5 h-3.5" />
            </Button>
          </div>
          {openTools === u.id && <UserToolsRow userId={u.id} />}
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-aba: Ferramentas
// ---------------------------------------------------------------------------

//: Altura de linha estimada (nome+badge + descrição truncada + padding) —
//: o virtualizer corrige com `measureElement` real depois do 1º paint;
//: só precisa ser próximo o bastante pra `getTotalSize()` inicial não
//: pular visivelmente.
const TOOL_ROW_ESTIMATE_PX = 58;

export function ToolsPanel() {
  const [tools, setTools] = useState<AdminTool[]>([]);
  const [loading, setLoading] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setLoading(true);
    api.tools
      .list()
      .then((d) => setTools(d.tools ?? []))
      .finally(() => setLoading(false));
  }, []);

  // Lista real: 166 tools nativas registradas — sem virtualização, isso é
  // 166 nós DOM montados de uma vez toda vez que a categoria abre.
  //
  // `getVirtualItems`/`getTotalSize`/`measureElement` do react-virtual não
  // podem ser memoizados com segurança (mesmo diagnóstico já investigado e
  // suprimido em message-list.tsx) — projeto não usa React Compiler, e os
  // valores só são lidos dentro deste componente, nunca passados adiante
  // como prop memoizada. Não é bug vivo, é diagnóstico de ferramenta sobre
  // um padrão já seguro aqui.
  // oxlint-disable-next-line react/incompatible-library
  const virtualizer = useVirtualizer({
    count: tools.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => TOOL_ROW_ESTIMATE_PX,
    overscan: 6,
  });

  const handleToggle = async (name: string, enabled: boolean) => {
    await api.tools.toggle(name, enabled);
    setTools((prev) =>
      prev.map((t) => (t.name === name ? { ...t, enabled } : t)),
    );
  };

  if (loading) {
    return (
      <div className="flex justify-center py-8">
        <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div ref={scrollRef} className="h-[65vh] overflow-y-auto custom-scrollbar">
      <div
        style={{
          height: `${virtualizer.getTotalSize()}px`,
          position: "relative",
        }}
      >
        {virtualizer.getVirtualItems().map((vItem) => {
          const t = tools[vItem.index]!;
          return (
            <div
              key={t.name}
              data-index={vItem.index}
              ref={virtualizer.measureElement}
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                width: "100%",
                transform: `translateY(${vItem.start}px)`,
              }}
              className="pb-1.5"
            >
              <div className="flex items-center gap-3 p-2.5 rounded-lg border bg-card">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5">
                    <span className="text-xs font-mono font-medium">
                      {t.name}
                    </span>
                    {t.destructive && (
                      <Badge
                        variant="destructive"
                        className="text-[9px] h-3.5 px-1"
                      >
                        {m.admin_tools_destructive()}
                      </Badge>
                    )}
                  </div>
                  <p className="text-[10px] text-muted-foreground truncate">
                    {t.description}
                  </p>
                </div>
                <Switch
                  checked={t.enabled}
                  onCheckedChange={(v) => void handleToggle(t.name, v)}
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-aba: Sistema
// ---------------------------------------------------------------------------

export function SystemPanel() {
  const [info, setInfo] = useState<SystemInfo | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .system()
      .then(setInfo)
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="flex justify-center py-8">
        <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
      </div>
    );
  }
  if (!info) return null;

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2">
        {[
          [m.admin_system_label_version(), info.version],
          [m.admin_system_label_platform(), info.platform],
          [m.admin_system_label_python(), info.python_version.split(" ")[0]],
          [m.admin_system_label_spans(), String(info.recent_spans_count)],
        ].map(([label, value]) => (
          <div key={label} className="rounded-lg border bg-card p-2.5">
            <p className="text-[10px] text-muted-foreground">{label}</p>
            <p className="text-xs font-medium truncate">{value}</p>
          </div>
        ))}
      </div>

      <div className="pt-3 border-t space-y-3">
        <p className="text-xs font-medium">{m.admin_system_config_heading()}</p>
        <ConfigSection />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Seção: Configuração (renderizada dentro de SystemPanel)
// ---------------------------------------------------------------------------

function ConfigSection() {
  const [config, setConfig] = useState<ServerConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  // Input controlado para VECTORA_TOKEN: o backend nunca devolve o token
  // em claro, então mantemos um buffer local. Vazio = não-alterar.
  const [tokenInput, setTokenInput] = useState("");
  const [showToken, setShowToken] = useState(false);

  useEffect(() => {
    api.config
      .get()
      .then(setConfig)
      .finally(() => setLoading(false));
  }, []);

  const handleSave = async () => {
    if (!config) return;
    setSaving(true);
    try {
      await api.config.patch({
        allow_public_signup: config.allow_public_signup,
        ...(tokenInput.trim() ? { vectora_token: tokenInput.trim() } : {}),
      });
      // Re-fetch para refletir o masked atualizado.
      if (tokenInput.trim()) {
        const fresh = await api.config.get();
        setConfig(fresh);
        setTokenInput("");
      }
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } finally {
      setSaving(false);
    }
  };

  if (loading || !config) {
    return (
      <div className="flex justify-center py-8">
        <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="space-y-1.5 pb-3 border-b">
        <div className="flex items-center gap-2">
          <p className="text-sm font-medium">{TOKEN_ENV_NAME}</p>
          {config.vectora_token_configured ? (
            <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border border-emerald-500/30">
              {m.admin_config_token_configured()}
            </span>
          ) : (
            <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-orange-500/15 text-orange-700 dark:text-orange-300 border border-orange-500/30">
              {m.admin_config_token_absent()}
            </span>
          )}
        </div>
        <p className="text-xs text-muted-foreground">
          {m.admin_config_token_desc()}{" "}
          <a
            href={DASHBOARD_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="text-primary hover:underline"
          >
            {DASHBOARD_URL.replace("https://", "")}
          </a>
          .
        </p>
        {config.vectora_token_configured && (
          <p className="text-xs text-muted-foreground font-mono">
            {m.admin_config_token_current()} {config.vectora_token_masked}
          </p>
        )}
        <div className="flex gap-1.5">
          <Input
            type={showToken ? "text" : "password"}
            value={tokenInput}
            onChange={(e) => setTokenInput(e.target.value)}
            placeholder={
              config.vectora_token_configured
                ? m.admin_config_token_placeholder_replace()
                : "vct_…"
            }
            className="h-8 text-xs font-mono flex-1"
            autoComplete="off"
          />
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-8 px-2"
            onClick={() => setShowToken((v) => !v)}
            aria-label={
              showToken
                ? m.admin_config_token_hide_aria()
                : m.admin_config_token_show_aria()
            }
          >
            {showToken
              ? m.admin_config_token_hide()
              : m.admin_config_token_show()}
          </Button>
        </div>
      </div>

      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm font-medium">{m.admin_config_signup_title()}</p>
          <p className="text-xs text-muted-foreground">
            {m.admin_config_signup_desc()}
          </p>
        </div>
        <Switch
          checked={config.allow_public_signup}
          onCheckedChange={(v) =>
            setConfig((prev) => prev && { ...prev, allow_public_signup: v })
          }
        />
      </div>

      <Button size="sm" onClick={handleSave} disabled={saving}>
        {saving ? <Loader2 className="w-3 h-3 animate-spin mr-1.5" /> : null}
        {saved ? m.admin_config_saved() : m.toolpolicy_save()}
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Pastas Seguras (SafeRoot)
// ---------------------------------------------------------------------------

interface SafeRootRow {
  id: string;
  path: string;
  label: string;
  builtin: boolean;
  created_at: string;
  created_by: string;
  archived_at: string | null;
}

export function SafeRootsPanel() {
  const [roots, setRoots] = useState<SafeRootRow[]>([]);
  // Só existe no desktop — no web a bridge inteira é undefined.
  const pickDirectory = window.vectora?.pickDirectory;
  const [loading, setLoading] = useState(true);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editLabel, setEditLabel] = useState("");
  const [newPath, setNewPath] = useState("");
  const [newLabel, setNewLabel] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [removeConfirmId, setRemoveConfirmId] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/admin/safe-roots");
      if (res.ok) {
        const data = await res.json();
        setRoots(data.roots ?? []);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Busca a lista de safe roots no backend ao montar.
    // oxlint-disable-next-line react/set-state-in-effect
    void reload();
  }, [reload]);

  const handleAdd = async () => {
    if (!newPath.trim()) return;
    setCreating(true);
    setError(null);
    try {
      const res = await fetch("/admin/safe-roots", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          path: newPath.trim(),
          label: newLabel.trim(),
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(
          data.detail ?? m.admin_saferoots_add_failed({ status: res.status }),
        );
      } else {
        setNewPath("");
        setNewLabel("");
        await reload();
      }
    } finally {
      setCreating(false);
    }
  };

  const handleSaveLabel = async (id: string) => {
    if (!editLabel.trim()) {
      setEditingId(null);
      return;
    }
    await fetch(`/admin/safe-roots/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ label: editLabel.trim() }),
    });
    setEditingId(null);
    await reload();
  };

  const handleRemove = (id: string) => {
    setRemoveConfirmId(id);
  };

  const handleRemoveConfirmed = async () => {
    if (!removeConfirmId) return;
    const id = removeConfirmId;
    setRemoveConfirmId(null);
    await fetch(`/admin/safe-roots/${id}`, { method: "DELETE" });
    await reload();
  };

  const handleRestore = async (id: string) => {
    await fetch(`/admin/safe-roots/${id}/restore`, { method: "POST" });
    await reload();
  };

  const handleBrowse = async () => {
    if (!pickDirectory) return;
    const picked = await pickDirectory();
    // `null` é cancelamento: preservar o que já estava digitado.
    if (picked) setNewPath(picked);
  };

  return (
    <div className="space-y-4">
      <div className="text-xs text-muted-foreground">
        {m.admin_saferoots_desc()}
      </div>

      {/* Adicionar nova */}
      <div className="rounded-md border border-border/60 p-3 space-y-2">
        <div className="text-xs font-medium text-foreground">
          {m.admin_saferoots_add_title()}
        </div>
        <div className="flex flex-col sm:flex-row gap-2">
          <div className="flex flex-1 gap-1.5 min-w-0">
            <Input
              value={newPath}
              onChange={(e) => setNewPath(e.target.value)}
              placeholder={m.admin_saferoots_path_placeholder()}
              autoComplete="off"
              className="font-mono text-xs"
            />
            {/* Só no desktop: no modo web não há bridge, e o campo de texto
             * continua sendo o único caminho. */}
            {pickDirectory && (
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="shrink-0"
                aria-label={m.admin_saferoots_browse()}
                title={m.admin_saferoots_browse()}
                onClick={handleBrowse}
              >
                <FolderOpen className="w-3.5 h-3.5" />
              </Button>
            )}
          </div>
          <Input
            value={newLabel}
            onChange={(e) => setNewLabel(e.target.value)}
            placeholder={m.admin_saferoots_label_placeholder()}
            autoComplete="off"
            className="text-xs sm:w-48"
          />
          <Button
            size="sm"
            onClick={handleAdd}
            disabled={creating || !newPath.trim()}
          >
            {creating ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              m.admin_saferoots_add_button()
            )}
          </Button>
        </div>
        {error && <div className="text-xs text-destructive">{error}</div>}
      </div>

      {/* Lista */}
      {loading ? (
        <div className="text-xs text-muted-foreground flex items-center gap-2">
          <Loader2 className="w-3.5 h-3.5 animate-spin" /> {m.admin_loading()}
        </div>
      ) : roots.length === 0 ? (
        <div className="text-xs text-muted-foreground">
          {m.admin_saferoots_empty()}
        </div>
      ) : (
        <div className="rounded-md border border-border/60 divide-y divide-border/60">
          {roots.map((r) => (
            <div
              key={r.id}
              className={`flex items-center gap-2 px-3 py-2 text-xs ${r.archived_at ? "opacity-60" : ""}`}
              data-testid={`safe-root-${r.id}`}
            >
              <FolderLock className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
              <div className="flex-1 min-w-0">
                {editingId === r.id ? (
                  <Input
                    value={editLabel}
                    onChange={(e) => setEditLabel(e.target.value)}
                    onBlur={() => handleSaveLabel(r.id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void handleSaveLabel(r.id);
                      if (e.key === "Escape") setEditingId(null);
                    }}
                    autoComplete="off"
                    autoFocus
                    className="h-6 text-xs"
                  />
                ) : (
                  <div className="font-medium text-foreground flex items-center gap-1.5">
                    {r.label}
                    {r.builtin && (
                      <Badge variant="secondary" className="text-[10px] py-0">
                        {m.admin_saferoots_builtin_badge()}
                      </Badge>
                    )}
                    {r.archived_at && (
                      <Badge variant="outline" className="text-[10px] py-0">
                        {m.admin_saferoots_archived_badge()}
                      </Badge>
                    )}
                  </div>
                )}
                <div className="font-mono text-[10px] text-muted-foreground truncate">
                  {r.path}
                </div>
              </div>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  setEditingId(r.id);
                  setEditLabel(r.label);
                }}
                title={m.admin_saferoots_rename_title()}
                disabled={Boolean(r.archived_at)}
              >
                <Pencil className="w-3.5 h-3.5" />
              </Button>
              {r.archived_at ? (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => void handleRestore(r.id)}
                  title={m.admin_saferoots_restore_title()}
                  aria-label={m.admin_saferoots_restore_title()}
                >
                  <RotateCcw className="w-3.5 h-3.5" />
                </Button>
              ) : (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => handleRemove(r.id)}
                  disabled={r.builtin}
                  title={
                    r.builtin
                      ? m.admin_saferoots_builtin_no_remove()
                      : m.admin_saferoots_remove_title()
                  }
                  className="text-destructive hover:text-destructive disabled:opacity-30"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </Button>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Confirmação de remoção de safe-root (Radix Dialog) */}
      <ConfirmDialog
        open={removeConfirmId !== null}
        title={m.admin_saferoots_confirm_title()}
        description={m.admin_saferoots_confirm_desc()}
        confirmLabel={m.admin_saferoots_remove_title()}
        variant="destructive"
        onConfirm={handleRemoveConfirmed}
        onCancel={() => setRemoveConfirmId(null)}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// StoragePanel
// ---------------------------------------------------------------------------

interface StorageBackendStatus {
  ok: boolean | null;
  error?: string | null;
  tables?: string[];
  latency_ms?: number;
  internal?: boolean;
}

interface StorageConfigSummary {
  storage_mode: "lite" | "complete";
  postgres_configured: boolean;
  redis_configured: boolean;
  qdrant_configured: boolean;
}

interface StorageHealth {
  checkpointer?: StorageBackendStatus;
  store?: StorageBackendStatus;
  lancedb?: StorageBackendStatus;
  postgres?: StorageBackendStatus;
  redis?: StorageBackendStatus;
  config?: StorageConfigSummary;
}

function StorageStatusBadge({ status }: { status: StorageBackendStatus }) {
  if (status.ok === null || status.ok === undefined) {
    return (
      <span className="text-xs text-muted-foreground">
        {m.admin_storage_not_configured()}
      </span>
    );
  }
  // Backends internos (SQLite/LanceDB locais) não têm conexão externa para
  // "estabelecer" — rotular como local em vez de prometer um OK de conexão.
  if (status.internal) {
    if (status.ok) {
      return (
        <span className="flex items-center gap-1 text-xs text-muted-foreground">
          <HardDrive className="w-3.5 h-3.5" />
          {m.admin_storage_local()}
        </span>
      );
    }
    return (
      <span className="flex items-center gap-1 text-xs text-destructive">
        <XCircle className="w-3.5 h-3.5" />
        {status.error ?? m.admin_storage_error()}
      </span>
    );
  }
  if (status.ok) {
    return (
      <span className="flex items-center gap-1 text-xs text-green-600">
        <CheckCircle2 className="w-3.5 h-3.5" />
        {m.admin_storage_connected()}
        {status.latency_ms !== undefined && (
          <span className="text-muted-foreground">
            ({status.latency_ms}
            {LATENCY_UNIT})
          </span>
        )}
      </span>
    );
  }
  return (
    <span className="flex items-center gap-1 text-xs text-destructive">
      <XCircle className="w-3.5 h-3.5" />
      {status.error ?? m.admin_storage_error()}
    </span>
  );
}

/** Resultado de `POST /admin/storage/test`. */
interface StorageTestResult {
  ok: boolean;
  error?: string;
  latency_ms?: number;
}

function StorageTestResultLine({ result }: { result: StorageTestResult }) {
  return (
    <div
      className={`text-xs flex items-center gap-1 ${result.ok ? "text-green-600" : "text-destructive"}`}
    >
      {result.ok ? (
        <>
          <CheckCircle2 className="w-3.5 h-3.5" />
          {m.admin_storage_test_ok()}
          {result.latency_ms !== undefined && (
            <span className="text-muted-foreground">
              ({result.latency_ms}
              {LATENCY_UNIT})
            </span>
          )}
        </>
      ) : (
        <>
          <XCircle className="w-3.5 h-3.5" />
          {result.error ?? m.admin_storage_test_fail()}
        </>
      )}
    </div>
  );
}

/**
 * Card de configuração de um backend "completo" (Postgres/Redis/Qdrant):
 * inputs para os campos da PATCH, botão "Testar" (POST /admin/storage/test)
 * e botão "Salvar" (PATCH /admin/storage). Off por padrão — só relevante no
 * modo "complete".
 */
function BackendConfigCard({
  title,
  status,
  fields,
  testBackend,
  onSave,
  disabled = false,
}: {
  title: string;
  status?: StorageBackendStatus;
  fields: {
    key: string;
    testKey: string;
    placeholder: string;
    type?: string;
  }[];
  testBackend: string;
  onSave: (values: Record<string, string>) => Promise<void>;
  /** Sem licença Pro os campos ficam só de leitura: o backend recusa o
   * PATCH com 402, então deixar digitar seria oferecer um caminho morto. */
  disabled?: boolean;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [testResult, setTestResult] = useState<StorageTestResult | null>(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);

  const hasInput = fields.some((f) => values[f.key]?.trim());

  const handleTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const body: Record<string, string> = { backend: testBackend };
      for (const f of fields) {
        const v = values[f.key]?.trim();
        if (v) body[f.testKey] = v;
      }
      const res = await fetch("/admin/storage/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify(body),
      });
      setTestResult(await res.json());
    } finally {
      setTesting(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await onSave(values);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-2 rounded border px-3 py-2">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Database className="w-3.5 h-3.5 text-muted-foreground" />
          <span className="text-xs font-medium">{title}</span>
        </div>
        {status ? (
          <StorageStatusBadge status={status} />
        ) : (
          <span className="text-xs text-muted-foreground">—</span>
        )}
      </div>
      <div className="space-y-1.5">
        {fields.map((f) => (
          <Input
            key={f.key}
            type={f.type ?? "text"}
            value={values[f.key] ?? ""}
            onChange={(e) =>
              setValues((v) => ({ ...v, [f.key]: e.target.value }))
            }
            autoComplete={f.type === "password" ? "new-password" : "off"}
            placeholder={f.placeholder}
            disabled={disabled}
            className="h-7 text-xs font-mono"
          />
        ))}
      </div>
      <div className="flex items-center gap-2">
        <Button
          size="sm"
          variant="outline"
          onClick={handleTest}
          disabled={disabled || testing || !hasInput}
          className="h-7 text-xs"
        >
          {testing ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            m.admin_storage_test_button()
          )}
        </Button>
        <Button
          size="sm"
          onClick={handleSave}
          disabled={disabled || saving || !hasInput}
          className="h-7 text-xs"
        >
          {saving ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            m.admin_storage_save_button()
          )}
        </Button>
      </div>
      {testResult && <StorageTestResultLine result={testResult} />}
    </div>
  );
}

export function StoragePanel() {
  const [health, setHealth] = useState<StorageHealth | null>(null);
  const [loading, setLoading] = useState(false);
  const [savingMode, setSavingMode] = useState(false);

  const { status: license, loading: licenseLoading } = useLicenseStatus();
  const isPro =
    !licenseLoading && license?.configured && license?.tier === "pro";

  const fetchHealth = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/admin/storage", { credentials: "include" });
      if (res.ok) setHealth(await res.json());
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Busca o status de armazenamento no backend ao montar.
    // oxlint-disable-next-line react/set-state-in-effect
    void fetchHealth();
  }, [fetchHealth]);

  const patchStorage = async (body: Record<string, string>) => {
    await fetch("/admin/storage", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify(body),
    });
    await fetchHealth();
  };

  const storageMode = health?.config?.storage_mode ?? "lite";

  const handleModeChange = async (mode: string) => {
    setSavingMode(true);
    try {
      await patchStorage({ storage_mode: mode });
    } finally {
      setSavingMode(false);
    }
  };

  const liteBackends: {
    key: "checkpointer" | "store" | "lancedb";
    label: string;
  }[] = [
    { key: "checkpointer", label: m.admin_storage_backend_checkpointer() },
    { key: "store", label: m.admin_storage_backend_store() },
    { key: "lancedb", label: m.admin_storage_backend_lancedb() },
  ];

  return (
    <div className="space-y-4">
      {/* Header com refresh */}
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-muted-foreground">
          {m.admin_storage_header()}
        </span>
        <button
          onClick={fetchHealth}
          disabled={loading}
          className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground disabled:opacity-50"
        >
          <RefreshCw className={`w-3 h-3 ${loading ? "animate-spin" : ""}`} />
          {m.admin_storage_refresh()}
        </button>
      </div>

      {/* Modo de armazenamento */}
      <div className="space-y-1.5 rounded border px-3 py-2">
        <span className="text-xs font-medium">
          {m.admin_storage_mode_title()}
        </span>
        <p className="text-xs text-muted-foreground">
          {m.admin_storage_mode_desc()}
        </p>
        <div className="flex items-center gap-2">
          <Select
            value={storageMode}
            onValueChange={handleModeChange}
            disabled={loading || savingMode}
          >
            <SelectTrigger className="h-7 text-xs w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="lite">
                {m.admin_storage_mode_lite()}
              </SelectItem>
              <SelectItem value="complete" disabled={!isPro}>
                {m.admin_storage_mode_complete()} {!isPro && "(Pro)"}
              </SelectItem>
            </SelectContent>
          </Select>
          {savingMode && (
            <Loader2 className="w-3.5 h-3.5 animate-spin text-muted-foreground" />
          )}
        </div>
      </div>

      {/* Cards de status — backends lite (só no modo lite) */}
      {storageMode === "lite" && (
        <div className="grid grid-cols-1 gap-2">
          {liteBackends.map(({ key, label }) => (
            <div
              key={key}
              className="flex items-center justify-between rounded border px-3 py-2"
            >
              <div className="flex items-center gap-2">
                <Database className="w-3.5 h-3.5 text-muted-foreground" />
                <span className="text-xs">{label}</span>
              </div>
              {loading ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin text-muted-foreground" />
              ) : health?.[key] ? (
                <StorageStatusBadge status={health[key]!} />
              ) : (
                <span className="text-xs text-muted-foreground">—</span>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Backends do modo completo (só no modo completo) */}
      {storageMode === "complete" && (
        <div className="space-y-2 pt-2 border-t">
          <span className="text-xs font-medium text-muted-foreground">
            {m.admin_storage_complete_backends()}
          </span>
          <div className="grid grid-cols-1 gap-2">
            <BackendConfigCard
              title={m.admin_storage_postgres_title()}
              status={health?.postgres}
              testBackend="postgres"
              fields={[
                {
                  key: "postgres_dsn",
                  testKey: "dsn",
                  placeholder: m.admin_storage_postgres_placeholder(),
                },
              ]}
              onSave={(v) => patchStorage(v)}
              disabled={!isPro}
            />
            <BackendConfigCard
              title={m.admin_storage_redis_title()}
              status={health?.redis}
              testBackend="redis"
              fields={[
                {
                  key: "redis_url",
                  testKey: "url",
                  placeholder: m.admin_storage_redis_placeholder(),
                },
              ]}
              onSave={(v) => patchStorage(v)}
              disabled={!isPro}
            />
            <BackendConfigCard
              title={m.admin_storage_qdrant_title()}
              status={undefined}
              testBackend="qdrant"
              fields={[
                {
                  key: "qdrant_url",
                  testKey: "url",
                  placeholder: m.admin_storage_qdrant_url_placeholder(),
                },
                {
                  key: "qdrant_api_key",
                  testKey: "api_key",
                  placeholder: m.admin_storage_field_api_key_optional(),
                  type: "password",
                },
              ]}
              onSave={(v) => patchStorage(v)}
              disabled={!isPro}
            />
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// As 5 sub-abas de Administração agora são categorias de primeiro nível no
// rail do SettingsOverlay (achatadas, mesmo tratamento já dado a
// Preferências e Ambiente) — não ficam mais escondidas atrás de um wrapper
// `AdminTab` com tab bar própria. `UsersPanel`/`ToolsPanel`/
// `SafeRootsPanel`/`SystemPanel`/`StoragePanel` são exportados diretamente;
// o gate de "Usuários" por tier (free não vê, ver `useLicenseStatus`
// abaixo) e o deep-link por sub-aba (`administracao-dialog-store`) vivem em
// `settings-categories.tsx`, não aqui.
// ---------------------------------------------------------------------------
