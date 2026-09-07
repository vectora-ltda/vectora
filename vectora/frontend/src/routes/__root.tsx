import { useEffect, useRef } from "react";
import {
  Outlet,
  createRootRouteWithContext,
  redirect,
  useLocation,
} from "@tanstack/react-router";
import type { RouterContext } from "../router";
import { useAuthStore } from "@/lib/stores/auth-store";
import { useWorkspacesStore } from "@/lib/stores/workspaces-store";
import { useSessionExpiry } from "@/lib/hooks/use-session-expiry";
import {
  useSettingsStore,
  hydrateFromBackend,
  FONT_SCALE_BASE_PX,
  type Lang,
} from "@/lib/stores/settings-store";
import {
  THEME_PRESETS,
  buildThemeTokens,
  applyThemeTokens,
  type ThemePresetDef,
} from "@/lib/theme/presets";
import { Toaster } from "@/components/ui/toaster";
import { NetworkStatusBanner } from "@/components/layout/network-status-banner";
import { UpdateBanner } from "@/components/layout/update-banner";
import { TitleBar } from "@/components/layout/title-bar";

const PUBLIC_PATH_PREFIXES = ["/auth/", "/share/", "/onboarding"];

// Cada `Lang` cobre todas as variantes regionais do idioma (mesmo padrão
// do `en`, que atende EUA/Reino Unido/Índia/etc sem distinção) — por isso
// o atributo `lang` do HTML usa o código BCP-47 genérico, sem região,
// para as três opções.
const HTML_LANG_BY_SETTING: Record<Lang, string> = {
  en: "en",
  es: "es",
  pt: "pt",
};

const AUTH_REQUIRED =
  (import.meta.env.VITE_VECTORA_AUTH_REQUIRED ?? "true").toLowerCase() !==
  "false";

function isPublicPath(pathname: string): boolean {
  return PUBLIC_PATH_PREFIXES.some((p) => pathname.startsWith(p));
}

function redirectToSignin(currentPath: string): never {
  // `as unknown as Parameters<...>`: a tipagem estrita do RedirectOptions
  // exige `from` para inferir tipos de `search`. O guard roda na raiz e
  // não tem `from` ainda.
  throw redirect({
    to: "/auth/signin",
    search: { from: currentPath },
  } as unknown as Parameters<typeof redirect>[0]);
}

/** Resolves built-in and installed presets to the tokens used by the root. */
export function resolveThemePreset(
  themePreset: string,
  installedThemes: ThemePresetDef[],
): ThemePresetDef | undefined {
  return [...THEME_PRESETS, ...installedThemes].find(
    (preset) => preset.id === themePreset,
  );
}

/**
 * Guard chamado pelo `beforeLoad` da rota raiz.
 *
 * Fluxo:
 *  1. Rotas públicas (`/auth/*`, `/share/*`, `/onboarding`) passam direto.
 *  2. `GET /auth/has-users`: vazio → redirect `/onboarding` (wizard local/VPS).
 *  3. `GET /auth/me`: 200 → libera + atualiza store.
 *  4. 401 → `POST /auth/refresh` silencioso; sucesso libera, falha
 *     limpa o store e vai para `/auth/signin?from=<currentPath>`.
 *
 * Cookies `vectora_access`/`vectora_refresh` httpOnly viajam em todas as
 * chamadas via `credentials: "include"`. O auth-store NUNCA é a fonte
 * de verdade — sempre validamos contra o backend.
 */
interface AuthFlags {
  authRequired: boolean;
  /** `false` quando `auth_required=false` mas o wizard (`POST
   * /auth/setup-local`) nunca rodou de verdade — ex.: `VECTORA_AUTH_REQUIRED
   * =false` esquecido num `.env` de projeto/dev. Sem essa distinção,
   * `auth_required=false` sozinho bastava pra pular o onboarding e fabricar
   * um usuário "Local User" fantasma sem o usuário nunca ter escolhido nome
   * nem modo — ver backend/api/handlers/flags.py. */
  localConfigured: boolean;
}

/** Lê `auth_required`/`local_configured` do backend em runtime — decidido
 * pelo wizard (`POST /auth/setup-local`), não fixado em build-time. Sem
 * resposta (rede offline/backend não subiu ainda), cai no `AUTH_REQUIRED`
 * estático como fallback seguro (mesmo comportamento de antes desta
 * mudança) e assume `localConfigured=true` pra não travar quem já tinha
 * sessão local válida antes do backend responder. */
async function getAuthFlags(): Promise<AuthFlags> {
  try {
    const res = await fetch("/settings/flags");
    if (!res.ok) return { authRequired: AUTH_REQUIRED, localConfigured: true };
    const data = (await res.json()) as {
      auth_required?: boolean;
      local_configured?: boolean;
    };
    return {
      authRequired: data.auth_required ?? AUTH_REQUIRED,
      localConfigured: data.local_configured ?? true,
    };
  } catch {
    return { authRequired: AUTH_REQUIRED, localConfigured: true };
  }
}

