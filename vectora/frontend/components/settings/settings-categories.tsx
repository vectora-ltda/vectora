"use client";

/**
 * Registro das categorias do `SettingsOverlay` — remapeamento 1:1 das
 * ~12 abas que hoje vivem espalhadas em 3 diálogos Radix independentes,
 * mais Plugins/Skills/Tool Policy (componentes prontos, mas nunca
 * renderizados por nenhum dos 3 diálogos até agora) e Billing/About
 * (conteúdo real relocado, não placeholder — CLAUDE.md §9).
 *
 * Cada categoria aponta pro MESMO componente de conteúdo que já existia
 * — o remapeamento troca só a casca (onde a categoria aparece e como se
 * navega até ela), nunca reescreve a lógica de negócio de dentro.
 */

import type { ComponentType } from "react";

import { lazyWithRetry } from "@/lib/lazy-with-retry";
import { m } from "@/lib/paraglide/messages";
import type { SettingsCategoryId } from "@/lib/stores/settings-overlay-store";

const GeralTab = lazyWithRetry(
  () =>
    import("./preferencias/tabs/preferencias-tab").then((mod) => ({
      default: mod.PreferenciasTab,
    })),
  "settings-geral-tab",
);
const FallbacksTab = lazyWithRetry(
  () =>
    import("./preferencias/tabs/fallbacks-tab").then((mod) => ({
      default: mod.FallbacksTab,
    })),
  "settings-fallbacks-tab",
);
const MemoriaTab = lazyWithRetry(
  () =>
    import("./preferencias/tabs/memoria-tab").then((mod) => ({
      default: mod.MemoriaTab,
    })),
  "settings-memoria-tab",
);
const ContaTab = lazyWithRetry(
  () =>
    import("./preferencias/tabs/conta-tab").then((mod) => ({
      default: mod.ContaTab,
    })),
  "settings-conta-tab",
);
const IntegracoesTab = lazyWithRetry(
  () =>
    import("./environment/tabs/integracoes-tab").then((mod) => ({
      default: mod.IntegracoesTab,
    })),
  "settings-integracoes-tab",
);
const ProviderRoutingTab = lazyWithRetry(
  () =>
    import("./environment/tabs/provider-routing-tab").then((mod) => ({
      default: mod.ProviderRoutingTab,
    })),
  "settings-provider-routing-tab",
);
const ConnectTab = lazyWithRetry(
  () =>
    import("./environment/tabs/connect-tab").then((mod) => ({
      default: mod.ConnectTab,
    })),
  "settings-connect-tab",
);
const PluginsTab = lazyWithRetry(
  () =>
    import("./environment/tabs/plugins-tab").then((mod) => ({
      default: mod.PluginsTab,
    })),
  "settings-plugins-tab",
);
const SkillsTab = lazyWithRetry(
  () =>
    import("./environment/tabs/skills-tab").then((mod) => {
      // `SkillsTab` só aceita `onSkillsChange` opcional, mas TS não permite
      // atribuir a `ComponentType<unknown>` (contravariância estrita) — o
      // wrapper só existe pra satisfazer o tipo, roda uma vez por load.
      // oxlint-disable-next-line unicorn/consistent-function-scoping
      const Wrapped = () => <mod.SkillsTab />;
      return { default: Wrapped };
    }),
  "settings-skills-tab",
);
const ToolPolicyPanel = lazyWithRetry(
  () =>
    import("./environment/tabs/tool-policy-panel").then((mod) => ({
      default: mod.ToolPolicyPanel,
    })),
  "settings-tool-policy-tab",
);
const HitlAllowlistPanel = lazyWithRetry(
  () =>
    import("./environment/tabs/hitl-allowlist-panel").then((mod) => ({
      default: mod.HitlAllowlistPanel,
    })),
  "settings-hitl-allowlist-tab",
);
const UsersPanel = lazyWithRetry(
  () =>
    import("./administracao/admin-tab").then((mod) => ({
      default: mod.UsersPanel,
    })),
  "settings-admin-users",
);
const ToolsPanel = lazyWithRetry(
  () =>
    import("./administracao/admin-tab").then((mod) => ({
      default: mod.ToolsPanel,
    })),
  "settings-admin-tools",
);
const SafeRootsPanel = lazyWithRetry(
  () =>
    import("./administracao/admin-tab").then((mod) => ({
      default: mod.SafeRootsPanel,
    })),
  "settings-admin-saferoots",
);
const SystemPanel = lazyWithRetry(
  () =>
    import("./administracao/admin-tab").then((mod) => ({
      default: mod.SystemPanel,
    })),
  "settings-admin-system",
);
const StoragePanel = lazyWithRetry(
  () =>
    import("./administracao/admin-tab").then((mod) => ({
      default: mod.StoragePanel,
    })),
  "settings-admin-storage",
);
const BillingPanelLazy = lazyWithRetry(
  () =>
    import("./billing-panel").then((mod) => ({ default: mod.BillingPanel })),
  "settings-billing-tab",
);
const AboutPanelLazy = lazyWithRetry(
  () => import("./about-panel").then((mod) => ({ default: mod.AboutPanel })),
  "settings-about-tab",
);

/** Cobrança + Sobre empilhados numa categoria só — telas pequenas demais
 * (poucos campos cada) pra justificar 2 entradas separadas no rail. */
function BillingAndAboutPanel() {
  return (
    <div className="space-y-8">
      <div>
        <h3 className="text-sm font-medium mb-3">
          {m.settings_category_billing()}
        </h3>
        <BillingPanelLazy />
      </div>
      <div>
        <h3 className="text-sm font-medium mb-3">
          {m.settings_category_about()}
        </h3>
        <AboutPanelLazy />
      </div>
    </div>
  );
}

