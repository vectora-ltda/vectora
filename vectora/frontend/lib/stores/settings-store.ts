/**
 * settings-store.ts — preferências do usuário.
 *
 * Persiste no localStorage com chave prefixada por user_id:
 *   "vectora-settings-{userId}"
 * Permite isolamento de preferências por usuário (auth multi-tenant).
 */

import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type { BaseThemeColors, ThemePresetDef } from "@/lib/theme/presets";
import { classifyMode } from "@/lib/theme/mode";
import { getDefaultModel } from "@/lib/config/deployment-config";
import { fetchPrefs, pushPrefs } from "@/lib/api/settings-prefs";
import {
  CHAT_SIDEBAR_OPEN_MIN_WIDTH,
  SIDE_COLUMN_MIN_WIDTH,
} from "@/lib/layout/panel-geometry";

// ---------------------------------------------------------------------------
// Tipos
// ---------------------------------------------------------------------------

export type Theme = "light" | "dark" | "system";
/** "default" = paleta padrão do Vectora; "custom" = `customThemeColors`;
 *  qualquer outro valor é o `id` de um item em `THEME_PRESETS`. */
export type ThemePreset = "default" | "custom" | (string & {});
export type Lang = "en" | "es" | "pt";
/** Lado da sidebar de sessões (workbench fica no lado oposto). */
export type SidebarPosition = "left" | "right";
/** Modos de permissão — espelham o permission_mode do backend. */
export type PermissionMode =
  "ask" | "accept_edits" | "plan" | "auto" | "bypass";
/** Nível de esforço de raciocínio do modelo. */
export type ReasoningEffort = "low" | "medium" | "high" | "max";
/** Modo de interface dentro do Dev (chatMode=false): "assistant" é o layout
 *  padrão de chat+workbench; "ide" é o layout VS Code com editor docked;
 *  "kanban" é o board multi-agente. */
export type UiMode = "assistant" | "ide" | "kanban";

/** Fontes configuráveis por superfície. O valor é uma família CSS, não um
 * caminho de arquivo; isso permite usar fontes instaladas no sistema sem
 * empacotar fontes proprietárias no Vectora. */
export type FontFamily = string;
export type EditorAutoSaveMode = "off" | "afterDelay" | "onFocusChange";

/** Presets de UI Scale exibidos no seletor — percentuais, não pixels; 100 =
 *  tamanho base (`FONT_SCALE_BASE_PX`). */
export const UI_SCALE_PRESETS = [90, 100, 110, 125, 150, 175] as const;
export type UiScalePercent = (typeof UI_SCALE_PRESETS)[number];

/** Modos de permissão em ordem de exibição no seletor. */
export const PERMISSION_MODES: PermissionMode[] = [
  "ask",
  "accept_edits",
  "plan",
  "auto",
  "bypass",
];

/** Níveis de esforço em ordem de exibição. */
export const REASONING_EFFORTS: ReasoningEffort[] = [
  "low",
  "medium",
  "high",
  "max",
];

/** Idiomas suportados — ordem de exibição no seletor */
export const SUPPORTED_LANGS: { value: Lang; label: string }[] = [
  { value: "en", label: "English" },
  { value: "es", label: "Español" },
  { value: "pt", label: "Português" },
];

/**
 * Detecta idioma preferido do browser; mapeia para o mais próximo suportado.
 * "pt" cobre todo português — pt-BR, pt-PT, pt — já que o Vectora só tem
 * português brasileiro, assim como "en" cobre en-US, en-GB, etc.
 */
function detectLanguage(): Lang {
  if (typeof navigator === "undefined") return "en";
  const lang = navigator.language.toLowerCase();
  if (lang.startsWith("pt")) return "pt";
  if (lang.startsWith("es")) return "es";
  return "en";
}

