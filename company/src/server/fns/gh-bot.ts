import { createServerFn } from "@tanstack/react-start";
import { z } from "zod";
import { servicesFetch } from "#/lib/services/client";

export const GH_BOT_PROVIDERS = [
  "anthropic",
  "openai",
  "google_genai",
  "openrouter",
] as const;

export type GhBotProvider = (typeof GH_BOT_PROVIDERS)[number];

export const GH_BOT_REVIEW_STYLES = ["lenient", "balanced", "strict"] as const;

export type GhBotReviewStyle = (typeof GH_BOT_REVIEW_STYLES)[number];

export interface GhBotSettings {
  provider: GhBotProvider;
  model: string;
  review_style: GhBotReviewStyle;
  /** "Usar minha própria instância Vectora" — revisão roda no motor nativo
   * da instância do usuário (via túnel do gateway) em vez do runner
   * efêmero do GitHub Actions. Cai pro modo padrão automaticamente se a
   * instância estiver offline no momento do PR (ver GET /gha-bot/config). */
  self_hosted_enabled: boolean;
  updated_at: string;
}

export interface GhBotToken {
  id: string;
  repo_scope: string | null;
  created_at: string;
  revoked_at: string | null;
}

export const getGhBotSettings = createServerFn({ method: "GET" }).handler(
  async (): Promise<GhBotSettings | null> => {
    return servicesFetch<GhBotSettings | null>("/gha-bot/settings");
  },
);

const SaveGhBotSettingsSchema = z.object({
  provider: z.enum(GH_BOT_PROVIDERS),
  model: z.string().min(1),
  providerApiKey: z.string().min(1),
  reviewStyle: z.enum(GH_BOT_REVIEW_STYLES),
  selfHostedEnabled: z.boolean(),
});

export const saveGhBotSettings = createServerFn({ method: "POST" })
  .validator(SaveGhBotSettingsSchema)
  .handler(async ({ data }) => {
    return servicesFetch<{ ok: true }>("/gha-bot/settings", {
      method: "PUT",
      body: JSON.stringify({
        provider: data.provider,
        model: data.model,
        provider_api_key: data.providerApiKey,
        review_style: data.reviewStyle,
        self_hosted_enabled: data.selfHostedEnabled,
      }),
    });
  });

export const listGhBotTokens = createServerFn({ method: "GET" }).handler(
  async (): Promise<GhBotToken[]> => {
    return servicesFetch<GhBotToken[]>("/gha-bot/tokens");
  },
);

export const createGhBotToken = createServerFn({ method: "POST" }).handler(
  async () => {
    return servicesFetch<{ secret: string }>("/gha-bot/tokens", {
      method: "POST",
      body: JSON.stringify({}),
    });
  },
);

const RevokeGhBotTokenSchema = z.object({ id: z.string().min(1) });

export const revokeGhBotToken = createServerFn({ method: "POST" })
  .validator(RevokeGhBotTokenSchema)
  .handler(async ({ data }) => {
    return servicesFetch<{ ok: true }>(`/gha-bot/tokens/${data.id}/revoke`, {
      method: "POST",
    });
  });
