"use client";

import { Check, Download, Loader2, Search } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Input } from "@/components/ui/input";
import { selectableCardClass } from "@/lib/selectable-card";
import {
  deriveBorderTint,
  deriveMutedForeground,
  type BaseThemeColors,
  type ThemePresetDef,
} from "@/lib/theme/presets";
import { classifyMode } from "@/lib/theme/mode";
import {
  installVscodeThemeFromMarketplace,
  searchVscodeMarketplaceThemes,
  type VscodeMarketplaceSearchItem,
} from "@/lib/theme/vscode-install";

/** Card de preview de tema: mini-UI fake (tira lateral + linhas de
 * título/subtítulo + pílula de bolha de usuário) pintada com as cores reais
 * da paleta — dá pra reconhecer o tema de relance, sem precisar aplicá-lo
 * primeiro. Nome/descrição ficam como legenda fora do card. */
/** Renders a compact preview card for one theme palette. */
function ThemePreview({
  colors,
  active,
  onClick,
}: {
  colors: BaseThemeColors;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={`relative h-20 w-full overflow-hidden rounded-xl border shadow-xs ${selectableCardClass({ active })}`}
      style={{ background: colors.background, borderColor: colors.border }}
    >
      <div className="flex h-full">
        <div
          className="w-12 border-r"
          style={{
            background: colors.sidebar,
            borderColor: deriveBorderTint(colors.sidebar, colors),
          }}
        />
        <div className="flex flex-1 flex-col gap-2 p-3">
          <div
            className="h-2.5 w-16 rounded-full"
            style={{ background: colors.foreground }}
          />
          <div
            className="h-2 w-24 rounded-full"
            style={{ background: deriveMutedForeground(colors) }}
          />
          <div className="mt-auto flex justify-end">
            <div
              className="h-5 w-16 rounded-full border"
              style={{
                background: colors.userBubble,
                borderColor: deriveBorderTint(colors.userBubble, colors),
              }}
            />
          </div>
        </div>
      </div>
      {active && (
        <span className="absolute right-1.5 top-1.5 flex h-4 w-4 items-center justify-center rounded-full bg-primary text-primary-foreground">
          <Check className="h-2.5 w-2.5" />
        </span>
      )}
    </button>
  );
}

interface ThemePickerOption {
  id: string;
  label: string;
  mode: "light" | "dark";
  colors: BaseThemeColors;
}

/** Busca (debounced) contra o VS Code Marketplace ao vivo — só existe no
 * app desktop (`window.vectora?.themes`); em modo navegador
 * `searchVscodeMarketplaceThemes` nunca é chamada porque o caller (abaixo)
 * já esconde a seção inteira quando a ponte não existe. */
