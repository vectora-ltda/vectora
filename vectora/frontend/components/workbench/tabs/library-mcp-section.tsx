"use client";

/**
 * McpSection — marketplace de conectores MCP: GET /mcp/registry,
 * POST /mcp/install, POST /mcp/uninstall.
 *
 * Conectores que exigem env vars pedem os valores antes de instalar,
 * persistidos via POST /auth/envs — o MCP instalado passa a aparecer na
 * aba Integrações como uma entrada "Customizada" automaticamente, já que
 * ela lista qualquer env key órfã do catálogo.
 *
 * "Adicionar MCP manual" (stdio/sse/http + política de tools) reaproveita
 * o componente PluginsTab.
 */

import { useEffect, useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronUp,
  Download,
  Loader2,
  Puzzle,
  Trash2,
} from "lucide-react";

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
import { PluginsTab } from "@/components/settings/environment/tabs/plugins-tab";
import { m } from "@/lib/paraglide/messages";
import { useLibraryStore, type MCPConnector } from "@/lib/stores/library-store";
import { useWorkspacesStore } from "@/lib/stores/workspaces-store";
import type { LibraryItem } from "./library-tab";

async function saveEnvVar(key: string, value: string): Promise<void> {
  const res = await fetch("/auth/envs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key, value }),
  });
  if (!res.ok) throw new Error(`Erro ${res.status}`);
}

async function installMcp(
  mcpId: string,
  confirmUnverified = false,
): Promise<{ status: string }> {
  const workspaceId = useWorkspacesStore.getState().active_id;
  const res = await fetch("/mcp/install", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      mcp_id: mcpId,
      workspace_id: workspaceId ?? undefined,
      confirm_unverified: confirmUnverified,
    }),
  });
  return res.json();
}

async function uninstallMcp(mcpId: string): Promise<void> {
  const workspaceId = useWorkspacesStore.getState().active_id;
  await fetch("/mcp/uninstall", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      mcp_id: mcpId,
      workspace_id: workspaceId ?? undefined,
    }),
  });
}

export function connectorToLibraryItem(c: MCPConnector): LibraryItem {
  return { id: c.id, name: c.name, description: c.description };
}

