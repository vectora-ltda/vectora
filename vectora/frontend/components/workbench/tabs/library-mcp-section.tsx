"use client";

/**
 * McpSection — marketplace de conectores MCP: GET /mcp/registry,
 * POST /mcp/install, POST /mcp/uninstall.
 *
 * Conectores que exigem env vars pedem os valores antes de instalar,
 * persistidos via POST /auth/envs — o MCP instalado passa a aparecer na
 * aba Integrações como uma entrada "Customizada" automaticamente, já que
 * ela lista qualquer env key órfã do catálogo.
 */

import { useEffect, useMemo, useState } from "react";
import { Download, Loader2, Puzzle, Star, Trash2 } from "lucide-react";

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
import { useLibraryStore, type MCPConnector } from "@/lib/stores/library-store";
import { useWindowsStore } from "@/lib/stores/windows-store";
import { useWorkspacesStore } from "@/lib/stores/workspaces-store";
import { LibraryCard, LibraryTag } from "./library-card";
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
    setError(null);
    const requiresConfirmation = [
      "community_listed",
      "unsigned",
      "verification_unavailable",
    ].includes(connector.trust_state ?? "");
    if (connector.trust_state === "invalid") {
      setError(m.library_mcp_trust_invalid_install());
      return;
    }
    if (requiresConfirmation && !window.confirm(m.library_mcp_trust_confirm()))
      return;

    setSaving(true);
    try {
      await Promise.all(
        connector.env_vars.map((key) => saveEnvVar(key, values[key].trim())),
      );
      const result = await installMcp(connector.id, requiresConfirmation);
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
  onOpen,
}: {
  connector: MCPConnector;
  installed: boolean;
  onChanged: () => void;
  onOpen: () => void;
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
      const requiresConfirmation = [
        "community_listed",
        "unsigned",
        "verification_unavailable",
      ].includes(connector.trust_state ?? "");
      if (connector.trust_state === "invalid") {
        setError(m.library_mcp_trust_invalid_install());
        return;
      }
      if (
        requiresConfirmation &&
        !window.confirm(m.library_mcp_trust_confirm())
      )
        return;
      const result = await installMcp(connector.id, requiresConfirmation);
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

  const verified = connector.vectora_verified === true;
  const publisherUrl = (() => {
    if (!connector.publisher_url) return null;
    try {
      const url = new URL(connector.publisher_url);
      return url.protocol === "https:" || url.protocol === "http:"
        ? url.href
        : null;
    } catch {
      return null;
    }
  })();
  const publisher = connector.publisher?.trim();
  const stars = connector.stars_count ?? 0;
  const downloads = connector.downloads_count ?? 0;

  return (
    <LibraryCard
      onClick={onOpen}
      icon={
        connector.icon_url ? (
          <img
            src={connector.icon_url}
            alt=""
            className="size-full rounded object-cover"
          />
        ) : (
          <Puzzle className="size-3.5" />
        )
      }
      title={connector.name}
      description={connector.description}
      tags={
        <>
          {verified && (
            <LibraryTag verified>{m.library_mcp_verified()}</LibraryTag>
          )}
        </>
      }
      action={
        <Button
          variant={installed ? "outline" : "default"}
          size="sm"
          className={
            installed
              ? "h-[19px] rounded-md border-[#555] bg-transparent px-1.5 py-1 text-[9px] text-muted-foreground"
              : "h-[19px] rounded-md border-0 bg-[#d4d4d4] px-1.5 py-1 text-[9px] font-medium text-[#1a1a1a] hover:bg-white"
          }
          onClick={installed ? handleUninstall : handleInstallClick}
          disabled={busy}
        >
          {busy ? (
            <Loader2 className="size-[11px] animate-spin" />
          ) : installed ? (
            <>
              <Trash2 className="size-[11px]" />
              {m.library_mcp_uninstall()}
            </>
          ) : (
            <>
              <Download className="size-[11px]" />
              {m.library_mcp_install()}
            </>
          )}
        </Button>
      }
      footer={
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-muted-foreground">
          {publisher &&
            (publisherUrl ? (
              <a
                href={publisherUrl}
                target="_blank"
                rel="noreferrer"
                className="truncate hover:text-foreground"
                onClick={(event) => event.stopPropagation()}
              >
                {m.library_mcp_preview_publisher({ publisher })}
              </a>
            ) : (
              <span className="truncate">
                {m.library_mcp_preview_publisher({ publisher })}
              </span>
            ))}
          {stars > 0 && (
            <span className="inline-flex items-center gap-1">
              <Star className="size-3" />
              {m.library_mcp_preview_stars({ count: stars })}
            </span>
          )}
          {downloads > 0 && (
            <span>{m.library_mcp_preview_downloads({ count: downloads })}</span>
          )}
          {error && <p className="basis-full text-destructive">{error}</p>}
        </div>
      }
    >
      {configuring && (
        <ConfigureDialog
          connector={connector}
          onClose={() => setConfiguring(false)}
          onInstalled={onChanged}
        />
      )}
    </LibraryCard>
  );
}