export type SettingsGroupId =
  "preferencias" | "ambiente" | "administracao" | "geral_grupo";

export interface SettingsCategory {
  id: SettingsCategoryId;
  group: SettingsGroupId;
  label: string;
  Component: ComponentType<Record<string, never>>;
}

export interface SettingsCategoryGroup {
  id: SettingsGroupId;
  label: string;
  categories: SettingsCategory[];
}

interface BuildCategoriesArgs {
  connectEnabled: boolean;
  isAdmin: boolean;
  /** `true` quando não há licença configurada (plano Free) — "Usuários" é
   * recurso multi-usuário puro (convites, roles de outras contas): sem
   * conta Pro só mostraria uma lista vazia, sem caminho pra ativar. */
  isFree: boolean;
}

/** Monta a lista de grupos/categorias visíveis pro usuário atual — gating
 * de feature flag (Connect), role (Administração) e tier (Usuários) decidido
 * aqui, num só lugar, em vez de espalhado pelos componentes individuais. */
export function buildSettingsCategoryGroups({
  connectEnabled,
  isAdmin,
  isFree,
}: BuildCategoriesArgs): SettingsCategoryGroup[] {
  const preferencias: SettingsCategory[] = [
    {
      id: "geral",
      group: "preferencias",
      label: m.settings_category_geral(),
      Component: GeralTab,
    },
    {
      id: "fallbacks",
      group: "preferencias",
      label: m.settings_category_fallbacks(),
      Component: FallbacksTab,
    },
    {
      id: "memoria",
      group: "preferencias",
      label: m.settings_category_memoria(),
      Component: MemoriaTab,
    },
    {
      id: "conta",
      group: "preferencias",
      label: m.settings_category_conta(),
      Component: ContaTab,
    },
  ];

  const ambiente: SettingsCategory[] = [
    {
      id: "integracoes",
      group: "ambiente",
      label: m.settings_category_integracoes(),
      Component: IntegracoesTab,
    },
    {
      id: "provider_routing",
      group: "ambiente",
      label: m.settings_category_provider_routing(),
      Component: ProviderRoutingTab,
    },
    ...(connectEnabled
      ? ([
          {
            id: "connect",
            group: "ambiente",
            label: m.settings_category_connect(),
            Component: ConnectTab,
          },
        ] as SettingsCategory[])
      : []),
    {
      id: "plugins",
      group: "ambiente",
      label: m.settings_category_plugins(),
      Component: PluginsTab,
    },
    {
      id: "skills",
      group: "ambiente",
      label: m.settings_category_skills(),
      Component: SkillsTab,
    },
    {
      id: "tool_policy",
      group: "ambiente",
      label: m.settings_category_tool_policy(),
      Component: ToolPolicyPanel,
    },
    {
      id: "hitl_allowlist",
      group: "ambiente",
      label: m.settings_category_hitl_allowlist(),
      Component: HitlAllowlistPanel,
    },
  ];

  const administracao: SettingsCategory[] = isAdmin
    ? [
        ...(isFree
          ? []
          : ([
              {
                id: "admin_users",
                group: "administracao",
                label: m.admin_tab_users(),
                Component: UsersPanel,
              },
            ] as SettingsCategory[])),
        {
          id: "admin_tools",
          group: "administracao",
          label: m.admin_tab_tools(),
          Component: ToolsPanel,
        },
        {
          id: "admin_saferoots",
          group: "administracao",
          label: m.admin_tab_saferoots(),
          Component: SafeRootsPanel,
        },
        {
          id: "admin_system",
          group: "administracao",
          label: m.admin_tab_system(),
          Component: SystemPanel,
        },
        {
          id: "admin_storage",
          group: "administracao",
          label: m.admin_tab_storage(),
          Component: StoragePanel,
        },
      ]
    : [];

  const geralGrupo: SettingsCategory[] = [
    {
      id: "billing",
      group: "geral_grupo",
      label: m.settings_category_billing(),
      Component: BillingAndAboutPanel,
    },
  ];

  const groups: SettingsCategoryGroup[] = [
    {
      id: "preferencias",
      label: m.settings_group_preferencias(),
      categories: preferencias,
    },
    {
      id: "ambiente",
      label: m.settings_group_environment(),
      categories: ambiente,
    },
  ];
  if (administracao.length > 0) {
    groups.push({
      id: "administracao",
      label: m.settings_group_admin(),
      categories: administracao,
    });
  }
  groups.push({ id: "geral_grupo", label: "", categories: geralGrupo });

  return groups;
}

/** "about" foi mesclado dentro da categoria "billing" (Cobrança + Sobre
 * empilhados) — mantido como alias pra não quebrar deep-links antigos que
 * ainda abrem `openCategory("about")` diretamente. */
const CATEGORY_ALIASES: Partial<
  Record<SettingsCategoryId, SettingsCategoryId>
> = {
  about: "billing",
};

export function findCategory(
  groups: SettingsCategoryGroup[],
  id: SettingsCategoryId,
): SettingsCategory | undefined {
  const resolvedId = CATEGORY_ALIASES[id] ?? id;
  for (const group of groups) {
    const found = group.categories.find((c) => c.id === resolvedId);
    if (found) return found;
  }
  return undefined;
}

/** Fallback pra quando `activeCategory` persistido/pedido não existe mais
 * na lista visível (ex.: usuário perdeu role admin) — nunca renderiza tela
 * em branco. */
export function firstAvailableCategory(
  groups: SettingsCategoryGroup[],
): SettingsCategoryId {
  for (const group of groups) {
    if (group.categories[0]) return group.categories[0].id;
  }
  return "geral";
}
