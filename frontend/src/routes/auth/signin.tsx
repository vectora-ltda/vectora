import { useEffect, useMemo, useState } from "react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Eye, EyeOff } from "lucide-react";
import { z } from "zod";

import { useAuthStore } from "@/lib/stores/auth-store";
import type { AuthUser } from "@/lib/types/auth";
import { consumeReturnTo } from "@/lib/utils/return-to";
import { m } from "@/lib/paraglide/messages";

const searchSchema = z.object({
  from: z.string().optional(),
});

export const Route = createFileRoute("/auth/signin")({
  validateSearch: searchSchema,
  component: SignInPage,
});

function SignInPage() {
  const navigate = useNavigate();
  const search = Route.useSearch();
  const setUser = useAuthStore((s) => s.setUser);

  const schema = useMemo(
    () =>
      z.object({
        email: z.string().email(m.auth_email_invalid()),
        password: z.string().min(1, m.auth_signin_password_required()),
      }),
    [],
  );

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [ssoEnabled, setSsoEnabled] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch("/auth/oidc/status", { credentials: "include" })
      .then((r) => r.json())
      .then((d: { enabled?: boolean }) => {
        if (!cancelled) setSsoEnabled(Boolean(d.enabled));
      })
      .catch(() => {
        // Sem SSO configurado (ou erro de rede) — só o login local aparece.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Primeiro acesso (sem usuários) → wizard novo (/onboarding), nunca mais
  // direto pro signup antigo. Chegar em /auth/signin com a instância zerada
  // só acontece por link direto/histórico do navegador — o fluxo normal de
  // primeiro acesso já é redirecionado pelo guard de __root.tsx.
  useEffect(() => {
    let cancelled = false;
    fetch("/auth/has-users", { credentials: "include" })
      .then((r) => r.json())
      .then((d: { exists?: boolean }) => {
        if (!cancelled && d.exists === false) {
          void navigate({ to: "/onboarding" });
        }
      })
      .catch(() => {
        // Falha de rede → permanece no login
      });
    return () => {
      cancelled = true;
    };
  }, [navigate]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    const result = schema.safeParse({ email, password });
    if (!result.success) {
      setError(result.error.issues[0]?.message ?? m.auth_signin_invalid_data());
      return;
    }

    setLoading(true);
    try {
      const res = await fetch("/auth/signin", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });

      const data = await res.json();
      if (!res.ok) {
        setError(data.detail ?? m.auth_signin_invalid_credentials());
        return;
      }

      setUser(data.user as AuthUser);
      // `vectora:return_to` (sessão caiu no meio do uso, salvo por
      // `redirectToLogin`) tem prioridade sobre `?from=` (carregamento
      // inicial sem sessão, ver __root.tsx) por carregar path+query completos.
      const returnTo = consumeReturnTo();
      const from = (search as { from?: string }).from;
      void navigate({ to: returnTo ?? from ?? "/" });
    } catch {
      setError(m.auth_conn_error());
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-full flex items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm space-y-6">
        <div className="flex flex-col items-center gap-2">
          <div className="flex items-center gap-2.5">
            <img src="/vectora.svg" alt="Vectora" width={36} height={36} />
            <h1
              className="text-2xl font-semibold tracking-tight text-foreground"
              style={{ fontFamily: "var(--font-aeonik-mono)" }}
            >
              Vectora
            </h1>
          </div>
          <p className="text-sm text-muted-foreground">
            {m.auth_signin_tagline()}
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-3">
          <div className="space-y-1">
            <label
              className="text-sm font-medium text-foreground"
              htmlFor="email"
            >
              {m.auth_email()}
            </label>
            <input
              id="email"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/60"
              placeholder={m.auth_email_ph()}
            />
          </div>

          <div className="space-y-1">
            <label
              className="text-sm font-medium text-foreground"
              htmlFor="password"
            >
              {m.auth_password()}
            </label>
            <div className="relative">
              <input
                id="password"
                type={showPassword ? "text" : "password"}
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                className="w-full rounded-md border border-border bg-background px-3 py-2 pr-10 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/60"
                placeholder={m.auth_signin_password_ph()}
              />
              <button
                type="button"
                onMouseDown={(e) => e.preventDefault()}
                onTouchEnd={(e) => {
                  e.preventDefault();
                  setShowPassword((v) => !v);
                }}
                onClick={() => setShowPassword((v) => !v)}
                className="absolute inset-y-0 right-0 flex items-center justify-center w-11 min-h-[44px] text-muted-foreground hover:text-foreground transition-colors touch-manipulation"
                aria-label={
                  showPassword ? m.auth_hide_password() : m.auth_show_password()
                }
              >
                {showPassword ? (
                  <EyeOff className="h-4 w-4" />
                ) : (
                  <Eye className="h-4 w-4" />
                )}
              </button>
            </div>
          </div>

          {error && (
            <p className="text-sm text-destructive bg-destructive/10 rounded-md px-3 py-2">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full rounded-md bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-60 transition-colors"
          >
            {loading ? m.auth_signin_submitting() : m.auth_signin_submit()}
          </button>
        </form>

        {ssoEnabled && (
          <>
            <div className="flex items-center gap-3">
              <div className="h-px flex-1 bg-border" />
              <span className="text-xs text-muted-foreground">
                {m.auth_signin_sso_divider()}
              </span>
              <div className="h-px flex-1 bg-border" />
            </div>
            <a
              href="/auth/oidc/login"
              className="block w-full rounded-md border border-border px-4 py-2.5 text-center text-sm font-medium text-foreground hover:bg-muted transition-colors"
            >
              {m.auth_signin_sso()}
            </a>
          </>
        )}
      </div>
    </div>
  );
}
