import { createFileRoute } from "@tanstack/react-router";
import {
  Bot,
  ClipboardList,
  Cloud,
  Database,
  FlaskConical,
  FolderTree,
  GitBranch,
  GitPullRequest,
  Globe,
  History,
  Languages,
  Library,
  ListTree,
  MessageCircle,
  MonitorSmartphone,
  Network,
  Plug,
  Route as RouteIcon,
  Scale,
  Server,
  ShieldAlert,
  ShieldCheck,
  TerminalSquare,
  Users,
  Video,
  Workflow,
} from "lucide-react";
import { m } from "#/paraglide/messages";
import Container from "#/components/shared/Container";
import PageHeader from "#/components/shared/PageHeader";
import FeaturesAccordion, {
  type FeatureItem,
} from "#/components/shared/FeaturesAccordion";

type Category = {
  id: string;
  label: () => string;
  items: FeatureItem[];
};

export function getFeatureCategories(): Category[] {
  return [
    {
      id: "agente",
      label: m.features_cat_agente,
      items: [
        {
          id: "native-engine",
          Icon: Bot,
          title: m.features_native_engine_title(),
          summary: m.features_native_engine_summary(),
          description: m.features_native_engine_desc(),
        },
        {
          id: "souls",
          Icon: Users,
          title: m.features_souls_title(),
          summary: m.features_souls_summary(),
          description: m.features_souls_desc(),
        },
        {
          id: "kanban",
          Icon: ListTree,
          title: m.features_kanban_title(),
          summary: m.features_kanban_summary(),
          description: m.features_kanban_desc(),
        },
        {
          id: "agent-run",
          Icon: History,
          title: m.features_agent_run_title(),
          summary: m.features_agent_run_summary(),
          description: m.features_agent_run_desc(),
        },
        {
          id: "plan-mode",
          Icon: ClipboardList,
          title: m.features_plan_mode_title(),
          summary: m.features_plan_mode_summary(),
          description: m.features_plan_mode_desc(),
        },
        {
          id: "decision-models",
          Icon: Scale,
          title: m.features_decision_models_title(),
          summary: m.features_decision_models_summary(),
          description: m.features_decision_models_desc(),
        },
        {
          id: "verification-lab",
          Icon: FlaskConical,
          title: m.features_verification_lab_title(),
          summary: m.features_verification_lab_summary(),
          description: m.features_verification_lab_desc(),
        },
      ],
    },
    {
      id: "contexto",
      label: m.features_cat_contexto,
      items: [
        {
          id: "context-graph",
          Icon: Network,
          title: m.capability_context_graph_title(),
          summary: m.capability_context_graph_desc(),
          description: m.features_context_graph_desc(),
        },
        {
          id: "rag",
          Icon: Server,
          title: m.features_rag_title(),
          summary: m.features_rag_summary(),
          description: m.features_rag_desc(),
        },
        {
          id: "terminal",
          Icon: TerminalSquare,
          title: m.capability_terminal_title(),
          summary: m.capability_terminal_desc(),
          description: m.features_terminal_desc(),
        },
        {
          id: "git",
          Icon: GitBranch,
          title: m.capability_git_title(),
          summary: m.capability_git_desc(),
          description: m.features_git_desc(),
        },
        {
          id: "explorer",
          Icon: FolderTree,
          title: m.capability_explorer_title(),
          summary: m.capability_explorer_desc(),
          description: m.features_explorer_desc(),
        },
      ],
    },
    {
      id: "navegador",
      label: m.features_cat_navegador,
      items: [
        {
          id: "browser",
          Icon: Globe,
          title: m.capability_browser_title(),
          summary: m.capability_browser_desc(),
          description: m.features_browser_desc(),
        },
        {
          id: "native-media",
          Icon: Video,
          title: m.features_native_media_title(),
          summary: m.features_native_media_summary(),
          description: m.features_native_media_desc(),
        },
        {
          id: "sandbox",
          Icon: ShieldCheck,
          title: m.capability_sandbox_title(),
          summary: m.capability_sandbox_desc(),
          description: m.features_sandbox_desc(),
        },
        {
          id: "egress-policies",
          Icon: ShieldAlert,
          title: m.features_egress_policies_title(),
          summary: m.features_egress_policies_summary(),
          description: m.features_egress_policies_desc(),
        },
      ],
    },
    {
      id: "modelos",
      label: m.features_cat_modelos,
      items: [
        {
          id: "nine-router",
          Icon: Workflow,
          title: m.features_nine_router_title(),
          summary: m.features_nine_router_summary(),
          description: m.features_nine_router_desc(),
        },
        {
          id: "ollama",
          Icon: Server,
          title: m.features_ollama_title(),
          summary: m.features_ollama_summary(),
          description: m.features_ollama_desc(),
        },
        {
          id: "openrouter",
          Icon: RouteIcon,
          title: m.features_openrouter_title(),
          summary: m.features_openrouter_summary(),
          description: m.features_openrouter_desc(),
        },
        {
          id: "mcp",
          Icon: Plug,
          title: m.features_mcp_title(),
          summary: m.features_mcp_summary(),
          description: m.features_mcp_desc(),
        },
        {
          id: "library",
          Icon: Library,
          title: m.features_library_title(),
          summary: m.features_library_summary(),
          description: m.features_library_desc(),
        },
      ],
    },
    {
      id: "pro",
      label: m.features_cat_pro,
      items: [
        {
          id: "vps",
          Icon: Server,
          title: m.features_vps_title(),
          summary: m.features_vps_summary(),
          description: m.features_vps_desc(),
          pro: true,
        },
        {
          id: "connect",
          Icon: MessageCircle,
          title: m.features_connect_title(),
          summary: m.features_connect_summary(),
          description: m.features_connect_desc(),
          pro: true,
        },
        {
          id: "storage-avancado",
          Icon: Database,
          title: m.features_storage_avancado_title(),
          summary: m.features_storage_avancado_summary(),
          description: m.features_storage_avancado_desc(),
          pro: true,
        },
        {
          id: "bot-gha",
          Icon: GitPullRequest,
          title: m.features_bot_gha_title(),
          summary: m.features_bot_gha_summary(),
          description: m.features_bot_gha_desc(),
          pro: true,
        },
      ],
    },
    {
      id: "plataforma",
      label: m.features_cat_plataforma,
      items: [
        {
          id: "desktop",
          Icon: MonitorSmartphone,
          title: m.features_desktop_title(),
          summary: m.features_desktop_summary(),
          description: m.features_desktop_desc(),
        },
        {
          id: "web-multiuser",
          Icon: Users,
          title: m.features_web_multiuser_title(),
          summary: m.features_web_multiuser_summary(),
          description: m.features_web_multiuser_desc(),
          pro: true,
        },
        {
          id: "services",
          Icon: Cloud,
          title: m.features_services_title(),
          summary: m.features_services_summary(),
          description: m.features_services_desc(),
        },
        {
          id: "i18n",
          Icon: Languages,
          title: m.features_i18n_title(),
          summary: m.features_i18n_summary(),
          description: m.features_i18n_desc(),
        },
      ],
    },
  ];
}

export const Route = createFileRoute("/features")({
  head: () => ({
    meta: [
      { title: m.page_features_title() },
      { name: "description", content: m.page_features_desc() },
      {
        property: "og:image",
        content: `/api/og?title=${encodeURIComponent(m.page_features_title())}&desc=${encodeURIComponent(m.page_features_desc())}`,
      },
    ],
  }),
  component: FeaturesPage,
});

function FeaturesPage() {
  const categories = getFeatureCategories();

  return (
    <Container size="prose" className="py-16">
      <PageHeader
        title={m.page_features_title()}
        subtitle={m.page_features_desc()}
      />

      <div className="mt-10 space-y-10">
        {categories.map((category) => (
          <div key={category.id}>
            <h2 className="mb-4 text-sm font-semibold uppercase tracking-wider text-muted-foreground">
              {category.label()}
            </h2>
            <FeaturesAccordion items={category.items} />
          </div>
        ))}
      </div>
    </Container>
  );
}
