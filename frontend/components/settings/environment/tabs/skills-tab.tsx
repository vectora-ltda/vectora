"use client";

/**
 * SkillsTab — gerenciador de skills do usuário.
 *
 * Lista skills instaladas e permite remover/verificar existentes. Novas
 * instalações entram exclusivamente pelo catálogo da Library.
 */

import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, Loader2, Trash2, XCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { m } from "@/lib/paraglide/messages";
interface Skill {
  id: string;
  name: string;
  description: string;
  source: string;
  path: string;
  installed_at: string;
  installed_by: string;
}

type VerifyState = { state: "idle" | "loading" | "ok" | "error"; msg: string };

interface SkillsTabProps {
  /** Notifica a contagem atual de skills instaladas após cada refresh. */
  onSkillsChange?: (count: number) => void;
}

export function SkillsTab({ onSkillsChange }: SkillsTabProps = {}) {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [verify, setVerify] = useState<Record<string, VerifyState>>({});

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/skills");
      const data = await res.json();
      const list = Array.isArray(data.skills) ? data.skills : [];
      setSkills(list);
      onSkillsChange?.(list.length);
    } catch {
      setError(m.skills_error_load());
    } finally {
      setLoading(false);
    }
  }, [onSkillsChange]);

  useEffect(() => {
    // Busca a lista de skills instaladas no backend ao montar.
    // oxlint-disable-next-line react/set-state-in-effect
    void refresh();
  }, [refresh]);

  async function handleRemove(id: string) {
    if (!confirm(m.skills_confirm_remove())) return;
    await fetch(`/skills/${encodeURIComponent(id)}`, { method: "DELETE" });
    await refresh();
  }

  async function handleVerify(id: string) {
    setVerify((v) => ({ ...v, [id]: { state: "loading", msg: "" } }));
    try {
      const res = await fetch(`/skills/${encodeURIComponent(id)}/verify`, {
        method: "POST",
      });
      const data = await res.json();
      setVerify((v) => ({
        ...v,
        [id]: {
          state: data.ok ? "ok" : "error",
          msg: data.ok ? m.skills_verify_ok() : (data.error ?? ""),
        },
      }));
    } catch {
      setVerify((v) => ({
        ...v,
        [id]: { state: "error", msg: m.skills_error_verify() },
      }));
    }
  }

  return (
    <div className="space-y-4">
      {error && <p className="text-xs text-destructive">{error}</p>}

      {loading ? (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="w-3.5 h-3.5 animate-spin" />
          {m.skills_loading()}
        </div>
      ) : skills.length === 0 ? (
        <p className="text-xs text-muted-foreground italic">
          {m.skills_empty()}
        </p>
      ) : (
        <div className="divide-y divide-border/60 rounded-md border border-border/60">
          {skills.map((s) => {
            const v = verify[s.id];
            return (
              <div key={s.id} className="p-3 space-y-1">
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium text-foreground truncate">
                      {s.name}
                    </div>
                    <div className="text-[11px] text-muted-foreground truncate">
                      {s.description}
                    </div>
                    <div className="text-[10px] text-muted-foreground font-mono truncate mt-0.5">
                      {s.source}
                    </div>
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => handleVerify(s.id)}
                      disabled={v?.state === "loading"}
                      className="h-7 px-2 text-xs"
                    >
                      {v?.state === "loading" ? (
                        <Loader2 className="w-3 h-3 animate-spin" />
                      ) : v?.state === "ok" ? (
                        <CheckCircle2 className="w-3 h-3 text-emerald-500" />
                      ) : v?.state === "error" ? (
                        <XCircle className="w-3 h-3 text-destructive" />
                      ) : (
                        m.skills_verify()
                      )}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => handleRemove(s.id)}
                      className="h-7 px-2"
                    >
                      <Trash2 className="w-3 h-3 text-destructive" />
                    </Button>
                  </div>
                </div>
                {v?.msg && (
                  <p
                    className={`text-[11px] ${
                      v.state === "ok" ? "text-emerald-500" : "text-destructive"
                    }`}
                  >
                    {v.msg}
                  </p>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
