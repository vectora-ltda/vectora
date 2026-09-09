import { useState, useRef, useEffect } from "react";
import { Link } from "@tanstack/react-router";
import { Menu, X } from "lucide-react";
import { m } from "#/paraglide/messages";
import type { SessionUser } from "#/server/fns/auth";
import { getLocale, locales, setLocale } from "#/paraglide/runtime";
import { getDocsUrl } from "#/lib/docs-url";
import Logo from "./Logo";
import ThemeToggle from "./ThemeToggle";

const LOCALE_LABELS: Partial<Record<string, string>> = {
  pt: "PT",
  en: "EN",
  es: "ES",
  fr: "FR",
  it: "IT",
  de: "DE",
  ru: "RU",
};

/** Pill compartilhada: fundo card + borda-raio 16 px + sombra sutil */
const pill =
  "flex items-center justify-center bg-card rounded-2xl shadow-[0px_1px_3px_rgba(24,25,28,0.3),0px_1px_2px_-1px_rgba(24,25,28,0.3)]";

export default function Header({ session }: { session: SessionUser | null }) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const [localeOpen, setLocaleOpen] = useState(false);
  const localeRef = useRef<HTMLDivElement>(null);
  const locale = getLocale();

  useEffect(() => {
    if (!localeOpen) return;
    const handler = (e: MouseEvent) => {
      if (localeRef.current && !localeRef.current.contains(e.target as Node)) {
        setLocaleOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [localeOpen]);

  return (
    <header className="sticky top-0 z-50 border-b border-border bg-background/80 backdrop-blur-md">
      {/*
        Container relativo: permite que a nav use `absolute start-1/2 -translate-x-1/2`
        para ficar de fato centrada na viewport, independente do que está nos lados.
        Desktop: max-w-[1124px] conforme Figma.
      */}
      <div className="relative mx-auto flex h-[62px] max-w-[1124px] items-center justify-between px-4 sm:px-6">
        {/* ── Logo ── */}
        <Logo size="md" />

        {/* ── Nav desktop (absolutamente centralizada) ── */}
        <nav
          className="absolute start-1/2 hidden -translate-x-1/2 items-center gap-6 text-sm md:flex"
          aria-label="Navegação principal"
        >
          <a
            href={getDocsUrl()}
            target="_blank"
            rel="noopener noreferrer"
            className="text-muted-foreground transition-colors hover:text-foreground"
          >
            {m.nav_docs()}
          </a>
          <Link
            to="/faq"
            className="text-muted-foreground transition-colors hover:text-foreground data-[status=active]:text-foreground"
          >
            {m.nav_faq()}
          </Link>
          <Link
            to="/issues"
            className="text-muted-foreground transition-colors hover:text-foreground data-[status=active]:text-foreground"
          >
            {m.nav_issues()}
          </Link>
          <Link
            to="/downloads"
            className="text-muted-foreground transition-colors hover:text-foreground data-[status=active]:text-foreground"
          >
            {m.nav_downloads()}
          </Link>
        </nav>

        {/* ── Ações (direita) ── */}
        <div className="flex items-center gap-4">
          {/* Locale dropdown (só desktop) */}
          <div ref={localeRef} className="relative hidden md:block">
            <button
              type="button"
              onClick={() => setLocaleOpen((v) => !v)}
              aria-label={m.language_label()}
              aria-expanded={localeOpen}
              aria-haspopup="listbox"
              className={`h-[34px] w-8 cursor-pointer text-sm text-muted-foreground ${pill}`}
            >
              {LOCALE_LABELS[locale] ?? locale.toUpperCase()}
            </button>
            {localeOpen && (
              <div
                role="listbox"
                className="absolute start-1/2 top-[calc(100%+6px)] z-50 w-fit min-w-[3.25rem] -translate-x-1/2 overflow-hidden rounded-2xl border border-border bg-card shadow-[0px_4px_12px_rgba(0,0,0,0.3)]"
              >
                {locales.map((l) => (
                  <button
                    key={l}
                    role="option"
                    aria-selected={l === locale}
                    type="button"
                    onClick={() => {
                      setLocale(l);
                      setLocaleOpen(false);
                    }}
                    className={`flex w-full cursor-pointer items-center justify-center px-3 py-2 text-sm transition-colors hover:bg-muted ${
                      l === locale
                        ? "font-medium text-primary"
                        : "text-muted-foreground"
                    }`}
                  >
                    {LOCALE_LABELS[l] ?? l.toUpperCase()}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Theme toggle (só desktop) */}
          <div className="hidden md:block">
            <ThemeToggle />
          </div>

          {session ? (
            <Link
              to="/dashboard"
              className={`hidden h-[34px] px-4 text-sm font-semibold text-primary transition-colors hover:text-primary/80 md:flex ${pill}`}
            >
              Dashboard
            </Link>
          ) : (
            <>
              {/* Entrar */}
              <Link
                to="/login"
                className={`hidden h-[34px] px-4 text-sm font-semibold text-primary transition-colors hover:text-primary/80 md:flex ${pill}`}
              >
                {m.nav_login()}
              </Link>

              {/* Criar Conta */}
              <Link
                to="/signup"
                className="hidden h-[34px] items-center rounded-2xl bg-primary px-4 text-sm font-semibold text-primary-foreground shadow-[0px_1px_3px_rgba(121,184,255,0.3),0px_1px_2px_-1px_rgba(121,184,255,0.3)] transition-colors hover:bg-primary/90 md:flex"
              >
                {m.nav_signup()}
              </Link>
            </>
          )}

          {/* Hamburger (só mobile) */}
          <button
            type="button"
            onClick={() => setMobileOpen((v) => !v)}
            aria-label={m.nav_menu()}
            aria-expanded={mobileOpen}
            className={`h-9 w-9 text-foreground/80 transition-colors hover:text-foreground md:hidden ${pill}`}
          >
            {mobileOpen ? (
              <X className="h-4 w-4" />
            ) : (
              <Menu className="h-4 w-4" />
            )}
          </button>
        </div>
      </div>

      {/* ── Menu mobile ── */}
      {mobileOpen && (
        <nav
          className="border-t border-border bg-background/95 px-5 py-4 md:hidden"
          aria-label="Menu mobile"
        >
          <div className="flex flex-col gap-0.5 text-sm">
            <a
              href={getDocsUrl()}
              target="_blank"
              rel="noopener noreferrer"
              onClick={() => setMobileOpen(false)}
              className="rounded-xl px-3 py-2.5 text-foreground/80 transition-colors hover:bg-muted hover:text-foreground"
            >
              {m.nav_docs()}
            </a>
            <Link
              to="/faq"
              onClick={() => setMobileOpen(false)}
              className="rounded-xl px-3 py-2.5 text-foreground/80 transition-colors hover:bg-muted hover:text-foreground data-[status=active]:bg-muted data-[status=active]:text-foreground"
            >
              {m.nav_faq()}
            </Link>
            <Link
              to="/issues"
              onClick={() => setMobileOpen(false)}
              className="rounded-xl px-3 py-2.5 text-foreground/80 transition-colors hover:bg-muted hover:text-foreground data-[status=active]:bg-muted data-[status=active]:text-foreground"
            >
              {m.nav_issues()}
            </Link>
            <Link
              to="/downloads"
              onClick={() => setMobileOpen(false)}
              className="rounded-xl px-3 py-2.5 text-foreground/80 transition-colors hover:bg-muted hover:text-foreground data-[status=active]:bg-muted data-[status=active]:text-foreground"
            >
              {m.nav_downloads()}
            </Link>

            {session ? (
              <Link
                to="/dashboard"
                onClick={() => setMobileOpen(false)}
                className="mt-3 rounded-xl bg-primary px-3 py-2.5 text-center font-semibold text-primary-foreground transition-colors hover:bg-primary/90"
              >
                Dashboard
              </Link>
            ) : (
              <>
                <Link
                  to="/login"
                  onClick={() => setMobileOpen(false)}
                  className="rounded-xl px-3 py-2.5 font-semibold text-primary transition-colors hover:bg-muted"
                >
                  {m.nav_login()}
                </Link>
                <Link
                  to="/signup"
                  onClick={() => setMobileOpen(false)}
                  className="mt-1 rounded-xl bg-primary px-3 py-2.5 text-center font-semibold text-primary-foreground transition-colors hover:bg-primary/90"
                >
                  {m.nav_signup()}
                </Link>
              </>
            )}

            {/* Theme + locale no rodapé do menu */}
            <div className="mt-3 flex items-center gap-3 border-t border-border pt-3">
              <ThemeToggle />
              <div className="flex flex-wrap gap-1">
                {locales.map((l) => (
                  <button
                    key={l}
                    type="button"
                    onClick={() => setLocale(l)}
                    className={`rounded-xl px-2.5 py-1 text-xs transition-colors ${
                      l === locale
                        ? "bg-primary text-primary-foreground"
                        : "bg-card text-foreground/80 hover:bg-muted"
                    }`}
                  >
                    {LOCALE_LABELS[l] ?? l.toUpperCase()}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </nav>
      )}
    </header>
  );
}