function ConfigureDialog({
  connector,
  onClose,
  onInstalled,
}: {
  connector: MCPConnector;
  onClose: () => void;
  onInstalled: () => void;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleConfirm = async () => {
    const missing = connector.env_vars.find((key) => !values[key]?.trim());
    if (missing) {
      setError(m.library_mcp_error_missing_env({ key: missing }));
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await Promise.all(
        connector.env_vars.map((key) => saveEnvVar(key, values[key].trim())),
      );
      const result = await installMcp(connector.id);
      if (result.status === "error") {
        setError(m.library_mcp_error_install());
        return;
      }
      onInstalled();
      onClose();
    } catch {
      setError(m.library_mcp_error_install());
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {m.library_mcp_configure_title({ name: connector.name })}
          </DialogTitle>
          <DialogDescription>
            {m.library_mcp_configure_desc()}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3 py-1">
          {connector.env_vars.map((key) => (
            <div key={key} className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground font-mono">
                {key}
              </label>
              <Input
                type="password"
                autoComplete="new-password"
                value={values[key] ?? ""}
                onChange={(e) =>
                  setValues((prev) => ({ ...prev, [key]: e.target.value }))
                }
                className="text-sm font-mono"
              />
            </div>
          ))}
          {error && <p className="text-xs text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={saving}>
            {m.envs_cancel()}
          </Button>
          <Button onClick={handleConfirm} disabled={saving}>
            {saving && <Loader2 className="w-4 h-4 animate-spin mr-2" />}
            {m.library_mcp_install()}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ConnectorCard({
  connector,
  installed,
  onChanged,
}: {
  connector: MCPConnector;
  installed: boolean;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [configuring, setConfiguring] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleInstallClick = () => {
    if (connector.env_vars.length > 0) {
      setConfiguring(true);
      return;
    }
    void handleInstall();
  };

  const handleInstall = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await installMcp(connector.id);
      if (result.status === "error") {
        setError(m.library_mcp_error_install());
        return;
      }
      onChanged();
    } catch {
      setError(m.library_mcp_error_install());
    } finally {
      setBusy(false);
    }
  };

  const handleUninstall = async () => {
    setBusy(true);
    setError(null);
    try {
      await uninstallMcp(connector.id);
      onChanged();
    } catch {
      setError(m.library_mcp_error_uninstall());
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-lg border bg-card p-3 space-y-2">
      <div className="flex items-center gap-3">
        <div className="w-8 h-8 rounded-md bg-muted flex items-center justify-center shrink-0 overflow-hidden">
          {connector.icon_url ? (
            <img
              src={connector.icon_url}
              alt=""
              className="w-full h-full object-cover"
            />
          ) : (
            <Puzzle className="w-4 h-4 text-muted-foreground" />
          )}
        </div>
        <div className="flex-1 min-w-0">
          <span className="block text-sm font-medium truncate">
            {connector.name}
          </span>
          <p className="text-xs text-muted-foreground truncate">
            {connector.description}
          </p>
          <div className="flex items-center gap-1.5 min-w-0 pt-0.5">
            <Badge
              variant="secondary"
              className="text-[10px] h-4 px-1.5 shrink-0"
            >
              {connector.category}
            </Badge>
            {connector.vectora_verified && (
              <Badge className="text-[10px] h-4 px-1.5 shrink-0">
                {m.library_mcp_verified()}
              </Badge>
            )}
          </div>
        </div>
        <Button
          variant={installed ? "outline" : "default"}
          size="sm"
          className="h-7 text-xs shrink-0"
          onClick={installed ? handleUninstall : handleInstallClick}
          disabled={busy}
        >
          {busy ? (
            <Loader2 className="w-3 h-3 animate-spin" />
          ) : installed ? (
            <>
              <Trash2 className="w-3 h-3 mr-1.5" />
              {m.library_mcp_uninstall()}
            </>
          ) : (
            <>
              <Download className="w-3 h-3 mr-1.5" />
              {m.library_mcp_install()}
            </>
          )}
        </Button>
      </div>
      {error && <p className="text-xs text-destructive">{error}</p>}
      {configuring && (
        <ConfigureDialog
          connector={connector}
          onClose={() => setConfiguring(false)}
          onInstalled={onChanged}
        />
      )}
    </div>
  );
}

export function McpSection({
  query,
  onCountChange,
}: {
  query: string;
  onCountChange: (count: number) => void;
}) {
  const connectors = useLibraryStore((s) => s.mcpItems);
  const installedIds = useLibraryStore((s) => s.mcpInstalledIds);
  const loading = useLibraryStore((s) => s.mcpLoading);
  const error = useLibraryStore((s) => s.mcpError);
  const ensureMcpLoaded = useLibraryStore((s) => s.ensureMcpLoaded);
  const invalidateMcp = useLibraryStore((s) => s.invalidateMcp);
  const [showAdvanced, setShowAdvanced] = useState(false);

  const load = useMemo(
    () => async () => {
      invalidateMcp();
      await ensureMcpLoaded(query);
    },
    [invalidateMcp, ensureMcpLoaded, query],
  );

  useEffect(() => {
    if (!query.trim()) {
      void ensureMcpLoaded(query);
      return;
    }
    const timer = setTimeout(() => {
      void ensureMcpLoaded(query);
    }, 350);
    return () => clearTimeout(timer);
  }, [query, ensureMcpLoaded]);

  useEffect(() => {
    onCountChange(connectors.length);
  }, [connectors.length, onCountChange]);

  if (loading) {
    return (
      <div className="flex justify-center py-6">
        <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (connectors.length === 0) {
    return (
      <div className="py-4 space-y-3">
        <p className="text-xs text-muted-foreground text-center">
          {m.library_empty_mcp()}
        </p>
        {error && (
          <p className="text-xs text-destructive text-center">{error}</p>
        )}
        <AdvancedToggle
          open={showAdvanced}
          onToggle={() => setShowAdvanced((v) => !v)}
        />
        {showAdvanced && <PluginsTab />}
      </div>
    );
  }

  return (
    <div className="space-y-2 py-1">
      {error && <p className="text-xs text-destructive">{error}</p>}
      {connectors.map((connector) => (
        <ConnectorCard
          key={connector.id}
          connector={connector}
          installed={installedIds.has(connector.id)}
          onChanged={load}
        />
      ))}
      <AdvancedToggle
        open={showAdvanced}
        onToggle={() => setShowAdvanced((v) => !v)}
      />
      {showAdvanced && <PluginsTab />}
    </div>
  );
}

function AdvancedToggle({
  open,
  onToggle,
}: {
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors pt-1"
    >
      {open ? (
        <ChevronUp className="w-3.5 h-3.5" />
      ) : (
        <ChevronDown className="w-3.5 h-3.5" />
      )}
      {m.library_mcp_advanced_toggle()}
    </button>
  );
}
