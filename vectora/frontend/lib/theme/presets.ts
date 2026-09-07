/**
 * presets.ts — paletas de tema prontas (inspiradas em temas populares do
 * VS Code, todos open-source) + utilitários para aplicar/derivar tokens
 * de cor como CSS custom properties em `document.documentElement`.
 *
 * Cada preset define só as cores "base" (as mais visíveis); as demais
 * variáveis do design system (`--muted-foreground`, `--ring` etc.) são
 * derivadas via `buildThemeTokens()` usando `color-mix()` e contraste
 * automático — evita ter que listar 19 cores por preset.
 */

export interface BaseThemeColors {
  background: string;
  foreground: string;
  card: string;
  border: string;
  primary: string;
  accent: string;
  muted: string;
  sidebar: string;
  userBubble: string;
}

export interface ThemePresetDef {
  id: string;
  label: string;
  mode: "light" | "dark";
  family: string;
  colors: BaseThemeColors;
}

/** Variáveis CSS publicadas em `:root` / `.light` (ver styles.css). */
const TOKEN_VAR_NAMES = [
  "--background",
  "--foreground",
  "--card",
  "--card-foreground",
  "--popover",
  "--popover-foreground",
  "--muted",
  "--muted-foreground",
  "--secondary",
  "--secondary-foreground",
  "--accent",
  "--accent-foreground",
  "--border",
  "--input",
  "--primary",
  "--primary-foreground",
  "--ring",
  "--sidebar",
  "--user-bubble",
  "--user-bubble-foreground",
  "--scrollbar-thumb",
  "--scrollbar-thumb-hover",
] as const;

/** Presets baseados nos temas originais do VS Code (paletas open-source).
 * `github-*`, `nous-*`, `catppuccin-*`, `everforest-*` e `solarized-*` usam
 * exatamente as cores da extensão VS Code correspondente; `midnight`, `ember`,
 * `mono`, `cyberpunk` e `slate` não têm variante clara na extensão de origem
 * (dark-only). */
