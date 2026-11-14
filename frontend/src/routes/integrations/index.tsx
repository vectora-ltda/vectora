import { Suspense } from "react";
import { ArrowLeft } from "lucide-react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { lazyWithRetry } from "@/lib/lazy-with-retry";
import { m } from "@/lib/paraglide/messages";

const IntegracoesTab = lazyWithRetry(async () => {
  const { IntegracoesTab: Tab } =
    await import("@/components/settings/environment/tabs/integracoes-tab");
  // Na página inteira não há atalho "abrir em página": já estamos nela.
  return { default: () => <Tab /> };
}, "integrations-page-tab");

export const Route = createFileRoute("/integrations/")({
  component: IntegrationsPage,
});

function IntegrationsPage() {
  return (
    <main className="flex-1 overflow-auto">
      <div className="max-w-2xl mx-auto p-6 space-y-6">
        <Link
          to="/"
          className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" />
          {m.integrations_page_back()}
        </Link>
        <div className="space-y-1">
          <h1 className="text-xl font-semibold">
            {m.integrations_page_title()}
          </h1>
          <p className="text-sm text-muted-foreground">
            {m.integrations_page_subtitle()}
          </p>
        </div>
        <Suspense
          fallback={<div className="h-40 animate-pulse rounded-lg bg-muted" />}
        >
          <IntegracoesTab />
        </Suspense>
      </div>
    </main>
  );
}