export interface SettingsState {
  /** Exibir tool calls na interface do chat */
  showToolCalls: boolean;
  /** Tema da interface (claro/escuro/sistema) */
  theme: Theme;
  /** Paleta de cores aplicada por cima do tema (presets ou customizada) */
  themePreset: ThemePreset;
  /** Cores base da paleta customizada (quando themePreset === "custom") */
  customThemeColors: BaseThemeColors | null;
  /** Temas instalados pelo usuário via VS Code Marketplace (só desktop) —
   *  `themePreset` pode apontar pro `id` de qualquer item aqui, igual a um
   *  preset embutido. Local-only, não sincroniza com o backend. */
  installedThemes: ThemePresetDef[];
  /** Escala geral da UI (%) — substitui os 4 sliders de fonte separados por
   *  um único controle; internamente ainda deriva `fontScaleUi/Chat/
   *  Markdown/monacoFontSize` (consumidos por __root.tsx/message-item.tsx/
   *  markdown-view.tsx/monaco-readonly.tsx), só que todos em sincronia. */
  uiScalePercent: number;
  /** Instrução personalizada prefixada ao system prompt */
  customSystemPrompt: string;
  /** Blocos de instrução de treinamento adicionais (um item por bloco) */
  trainingInstructions: string[];
  /** Idioma da interface */
  language: Lang;
  /** Modo de permissão para ações destrutivas */
  permissionMode: PermissionMode;
  /** Esforço de raciocínio do modelo */
  reasoningEffort: ReasoningEffort;
  /** Largura da sidebar em px (desktop), arrastável pela borda direita. */
  sidebarWidth: number;
  /** Lado da sidebar de sessões; workbench fica no lado oposto. */
  sidebarPosition: SidebarPosition;
  /** Modo chat puro: oculta workbench, WorkspaceSelector e tools de filesystem. */
  chatMode: boolean;
  /** Sub-modo de interface dentro do Dev (ver `UiMode`). */
  uiMode: UiMode;
  /** Largura do painel de chat lateral no modo IDE (px). */
  chatSidebarWidth: number;
  /** Modelo ativo do chat ("provider:model") — persistido para sobreviver a
   *  restart/reload; sem isso cada mount de `$threadId.tsx` reiniciava do
   *  zero em `getDefaultModel()`, ignorando a escolha do usuário. */
  selectedModel: string;
  /** Auto-update do app desktop (Electron) — lido pelo main process no
   *  próximo boot (só toma efeito na próxima abertura do app, não em
   *  runtime). Sem efeito no navegador/modo servidor. */
  autoUpdateEnabled: boolean;
  weeklyInsightEnabled: boolean;
  weeklyInsightWeeks: 1 | 2 | 4;
  weeklyInsightDismissedWindow: string;
  /** Tamanho de fonte da interface geral (px), aplicado via CSS var
   *  --font-scale-ui (convertida pra razão contra FONT_SCALE_BASE_PX). */
  fontScaleUi: number;
  /** Tamanho de fonte das mensagens do chat (px), --font-scale-chat. */
  fontScaleChat: number;
  /** Tamanho de fonte do markdown renderizado (Plan/Memory/Files/preview, px), --font-scale-markdown. */
  fontScaleMarkdown: number;
  /** Tamanho de fonte (px) do editor Monaco. */
  monacoFontSize: number;
  /** Salva arquivos do editor automaticamente após uma pausa na edição. */
  editorAutoSave: boolean;
  /** Estratégia de salvamento automático do editor. */
  editorAutoSaveMode: EditorAutoSaveMode;
  /** Intervalo de silêncio antes do salvamento automático, em milissegundos. */
  editorAutoSaveDelay: number;
  editorMinimap: boolean;
  editorWordWrap: boolean;
  editorFormatOnType: boolean;
  editorQuickSuggestions: boolean;
  editorLineNumbers: boolean;
  editorTabSize: number;
  editorRenderWhitespace: "none" | "selection" | "all";
  editorStickyScroll: boolean;
  editorSmoothScrolling: boolean;
  editorFontLigatures: boolean;
  editorGlyphMargin: boolean;
  editorBracketPairGuides: boolean;
  editorInsertSpaces: boolean;
  editorParameterHints: boolean;
  editorCursorStyle: "line" | "block" | "underline";
  /** Família tipográfica do código no Monaco (Godot usa JetBrains Mono). */
  editorFontFamily: FontFamily;
  /** Família monoespaçada usada por terminais, diffs e blocos de código. */
  monoFontFamily: FontFamily;
  /** Família proporcional usada pela interface. */
  uiFontFamily: FontFamily;
  /** Executa hooks locais antes do commit. */
  gitHooksEnabled: boolean;
  /** Adiciona o trailer Signed-off-by usando a identidade Git configurada. */
  gitSignoffEnabled: boolean;
  /** Permite solicitar bypass de proteções Git no Workbench. */
  gitBypassEnabled: boolean;
  /** Expande automaticamente itens de planos recém-abertos. */
  planAutoExpand: boolean;
  /** Ordem usada para listar itens do plano. */
  planSort: "created" | "title";
  /** Exibe notificações para tarefas em segundo plano. */
  taskNotifications: boolean;
  /** Número de tentativas automáticas de tarefas que falham. */
  taskRetryCount: number;
  /** Limite de execuções concorrentes de tarefas. */
  taskConcurrency: number;
  /** Atraso inicial entre tentativas automáticas, em milissegundos. */
  taskRetryBackoffMs: number;
  /** Exibe skills na Library. */
  libraryShowSkills: boolean;
  /** Exibe servidores MCP na Library. */
  libraryShowMcp: boolean;
  /** Permite que páginas abram pop-ups em novas janelas do Browser. */
  browserAllowPopups: boolean;
  /** Zoom das views Chromium do Browser, em percentual. */
  browserZoomPercent: number;
  /** Política padrão de permissões de origem no Browser. */
  browserPermissionMode: "allow" | "deny";

