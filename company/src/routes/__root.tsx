import {
  HeadContent,
  Scripts,
  createRootRouteWithContext,
  useRouterState,
} from "@tanstack/react-router";
import { Toaster } from "sonner";
import Devtools from "#/components/shared/Devtools";
import { getLocale } from "#/paraglide/runtime";
import { m } from "#/paraglide/messages";
import { GA4_ID } from "#/lib/analytics/ga4";
import { getSession } from "#/server/fns/auth";
import { THEME_INIT_SCRIPT } from "#/lib/theme";
import Header from "#/components/shared/Header";
import Footer from "#/components/shared/Footer";
import CookieConsent from "#/components/shared/CookieConsent";
import appCss from "../styles.css?url";
import type { QueryClient } from "@tanstack/react-query";
import { directionForLocale } from "../lib/direction";

interface MyRouterContext {
  queryClient: QueryClient;
}

export const Route = createRootRouteWithContext<MyRouterContext>()({
  beforeLoad: async () => {
    if (typeof document !== "undefined") {
      const locale = getLocale();
      document.documentElement.setAttribute("lang", locale);
      document.documentElement.setAttribute("dir", directionForLocale(locale));
    }
    const session = await getSession();
    return { session };
  },

  head: () => {
    const plausibleDomain =
      typeof import.meta !== "undefined"
        ? (import.meta.env.VITE_PLAUSIBLE_DOMAIN ?? "vectora.company")
        : "vectora.company";

    return {
      meta: [
        { charSet: "utf-8" },
        { name: "viewport", content: "width=device-width, initial-scale=1" },
        { title: m.site_title() },
        { name: "description", content: m.site_description() },
        { property: "og:site_name", content: "Vectora" },
        { property: "og:type", content: "website" },
        { name: "twitter:card", content: "summary_large_image" },
        ...(typeof import.meta !== "undefined" &&
        import.meta.env.VITE_GOOGLE_SITE_VERIFICATION
          ? [
              {
                name: "google-site-verification",
                content: import.meta.env.VITE_GOOGLE_SITE_VERIFICATION,
              },
            ]
          : []),
      ],
      links: [
        { rel: "stylesheet", href: appCss },
        { rel: "icon", href: "/favicon-32x32.png", sizes: "32x32" },
        { rel: "icon", href: "/favicon-16x16.png", sizes: "16x16" },
        {
          rel: "preload",
          href: "/fonts/aeonikmono-regular.otf",
          as: "font",
          type: "font/otf",
          crossOrigin: "anonymous",
        },
      ],
      scripts: [
        // Anti-FOUC: aplica .dark/.light antes do primeiro paint.
        { children: THEME_INIT_SCRIPT },
        {
          src: `https://plausible.io/js/script.js`,
          defer: true,
          "data-domain": plausibleDomain,
        },
        ...(GA4_ID
          ? [
              // Consent Mode v2: defaults negados até o usuário aceitar.
              // Lê localStorage aqui (inline, client-only) antes de qualquer
              // hit do GA4 para estar em conformidade com LGPD/GDPR/CCPA.
              {
                children: `window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments)}try{var _c=localStorage.getItem('cookie-consent');gtag('consent','default',{analytics_storage:_c==='accepted'?'granted':'denied',ad_storage:'denied',ad_user_data:'denied',ad_personalization:'denied'})}catch(e){}`,
              },
              {
                src: `https://www.googletagmanager.com/gtag/js?id=${GA4_ID}`,
                async: true,
              },
              {
                children: `gtag('js',new Date());gtag('config','${GA4_ID}');`,
              },
            ]
          : []),
      ],
    };
  },

  shellComponent: RootDocument,
});

function RootDocument({ children }: { children: React.ReactNode }) {
  const { session } = Route.useRouteContext();
  const { location } = useRouterState();
  const isDashboard =
    location.pathname.startsWith("/dashboard") ||
    location.pathname.startsWith("/admin");
  const isAuth =
    location.pathname === "/login" ||
    location.pathname === "/signup" ||
    location.pathname.startsWith("/auth/");

  return (
    // suppressHydrationWarning: a classe do tema (dark/light) é aplicada por
    // script inline antes da hidratação e nunca bate com o HTML do SSR.
    <html
      lang={getLocale()}
      dir={directionForLocale(getLocale())}
      suppressHydrationWarning
    >
      <head>
        <HeadContent />
      </head>
      <body>
        {!isDashboard && !isAuth && <Header session={session} />}
        {children}
        {!isDashboard && !isAuth && <Footer />}
        <CookieConsent />
        <Toaster
          position="bottom-right"
          toastOptions={{
            style: {
              background: "var(--card)",
              border: "1px solid var(--border)",
              color: "var(--card-foreground)",
            },
          }}
        />
        <Devtools />
        <Scripts />
      </body>
    </html>
  );
}