export const THEME_PRESETS: ThemePresetDef[] = [
  {
    id: "default-dark",
    label: "Default Dark",
    mode: "dark",
    family: "default",
    colors: {
      background: "#1f1f1f",
      foreground: "#d4d4d4",
      card: "#1a1a1a",
      border: "#2a2a2a",
      primary: "#d4d4d4",
      accent: "#2a2a2a",
      muted: "#262626",
      sidebar: "#181818",
      userBubble: "#2563eb",
    },
  },
  {
    id: "default-light",
    label: "Default Light",
    mode: "light",
    family: "default",
    colors: {
      background: "#ffffff",
      foreground: "#2b2b2b",
      card: "#f6f6f6",
      border: "#d8d8d8",
      primary: "#2b2b2b",
      accent: "#eeeeee",
      muted: "#f0f0f0",
      sidebar: "#f3f3f3",
      userBubble: "#2563eb",
    },
  },
  {
    id: "github-dark",
    label: "GitHub Dark",
    mode: "dark",
    family: "github",
    colors: {
      background: "#0d1117",
      foreground: "#e6edf3",
      card: "#010409",
      border: "#30363d",
      primary: "#4f9e5e",
      accent: "#192a24",
      muted: "#1a1e24",
      sidebar: "#010409",
      userBubble: "#0f2018",
    },
  },
  {
    id: "github-light",
    label: "GitHub Light",
    mode: "light",
    family: "github",
    colors: {
      background: "#ffffff",
      foreground: "#1f2328",
      card: "#f6f8fa",
      border: "#d0d7de",
      primary: "#196d31",
      accent: "#e3ede6",
      muted: "#f6f6f6",
      sidebar: "#f6f8fa",
      userBubble: "#dbe7e2",
    },
  },
  {
    id: "nous-light",
    label: "Nous Light",
    mode: "light",
    family: "nous",
    colors: {
      background: "#ffffff",
      foreground: "#1f2328",
      card: "#f6f8fa",
      border: "#d0d7de",
      primary: "#0053fd",
      accent: "#e3edff",
      muted: "#f6f6f6",
      sidebar: "#f6f8fa",
      userBubble: "#dae7fd",
    },
  },
  {
    id: "nous-dark",
    label: "Nous Dark",
    mode: "dark",
    family: "nous",
    colors: {
      background: "#0d1117",
      foreground: "#e6edf3",
      card: "#010409",
      border: "#30363d",
      primary: "#4a84fe",
      accent: "#17243a",
      muted: "#1a1e24",
      sidebar: "#010409",
      userBubble: "#07162c",
    },
  },
  {
    id: "catppuccin-light",
    label: "Catppuccin Latte",
    mode: "light",
    family: "catppuccin",
    colors: {
      background: "#eff1f5",
      foreground: "#4c4f69",
      card: "#e6e9ef",
      border: "#acb0be",
      primary: "#6d2ebf",
      accent: "#dfdaef",
      muted: "#e8ebef",
      sidebar: "#e6e9ef",
      userBubble: "#d7d3e9",
    },
  },
  {
    id: "catppuccin-dark",
    label: "Catppuccin Mocha",
    mode: "dark",
    family: "catppuccin",
    colors: {
      background: "#1e1e2e",
      foreground: "#cdd6f4",
      card: "#181825",
      border: "#585b70",
      primary: "#cba6f7",
      accent: "#3d3652",
      muted: "#29293a",
      sidebar: "#181825",
      userBubble: "#38324b",
    },
  },
  {
    id: "everforest-light",
    label: "Everforest Light",
    mode: "light",
    family: "everforest",
    colors: {
      background: "#fdf6e3",
      foreground: "#5c6a72",
      card: "#fdf6e3",
      border: "#e5ddc0",
      primary: "#586b35",
      accent: "#e9e5ce",
      muted: "#f7f0de",
      sidebar: "#fdf6e3",
      userBubble: "#e9e5ce",
    },
  },
  {
    id: "everforest-dark",
    label: "Everforest Dark",
    mode: "dark",
    family: "everforest",
    colors: {
      background: "#2d353b",
      foreground: "#d3c6aa",
      card: "#2d353b",
      border: "#3a4248",
      primary: "#a7c080",
      accent: "#434e47",
      muted: "#373e42",
      sidebar: "#2d353b",
      userBubble: "#434e47",
    },
  },
  {
    id: "solarized-light",
    label: "Solarized Light",
    mode: "light",
    family: "solarized",
    colors: {
      background: "#fdf6e3",
      foreground: "#1f1f1f",
      card: "#d3cbb7",
      border: "#ddd6c1",
      primary: "#675e34",
      accent: "#ebe4ce",
      muted: "#f4eddb",
      sidebar: "#eee8d5",
      userBubble: "#c6bea7",
    },
  },
  {
    id: "solarized-dark",
    label: "Solarized Dark",
    mode: "dark",
    family: "solarized",
    colors: {
      background: "#002b36",
      foreground: "#839496",
      card: "#002b36",
      border: "#234751",
      primary: "#6ea1c4",
      accent: "#144050",
      muted: "#08313c",
      sidebar: "#001f26",
      userBubble: "#144050",
    },
  },
  {
    id: "midnight",
    label: "Midnight",
    mode: "dark",
    family: "midnight",
    colors: {
      background: "#08081c",
      foreground: "#ddd6ff",
      card: "#0d0d28",
      border: "#1e1e52",
      primary: "#ddd6ff",
      accent: "#1a1a44",
      muted: "#13133a",
      sidebar: "#06061a",
      userBubble: "#14143a",
    },
  },
  {
    id: "ember",
    label: "Ember",
    mode: "dark",
    family: "ember",
    colors: {
      background: "#160800",
      foreground: "#ffd8b0",
      card: "#1e0e04",
      border: "#3a1c08",
      primary: "#ffd8b0",
      accent: "#301600",
      muted: "#2a1408",
      sidebar: "#100600",
      userBubble: "#2a1000",
    },
  },
  {
    id: "mono",
    label: "Mono",
    mode: "dark",
    family: "mono",
    colors: {
      background: "#0e0e0e",
      foreground: "#eaeaea",
      card: "#141414",
      border: "#2a2a2a",
      primary: "#eaeaea",
      accent: "#222222",
      muted: "#1e1e1e",
      sidebar: "#0a0a0a",
      userBubble: "#1a1a1a",
    },
  },
  {
    id: "cyberpunk",
    label: "Cyberpunk",
    mode: "dark",
    family: "cyberpunk",
    colors: {
      background: "#000a00",
      foreground: "#00ff41",
      card: "#001200",
      border: "#003000",
      primary: "#00ff41",
      accent: "#002000",
      muted: "#001a00",
      sidebar: "#000600",
      userBubble: "#001400",
    },
  },
  {
    id: "slate",
    label: "Slate",
    mode: "dark",
    family: "slate",
    colors: {
      background: "#0d1117",
      foreground: "#c9d1d9",
      card: "#161b22",
      border: "#30363d",
      primary: "#c9d1d9",
      accent: "#1e2530",
      muted: "#21262d",
      sidebar: "#090d13",
      userBubble: "#1e2a38",
    },
  },
  {
    id: "midnight-light",
    label: "Midnight Light",
    mode: "light",
    family: "midnight",
    colors: {
      background: "#f4f5ff",
      foreground: "#272744",
      card: "#ffffff",
      border: "#c8c9e5",
      primary: "#4b3fbf",
      accent: "#e5e5fa",
      muted: "#ececff",
      sidebar: "#eef0ff",
      userBubble: "#d9d9f5",
    },
  },
  {
    id: "ember-light",
    label: "Ember Light",
    mode: "light",
    family: "ember",
    colors: {
      background: "#fff8f2",
      foreground: "#4a2412",
      card: "#ffffff",
      border: "#e4c6b2",
      primary: "#a33b12",
      accent: "#f8dfcf",
      muted: "#f8eee8",
      sidebar: "#fff1e6",
      userBubble: "#f2cfb9",
    },
  },
  {
    id: "mono-light",
    label: "Mono Light",
    mode: "light",
    family: "mono",
    colors: {
      background: "#ffffff",
      foreground: "#171717",
      card: "#f7f7f7",
      border: "#d4d4d4",
      primary: "#171717",
      accent: "#e8e8e8",
      muted: "#f0f0f0",
      sidebar: "#fafafa",
      userBubble: "#dedede",
    },
  },
  {
    id: "cyberpunk-light",
    label: "Cyberpunk Light",
    mode: "light",
    family: "cyberpunk",
    colors: {
      background: "#f4fff7",
      foreground: "#073b1b",
      card: "#ffffff",
      border: "#9bd6ad",
      primary: "#087f35",
      accent: "#d5f5df",
      muted: "#e8f9ed",
      sidebar: "#edfff2",
      userBubble: "#c7efd3",
    },
  },
  {
    id: "slate-light",
    label: "Slate Light",
    mode: "light",
    family: "slate",
    colors: {
      background: "#f6f8fa",
      foreground: "#1f2933",
      card: "#ffffff",
      border: "#c7d0d9",
      primary: "#245a8d",
      accent: "#e1e9f2",
      muted: "#edf1f5",
      sidebar: "#eef2f6",
      userBubble: "#d7e2ee",
    },
  },
];