  // Ações
  setShowToolCalls: (v: boolean) => void;
  setTheme: (v: Theme) => void;
  setThemePreset: (v: ThemePreset) => void;
  setCustomThemeColors: (v: BaseThemeColors | null) => void;
  addInstalledTheme: (theme: ThemePresetDef) => void;
  removeInstalledTheme: (id: string) => void;
  setUiScalePercent: (v: number) => void;
  setCustomSystemPrompt: (v: string) => void;
  setTrainingInstructions: (v: string[]) => void;
  setLanguage: (v: Lang) => void;
  setPermissionMode: (v: PermissionMode) => void;
  setReasoningEffort: (v: ReasoningEffort) => void;
  setSidebarWidth: (v: number) => void;
  setSidebarPosition: (v: SidebarPosition) => void;
  setChatMode: (v: boolean) => void;
  setUiMode: (v: UiMode) => void;
  setChatSidebarWidth: (v: number) => void;
  setSelectedModel: (v: string) => void;
  setAutoUpdateEnabled: (v: boolean) => void;
  setWeeklyInsightEnabled: (v: boolean) => void;
  setWeeklyInsightWeeks: (v: 1 | 2 | 4) => void;
  setWeeklyInsightDismissedWindow: (v: string) => void;
  setFontScaleUi: (v: number) => void;
  setFontScaleChat: (v: number) => void;
  setFontScaleMarkdown: (v: number) => void;
  setMonacoFontSize: (v: number) => void;
  setEditorAutoSave: (v: boolean) => void;
  setEditorAutoSaveMode: (v: EditorAutoSaveMode) => void;
  setEditorAutoSaveDelay: (v: number) => void;
  setEditorMinimap: (v: boolean) => void;
  setEditorWordWrap: (v: boolean) => void;
  setEditorFormatOnType: (v: boolean) => void;
  setEditorQuickSuggestions: (v: boolean) => void;
  setEditorLineNumbers: (v: boolean) => void;
  setEditorTabSize: (v: number) => void;
  setEditorRenderWhitespace: (v: "none" | "selection" | "all") => void;
  setEditorStickyScroll: (v: boolean) => void;
  setEditorSmoothScrolling: (v: boolean) => void;
  setEditorFontLigatures: (v: boolean) => void;
  setEditorGlyphMargin: (v: boolean) => void;
  setEditorBracketPairGuides: (v: boolean) => void;
  setEditorInsertSpaces: (v: boolean) => void;
  setEditorParameterHints: (v: boolean) => void;
  setEditorCursorStyle: (v: "line" | "block" | "underline") => void;
  setEditorFontFamily: (v: FontFamily) => void;
  setMonoFontFamily: (v: FontFamily) => void;
  setUiFontFamily: (v: FontFamily) => void;
  setGitHooksEnabled: (v: boolean) => void;
  setGitSignoffEnabled: (v: boolean) => void;
  setGitBypassEnabled: (v: boolean) => void;
  setPlanAutoExpand: (v: boolean) => void;
  setPlanSort: (v: "created" | "title") => void;
  setTaskNotifications: (v: boolean) => void;
  setTaskRetryCount: (v: number) => void;
  setTaskConcurrency: (v: number) => void;
  setTaskRetryBackoffMs: (v: number) => void;
  setLibraryShowSkills: (v: boolean) => void;
  setLibraryShowMcp: (v: boolean) => void;
  setBrowserAllowPopups: (v: boolean) => void;
  setBrowserZoomPercent: (v: number) => void;
  setBrowserPermissionMode: (v: "allow" | "deny") => void;
  resetSettings: () => void;
}

/** Limites de largura da sidebar; o piso corresponde ao token Tailwind min-w-60. */
const SIDEBAR_MIN_WIDTH = SIDE_COLUMN_MIN_WIDTH;
const SIDEBAR_MAX_WIDTH = 480;

/** Limites de largura do painel de chat no modo IDE (px). Teto de 480: um
 * valor arrastado até 800px deixava o chat maior que o próprio editor —
 * nenhum caso de uso legítimo precisa de um rail de chat mais largo que o
 * teto da sidebar de sessões. */
const CHAT_SIDEBAR_MIN_WIDTH = CHAT_SIDEBAR_OPEN_MIN_WIDTH;
const CHAT_SIDEBAR_MAX_WIDTH = 480;

/** Referência de conversão: 16px = "100%" no range legado em porcentagem. */
export const FONT_SCALE_BASE_PX = 16;
/** Limites de tamanho de fonte (px) — 13px a 24px, equivalente ao range
 *  legado de 80%-150% sobre a base de 16px. */
export const FONT_SCALE_MIN = 13;
export const FONT_SCALE_MAX = 24;
/** Limites de tamanho de fonte do Monaco (px). */
export const MONACO_FONT_SIZE_MIN = 10;
export const MONACO_FONT_SIZE_MAX = 24;

function clampFontScale(v: number): number {
  return Math.max(FONT_SCALE_MIN, Math.min(FONT_SCALE_MAX, Math.round(v)));
}