export async function ensureAuthenticated(currentPath: string): Promise<void> {
  if (isPublicPath(currentPath)) return;

  const { authRequired, localConfigured } = await getAuthFlags();

  // Aguarda o rehydrate do persist antes de inspecionar o store: o
  // contrato do `persist` em Zustand é assíncrono e ler o estado vazio
  // antes da hidratação faria o guard despachar para `/auth/signin`
  // mesmo com sessão válida em `sessionStorage`.
  if (
    typeof (
      useAuthStore as unknown as {
        persist?: { rehydrate?: () => Promise<void> };
      }
    ).persist?.rehydrate === "function"
  ) {
    try {
      await (
        useAuthStore as unknown as {
          persist: { rehydrate: () => Promise<void> };
        }
      ).persist.rehydrate();
    } catch {
      /* rehydrate é best-effort — não bloqueia o guard */
    }
  }

  const store = useAuthStore.getState();

  // Primeiro acesso (backend sem usuários) → wizard de identidade/modo
  // (local vs VPS). Verificado quando auth é exigida — depois do wizard
  // rodar (`POST /auth/setup-local`), auth_required vira false e o modo
  // local nunca cria linha em `users`, então checar has-users
  // incondicionalmente prenderia toda visita local num loop de volta pro
  // onboarding.
  //
  // `!localConfigured` cobre o caso auth_required=false SEM o wizard ter
  // rodado (env var externa desligando auth por engano) — sem isso o guard
  // pulava direto pro app com um usuário "Local User" fabricado, sem o
  // usuário nunca ter escolhido nome/username/modo.
  if (authRequired || !localConfigured) {
    if (!authRequired) {
      store.clearUser();
      throw redirect({ to: "/onboarding" });
    }
    try {
      const hasUsersRes = await fetch("/auth/has-users", {
        credentials: "include",
      });
      if (hasUsersRes.ok) {
        const data = (await hasUsersRes.json()) as { exists?: boolean };
        if (data.exists === false) {
          store.clearUser();
          throw redirect({ to: "/onboarding" });
        }
      }
    } catch (err) {
      if (err && typeof err === "object" && "to" in err) throw err;
      // Backend offline — não interrompe. O fetch abaixo vai falhar igual.
    }
  }

  // Busca /auth/me incondicionalmente — mesmo com auth_required=false o
  // backend sempre devolve alguém (conta real no Pro, usuário virtual
  // "local" no Free — ver _get_virtual_local_user em
  // backend/api/middleware/auth.py). Sem isso o store nunca populava
  // `user` no modo Free e a UI (nome no SettingsMenu, botão Administração)
  // ficava vazia mesmo com sessão local válida.
  let meRes: Response;
  try {
    meRes = await fetch("/auth/me", { credentials: "include" });
  } catch {
    store.clearUser();
    if (authRequired) redirectToSignin(currentPath);
    return;
  }

  if (meRes.ok) {
    // Sincroniza o store com a resposta canônica do backend.
    // Se o JSON falhar (ex.: proxy retornou index.html quando backend está
    // offline), trata como não autenticado — NÃO retorna silenciosamente.
    try {
      const user = await meRes.json();
      if (user?.id) {
        useAuthStore.getState().setUser(user);
        return;
      }
    } catch {
      /* corpo inválido — cai no clear+redirect abaixo */
    }
    store.clearUser();
    if (authRequired) redirectToSignin(currentPath);
    return;
  }

  if (meRes.status === 401) {
    try {
      const refresh = await fetch("/auth/refresh", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      if (refresh.ok) {
        const retry = await fetch("/auth/me", { credentials: "include" });
        if (retry.ok) {
          try {
            useAuthStore.getState().setUser(await retry.json());
            return;
          } catch {
            /* idem */
          }
        }
      }
    } catch {
      /* cai no clear+redirect abaixo */
    }
  }

  store.clearUser();
  if (authRequired) redirectToSignin(currentPath);
}

export const Route = createRootRouteWithContext<RouterContext>()({
  beforeLoad: async ({ location }) => {
    await ensureAuthenticated(location.pathname);
  },
  component: RootComponent,
});

function RootComponent() {
  const location = useLocation();
  // Idioma persistido em settings-store — reflete a preferência de fato
  // escolhida pelo usuário no atributo `lang` do HTML.
  const language = useSettingsStore((s) => s.language);
  // Tema persistido em settings-store (light/dark/system), com reatividade
  // imediata refletida na classe do `<html>`.
  const theme = useSettingsStore((s) => s.theme);
  // Agenda o aviso "sessão expira em breve" perto da raiz, uma única vez
  // por árvore (não por tela).
  useSessionExpiry();
  // `workspaces` do useWorkspacesStore não é persistido (só `active_id`
  // é — ver partialize do store), então precisa ser buscado a cada carga
  // do app. Antes, o único gatilho era o useEffect de mount do
  // WorkspaceSelector (removido da AppBar do chat no commit 84f07292),
  // deixando `workspaces` vazio na maioria das sessões — a sidebar então
  // caía no fallback de agrupamento por data em vez da árvore por
  // workspace. Roda aqui (raiz, monta antes de qualquer sidebar) em vez
  // de depender de qual componente específico está montado.
  useEffect(() => {
    if (isPublicPath(location.pathname)) return;
    const { workspaces, hydrate } = useWorkspacesStore.getState();
    if (workspaces.length === 0) void hydrate();
  }, [location.pathname]);
  // Aplica as preferências durável do backend (fonte de verdade) por cima do
  // cache local — uma vez por sessão, depois que a rota deixa de ser pública
  // (usuário resolvido). Ver settings-store.ts::hydrateFromBackend.
  const prefsHydratedRef = useRef(false);
  useEffect(() => {
    if (isPublicPath(location.pathname)) return;
    if (prefsHydratedRef.current) return;
    prefsHydratedRef.current = true;
    void hydrateFromBackend();
  }, [location.pathname]);
  useEffect(() => {
    if (typeof document !== "undefined") {
      document.documentElement.lang = HTML_LANG_BY_SETTING[language] ?? "en";
    }
  }, [language]);
  useEffect(() => {
    if (typeof document === "undefined") return;
    const root = document.documentElement;
    // `:root` já é o tema escuro (default); `.light` sobrescreve as
    // variáveis para o tema claro (ver styles.css). Por isso alternamos
    // as duas classes — não basta remover `.dark`.
    const apply = (dark: boolean) => {
      root.classList.toggle("dark", dark);
      root.classList.toggle("light", !dark);
    };

    if (theme === "system") {
      if (typeof window.matchMedia !== "function") {
        apply(true);
        return;
      }
      const mq = window.matchMedia("(prefers-color-scheme: dark)");
      apply(mq.matches);
      const handler = (e: MediaQueryListEvent) => apply(e.matches);
      mq.addEventListener("change", handler);
      return () => mq.removeEventListener("change", handler);
    }

    apply(theme === "dark");
  }, [theme]);

  // Paleta de cores (presets inspirados em temas do VS Code ou customização
  // do usuário) sobrepõe os tokens de cor via CSS custom properties em
  // :root, independente de claro/escuro/sistema acima.
  const themePreset = useSettingsStore((s) => s.themePreset);
  const installedThemes = useSettingsStore((s) => s.installedThemes);
  const customThemeColors = useSettingsStore((s) => s.customThemeColors);
  useEffect(() => {
    if (themePreset === "default") {
      applyThemeTokens(null);
      return;
    }
    if (themePreset === "custom") {
      applyThemeTokens(
        customThemeColors ? buildThemeTokens(customThemeColors) : null,
      );
      return;
    }
    const preset = resolveThemePreset(themePreset, installedThemes);
    applyThemeTokens(preset ? buildThemeTokens(preset.colors) : null);
  }, [themePreset, installedThemes, customThemeColors]);

  // Escala de fonte por superfície (Preferências → Aparência) — CSS vars
  // consumidas por styles.css (--font-scale-ui) e por markdown-view.tsx/
  // message-item.tsx (--font-scale-markdown/--font-scale-chat).
  const fontScaleUi = useSettingsStore((s) => s.fontScaleUi);
  const fontScaleChat = useSettingsStore((s) => s.fontScaleChat);
  const fontScaleMarkdown = useSettingsStore((s) => s.fontScaleMarkdown);
  useEffect(() => {
    if (typeof document === "undefined") return;
    const root = document.documentElement;
    root.style.setProperty(
      "--font-scale-ui",
      String(fontScaleUi / FONT_SCALE_BASE_PX),
    );
    root.style.setProperty(
      "--font-scale-chat",
      String(fontScaleChat / FONT_SCALE_BASE_PX),
    );
    root.style.setProperty(
      "--font-scale-markdown",
      String(fontScaleMarkdown / FONT_SCALE_BASE_PX),
    );
  }, [fontScaleUi, fontScaleChat, fontScaleMarkdown]);

  return (
    <div
      className="h-screen flex flex-col overflow-hidden"
      data-route={location.pathname}
    >
      <TitleBar />
      <NetworkStatusBanner />
      <UpdateBanner />
      {/* flex-1 min-h-0: dá altura definida pro Outlet — sem isso, rotas que
          assumem "h-full" (chat, workbench) não conseguem resolver a
          porcentagem contra um ancestral só com min-h-screen (altura
          indefinida), e o overflow acaba subindo pro documento inteiro,
          arrastando a TitleBar (que não é fixed) junto no scroll. */}
      <div className="flex-1 min-h-0 overflow-y-auto overflow-x-hidden">
        <Outlet />
      </div>
      <Toaster />
    </div>
  );
}
