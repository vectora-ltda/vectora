"use client";

/**
 * ContaTab
 *
 * Exibe nome, email e role do usuário ativo. Permite editar o nome inline
 * (PATCH /auth/me) — aceita UTF-8 livre, espaços, acentos. Botão de mudar
 * senha continua placeholder.
 */

import { Check, Loader2, LogOut, Pencil, X } from "lucide-react";
import { useState } from "react";
import { useRouter } from "next/navigation";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { useAuthStore } from "@/lib/stores/auth-store";
import { m } from "@/lib/paraglide/messages";
import type { AuthUser } from "@/lib/types/auth";

const ROLE_LABELS: Record<string, string> = {
  root: "Root",
  admin: "Admin",
  member: "Membro",
  viewer: "Visualizador",
};

const ROLE_VARIANTS: Record<
  string,
  "default" | "secondary" | "outline" | "destructive"
> = {
  root: "destructive",
  admin: "default",
  member: "secondary",
  viewer: "outline",
};

export function ContaTab() {
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const setUser = useAuthStore((s) => s.setUser);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const clearUser = useAuthStore((s) => s.clearUser);

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(user?.name ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // `draft` sincroniza com `user?.name` quando ele muda por fora (ex.: outra
  // aba salvou o nome) mas continua editável localmente enquanto o usuário
  // digita — comparação durante o render (não num effect) é o padrão que o
  // próprio React recomenda pra "ajustar estado quando uma prop muda":
  // https://react.dev/learn/you-might-not-need-an-effect#adjusting-some-state-when-a-prop-changes
  const [prevUserName, setPrevUserName] = useState(user?.name ?? "");
  if ((user?.name ?? "") !== prevUserName) {
    setPrevUserName(user?.name ?? "");
    setDraft(user?.name ?? "");
  }

  if (!user) {
    return (
      <div className="flex flex-col items-center justify-center py-8 text-center">
        <p className="text-sm text-muted-foreground">
          Nenhum usuário autenticado.
        </p>
      </div>
    );
  }

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      const res = await fetch("/auth/me", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: draft.trim() }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail ?? `Erro ${res.status}`);
      }
      const updated = (await res.json()) as AuthUser;
      setUser(updated);
      setEditing(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Erro ao salvar.");
    } finally {
      setSaving(false);
    }
  }

  async function handleLogout() {
    try {
      await fetch("/auth/signout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
    } catch {
      // Ignora erro de rede — limpa localmente de qualquer jeito
    }
    clearUser();
    router.replace("/auth/signin");
  }

  // O backend sempre injeta um usuário em /auth/me (real no Pro, virtual
  // "local" no Free — ver _get_virtual_local_user em
  // backend/api/middleware/auth.py) — só "Sair" distingue conta real de
  // usuário local virtual (sair de uma sessão local não faz sentido).
  const isRealAccount = isAuthenticated && user.id !== "local";

  const displayName = user.name?.trim() || "—";

  return (
    <div className="space-y-6">
      {/* Nome */}
      <div className="space-y-1.5">
        <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">
          Nome
        </p>
        {editing ? (
          <div className="flex items-center gap-2">
            <Input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              maxLength={100}
              autoComplete="off"
              placeholder="Como o Vectora deve te chamar?"
              className="text-sm"
              autoFocus
              onKeyDown={(e) => {
                if (e.key === "Enter") void handleSave();
                if (e.key === "Escape") {
                  setEditing(false);
                  setDraft(user.name ?? "");
                }
              }}
            />
            <Button
              size="sm"
              onClick={handleSave}
              disabled={saving}
              className="h-8 px-2"
            >
              {saving ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Check className="w-4 h-4" />
              )}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setEditing(false);
                setDraft(user.name ?? "");
                setError(null);
              }}
              disabled={saving}
              className="h-8 px-2"
            >
              <X className="w-4 h-4" />
            </Button>
          </div>
        ) : (
          <div className="flex items-center gap-2">
            <p className="text-sm flex-1 truncate">{displayName}</p>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-xs text-muted-foreground"
              onClick={() => setEditing(true)}
            >
              <Pencil className="w-3 h-3 mr-1" />
              Editar
            </Button>
          </div>
        )}
        {error && <p className="text-xs text-destructive">{error}</p>}
      </div>

      {/* Usuário + Role — Vectora identifica a conta por username, não email
          (email é opcional e fica vazio em modo local, sem conta). */}
      <div className="flex items-center justify-between">
        <div className="space-y-1">
          <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">
            Usuário
          </p>
          <p className="text-sm font-mono">{user.username}</p>
        </div>
        <Badge variant={ROLE_VARIANTS[user.role] ?? "secondary"}>
          {ROLE_LABELS[user.role] ?? user.role}
        </Badge>
      </div>

      <Separator />

      {/* Ações */}
      <div className="space-y-2">
        <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">
          Segurança
        </p>
        <Button
          variant="outline"
          size="sm"
          className="w-full justify-start"
          disabled
          title="Em breve"
        >
          Alterar senha
        </Button>
        {isRealAccount && (
          <Button
            variant="outline"
            size="sm"
            className="w-full justify-start text-destructive hover:text-destructive"
            onClick={() => void handleLogout()}
          >
            <LogOut className="w-3.5 h-3.5 mr-1.5" />
            {m.user_logout()}
          </Button>
        )}
      </div>
    </div>
  );
}