/** Converte um valor de fontScale persistido no formato legado (%, 80-150)
 * pro formato atual (px, 13-24) — os dois ranges nunca se sobrepõem, então a
 * heurística de "acima do máximo em px" identifica o formato percentual sem
 * precisar de um marcador de versão explícito. Valores já em px passam
 * direto pelo clamp, sem reconversão (idempotente). */
export function migrateFontScaleValue(v: unknown): number {
  const n = typeof v === "number" ? v : FONT_SCALE_BASE_PX;
  if (n > FONT_SCALE_MAX) {
    return clampFontScale(Math.round((n / 100) * FONT_SCALE_BASE_PX));
  }
  return clampFontScale(n);
}

/** Defaults antigos de largura de sidebar (v2 e antes), substituídos pelo
 * padrão do VS Code. */
export const LEGACY_SIDEBAR_WIDTH_DEFAULT = 224;
export const LEGACY_CHAT_SIDEBAR_WIDTH_DEFAULT = 256;

/** Bumpa `sidebarWidth`/`chatSidebarWidth` do default antigo pro novo —
 * só quando o valor persistido bate exatamente com o default antigo,
 * pra nunca sobrescrever uma largura escolhida manualmente pelo usuário.
 * Além disso, sempre clampa pros limites atuais (`SIDEBAR_MIN/MAX_WIDTH`,
 * `CHAT_SIDEBAR_MIN/MAX_WIDTH`) — um valor arrastado antes do teto do chat
 * cair de 800→480 (ou qualquer resquício de bug antigo) nunca fica "preso"
 * fora do range só porque não bate com o default legado exato. */
export function migrateSidebarWidths(
  sidebarWidth: unknown,
  chatSidebarWidth: unknown,
): { sidebarWidth: unknown; chatSidebarWidth: unknown } {
  const bumpedSidebar =
    sidebarWidth === LEGACY_SIDEBAR_WIDTH_DEFAULT
      ? DEFAULTS.sidebarWidth
      : sidebarWidth;
  const bumpedChat =
    chatSidebarWidth === LEGACY_CHAT_SIDEBAR_WIDTH_DEFAULT
      ? DEFAULTS.chatSidebarWidth
      : chatSidebarWidth;
  return {
    sidebarWidth:
      typeof bumpedSidebar === "number"
        ? Math.max(
            SIDEBAR_MIN_WIDTH,
            Math.min(SIDEBAR_MAX_WIDTH, bumpedSidebar),
          )
        : bumpedSidebar,
    chatSidebarWidth:
      typeof bumpedChat === "number"
        ? Math.max(
            CHAT_SIDEBAR_MIN_WIDTH,
            Math.min(CHAT_SIDEBAR_MAX_WIDTH, bumpedChat),
          )
        : bumpedChat,
  };
}

/** Normalizes installed themes from persisted data without trusting its shape. */
export function migrateInstalledThemes(value: unknown): ThemePresetDef[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((theme): ThemePresetDef[] => {
    if (!theme || typeof theme !== "object") return [];
    const item = theme as Record<string, unknown>;
    const colors = item.colors;
    if (!colors || typeof colors !== "object") return [];
    const rawColors = colors as Record<string, unknown>;
    const colorKeys = [
      "background",
      "foreground",
      "card",
      "border",
      "primary",
      "accent",
      "muted",
      "sidebar",
      "userBubble",
    ] as const;
    if (
      colorKeys.some(
        (key) =>
          typeof rawColors[key] !== "string" || rawColors[key].trim() === "",
      )
    ) {
      return [];
    }
    const background = rawColors.background as string;
    const id = typeof item.id === "string" ? item.id : "";
    const label = typeof item.label === "string" ? item.label : "";
    if (!id || !label) return [];
    return [
      {
        ...item,
        id,
        label,
        mode:
          item.mode === "light" || item.mode === "dark"
            ? item.mode
            : classifyMode({ background }),
        family:
          typeof item.family === "string" && item.family.length > 0
            ? item.family
            : `vscode:${id}`,
        colors: Object.fromEntries(
          colorKeys.map((key) => [key, rawColors[key]]),
        ) as unknown as BaseThemeColors,
      } as unknown as ThemePresetDef,
    ];
  });
}

function clampMonacoFontSize(v: number): number {
  return Math.max(
    MONACO_FONT_SIZE_MIN,
    Math.min(MONACO_FONT_SIZE_MAX, Math.round(v)),
  );
}

// ---------------------------------------------------------------------------
// Defaults
// ---------------------------------------------------------------------------

