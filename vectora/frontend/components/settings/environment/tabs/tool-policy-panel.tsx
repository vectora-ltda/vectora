"use client";

/**
 * ToolPolicyPanel — controle do usuário sobre quais tools built-in o agente
 * pode usar em seu nome.
 *
 * GET /tools/policy → {disabled, available}; PUT salva as desabilitadas.
 * Mudanças entram em vigor no próximo request, que invalida o cache.
 */

import { useEffect, useState } from "react";
import { Check, ChevronDown, ChevronRight, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { m } from "@/lib/paraglide/messages";
interface Policy {
  disabled: string[];
  available: string[];
  schemas?: Record<string, unknown>;
}

export function ToolPolicyPanel() {
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [disabled, setDisabled] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [usage, setUsage] = useState<Record<string, number> | null>(null);
  const [usageLoading, setUsageLoading] = useState(true);
  const [usageError, setUsageError] = useState(false);
  const [openSchema, setOpenSchema] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch("/tools/policy")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (cancelled) return;
        if (d?.available) {
          setPolicy(d);
          setDisabled(new Set(d.disabled ?? []));
        } else {
          setError(m.toolpolicy_error_load());
        }
      })
      .catch(() => setError(m.toolpolicy_error_load()))
      .finally(() => setLoading(false));
    fetch("/tools/usage")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (cancelled) return;
        if (d?.usage) setUsage(d.usage);
        else setUsageError(true);
      })
      .catch(() => setUsageError(true))
      .finally(() => setUsageLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

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
    setError(null);
    try {
      const res = await fetch("/tools/policy", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ disabled: [...disabled] }),
      });
      if (!res.ok) {
        setError(m.toolpolicy_error_save());
        return;
      }
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch {
      setError(m.toolpolicy_error_save());
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center py-8">
        <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
      </div>
    );
  }
  if (!policy) {
    return <p className="text-xs text-destructive">{error}</p>;
  }

  const dirty = (() => {
    const base = new Set(policy.disabled);
    if (base.size !== disabled.size) return true;
    for (const n of disabled) if (!base.has(n)) return true;
    return false;
  })();

  return (
    <div className="space-y-3">
      <div className="space-y-0.5">
        <p className="text-sm font-medium">{m.toolpolicy_title()}</p>
        <p className="text-xs text-muted-foreground">
          {m.toolpolicy_subtitle()}
        </p>
      </div>

      <div className="rounded-lg border bg-card/50 divide-y divide-border/60">
        {policy.available.map((name) => {
          const isEnabled = !disabled.has(name);
          const schema = policy.schemas?.[name];
          return (
            <div key={name} className="px-3 py-2">
              <div className="flex items-center justify-between">
                <span className="text-xs font-mono">{name}</span>
                <Switch
                  checked={isEnabled}
                  onCheckedChange={() => toggle(name)}
                />
              </div>
              <div className="mt-1 flex items-center justify-between gap-2 text-[11px] text-muted-foreground">
                <span>
                  {usageLoading
                    ? m.toolpolicy_usage_loading()
                    : usageError
                      ? m.toolpolicy_usage_error()
                      : (usage?.[name] ?? 0) === 1
                        ? m.toolpolicy_usage_one({ count: usage?.[name] ?? 0 })
                        : m.toolpolicy_usage_many({
                            count: usage?.[name] ?? 0,
                          })}
                </span>
                {schema !== undefined && (
                  <button
                    type="button"
                    className="inline-flex items-center gap-1"
                    aria-expanded={openSchema === name}
                    aria-controls={`tool-schema-${name}`}
                    onClick={() =>
                      setOpenSchema(openSchema === name ? null : name)
                    }
                  >
                    {openSchema === name ? (
                      <ChevronDown className="h-3 w-3" />
                    ) : (
                      <ChevronRight className="h-3 w-3" />
                    )}
                    {openSchema === name
                      ? m.toolpolicy_hide_schema()
                      : m.toolpolicy_show_schema()}
                  </button>
                )}
              </div>
              {openSchema === name && schema !== undefined && (
                <pre
                  id={`tool-schema-${name}`}
                  role="region"
                  aria-label={m.toolpolicy_schema_label({ name })}
                  className="mt-2 max-h-48 overflow-auto rounded bg-muted p-2 text-[10px]"
                >
                  {JSON.stringify(schema, null, 2)}
                </pre>
              )}
            </div>
          );
        })}
      </div>

      {error && <p className="text-xs text-destructive">{error}</p>}

      <div className="flex items-center gap-2 justify-end">
        {saved && (
          <span className="text-xs text-green-500 inline-flex items-center gap-1">
            <Check className="w-3 h-3" />
            {m.toolpolicy_saved()}
          </span>
        )}
        <Button
          size="sm"
          className="h-7 text-xs"
          onClick={handleSave}
          disabled={saving || !dirty}
        >
          {saving && <Loader2 className="w-3 h-3 animate-spin mr-1.5" />}
          {m.toolpolicy_save()}
        </Button>
      </div>
    </div>
  );
}
