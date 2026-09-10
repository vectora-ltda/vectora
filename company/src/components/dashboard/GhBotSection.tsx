import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { m } from "#/paraglide/messages";
import { toast } from "sonner";
import { Copy, Loader2 } from "lucide-react";
import {
  getGhBotSettings,
  saveGhBotSettings,
  listGhBotTokens,
  createGhBotToken,
  revokeGhBotToken,
  GH_BOT_PROVIDERS,
  GH_BOT_REVIEW_STYLES,
  type GhBotProvider,
  type GhBotReviewStyle,
} from "#/server/fns/gh-bot";

const PROVIDER_LABELS: Record<GhBotProvider, string> = {
  anthropic: "Anthropic",
  openai: "OpenAI",
  google_genai: "Google (Gemini)",
  openrouter: "OpenRouter",
  ollama: "Ollama",
};

const REVIEW_STYLE_LABELS: Record<GhBotReviewStyle, () => string> = {
  lenient: m.gh_bot_review_style_lenient,
  balanced: m.gh_bot_review_style_balanced,
  strict: m.gh_bot_review_style_strict,
};

function formatDate(iso: string): string {
  return new Date(iso.replace(" ", "T") + "Z").toLocaleString();
}

const WORKFLOW_YAML = `name: Vectora

on:
  pull_request:
    types: [opened, synchronize]

permissions:
  pull-requests: write
  contents: read

jobs:
  review:
    runs-on: ubuntu-latest
    environment: vectora-bot
    steps:
      - uses: actions/checkout@v6
        with:
          fetch-depth: 0

      - uses: vectora-ltda/vectora-review-action@v1
        with:
          token: \${{ secrets.VECTORA_BOT_TOKEN }}
          github-token: \${{ secrets.GITHUB_TOKEN }}`;