const DEFAULTS = {
  showToolCalls: false,
  theme: "system" as Theme,
  themePreset: "default" as ThemePreset,
  customThemeColors: null as BaseThemeColors | null,
  installedThemes: [] as ThemePresetDef[],
  uiScalePercent: 100,
  customSystemPrompt: "",
  trainingInstructions: [] as string[],
  language: "en" as Lang, // Sobrescrito pelo detectLanguage() no create()
  permissionMode: "ask" as PermissionMode,
  reasoningEffort: "medium" as ReasoningEffort,
  sidebarWidth: 280,
  sidebarPosition: "left" as SidebarPosition,
  chatMode: false,
  uiMode: "assistant" as UiMode,
  chatSidebarWidth: 300,
  selectedModel: getDefaultModel(),
  autoUpdateEnabled: true,
  weeklyInsightEnabled: false,
  weeklyInsightWeeks: 1 as 1 | 2 | 4,
  weeklyInsightDismissedWindow: "",
  fontScaleUi: FONT_SCALE_BASE_PX,
  fontScaleChat: FONT_SCALE_BASE_PX,
  fontScaleMarkdown: FONT_SCALE_BASE_PX,
  monacoFontSize: 13,
  editorAutoSave: false,
  editorAutoSaveMode: "off" as EditorAutoSaveMode,
  editorAutoSaveDelay: 800,
  editorMinimap: true,
  editorWordWrap: false,
  editorFormatOnType: true,
  editorQuickSuggestions: true,
  editorLineNumbers: true,
  editorTabSize: 2,
  editorRenderWhitespace: "selection" as "none" | "selection" | "all",
  editorStickyScroll: false,
  editorSmoothScrolling: true,
  editorFontLigatures: false,
  editorGlyphMargin: true,
  editorBracketPairGuides: true,
  editorInsertSpaces: true,
  editorParameterHints: true,
  editorCursorStyle: "line" as "line" | "block" | "underline",
  editorFontFamily: '"JetBrains Mono", ui-monospace, monospace',
  monoFontFamily: '"JetBrains Mono", ui-monospace, monospace',
  uiFontFamily: "Inter, ui-sans-serif, system-ui, sans-serif",
  gitHooksEnabled: false,
  gitSignoffEnabled: false,
  gitBypassEnabled: false,
  planAutoExpand: true,
  planSort: "created" as "created" | "title",
  taskNotifications: true,
  taskRetryCount: 0,
  taskConcurrency: 2,
  taskRetryBackoffMs: 1000,
  libraryShowSkills: true,
  libraryShowMcp: true,
  browserAllowPopups: false,
  browserZoomPercent: 100,
  browserPermissionMode: "deny" as "allow" | "deny",
};

// ---------------------------------------------------------------------------
// Chave de storage
// ---------------------------------------------------------------------------

export const SETTINGS_KEY_PREFIX = "vectora-settings-";

/** Retorna a chave do localStorage para o usuário informado.
 *  Sem userId → usa "local" (modo CLI / sem auth). */
