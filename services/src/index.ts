/**
 * vectora-services — Worker único que serve `gateway/`, `updates/` e o
 * backend da company. Dispatch por hostname:
 *
 * - `gateway.vectora.chat` e `{token}.vectora.chat` → gateway (WebSocket
 *   bidirecional de OAuth/webhooks pro app desktop — ex-relay, renomeado
 *   sem alias de transição por decisão do produto).
 * - Qualquer outro host → um único Hono app com auth/profile/billing/
 *   license/gdpr/api-keys/gha-bot/issues/rag-library/registry/telemetry e updates
 *   (electron-updater + download público, `src/updates/worker.ts`, mesclado
 *   na raiz via `.route("/", ...)`) — sem domínio dedicado.
 *
 * `queue()` processa as duas filas do Worker (`vectora-email`, `vectora-jobs`
 * — ver src/queue-consumer.ts e wrangler.toml). `scheduled()` também roda
 * `runDiscovery` (src/registry/discovery.ts) — popula mcp_catalog/
 * skills_catalog automaticamente, além do seed manual da migration.
 */

import { Hono } from "hono";
import gatewayHandler, {
  GatewaySession,
  GATEWAY_HOST,
  GATEWAY_BASE_DOMAIN,
} from "./gateway";
import updatesApp from "./updates/worker";
import { auth } from "./auth/routes";
import { admin } from "./admin/routes";
import { profile } from "./profile/routes";
import { billing } from "./billing/routes";
import { license } from "./license/routes";
import { oauth } from "./oauth/routes";
import { gdpr, enqueueExpiredUserDeletions } from "./gdpr/routes";
import { expireGiftSubscriptions } from "./billing/routes";
import { apiKeys } from "./api-keys/routes";
import { ghaBot } from "./gha-bot/routes";
import { issues } from "./issues/routes";
import { ragLibrary } from "./rag-library/routes";
import { registry } from "./registry/routes";
import { runDiscovery } from "./registry/discovery";
import { telemetry } from "./telemetry/routes";
import { handleQueue } from "./queue-consumer";
import type { Env } from "./gateway/types";
import { OAuthResult } from "./oauth/oauth-result";

export { GatewaySession, OAuthResult };

function isGatewayHost(hostname: string): boolean {
  return (
    hostname === GATEWAY_HOST || hostname.endsWith(`.${GATEWAY_BASE_DOMAIN}`)
  );
}

const servicesApp = new Hono<{ Bindings: Env }>();
servicesApp.route("/auth", auth);
servicesApp.route("/admin", admin);
servicesApp.route("/profile", profile);
servicesApp.route("/billing", billing);
servicesApp.route("/license", license);
servicesApp.route("/oauth", oauth);
servicesApp.route("/gdpr", gdpr);
servicesApp.route("/api-keys", apiKeys);
servicesApp.route("/gha-bot", ghaBot);
servicesApp.route("/issues", issues);
servicesApp.route("/rag-library", ragLibrary);
servicesApp.route("/registry", registry);
servicesApp.route("/telemetry", telemetry);
// updates/worker.ts mesclado na raiz, sem prefixo: /download/*, /updates/*,
// /telemetry/update-result somam às rotas acima.
servicesApp.route("/", updatesApp);
servicesApp.get("/health", (c) =>
  c.json({ ok: true, server: "vectora-services" }),
);

// Handler global de erro — achado da auditoria de segurança de
// 2026-08-30: a maioria dos módulos de rota (gdpr, rag-library,
// telemetry, oauth, profile, license, gha-bot) não tinha try/catch
// nenhum em I/O (D1/KV/fetch externo), e só 2 arquivos em todo o repo
// logavam algo — uma falha de rede virava um 500 genérico do runtime,
// sem nenhum registro correlacionável em produção. Um onError central
// no app inteiro cobre TODO handler de uma vez (Hono já intercepta
// exceção não capturada e chama isto antes de devolver a resposta) —
// mais robusto que espalhar try/catch repetido em cada rota, e não
// exige tocar nos 7 módulos individualmente pra ganhar observabilidade
// real.
servicesApp.onError((err, c) => {
  console.error("unhandled_error", {
    method: c.req.method,
    path: c.req.path,
    message: err instanceof Error ? err.message : String(err),
    stack: err instanceof Error ? err.stack : undefined,
  });
  return c.json({ error: "internal_error" }, 500);
});

export default {
  fetch(
    request: Request,
    env: Env,
    ctx: ExecutionContext,
  ): Response | Promise<Response> {
    const url = new URL(request.url);
    if (isGatewayHost(url.hostname)) {
      return gatewayHandler.fetch(request, env);
    }
    return servicesApp.fetch(request, env, ctx);
  },

  async scheduled(
    _controller: ScheduledController,
    env: Env,
    ctx: ExecutionContext,
  ): Promise<void> {
    ctx.waitUntil(enqueueExpiredUserDeletions(env).then(() => undefined));
    ctx.waitUntil(expireGiftSubscriptions(env.DB).then(() => undefined));
    ctx.waitUntil(runDiscovery(env));
  },

  async queue(batch: MessageBatch<unknown>, env: Env): Promise<void> {
    await handleQueue(batch, env);
  },
} satisfies ExportedHandler<Env>;
