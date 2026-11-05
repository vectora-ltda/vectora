"use client";

/**
 * SettingsOverlay — shell único de configurações, no molde do Hermes
 * (`OverlaySplitLayout`/`OverlayNav`): rail lateral fixo + área de
 * conteúdo, dentro de um único `Dialog` Radix quase-tela-cheia.
 *
 * Substitui os 3 `Dialog` independentes (Preferências/Ambiente/
 * Administração) que existiam antes — trocar de categoria não fecha/
 * reabre nada, é só troca de painel dentro do mesmo Dialog já aberto.
 * Abaixo do breakpoint, o rail colapsa pra um `<select>` (mesmo padrão
 * do Hermes: `TabDropdown` horizontal em telas estreitas).
 */

import { Suspense, useMemo, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ErrorBoundary } from "@/components/ui/error-boundary";
import { useAuthStore } from "@/lib/stores/auth-store";
import { useFeatureFlags } from "@/lib/hooks/use-feature-flags";
import { useLicenseStatus } from "@/lib/hooks/use-license-status";
import { useElementWidth } from "@/lib/hooks/use-element-width";
import { useSettingsOverlayStore } from "@/lib/stores/settings-overlay-store";
import type { SettingsCategoryId } from "@/lib/stores/settings-overlay-store";
import {
  buildSettingsCategoryGroups,
  findCategory,
  firstAvailableCategory,
} from "./settings-categories";
import { m } from "@/lib/paraglide/messages";

//: Abaixo desta largura o rail vira dropdown — mesmo espírito do
//: `47.5rem` do Hermes, ajustado ao conteúdo real deste rail (menos
//: categorias, rótulos mais curtos).
const RAIL_COLLAPSE_BELOW = 640;

function TabFallback() {
  return (
    <div className="flex items-center justify-center py-12 text-xs text-muted-foreground">
      …
    </div>
  );
}

export function SettingsOverlay() {
  const open = useSettingsOverlayStore((s) => s.open);
  const setOpen = useSettingsOverlayStore((s) => s.setOpen);
  const activeCategory = useSettingsOverlayStore((s) => s.activeCategory);
  const setActiveCategory = useSettingsOverlayStore((s) => s.setActiveCategory);
  const user = useAuthStore((s) => s.user);
  const { enableFeaturesBeta } = useFeatureFlags();
  const { status: license, loading: licenseLoading } = useLicenseStatus();
  const [query, setQuery] = useState("");
  const [containerRef, containerWidth] = useElementWidth<HTMLDivElement>();

  const isAdmin = user?.role === "root" || user?.role === "admin";
  // Enquanto `licenseLoading`, trata como não-free — sem isso a categoria
  // "Usuários" pisca visível→escondida no primeiro render pra quem É Pro.
  const isFree = !licenseLoading && !license?.configured;
  const groups = useMemo(
    () =>
      buildSettingsCategoryGroups({
        connectEnabled: enableFeaturesBeta,
        isAdmin,
        isFree,
      }),
    [enableFeaturesBeta, isAdmin, isFree],
  );

  const effectiveCategoryId =
    findCategory(groups, activeCategory)?.id ?? firstAvailableCategory(groups);
  const activeCategoryDef = findCategory(groups, effectiveCategoryId);

  const normalizedQuery = query.trim().toLowerCase();
  const filteredGroups = normalizedQuery
    ? groups
        .map((g) => ({
          ...g,
          categories: g.categories.filter((c) =>
            c.label.toLowerCase().includes(normalizedQuery),
          ),
        }))
        .filter((g) => g.categories.length > 0)
    : groups;

  const isCollapsed =
    containerWidth > 0 && containerWidth < RAIL_COLLAPSE_BELOW;

  function selectCategory(id: SettingsCategoryId) {
    setActiveCategory(id);
  }

  const flatCategories = groups.flatMap((g) => g.categories);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent
        className="p-0 gap-0 w-[92vw] h-[88vh] max-w-6xl overflow-hidden flex flex-col sm:max-w-6xl"
        showCloseButton
      >
        <DialogHeader className="sr-only">
          <DialogTitle>{m.settings_overlay_title()}</DialogTitle>
          <DialogDescription>{m.settings_overlay_desc()}</DialogDescription>
        </DialogHeader>

        <div ref={containerRef} className="flex flex-1 min-h-0 overflow-hidden">
          {isCollapsed ? (
            <div className="flex flex-col flex-1 min-h-0 overflow-hidden">
              <div className="shrink-0 border-b border-border/60 p-3">
                <select
                  aria-label={m.settings_overlay_title()}
                  value={effectiveCategoryId}
                  onChange={(e) =>
                    selectCategory(e.target.value as SettingsCategoryId)
                  }
                  className="w-full h-8 text-sm rounded-md border border-border bg-background px-2"
                >
                  {flatCategories.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar px-6 pb-6 pt-12">
                <ErrorBoundary>
                  <Suspense fallback={<TabFallback />}>
                    {activeCategoryDef && <activeCategoryDef.Component />}
                  </Suspense>
                </ErrorBoundary>
              </div>
            </div>
          ) : (
            <>
              {/* Rail lateral — ~14rem, mesma proporção do OverlaySplitLayout
                  do Hermes (13rem), ajustado ao design system do Vectora. */}
              <nav
                aria-label={m.settings_overlay_title()}
                className="w-56 shrink-0 border-r border-border/60 flex flex-col min-h-0 bg-sidebar"
              >
                <div className="shrink-0 p-3 border-b border-border/60">
                  <input
                    type="text"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder={m.settings_overlay_search_placeholder()}
                    className="w-full h-8 text-xs rounded-md border border-border bg-background px-2.5"
                  />
                </div>
                <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar py-2">
                  {filteredGroups.length === 0 ? (
                    <p className="px-3 py-4 text-xs text-muted-foreground">
                      {m.settings_overlay_no_results()}
                    </p>
                  ) : (
                    filteredGroups.map((group) => (
                      <div key={group.id} className="mb-3">
                        {group.label && (
                          <p className="px-3 pb-1 text-[10px] uppercase tracking-wide text-muted-foreground font-medium">
                            {group.label}
                          </p>
                        )}
                        {group.categories.map((cat) => {
                          const active = cat.id === effectiveCategoryId;
                          return (
                            <button
                              key={cat.id}
                              type="button"
                              aria-current={active ? "page" : undefined}
                              onClick={() => selectCategory(cat.id)}
                              className={`w-full text-left px-3 py-1.5 text-xs transition-colors ${
                                active
                                  ? "bg-muted text-foreground font-medium"
                                  : "text-muted-foreground hover:text-foreground hover:bg-muted/40"
                              }`}
                            >
                              {cat.label}
                            </button>
                          );
                        })}
                      </div>
                    ))
                  )}
                </div>
              </nav>

              {/* pt-12 (não p-6 uniforme): o botão de fechar do Dialog é
                  `absolute top-4 right-4` sobre TODO o overlay — sem essa
                  folga, uma ação no canto superior direito de uma categoria
                  (ex.: "Adicionar" em Memória) fica colada nele. */}
              <div className="flex-1 min-w-0 min-h-0 overflow-y-auto custom-scrollbar px-6 pb-6 pt-12">
                <ErrorBoundary>
                  <Suspense fallback={<TabFallback />}>
                    {activeCategoryDef && <activeCategoryDef.Component />}
                  </Suspense>
                </ErrorBoundary>
              </div>
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