export function getStorageKey(userId?: string): string {
  return `${SETTINGS_KEY_PREFIX}${userId ?? "local"}`;
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set) => ({
      ...DEFAULTS,
      // Detecta idioma do browser como valor inicial — sobrescrito pelo
      // localStorage ao reidratar se o usuário já havia salvo uma preferência.
      language: detectLanguage(),

      setShowToolCalls: (v) => set({ showToolCalls: v }),
      setTheme: (v) => {
        set({ theme: v });
        void pushPrefs({ theme: v });
      },
      setThemePreset: (v) => set({ themePreset: v }),
      setCustomThemeColors: (v) => set({ customThemeColors: v }),
      addInstalledTheme: (theme) =>
        set((s) => ({
          installedThemes: [
            ...s.installedThemes.filter((t) => t.id !== theme.id),
            theme,
          ],
        })),
      removeInstalledTheme: (id) =>
        set((s) => ({
          installedThemes: s.installedThemes.filter((t) => t.id !== id),
        })),
      setUiScalePercent: (v) => {
        const percent = Math.max(50, Math.min(200, Math.round(v)));
        const nativeZoom =
          typeof window !== "undefined" && Boolean(window.vectora?.zoom);
        if (nativeZoom) {
          // Desktop Electron: o zoom nativo do Chromium já escala TODO o
          // conteúdo pintado na tela (incluindo o canvas do Monaco, que
          // não é CSS-relativo) — aplicar a escala por CSS/fonte por cima
          // dobraria o efeito. Os campos derivados ficam no tamanho base;
          // só o zoom nativo muda.
          window.vectora?.zoom?.setPercent(percent);
          set({
            uiScalePercent: percent,
            fontScaleUi: FONT_SCALE_BASE_PX,
            fontScaleChat: FONT_SCALE_BASE_PX,
            fontScaleMarkdown: FONT_SCALE_BASE_PX,
            monacoFontSize: DEFAULTS.monacoFontSize,
          });
          return;
        }
        set({
          uiScalePercent: percent,
          fontScaleUi: clampFontScale((FONT_SCALE_BASE_PX * percent) / 100),
          fontScaleChat: clampFontScale((FONT_SCALE_BASE_PX * percent) / 100),
          fontScaleMarkdown: clampFontScale(
            (FONT_SCALE_BASE_PX * percent) / 100,
          ),
          monacoFontSize: clampMonacoFontSize(
            (DEFAULTS.monacoFontSize * percent) / 100,
          ),
        });
      },
      setCustomSystemPrompt: (v) => set({ customSystemPrompt: v }),
      setTrainingInstructions: (v) => set({ trainingInstructions: v }),
      setLanguage: (v) => {
        set({ language: v });
        void pushPrefs({ language: v });
        // Sincroniza o locale do Paraglide (recarrega para aplicar o idioma
        // a todas as mensagens m.* renderizadas).
        void import("@/lib/paraglide/runtime").then(
          ({ setLocale, getLocale }) => {
            if (getLocale() !== v) setLocale(v);
          },
        );
      },
      setPermissionMode: (v) => {
        set({ permissionMode: v });
        void pushPrefs({ permissionMode: v });
      },
      setReasoningEffort: (v) => {
        set({ reasoningEffort: v });
        void pushPrefs({ reasoningEffort: v });
      },
      setSidebarWidth: (v) =>
        set({
          sidebarWidth: Math.max(
            SIDEBAR_MIN_WIDTH,
            Math.min(SIDEBAR_MAX_WIDTH, Math.round(v)),
          ),
        }),
      setSidebarPosition: (v) => {
        set({ sidebarPosition: v });
        void pushPrefs({ sidebarPosition: v });
      },
      setChatMode: (v) => {
        set({ chatMode: v });
        void pushPrefs({ chatMode: v });
      },
      setUiMode: (v) => set({ uiMode: v }),
      setChatSidebarWidth: (v) =>
        set({
          chatSidebarWidth: Math.max(
            CHAT_SIDEBAR_MIN_WIDTH,
            Math.min(CHAT_SIDEBAR_MAX_WIDTH, Math.round(v)),
          ),
        }),
      setSelectedModel: (v) => {
        set({ selectedModel: v });
        void pushPrefs({ selectedModel: v });
      },
      setAutoUpdateEnabled: (v) => {
        set({ autoUpdateEnabled: v });
        void pushPrefs({ autoUpdateEnabled: v });
      },
      setWeeklyInsightEnabled: (v) => {
        set({ weeklyInsightEnabled: v });
        void pushPrefs({ weeklyInsightEnabled: v });
      },
      setWeeklyInsightWeeks: (v) => {
        set({ weeklyInsightWeeks: v });
        void pushPrefs({ weeklyInsightWeeks: v });
      },
      setWeeklyInsightDismissedWindow: (v) => {
        set({ weeklyInsightDismissedWindow: v });
        void pushPrefs({ weeklyInsightDismissedWindow: v });
      },
      setFontScaleUi: (v) => set({ fontScaleUi: clampFontScale(v) }),
      setFontScaleChat: (v) => set({ fontScaleChat: clampFontScale(v) }),
      setFontScaleMarkdown: (v) =>
        set({ fontScaleMarkdown: clampFontScale(v) }),
      setMonacoFontSize: (v) => set({ monacoFontSize: clampMonacoFontSize(v) }),
      setEditorAutoSave: (v) =>
        set({
          editorAutoSave: v,
          editorAutoSaveMode: v ? "afterDelay" : "off",
        }),
      setEditorAutoSaveMode: (v) =>
        set({ editorAutoSaveMode: v, editorAutoSave: v !== "off" }),
      setEditorAutoSaveDelay: (v) =>
        set({
          editorAutoSaveDelay: Math.max(200, Math.min(5000, Math.round(v))),
        }),
      setEditorMinimap: (v) => set({ editorMinimap: v }),
      setEditorWordWrap: (v) => set({ editorWordWrap: v }),
      setEditorFormatOnType: (v) => set({ editorFormatOnType: v }),
      setEditorQuickSuggestions: (v) => set({ editorQuickSuggestions: v }),
      setEditorLineNumbers: (v) => set({ editorLineNumbers: v }),
      setEditorTabSize: (v) =>
        set({ editorTabSize: Math.max(1, Math.min(8, Math.round(v))) }),
      setEditorRenderWhitespace: (v) => set({ editorRenderWhitespace: v }),
      setEditorStickyScroll: (v) => set({ editorStickyScroll: v }),
      setEditorSmoothScrolling: (v) => set({ editorSmoothScrolling: v }),
      setEditorFontLigatures: (v) => set({ editorFontLigatures: v }),
      setEditorGlyphMargin: (v) => set({ editorGlyphMargin: v }),
      setEditorBracketPairGuides: (v) => set({ editorBracketPairGuides: v }),
      setEditorInsertSpaces: (v) => set({ editorInsertSpaces: v }),
      setEditorParameterHints: (v) => set({ editorParameterHints: v }),
      setEditorCursorStyle: (v) => set({ editorCursorStyle: v }),
      setEditorFontFamily: (v) => set({ editorFontFamily: v }),
      setMonoFontFamily: (v) => set({ monoFontFamily: v }),
      setUiFontFamily: (v) => set({ uiFontFamily: v }),
      setGitHooksEnabled: (v) => set({ gitHooksEnabled: v }),
      setGitSignoffEnabled: (v) => set({ gitSignoffEnabled: v }),
      setGitBypassEnabled: (v) => set({ gitBypassEnabled: v }),
      setPlanAutoExpand: (v) => set({ planAutoExpand: v }),
      setPlanSort: (v) => set({ planSort: v }),
      setTaskNotifications: (v) => set({ taskNotifications: v }),
      setTaskRetryCount: (v) => {
        const value = Math.max(0, Math.min(5, Math.round(v)));
        set({ taskRetryCount: value });
        void pushPrefs({ taskRetryCount: value });
      },
      setTaskConcurrency: (v) => {
        const value = Math.max(1, Math.min(8, Math.round(v)));
        set({ taskConcurrency: value });
        void pushPrefs({ taskConcurrency: value });
      },
      setTaskRetryBackoffMs: (v) => {
        const value = Math.max(100, Math.min(30_000, Math.round(v)));
        set({ taskRetryBackoffMs: value });
        void pushPrefs({ taskRetryBackoffMs: value });
      },
      setLibraryShowSkills: (v) => set({ libraryShowSkills: v }),
      setLibraryShowMcp: (v) => set({ libraryShowMcp: v }),
      setBrowserAllowPopups: (v) => set({ browserAllowPopups: v }),
      setBrowserZoomPercent: (v) =>
        set({ browserZoomPercent: Math.max(25, Math.min(500, Math.round(v))) }),
      setBrowserPermissionMode: (v) => set({ browserPermissionMode: v }),
      resetSettings: () =>
        set({
          ...DEFAULTS,
          language: detectLanguage(),
          selectedModel: getDefaultModel(),
        }),
    }),
    {
      name: getStorageKey(), // Chave default; re-hidratada ao chamar loadUserSettings()
      version: 7, // v7: adiciona modo e intervalo do salvamento automático
      // v4: clampa sidebarWidth/chatSidebarWidth pros limites atuais mesmo fora do default legado exato (teto do chat caiu de 800→480)
      migrate: (persistedState) => {
        const s = persistedState as Record<string, unknown>;
        if (s && typeof s === "object") {
          if ("fontScaleUi" in s)
            s.fontScaleUi = migrateFontScaleValue(s.fontScaleUi);
          if ("fontScaleChat" in s)
            s.fontScaleChat = migrateFontScaleValue(s.fontScaleChat);
          if ("fontScaleMarkdown" in s)
            s.fontScaleMarkdown = migrateFontScaleValue(s.fontScaleMarkdown);
          if (s.uiMode === "ide" || s.uiMode === undefined) {
            s.uiMode = "assistant";
          }
          // Default antigo (224/256) → novo (280/300), mesmo padrão do
          // VS Code. Só bumpa quem está exatamente no default antigo —
          // largura escolhida manualmente pelo usuário não é sobrescrita.
          const widths = migrateSidebarWidths(
            s.sidebarWidth,
            s.chatSidebarWidth,
          );
          s.sidebarWidth = widths.sidebarWidth;
          s.chatSidebarWidth = widths.chatSidebarWidth;
          if (s.themePreset === "dark") s.themePreset = "default-dark";
          else if (s.themePreset === "light") s.themePreset = "default-light";
          else if (s.themePreset === "godot-dark")
            s.themePreset = "default-dark";
          else if (s.themePreset === "godot-light")
            s.themePreset = "default-light";
          s.installedThemes = migrateInstalledThemes(s.installedThemes);
          if (s.editorAutoSaveMode === undefined) {
            s.editorAutoSaveMode =
              s.editorAutoSave === true ? "afterDelay" : "off";
          }
          if (typeof s.editorAutoSaveDelay !== "number")
            s.editorAutoSaveDelay = 800;
        }
        return s;
      },
      storage: createJSONStorage(() =>
        typeof window !== "undefined"
          ? localStorage
          : {
              getItem: () => null,
              setItem: () => undefined,
              removeItem: () => undefined,
            },
      ),
      partialize: (state) => ({
        showToolCalls: state.showToolCalls,
        theme: state.theme,
        themePreset: state.themePreset,
        customThemeColors: state.customThemeColors,
        installedThemes: state.installedThemes,
        uiScalePercent: state.uiScalePercent,
        customSystemPrompt: state.customSystemPrompt,
        trainingInstructions: state.trainingInstructions,
        language: state.language,
        permissionMode: state.permissionMode,
        reasoningEffort: state.reasoningEffort,
        sidebarWidth: state.sidebarWidth,
        chatMode: state.chatMode,
        uiMode: state.uiMode,
        chatSidebarWidth: state.chatSidebarWidth,
        selectedModel: state.selectedModel,
        autoUpdateEnabled: state.autoUpdateEnabled,
        fontScaleUi: state.fontScaleUi,
        fontScaleChat: state.fontScaleChat,
        fontScaleMarkdown: state.fontScaleMarkdown,
        monacoFontSize: state.monacoFontSize,
        editorAutoSave: state.editorAutoSave,
        editorAutoSaveMode: state.editorAutoSaveMode,
        editorAutoSaveDelay: state.editorAutoSaveDelay,
        editorMinimap: state.editorMinimap,
        editorWordWrap: state.editorWordWrap,
        editorFormatOnType: state.editorFormatOnType,
        editorQuickSuggestions: state.editorQuickSuggestions,
        editorLineNumbers: state.editorLineNumbers,
        editorTabSize: state.editorTabSize,
        editorRenderWhitespace: state.editorRenderWhitespace,
        editorStickyScroll: state.editorStickyScroll,
        editorSmoothScrolling: state.editorSmoothScrolling,
        editorFontLigatures: state.editorFontLigatures,
        editorGlyphMargin: state.editorGlyphMargin,
        editorBracketPairGuides: state.editorBracketPairGuides,
        editorInsertSpaces: state.editorInsertSpaces,
        editorParameterHints: state.editorParameterHints,
        editorCursorStyle: state.editorCursorStyle,
        editorFontFamily: state.editorFontFamily,
        monoFontFamily: state.monoFontFamily,
        uiFontFamily: state.uiFontFamily,
        gitHooksEnabled: state.gitHooksEnabled,
        gitSignoffEnabled: state.gitSignoffEnabled,
        gitBypassEnabled: state.gitBypassEnabled,
        planAutoExpand: state.planAutoExpand,
        planSort: state.planSort,
        taskNotifications: state.taskNotifications,
        taskRetryCount: state.taskRetryCount,
        taskConcurrency: state.taskConcurrency,
        taskRetryBackoffMs: state.taskRetryBackoffMs,
        libraryShowSkills: state.libraryShowSkills,
        libraryShowMcp: state.libraryShowMcp,
        browserAllowPopups: state.browserAllowPopups,
        browserZoomPercent: state.browserZoomPercent,
        browserPermissionMode: state.browserPermissionMode,
      }),
    },
  ),
);

