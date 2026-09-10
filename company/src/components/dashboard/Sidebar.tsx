import { Link, useRouterState, useRouteContext } from "@tanstack/react-router";
import { m } from "#/paraglide/messages";
import {
  Key,
  Shield,
  CreditCard,
  Zap,
  User,
  HelpCircle,
  ShieldCheck,
  LogOut,
  Bot,
} from "lucide-react";
import Logo from "#/components/shared/Logo";
import ThemeToggle from "#/components/shared/ThemeToggle";
import { signOut } from "#/server/fns/auth";

const NAV_ITEMS = [
  { to: "/dashboard" as const, exact: true, icon: Key, label: m.nav_token },
  {
    to: "/dashboard/license" as const,
    exact: false,
    icon: Shield,
    label: m.nav_license,
  },
  {
    to: "/dashboard/billing" as const,
    exact: false,
    icon: CreditCard,
    label: m.nav_billing,
  },
  {
    to: "/dashboard/api-keys" as const,
    exact: false,
    icon: Zap,
    label: m.nav_api_keys,
  },
  {
    to: "/dashboard/account" as const,
    exact: false,
    icon: User,
    label: m.nav_account,
  },
  {
    to: "/dashboard/gh-bot" as const,
    exact: false,
    icon: Bot,
    label: m.nav_gh_bot,
  },
  {
    to: "/support" as const,
    exact: false,
    icon: HelpCircle,
    label: m.nav_support,
  },
] as const;

export default function Sidebar() {
  const { location } = useRouterState();
  const pathname = location.pathname;
  const { session } = useRouteContext({ from: "__root__" });
  const isAdmin = session?.role === "admin";

  const desktopItems = isAdmin
    ? [
        ...NAV_ITEMS,
        {
          to: "/admin" as const,
          exact: false,
          icon: ShieldCheck,
          label: m.nav_admin,
        },
      ]
    : NAV_ITEMS;

  return (
    <>
      {/* Desktop sidebar */}
      <aside className="hidden lg:flex lg:flex-col w-56 shrink-0 border-s border-border bg-background min-h-screen py-6">
        <div className="mb-8 flex items-center justify-between px-5">
          <Logo size="sm" />
          <ThemeToggle />
        </div>
        <nav className="flex flex-col gap-0.5 px-3">
          {desktopItems.map((item) => {
            const active = item.exact
              ? pathname === item.to
              : pathname.startsWith(item.to);
            const Icon = item.icon;
            return (
              <Link
                key={item.to}
                to={item.to}
                className={`flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-all ${
                  active
                    ? "bg-primary/15 text-primary"
                    : "text-muted-foreground hover:bg-card hover:text-foreground"
                }`}
              >
                <Icon
                  className={`h-4 w-4 shrink-0 ${active ? "text-primary" : ""}`}
                />
                {item.label()}
              </Link>
            );
          })}
        </nav>
        <button
          type="button"
          onClick={async () => {
            await signOut();
            window.location.href = "/login";
          }}
          className="mt-auto flex items-center gap-3 rounded-lg px-3 py-2.5 mx-3 text-sm font-medium text-muted-foreground transition-all hover:bg-card hover:text-foreground"
        >
          <LogOut className="h-4 w-4 shrink-0" />
          {m.nav_logout()}
        </button>
      </aside>

      {/* Mobile bottom tab bar (first 5 items only — gh-bot/support excluded) */}
      <nav className="lg:hidden fixed bottom-0 inset-x-0 z-50 border-t border-border bg-background/95 backdrop-blur flex">
        {NAV_ITEMS.slice(0, 5).map((item) => {
          const active = item.exact
            ? pathname === item.to
            : pathname.startsWith(item.to);
          const Icon = item.icon;
          return (
            <Link
              key={item.to}
              to={item.to}
              className={`flex-1 flex flex-col items-center py-2 gap-0.5 text-[10px] font-medium transition-colors ${
                active
                  ? "text-primary"
                  : "text-muted-foreground hover:text-foreground/90"
              }`}
            >
              <Icon className="h-5 w-5" />
              {item.label()}
            </Link>
          );
        })}
      </nav>
    </>
  );
}
