import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Eye, EyeOff } from "lucide-react";
import { m } from "#/paraglide/messages";
import AuthLayout from "#/components/shared/AuthLayout";
import Turnstile from "#/components/shared/Turnstile";
import { getSession, signUp } from "#/server/fns/auth";
import { track } from "#/lib/analytics/plausible";
import { toast } from "sonner";

export const Route = createFileRoute("/signup")({
  beforeLoad: async () => {
    const user = await getSession();
    if (user) throw { redirect: { to: "/dashboard" } };
  },
  head: () => ({ meta: [{ title: m.page_signup_title() }] }),
  component: SignupPage,
});

const AUTH_ERROR_MAP: Partial<Record<string, () => string>> = {
  email_taken: m.error_email_taken,
  password_too_short: m.error_password_weak,
  turnstile_failed: m.error_turnstile,
  name_required: m.error_generic,
  invalid_email: m.error_generic,
};

function SignupPage() {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [turnstileToken, setTurnstileToken] = useState<string | null>(null);
  const [confirmationSent, setConfirmationSent] = useState(false);

  const mutation = useMutation({
    mutationFn: () =>
      signUp({
        data: {
          name,
          email,
          password,
          turnstileToken: turnstileToken!,
        },
      }),
    onSuccess: () => {
      track("signup", { plan: "pro" });
      setConfirmationSent(true);
    },
    onError: (err: Error) => {
      const msgFn =
        AUTH_ERROR_MAP[err.message] ??
        Object.entries(AUTH_ERROR_MAP).find(([k]) =>
          err.message.startsWith(k),
        )?.[1];
      toast.error(msgFn ? msgFn() : err.message || m.error_generic());
    },
  });

  const canSubmit =
    name.length >= 2 &&
    email.includes("@") &&
    password.length >= 8 &&
    turnstileToken !== null &&
    !mutation.isPending;

  if (confirmationSent) {
    return (
      <AuthLayout heading={m.signup_confirm_heading()}>
        <div className="space-y-4 text-sm leading-relaxed text-muted-foreground">
          <p>
            {m.signup_confirm_desc()}{" "}
            <strong className="text-foreground">{email}</strong>.
          </p>
          <p>{m.signup_confirm_hint()}</p>
          <Link
            to="/login"
            className="inline-block text-primary transition-colors hover:text-primary/80"
          >
            {m.signup_have_account()}
          </Link>
        </div>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout heading={m.signup_heading()}>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (canSubmit) mutation.mutate();
        }}
        className="space-y-4"
      >
        <div>
          <label className="mb-1.5 block text-sm font-medium text-foreground/90">
            {m.form_name()}
          </label>
          <input
            type="text"
            required
            minLength={2}
            autoComplete="name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="w-full rounded-xl border border-border bg-card/60 px-4 py-2.5 text-sm text-foreground placeholder:text-muted-foreground outline-none focus:border-primary transition-colors"
          />
        </div>

        <div>
          <label className="mb-1.5 block text-sm font-medium text-foreground/90">
            {m.form_email()}
          </label>
          <input
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded-xl border border-border bg-card/60 px-4 py-2.5 text-sm text-foreground placeholder:text-muted-foreground outline-none focus:border-primary transition-colors"
          />
        </div>

        <div>
          <label className="mb-1.5 block text-sm font-medium text-foreground/90">
            {m.form_password()}
          </label>
          <div className="relative">
            <input
              type={showPassword ? "text" : "password"}
              required
              minLength={8}
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-xl border border-border bg-card/60 px-4 py-2.5 pe-11 text-sm text-foreground placeholder:text-muted-foreground outline-none focus:border-primary transition-colors"
            />
            <button
              type="button"
              onClick={() => setShowPassword((v) => !v)}
              aria-label={
                showPassword ? m.form_password_hide() : m.form_password_show()
              }
              className="absolute end-3 top-1/2 -translate-y-1/2 text-muted-foreground transition-colors hover:text-foreground"
            >
              {showPassword ? (
                <EyeOff className="h-4 w-4" />
              ) : (
                <Eye className="h-4 w-4" />
              )}
            </button>
          </div>
        </div>

        <Turnstile onSuccess={setTurnstileToken} />

        <button
          type="submit"
          disabled={!canSubmit}
          className="w-full rounded-xl bg-primary py-3 text-sm font-semibold text-primary-foreground shadow shadow-primary/25 transition-all hover:bg-primary/90 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {mutation.isPending ? m.form_submitting() : m.signup_cta()}
        </button>
      </form>

      <div className="mt-6 flex justify-between text-sm text-muted-foreground">
        <Link
          to="/login"
          className="hover:text-foreground/90 transition-colors"
        >
          {m.signup_have_account()}
        </Link>
        <Link
          to="/"
          hash="pricing"
          className="hover:text-foreground/90 transition-colors"
        >
          {m.signup_see_pricing()}
        </Link>
      </div>
    </AuthLayout>
  );
}