/**
 * Re-hidrata o store com a chave específica do usuário.
 * Chamar após login para carregar as preferências salvas.
 *
 * @example
 *   loadUserSettings("user_abc123")
 */
export function loadUserSettings(userId?: string): void {
  const key = getStorageKey(userId);
  useSettingsStore.persist.setOptions({ name: key });
  void Promise.resolve(useSettingsStore.persist.rehydrate()).then(() => {
    // No desktop Electron o zoom nativo vive só no processo principal —
    // sem reaplicar aqui, o `uiScalePercent` reidratado (ex.: trocar de
    // usuário, reabrir o app) ficaria só na store, com o zoom nativo
    // ainda no valor da sessão anterior (ou 100% no primeiro boot).
    if (typeof window !== "undefined" && window.vectora?.zoom) {
      window.vectora.zoom.setPercent(
        useSettingsStore.getState().uiScalePercent,
      );
    }
  });
}

/**
 * Aplica as preferências durável do backend por cima do cache local.
 * Backend é fonte de verdade: as preferências sobrevivem a reinstalar o app
 * ou limpar o cache do navegador, já que o localStorage é por origem do
 * browser, não por instalação do app. Chamar uma vez no boot, depois que o
 * usuário estiver resolvido (ver `__root.tsx`).
 */