/** Finds the preset in the same family for the requested light/dark mode. */
export function getPairedPresetId(
  id: string,
  targetMode: "light" | "dark",
): string | undefined {
  const preset = THEME_PRESETS.find((item) => item.id === id);
  if (!preset) return undefined;
  return THEME_PRESETS.find(
    (item) => item.family === preset.family && item.mode === targetMode,
  )?.id;
}

export const DEFAULT_CUSTOM_COLORS: BaseThemeColors = {
  background: "#1f1f1f",
  foreground: "#d4d4d4",
  card: "#1a1a1a",
  border: "#2a2a2a",
  primary: "#79b8ff",
  accent: "#2a2a2a",
  muted: "#262626",
  sidebar: "#181818",
  userBubble: "#2563eb",
};

/** Luminância relativa aproximada (sRGB) — usada para escolher fg de contraste. */
export function relativeLuminance(hex: string): number {
  const m = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex.trim());
  if (!m) return 0;
  const [r, g, b] = [m[1]!, m[2]!, m[3]!].map((h) => parseInt(h, 16) / 255);
  return 0.2126 * r! + 0.7152 * g! + 0.0722 * b!;
}

/** Retorna preto ou branco — o que tiver mais contraste contra `hex`. */
function contrastFg(hex: string): string {
  return relativeLuminance(hex) > 0.5 ? "#0a0a0a" : "#fafafa";
}

/** Cor de texto secundário — mistura de `foreground` com `background`, usada
 * tanto pelo token `--muted-foreground` (`buildThemeTokens`) quanto pela
 * barra de subtítulo do card de preview de tema (`ThemePreview`). */
export function deriveMutedForeground(base: BaseThemeColors): string {
  return `color-mix(in srgb, ${base.foreground} 65%, ${base.background})`;
}

/** Tom de borda derivado de uma cor de preenchimento (ex.: `sidebar`,
 * `userBubble`) — usado onde a paleta não define uma borda própria para
 * esses elementos (ex.: a tira lateral e a pílula do card de preview). */
export function deriveBorderTint(hex: string, base: BaseThemeColors): string {
  return `color-mix(in srgb, ${hex} 70%, ${base.foreground})`;
}

/**
 * Expande as 7 cores-base de um preset/customização para o conjunto completo
 * de tokens do design system, retornando um mapa `--var → valor` pronto
 * para `style.setProperty`.
 */
export function buildThemeTokens(
  base: BaseThemeColors,
): Record<string, string> {
  return {
    "--background": base.background,
    "--foreground": base.foreground,
    "--card": base.card,
    "--card-foreground": base.foreground,
    "--popover": base.card,
    "--popover-foreground": base.foreground,
    "--muted": base.muted,
    "--muted-foreground": deriveMutedForeground(base),
    "--secondary": base.muted,
    "--secondary-foreground": base.foreground,
    "--accent": base.accent,
    "--accent-foreground": contrastFg(base.accent),
    "--border": base.border,
    "--input": base.border,
    "--primary": base.primary,
    "--primary-foreground": contrastFg(base.primary),
    "--ring": base.primary,
    "--sidebar": base.sidebar,
    "--user-bubble": base.userBubble,
    "--user-bubble-foreground": contrastFg(base.userBubble),
    "--scrollbar-thumb": `color-mix(in srgb, ${base.foreground} 65%, ${base.background})`,
    "--scrollbar-thumb-hover": base.foreground,
  };
}

/**
 * Aplica (ou remove, se `tokens` for `null`) os overrides de cor em
 * `document.documentElement.style`. `null` restaura os valores padrão
 * de `:root` / `.light` definidos em styles.css.
 */
export function applyThemeTokens(tokens: Record<string, string> | null): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  if (!tokens) {
    for (const name of TOKEN_VAR_NAMES) root.style.removeProperty(name);
    return;
  }
  for (const name of TOKEN_VAR_NAMES) {
    const value = tokens[name];
    if (value) root.style.setProperty(name, value);
    else root.style.removeProperty(name);
  }
}
