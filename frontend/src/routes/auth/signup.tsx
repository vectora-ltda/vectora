import { useState, useEffect, useMemo } from "react";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import { Eye, EyeOff } from "lucide-react";
import { z } from "zod";

import { useAuthStore } from "@/lib/stores/auth-store";
import type { AuthUser } from "@/lib/types/auth";
import { m } from "@/lib/paraglide/messages";
import { consumeVpsGatePassed } from "@/lib/stores/onboarding-signal";
import {
  slugifyUsername,
  checkUsername,
  type UsernameStatus,
} from "@/lib/api/username";

/** Tamanho mínimo de senha — espelha a validação do backend. */
const PASSWORD_MIN = 8;
const NAME_MAX = 100;

const searchSchema = z.object({
  invite: z.string().optional(),
  name: z.string().optional(),
  username: z.string().optional(),
});

export const Route = createFileRoute("/auth/signup")({
  validateSearch: searchSchema,
  component: SignUpPage,
});

function SignUpPage() {
  const navigate = useNavigate();
  const {
    invite: inviteFromUrl,
    name: nameFromUrl,
    username: usernameFromUrl,
  } = Route.useSearch();
  const setUser = useAuthStore((s) => s.setUser);

  const schema = useMemo(
    () =>
      z
        .object({
          name: z
            .string()
            .trim()
            .min(1, m.auth_signup_name_required())
            .max(NAME_MAX, m.auth_signup_name_too_long({ n: NAME_MAX })),
          email: z.string().email(m.auth_email_invalid()),
          password: z
            .string()
            .min(PASSWORD_MIN, m.auth_signup_password_min({ n: PASSWORD_MIN })),
          confirm: z.string(),
        })
        .refine((d) => d.password === d.confirm, {
          message: m.auth_signup_passwords_mismatch(),
          path: ["confirm"],
        }),
    [],
  );

  const [name, setName] = useState(nameFromUrl ?? "");
  const [username, setUsername] = useState(() =>
    usernameFromUrl?.trim()
      ? slugifyUsername(usernameFromUrl)
      : slugifyUsername(nameFromUrl ?? ""),
  );
  // Enquanto o usuário não editar o username manualmente, ele segue o slug do
  // nome. Após a primeira edição, para de auto-sincronizar — um username já
  // escolhido no wizard de onboarding (usernameFromUrl) conta como "editado"
  // pra não ser sobrescrito pelo slug do nome no primeiro render.
  const [usernameEdited, setUsernameEdited] = useState(
    () => !!usernameFromUrl?.trim(),
  );
  const [usernameStatus, setUsernameStatus] = useState<UsernameStatus | null>(
    null,
  );
  const [checkingUsername, setCheckingUsername] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [ready, setReady] = useState(false);
  const [inviteToken, setInviteToken] = useState("");
  const [inviteRole, setInviteRole] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function resolve() {
      let hasUsers = true;
      try {
        const r = await fetch("/auth/has-users", { credentials: "include" });
        hasUsers = Boolean((await r.json()).exists);
      } catch {
        hasUsers = true;
      }
      if (cancelled) return;

      if (!hasUsers) {
        // Só mostra o formulário direto se veio do gate VPS do wizard
        // (token Pro já validado) — chegada direta (bookmark, histórico do
        // navegador) com a instância ainda zerada volta pro wizard novo.
        if (consumeVpsGatePassed()) {
          setReady(true);
          return;
        }
        void navigate({ to: "/onboarding" });
        return;
      }

      const token = inviteFromUrl ?? "";
      if (token) {
        try {
          const r = await fetch(`/auth/invite/${token}`, {
            credentials: "include",
          });
          const data = await r.json();
          if (!cancelled && data.valid) {
            setInviteToken(token);
            setInviteRole(data.role ?? "member");
            if (data.email) setEmail(data.email);
            setReady(true);
            return;
          }
        } catch {
          // cai no redirect abaixo
        }
      }

      if (!cancelled) {
        void navigate({ to: "/auth/signin" });
      }
    }

    void resolve();
    return () => {
      cancelled = true;
    };
  }, [navigate, inviteFromUrl]);

  // Checagem de disponibilidade com debounce — reflete o backend ao vivo.
  useEffect(() => {
    const u = username.trim();
    if (!u) {
      // Sincroniza com a checagem debounçada abaixo — username vazio não
      // tem request pendente a esperar.
      // oxlint-disable-next-line react/set-state-in-effect
      setUsernameStatus(null);
      setCheckingUsername(false);
      return;
    }
    setCheckingUsername(true);
    let cancelled = false;
    const handle = setTimeout(() => {
      void checkUsername(u).then((status) => {
        if (cancelled) return;
        setUsernameStatus(status);
        setCheckingUsername(false);
      });
    }, 350);
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [username]);

  function applySuggestion() {
    if (!usernameStatus) return;
    setUsernameEdited(true);
    setUsername(usernameStatus.suggestion);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setServerError(null);
    setErrors({});

    if (!username.trim()) {
      setErrors({ username: m.auth_signup_username_required() });
      return;
    }
    // Bloqueia envio quando o backend já sabe que está em uso (o 409 é o
    // backstop de corrida; aqui é UX imediata).
    if (usernameStatus && !usernameStatus.available) {
      setErrors({ username: m.auth_signup_username_taken() });
      return;
    }

    const result = schema.safeParse({ name, email, password, confirm });
    if (!result.success) {
      const fieldErrors: Record<string, string> = {};
      for (const err of result.error.issues) {
        const field = err.path[0]?.toString() ?? "form";
        fieldErrors[field] = err.message;
      }
      setErrors(fieldErrors);
      return;
    }

    setLoading(true);
    try {
      const res = await fetch("/auth/signup", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: name.trim(),
          username: username.trim(),
          email,
          password,
          invite_token: inviteToken,
        }),
      });

      const data = await res.json();
      if (!res.ok) {
        setServerError(data.detail ?? m.auth_signup_create_error());
        return;
      }

      setUser(data.user as AuthUser);
      // Continua no mesmo welcome multi-step (token/storage/chaves/workspace/
      // memória/capacidades) antes do reload final — unifica local e VPS num
      // único fluxo em vez de cair direto no app.
      void navigate({ to: "/onboarding", search: { continue: "1" } });
    } catch {
      setServerError(m.auth_conn_error());
    } finally {
      setLoading(false);
    }
  }

  if (!ready) {
    return (
      <div className="min-h-full flex items-center justify-center bg-background">
        <p className="text-sm text-muted-foreground">{m.auth_loading()}</p>
      </div>
    );
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
          <div className="text-center">
            {inviteRole ? (
              <>
                <p className="text-sm text-muted-foreground">
                  {m.auth_signup_invite_title()}
                </p>
                <p className="text-xs text-muted-foreground/70 mt-0.5">
                  {m.auth_signup_invite_role()}{" "}
                  <span className="text-primary font-medium">{inviteRole}</span>
                </p>
              </>
            ) : (
              <>
                <p className="text-sm text-muted-foreground">
                  {m.auth_signup_first_access()}
                </p>
                <p className="text-xs text-muted-foreground/70 mt-0.5">
                  {m
                    .auth_signup_root_hint({ root: "{root}" })
                    .split("{root}")
                    .flatMap((part, i) =>
                      i === 0
                        ? [part]
                        : [
                            <span
                              key="root"
                              className="text-yellow-400 font-medium"
                            >
                              root
                            </span>,
                            part,
                          ],
                    )}
                </p>
              </>
            )}
          </div>
        </div>

        <form onSubmit={handleSubmit} className="space-y-3">
          <div className="space-y-1">
            <label
              className="text-sm font-medium text-foreground"
              htmlFor="name"
            >
              {m.auth_signup_name()}
            </label>
            <input
              id="name"
              type="text"
              autoComplete="name"
              value={name}
              onChange={(e) => {
                const next = e.target.value;
                setName(next);
                // Auto-preenche o username a partir do nome enquanto não
                // houver edição manual (ver handler do campo username).
                if (!usernameEdited) setUsername(slugifyUsername(next));
              }}
              required
              maxLength={NAME_MAX}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/60"
              placeholder={m.auth_signup_name_ph()}
            />
            {errors.name && (
              <p className="text-xs text-destructive">{errors.name}</p>
            )}
          </div>

          <div className="space-y-1">
            <label
              className="text-sm font-medium text-foreground"
              htmlFor="username"
            >
              {m.auth_signup_username()}
            </label>
            <div className="flex items-center rounded-md border border-border bg-background px-3 focus-within:ring-2 focus-within:ring-primary/60">
              <span className="text-sm text-muted-foreground select-none">
                @
              </span>
              <input
                id="username"
                type="text"
                autoComplete="off"
                value={username}
                onChange={(e) => {
                  setUsernameEdited(true);
                  setUsername(e.target.value);
                }}
                required
                className="w-full bg-transparent py-2 pl-1 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none"
                placeholder={m.auth_signup_username_ph()}
              />
            </div>
            {errors.username ? (
              <p className="text-xs text-destructive">{errors.username}</p>
            ) : checkingUsername ? (
              <p className="text-xs text-muted-foreground">
                {m.auth_signup_username_checking()}
              </p>
            ) : usernameStatus && username.trim() ? (
              usernameStatus.available ? (
                <p className="text-xs text-green-500">
                  {m.auth_signup_username_available()}
                </p>
              ) : (
                <p className="text-xs text-destructive">
                  {m.auth_signup_username_taken()}{" "}
                  <button
                    type="button"
                    onClick={applySuggestion}
                    className="text-primary hover:underline"
                  >
                    {m.auth_signup_username_use_suggestion({
                      suggestion: usernameStatus.suggestion,
                    })}
                  </button>
                </p>
              )
            ) : null}
          </div>

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
            {errors.email && (
              <p className="text-xs text-destructive">{errors.email}</p>
            )}
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
                autoComplete="new-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                className="w-full rounded-md border border-border bg-background px-3 py-2 pr-10 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/60"
                placeholder={m.auth_signup_password_ph({ n: PASSWORD_MIN })}
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
            {errors.password && (
              <p className="text-xs text-destructive">{errors.password}</p>
            )}
          </div>

          <div className="space-y-1">
            <label
              className="text-sm font-medium text-foreground"
              htmlFor="confirm"
            >
              {m.auth_signup_confirm()}
            </label>
            <div className="relative">
              <input
                id="confirm"
                type={showConfirm ? "text" : "password"}
                autoComplete="new-password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                required
                className="w-full rounded-md border border-border bg-background px-3 py-2 pr-10 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/60"
                placeholder={m.auth_signup_confirm_ph()}
              />
              <button
                type="button"
                onMouseDown={(e) => e.preventDefault()}
                onTouchEnd={(e) => {
                  e.preventDefault();
                  setShowConfirm((v) => !v);
                }}
                onClick={() => setShowConfirm((v) => !v)}
                className="absolute inset-y-0 right-0 flex items-center justify-center w-11 min-h-[44px] text-muted-foreground hover:text-foreground transition-colors touch-manipulation"
                aria-label={
                  showConfirm
                    ? m.auth_signup_hide_confirm()
                    : m.auth_signup_show_confirm()
                }
              >
                {showConfirm ? (
                  <EyeOff className="h-4 w-4" />
                ) : (
                  <Eye className="h-4 w-4" />
                )}
              </button>
            </div>
            {errors.confirm && (
              <p className="text-xs text-destructive">{errors.confirm}</p>
            )}
          </div>

          {serverError && (
            <p className="text-sm text-destructive bg-destructive/10 rounded-md px-3 py-2">
              {serverError}
            </p>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full rounded-md bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-60 transition-colors"
          >
            {loading ? m.auth_signup_submitting() : m.auth_signup_submit()}
          </button>
        </form>

        <p className="text-center text-xs text-muted-foreground">
          {m.auth_signup_have_account()}{" "}
          <Link to={"/auth/signin"} className="text-primary hover:underline">
            {m.auth_signup_signin_link()}
          </Link>
        </p>
      </div>
    </div>
  );
}