export async function hydrateFromBackend(): Promise<void> {
  const prefs = await fetchPrefs();
  const s = useSettingsStore.getState();
  if (prefs.selectedModel) s.setSelectedModel(prefs.selectedModel);
  if (prefs.theme) s.setTheme(prefs.theme as Theme);
  if (prefs.language) s.setLanguage(prefs.language as Lang);
  if (typeof prefs.chatMode === "boolean") s.setChatMode(prefs.chatMode);
  if (prefs.permissionMode)
    s.setPermissionMode(prefs.permissionMode as PermissionMode);
  if (prefs.reasoningEffort)
    s.setReasoningEffort(prefs.reasoningEffort as ReasoningEffort);
  if (prefs.sidebarPosition)
    s.setSidebarPosition(prefs.sidebarPosition as SidebarPosition);
  if (typeof prefs.autoUpdateEnabled === "boolean")
    s.setAutoUpdateEnabled(prefs.autoUpdateEnabled);
  if (typeof prefs.weeklyInsightEnabled === "boolean")
    s.setWeeklyInsightEnabled(prefs.weeklyInsightEnabled);
  if (
    prefs.weeklyInsightWeeks === 1 ||
    prefs.weeklyInsightWeeks === 2 ||
    prefs.weeklyInsightWeeks === 4
  )
    s.setWeeklyInsightWeeks(prefs.weeklyInsightWeeks);
  if (typeof prefs.weeklyInsightDismissedWindow === "string")
    s.setWeeklyInsightDismissedWindow(prefs.weeklyInsightDismissedWindow);
}
