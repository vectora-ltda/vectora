"use client";

/**
 * PluginsTab — gerenciador de servidores MCP.
 *
 * Lista os servidores MCP do usuário, permite adicionar/remover e fazer
 * health-check. Cada servidor tem transporte stdio (command+args) ou sse/http
 * (url). As tools de servidores conectados ficam disponíveis no chat.
 */

import { useCallback, useEffect, useState } from "react";
import {
  CheckCircle2,
  Loader2,
  Plug,
  Plus,
  Trash2,
  XCircle,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ToolPolicyPanel } from "./tool-policy-panel";
import { m } from "@/lib/paraglide/messages";

interface McpServer {
  name: string;
  transport: "stdio" | "sse" | "http";
  command: string;
  args: string[];
  url: string;
}

type VerifyState = { state: "idle" | "loading" | "ok" | "error"; msg: string };

const EMPTY_FORM: McpServer = {
  name: "",
  transport: "stdio",
  command: "",
  args: [],
  url: "",
};

export function PluginsTab() {
  const [servers, setServers] = useState<McpServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState<McpServer>(EMPTY_FORM);
  const [argsText, setArgsText] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [verify, setVerify] = useState<Record<string, VerifyState>>({});

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/plugins");
      const data = res.ok ? await res.json() : { servers: [] };
      setServers(data.servers ?? []);
    } catch {
      setError(m.plugins_error_load());
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Busca a lista de servidores MCP configurados no backend ao montar.
    // oxlint-disable-next-line react/set-state-in-effect
    void load();
  }, [load]);

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    const payload: McpServer = {
      ...form,
      args: argsText
        .split("\n")
        .map((a) => a.trim())
        .filter(Boolean),
    };
    try {
      const res = await fetch("/plugins", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        setError(d.detail ?? m.plugins_error_save());
        return;
      }
      setForm(EMPTY_FORM);
      setArgsText("");
      setAdding(false);
      await load();
    } catch {
      setError(m.plugins_error_save());
    } finally {
      setSaving(false);
    }
  };

  const handleRemove = async (name: string) => {
    await fetch(`/plugins/${encodeURIComponent(name)}`, {
      method: "DELETE",
    });
    setServers((prev) => prev.filter((s) => s.name !== name));
  };

  const handleVerify = async (name: string) => {
    setVerify((p) => ({ ...p, [name]: { state: "loading", msg: "" } }));
    try {
      const res = await fetch(`/plugins/${encodeURIComponent(name)}/verify`, {
        method: "POST",
      });
      const d = await res.json();
      setVerify((p) => ({
        ...p,
        [name]: d.ok
          ? { state: "ok", msg: m.plugins_verify_ok({ n: d.tools.length }) }
          : { state: "error", msg: d.error || m.plugins_verify_fail() },
      }));
    } catch {
      setVerify((p) => ({
        ...p,
        [name]: { state: "error", msg: m.plugins_verify_fail() },
      }));
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center py-12">
        <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="space-y-0.5">
        <p className="text-sm font-medium">{m.plugins_title()}</p>
        <p className="text-xs text-muted-foreground">{m.plugins_subtitle()}</p>
      </div>

      {/* Lista */}
      <div className="space-y-2">
        {servers.length === 0 && !adding && (
          <p className="text-xs text-muted-foreground py-2">
            {m.plugins_empty()}
          </p>
        )}
        {servers.map((s) => {
          const v = verify[s.name];
          return (
            <div key={s.name} className="rounded-lg border bg-card p-3">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-md bg-muted flex items-center justify-center shrink-0">
                  <Plug className="w-4 h-4 text-muted-foreground" />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium truncate">
                      {s.name}
                    </span>
                    <Badge
                      variant="secondary"
                      className="text-[10px] h-4 px-1.5"
                    >
                      {s.transport}
                    </Badge>
                  </div>
                  <p className="text-xs text-muted-foreground truncate font-mono">
                    {s.transport === "stdio"
                      ? [s.command, ...s.args].join(" ")
                      : s.url}
                  </p>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 px-2 text-xs"
                    onClick={() => void handleVerify(s.name)}
                    disabled={v?.state === "loading"}
                  >
                    {v?.state === "loading" ? (
                      <Loader2 className="w-3 h-3 animate-spin" />
                    ) : v?.state === "ok" ? (
                      <CheckCircle2 className="w-3 h-3 text-green-500" />
                    ) : v?.state === "error" ? (
                      <XCircle className="w-3 h-3 text-destructive" />
                    ) : (
                      m.plugins_verify()
                    )}
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 w-7 p-0 text-muted-foreground hover:text-destructive"
                    onClick={() => void handleRemove(s.name)}
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </Button>
                </div>
              </div>
              {v?.msg && (
                <p
                  className={`mt-2 text-xs px-2 py-1 rounded ${v.state === "ok" ? "bg-green-500/10 text-green-600 dark:text-green-400" : "bg-destructive/10 text-destructive"}`}
                >
                  {v.msg}
                </p>
              )}
            </div>
          );
        })}
      </div>

      {/* Form de adicionar */}
      {adding ? (
        <div className="rounded-lg border bg-card/50 p-3 space-y-2">
          <div className="grid grid-cols-2 gap-2">
            <div className="space-y-1">
              <label className="text-[10px] text-muted-foreground">
                {m.plugins_name()}
              </label>
              <Input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                autoComplete="off"
                className="h-8 text-xs"
              />
            </div>
            <div className="space-y-1">
              <label className="text-[10px] text-muted-foreground">
                {m.plugins_transport()}
              </label>
              <Select
                value={form.transport}
                onValueChange={(v) =>
                  setForm({ ...form, transport: v as McpServer["transport"] })
                }
              >
                <SelectTrigger className="h-8 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="stdio">
                    {m.plugins_transport_stdio()}
                  </SelectItem>
                  <SelectItem value="sse">
                    {m.plugins_transport_sse()}
                  </SelectItem>
                  <SelectItem value="http">
                    {m.plugins_transport_http()}
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          {form.transport === "stdio" ? (
            <>
              <div className="space-y-1">
                <label className="text-[10px] text-muted-foreground">
                  {m.plugins_command()}
                </label>
                <Input
                  value={form.command}
                  onChange={(e) =>
                    setForm({ ...form, command: e.target.value })
                  }
                  autoComplete="off"
                  className="h-8 text-xs font-mono"
                  placeholder={m.plugins_command_placeholder()}
                />
              </div>
              <div className="space-y-1">
                <label className="text-[10px] text-muted-foreground">
                  {m.plugins_args()}
                </label>
                <textarea
                  value={argsText}
                  onChange={(e) => setArgsText(e.target.value)}
                  className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-xs font-mono min-h-[60px]"
                  placeholder={m.plugins_args_placeholder()}
                />
              </div>
            </>
          ) : (
            <div className="space-y-1">
              <label className="text-[10px] text-muted-foreground">
                {m.plugins_url()}
              </label>
              <Input
                value={form.url}
                onChange={(e) => setForm({ ...form, url: e.target.value })}
                autoComplete="off"
                className="h-8 text-xs font-mono"
                placeholder={m.plugins_url_placeholder()}
              />
            </div>
          )}

          {error && <p className="text-xs text-destructive">{error}</p>}

          <div className="flex gap-2 justify-end">
            <Button
              variant="ghost"
              size="sm"
              className="h-7 text-xs"
              onClick={() => {
                setAdding(false);
                setForm(EMPTY_FORM);
                setArgsText("");
                setError(null);
              }}
            >
              {m.plugins_cancel()}
            </Button>
            <Button
              size="sm"
              className="h-7 text-xs"
              onClick={handleSave}
              disabled={saving || !form.name.trim()}
            >
              {saving && <Loader2 className="w-3 h-3 animate-spin mr-1.5" />}
              {m.plugins_save()}
            </Button>
          </div>
        </div>
      ) : (
        <Button
          variant="outline"
          size="sm"
          className="h-8 text-xs w-full"
          onClick={() => setAdding(true)}
        >
          <Plus className="w-3.5 h-3.5 mr-1.5" />
          {m.plugins_add()}
        </Button>
      )}

      {/* Política de tools (self-service) */}
      <div className="border-t border-border/60 pt-3">
        <ToolPolicyPanel />
      </div>
    </div>
  );
}
