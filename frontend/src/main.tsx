import React from "react";
import ReactDOM from "react-dom/client";
import { RouterProvider } from "@tanstack/react-router";
import { QueryClientProvider } from "@tanstack/react-query";
import { TooltipProvider } from "@/components/ui/tooltip";

import { router, queryClient } from "./router";
import { getLocale, setLocale } from "@/lib/paraglide/runtime";
import { useSettingsStore } from "@/lib/stores/settings-store";
import "./styles.css";

// Sincroniza o locale do Paraglide com o idioma persistido antes de renderizar,
// para que as mensagens m.* saiam no idioma correto já no primeiro paint.
{
  const lang = useSettingsStore.getState().language;
  if (getLocale() !== lang) setLocale(lang, { reload: false });
}

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

// PWA: o service worker (registerType "autoUpdate") precacheia o app. Após um
// rebuild, o novo SW instala e — com skipWaiting/clientsClaim — assume o controle,
// disparando `controllerchange`. Recarregamos UMA vez aqui para servir os assets
// novos em vez do cache antigo, evitando ver UI desatualizada após `scons frontend`.
//
// Registro manual (`injectRegister: false` em vite.config.ts) — o script
// auto-injetado do vite-plugin-pwa chamava `serviceWorker.register()` sem
// checar o protocolo da origem; no app desktop (Electron, scheme customizado
// `vectora-app://`) isso sempre falha (service worker só existe em
// http:/https:, spec do browser), gerando erro de console todo boot. Só
// registra em origem http(s) — no Electron pula inteiro, sem erro.
if (
  typeof navigator !== "undefined" &&
  "serviceWorker" in navigator &&
  typeof window !== "undefined" &&
  /^https?:$/.test(window.location.protocol)
) {
  // Na 1ª visita a página ainda não é controlada por um SW; nesse caso o
  // controllerchange é a instalação inicial (não um update) e NÃO recarregamos.
  const hadController = Boolean(navigator.serviceWorker.controller);
  let reloaded = false;
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (reloaded || !hadController) return;
    reloaded = true;
    window.location.reload();
  });

  void import("virtual:pwa-register").then(({ registerSW }) => {
    registerSW({ immediate: true });
  });
}

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error("#root não encontrado em index.html");
}

ReactDOM.createRoot(rootElement).render(
  <React.StrictMode>
    <TooltipProvider delayDuration={200}>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} context={{ queryClient }} />
      </QueryClientProvider>
    </TooltipProvider>
  </React.StrictMode>,
);