export default function GhBotSection() {
  const queryClient = useQueryClient();
  const [provider, setProvider] = useState<GhBotProvider>("anthropic");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [reviewStyle, setReviewStyle] = useState<GhBotReviewStyle>("balanced");
  const [selfHostedEnabled, setSelfHostedEnabled] = useState<boolean | null>(
    null,
  );
  const [settingsError, setSettingsError] = useState<string | null>(null);
  const [generatedSecret, setGeneratedSecret] = useState<string | null>(null);

  const { data: settings, isLoading: loadingSettings } = useQuery({
    queryKey: ["gh-bot-settings"],
    queryFn: () => getGhBotSettings(),
    staleTime: 30_000,
  });

  const { data: tokens, isLoading: loadingTokens } = useQuery({
    queryKey: ["gh-bot-tokens"],
    queryFn: () => listGhBotTokens(),
    staleTime: 10_000,
  });

  const displayProvider = settings?.provider ?? provider;
  const displayModel = model || (settings?.model ?? "");
  const displayReviewStyle = settings?.review_style ?? reviewStyle;
  const displaySelfHosted =
    selfHostedEnabled ?? settings?.self_hosted_enabled ?? false;

  const saveMutation = useMutation({
    mutationFn: () =>
      saveGhBotSettings({
        data: {
          provider: displayProvider,
          model: displayModel,
          providerApiKey: apiKey,
          reviewStyle: displayReviewStyle,
          selfHostedEnabled: displaySelfHosted,
        },
      }),
    onSuccess: () => {
      toast.success(m.gh_bot_saved());
      setApiKey("");
      queryClient.invalidateQueries({ queryKey: ["gh-bot-settings"] });
    },
    onError: () => toast.error(m.error_generic()),
  });

  const createTokenMutation = useMutation({
    mutationFn: () => createGhBotToken(),
    onSuccess: (res) => {
      setGeneratedSecret(res.secret);
      queryClient.invalidateQueries({ queryKey: ["gh-bot-tokens"] });
    },
    onError: () => toast.error(m.error_generic()),
  });

  const revokeTokenMutation = useMutation({
    mutationFn: (id: string) => revokeGhBotToken({ data: { id } }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["gh-bot-tokens"] });
    },
    onError: () => toast.error(m.error_generic()),
  });

  function handleSave() {
    setSettingsError(null);
    if (!displayModel.trim()) {
      setSettingsError(m.gh_bot_error_missing_model());
      return;
    }
    if (!apiKey.trim()) {
      setSettingsError(m.gh_bot_error_missing_key());
      return;
    }
    saveMutation.mutate();
  }

  async function handleCopyYaml() {
    try {
      await navigator.clipboard.writeText(WORKFLOW_YAML);
      toast.success(m.gh_bot_yaml_copied());
    } catch {
      toast.error(m.error_generic());
    }
  }

  if (loadingSettings) {
    return (
      <div className="h-40 max-w-xl rounded-xl bg-card/30 animate-pulse" />
    );
  }

  return (
    <div className="max-w-xl space-y-4">
      <p className="text-sm text-muted-foreground">{m.gh_bot_lead()}</p>

      <div className="rounded-xl border border-border bg-card/30 p-6 space-y-3">
        <p className="text-sm font-medium">{m.gh_bot_settings_title()}</p>

        <label className="text-xs font-medium text-muted-foreground">
          {m.gh_bot_label_provider()}
        </label>
        <select
          value={displayProvider}
          onChange={(e) => setProvider(e.target.value as GhBotProvider)}
          className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
        >
          {GH_BOT_PROVIDERS.map((p) => (
            <option key={p} value={p}>
              {PROVIDER_LABELS[p]}
            </option>
          ))}
        </select>

        <label className="text-xs font-medium text-muted-foreground">
          {m.gh_bot_label_model()}
        </label>
        <input
          type="text"
          value={displayModel}
          onChange={(e) => setModel(e.target.value)}
          placeholder={m.gh_bot_model_placeholder()}
          className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
        />

        <label className="text-xs font-medium text-muted-foreground">
          {m.gh_bot_label_api_key()}
        </label>
        <input
          type="password"
          autoComplete="off"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder={m.gh_bot_api_key_placeholder()}
          className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm font-mono"
        />

        <label className="text-xs font-medium text-muted-foreground">
          {m.gh_bot_label_review_style()}
        </label>
        <select
          value={displayReviewStyle}
          onChange={(e) => setReviewStyle(e.target.value as GhBotReviewStyle)}
          className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
        >
          {GH_BOT_REVIEW_STYLES.map((style) => (
            <option key={style} value={style}>
              {REVIEW_STYLE_LABELS[style]()}
            </option>
          ))}
        </select>

        <label className="flex items-start gap-3 rounded-lg border border-border bg-background px-3 py-2.5 cursor-pointer">
          <input
            type="checkbox"
            checked={displaySelfHosted}
            onChange={(e) => setSelfHostedEnabled(e.target.checked)}
            className="mt-0.5 h-4 w-4 shrink-0 accent-primary"
          />
          <span className="space-y-0.5">
            <span className="block text-xs font-medium text-foreground">
              {m.gh_bot_self_hosted_label()}
            </span>
            <span className="block text-xs text-muted-foreground">
              {m.gh_bot_self_hosted_hint()}
            </span>
          </span>
        </label>

        {settingsError && (
          <p className="text-xs text-destructive bg-destructive/10 px-3 py-2 rounded-md">
            {settingsError}
          </p>
        )}

        <button
          onClick={handleSave}
          disabled={saveMutation.isPending}
          className="w-full rounded-xl bg-primary py-2.5 text-sm font-semibold text-primary-foreground shadow shadow-primary/25 transition-all hover:bg-primary/90 disabled:opacity-50"
        >
          {saveMutation.isPending
            ? m.form_submitting()
            : m.gh_bot_save_button()}
        </button>
      </div>

      <div className="rounded-xl border border-border bg-card/30 p-6 space-y-3">
        <p className="text-sm font-medium">{m.gh_bot_tokens_title()}</p>

        {loadingTokens ? (
          <div className="h-16 rounded-lg bg-card/30 animate-pulse" />
        ) : tokens && tokens.length > 0 ? (
          <table className="w-full text-xs">
            <thead>
              <tr className="text-muted-foreground text-start">
                <th className="font-medium pb-2">{m.gh_bot_table_id()}</th>
                <th className="font-medium pb-2">
                  {m.gh_bot_table_created_at()}
                </th>
                <th className="font-medium pb-2">{m.gh_bot_table_status()}</th>
                <th className="pb-2" />
              </tr>
            </thead>
            <tbody>
              {tokens.map((t) => {
                const revoked = Boolean(t.revoked_at);
                return (
                  <tr key={t.id} className="border-t border-border">
                    <td className="py-2 font-mono">{t.id.slice(0, 8)}</td>
                    <td className="py-2 text-muted-foreground">
                      {formatDate(t.created_at)}
                    </td>
                    <td className="py-2">
                      <span
                        className={`text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded border ${
                          revoked
                            ? "bg-destructive/10 text-destructive border-destructive/30"
                            : "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border-emerald-500/30"
                        }`}
                      >
                        {revoked
                          ? m.gh_bot_status_revoked()
                          : m.gh_bot_status_active()}
                      </span>
                    </td>
                    <td className="py-2 text-end">
                      {!revoked && (
                        <button
                          onClick={() => revokeTokenMutation.mutate(t.id)}
                          disabled={revokeTokenMutation.isPending}
                          className="text-muted-foreground hover:text-destructive disabled:opacity-40"
                        >
                          {revokeTokenMutation.isPending ? (
                            <Loader2 className="w-3.5 h-3.5 animate-spin inline" />
                          ) : (
                            m.gh_bot_revoke_button()
                          )}
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : (
          <p className="text-xs text-muted-foreground">
            {m.gh_bot_no_tokens()}
          </p>
        )}

        <button
          onClick={() => createTokenMutation.mutate()}
          disabled={createTokenMutation.isPending}
          className="w-full flex items-center justify-center gap-2 rounded-xl border border-border px-4 py-2.5 text-sm font-medium text-foreground/90 hover:border-primary hover:text-foreground disabled:opacity-50 transition-all"
        >
          {createTokenMutation.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : null}
          {m.gh_bot_new_token_button()}
        </button>

        {generatedSecret && (
          <p className="text-xs text-emerald-700 dark:text-emerald-300 bg-emerald-500/10 px-3 py-2 rounded-md font-mono break-all">
            {m.gh_bot_token_generated()} {generatedSecret}
          </p>
        )}
      </div>

      <div className="rounded-xl border border-border bg-card/30 p-6 space-y-3">
        <p className="text-sm font-medium">{m.gh_bot_install_title()}</p>
        <p className="text-xs text-muted-foreground">
          1. {m.gh_bot_install_step1()}
        </p>
        <p className="text-xs text-muted-foreground">
          2. {m.gh_bot_install_step2()}
        </p>
        <div className="relative">
          <pre className="rounded-lg border border-border bg-background p-3 pe-10 text-[11px] overflow-x-auto">
            {WORKFLOW_YAML}
          </pre>
          <button
            type="button"
            onClick={() => void handleCopyYaml()}
            className="absolute top-2 end-2 rounded-lg p-1.5 text-muted-foreground hover:bg-card hover:text-foreground transition-colors"
            title={m.gh_bot_yaml_copy_cta()}
          >
            <Copy className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
    </div>
  );
}