export function McpSection({
  query,
  threadId = "library",
}: {
  query: string;
  threadId?: string;
}) {
  const connectors = useLibraryStore((s) => s.mcpItems);
  const sourceStatus = useLibraryStore(
    (s) => s.mcpStatus ?? { status: "never" },
  );
  const installedIds = useLibraryStore((s) => s.mcpInstalledIds);
  const loading = useLibraryStore((s) => s.mcpLoading);
  const error = useLibraryStore((s) => s.mcpError);
  const ensureMcpLoaded = useLibraryStore((s) => s.ensureMcpLoaded);
  const invalidateMcp = useLibraryStore((s) => s.invalidateMcp);
  const openCanvasDocument = useWindowsStore((s) => s.openCanvasDocument);

  const openConnector = (connector: MCPConnector) => {
    const workspaceId = useWorkspacesStore.getState().active_id;
    const workspaceKey = workspaceId ?? "no-workspace";
    openCanvasDocument({
      id: `mcp:${workspaceKey}:${threadId}:${connector.id}`,
      kind: "mcp-preview",
      workspaceId,
      threadId,
      title: m.library_mcp_preview_title({ name: connector.name }),
      mcp: {
        id: connector.id,
        name: connector.name,
        description: connector.description,
        installCommand: connector.install_cmd,
        envVars: connector.env_vars,
        homepage: connector.homepage,
        category: connector.category,
        iconUrl: connector.icon_url,
        publisher: connector.publisher,
        publisherUrl: connector.publisher_url,
        starsCount: connector.stars_count,
        downloadsCount: connector.downloads_count,
        runtimeHint: connector.runtime_hint,
        transport: connector.transport,
      },
    });
  };

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
        {sourceStatus.status === "unavailable" && (
          <p className="text-xs text-destructive text-center" role="status">
            {m.library_mcp_status_unavailable()}
          </p>
        )}
        {sourceStatus.status === "never" && (
          <p
            className="text-xs text-muted-foreground text-center"
            role="status"
          >
            {m.library_mcp_status_never()}
          </p>
        )}
        <p className="text-xs text-muted-foreground text-center">
          {m.library_empty_mcp()}
        </p>
        {error && (
          <p className="text-xs text-destructive text-center">{error}</p>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-2 py-1">
      {sourceStatus.status === "unavailable" && (
        <p className="text-xs text-destructive" role="status">
          {m.library_mcp_status_unavailable()}
        </p>
      )}
      {error && <p className="text-xs text-destructive">{error}</p>}
      {connectors.map((connector) => (
        <ConnectorCard
          key={connector.id}
          connector={connector}
          installed={installedIds.has(connector.id)}
          onChanged={load}
          onOpen={() => openConnector(connector)}
        />
      ))}
    </div>
  );
}