/** Searches and installs VS Code themes from the desktop marketplace bridge. */
function MarketplaceResults({
  query,
  installedIds,
  onInstalled,
  errorLabel,
  installLabel,
  installedLabel,
  loadingLabel,
}: {
  query: string;
  installedIds: Set<string>;
  onInstalled: (theme: ThemePresetDef) => void;
  errorLabel: string;
  installLabel: string;
  installedLabel: string;
  loadingLabel: string;
}) {
  const [results, setResults] = useState<VscodeMarketplaceSearchItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [installingId, setInstallingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const q = query.trim();
    let alive = true;
    // oxlint-disable-next-line react/set-state-in-effect
    setResults([]);
    // oxlint-disable-next-line react/set-state-in-effect
    setError(null);
    if (!q) {
      // oxlint-disable-next-line react/set-state-in-effect
      setLoading(false);
      return () => {
        alive = false;
      };
    }
    const timer = setTimeout(() => {
      if (!alive) return;
      setLoading(true);
      void searchVscodeMarketplaceThemes(q)
        .then((items) => {
          if (alive) setResults(items);
        })
        .catch(() => {
          if (alive) {
            setResults([]);
            setError(errorLabel);
          }
        })
        .finally(() => {
          if (alive) setLoading(false);
        });
    }, 300);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [query, errorLabel]);

  if (!query.trim()) return null;

  const install = (item: VscodeMarketplaceSearchItem) => {
    if (installingId) return;
    setInstallingId(item.extensionId);
    setError(null);
    installVscodeThemeFromMarketplace(item.extensionId)
      .then((theme) => onInstalled(theme))
      .catch(() => setError(errorLabel))
      .finally(() => setInstallingId(null));
  };

  return (
    <div className="mt-3 space-y-2">
      {loading && (
        <p
          role="status"
          className="flex items-center gap-2 text-xs text-muted-foreground"
        >
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          <span className="sr-only">{loadingLabel}</span>
        </p>
      )}
      {error && <p className="text-xs text-destructive">{error}</p>}
      <div className="grid gap-2 sm:grid-cols-2">
        {results.map((item) => {
          const installed =
            installedIds.has(`vscode-${item.extensionId}`) ||
            Array.from(installedIds).some((id) =>
              id.startsWith(`vscode-${item.extensionId}-`),
            );
          const busy = installingId === item.extensionId;
          return (
            <button
              key={item.extensionId}
              type="button"
              disabled={Boolean(installingId) && !busy}
              onClick={() => install(item)}
              className={`flex items-center gap-2.5 px-2.5 py-2 text-left disabled:opacity-60 ${selectableCardClass({ prominent: installed })}`}
            >
              <span className="min-w-0 flex-1">
                <span className="block truncate text-xs font-medium">
                  {item.displayName}
                </span>
                <span className="block truncate text-[11px] text-muted-foreground">
                  {item.publisher}
                </span>
              </span>
              <span
                className="shrink-0 text-muted-foreground"
                title={installed ? installedLabel : installLabel}
              >
                {busy ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : installed ? (
                  <Check className="h-4 w-4 text-green-500" />
                ) : (
                  <Download className="h-4 w-4" />
                )}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

/** Filters theme presets by active mode and paginates the visible cards. */
export function ThemePicker({
  value,
  onChange,
  activeMode,
  presets,
  installedThemes,
  customLabel,
  showMoreLabel,
  showLessLabel,
  customColors,
  searchPlaceholder,
  marketplaceSupported,
  marketplaceErrorLabel,
  marketplaceInstallLabel,
  marketplaceInstalledLabel,
  marketplaceLoadingLabel,
  onThemeInstalled,
}: {
  value: string;
  onChange: (id: string) => void;
  activeMode?: "light" | "dark";
  presets: ThemePresetDef[];
  installedThemes: ThemePresetDef[];
  customLabel: string;
  showMoreLabel?: string;
  showLessLabel?: string;
  customColors: BaseThemeColors;
  searchPlaceholder: string;
  marketplaceSupported: boolean;
  marketplaceErrorLabel: string;
  marketplaceInstallLabel: string;
  marketplaceInstalledLabel: string;
  marketplaceLoadingLabel: string;
  onThemeInstalled: (theme: ThemePresetDef) => void;
}) {
  const [query, setQuery] = useState("");
  const [visibleCount, setVisibleCount] = useState(6);
  const previousModeRef = useRef(activeMode);
  // oxlint-disable-next-line react/refs
  const effectiveVisibleCount =
    // oxlint-disable-next-line react/refs
    previousModeRef.current === activeMode ? visibleCount : 6;

  useEffect(() => {
    previousModeRef.current = activeMode;
    // oxlint-disable-next-line react/set-state-in-effect
    setVisibleCount(6);
  }, [activeMode]);

  const options: ThemePickerOption[] = useMemo(
    () => [
      ...presets.map((preset) => ({
        id: preset.id,
        label: preset.label,
        mode: preset.mode,
        colors: preset.colors,
      })),
      ...installedThemes.map((theme) => ({
        id: theme.id,
        label: theme.label,
        mode: theme.mode ?? classifyMode(theme.colors),
        colors: theme.colors,
      })),
    ],
    [presets, installedThemes],
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return options.filter(
      (opt) =>
        (!q || opt.label.toLowerCase().includes(q)) &&
        (opt.mode === (activeMode ?? "dark") ||
          (opt.id === value && opt.mode !== (activeMode ?? "dark"))),
    );
  }, [options, query, activeMode, value]);

  const visible = useMemo(() => {
    if (!value) return filtered.slice(0, effectiveVisibleCount);

    const selectedIndex = filtered.findIndex((opt) => opt.id === value);
    if (selectedIndex < 0 || selectedIndex < effectiveVisibleCount) {
      return filtered.slice(0, effectiveVisibleCount);
    }

    return [
      filtered[selectedIndex]!,
      ...filtered
        .filter((opt) => opt.id !== value)
        .slice(0, effectiveVisibleCount - 1),
    ];
  }, [effectiveVisibleCount, filtered, value]);

  const installedIds = useMemo(
    () => new Set(installedThemes.map((t) => t.id)),
    [installedThemes],
  );

  return (
    <div className="space-y-2">
      <div className="relative">
        <Search
          className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
          aria-hidden
        />
        <Input
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setVisibleCount(6);
          }}
          placeholder={searchPlaceholder}
          className="h-8 pl-7 text-xs"
        />
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {visible.map((opt) => (
          <div key={opt.id} className="space-y-1.5">
            <ThemePreview
              colors={opt.colors}
              active={opt.id === value}
              onClick={() => onChange(opt.id)}
            />
            <p className="truncate px-0.5 text-xs font-medium text-foreground">
              {opt.label}
            </p>
          </div>
        ))}
        <div className="space-y-1.5">
          <ThemePreview
            colors={customColors}
            active={value === "custom"}
            onClick={() => onChange("custom")}
          />
          <p className="truncate px-0.5 text-xs font-medium text-foreground">
            {customLabel}
          </p>
        </div>
      </div>
      <div className="flex gap-2">
        {filtered.length > visibleCount && (
          <button
            type="button"
            className="text-xs text-primary underline"
            onClick={() => setVisibleCount((count) => count + 6)}
          >
            {showMoreLabel ?? "Show more"}
          </button>
        )}
        {visibleCount > 6 && (
          <button
            type="button"
            className="text-xs text-primary underline"
            onClick={() => setVisibleCount(6)}
          >
            {showLessLabel ?? "Show less"}
          </button>
        )}
      </div>
      {marketplaceSupported && (
        <MarketplaceResults
          query={query}
          installedIds={installedIds}
          errorLabel={marketplaceErrorLabel}
          installLabel={marketplaceInstallLabel}
          installedLabel={marketplaceInstalledLabel}
          loadingLabel={marketplaceLoadingLabel}
          onInstalled={onThemeInstalled}
        />
      )}
    </div>
  );
}
