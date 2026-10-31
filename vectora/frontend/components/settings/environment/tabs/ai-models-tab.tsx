"use client";

import { MediaModelsSection } from "./provider-routing-tab";
import { FallbacksTab } from "../../preferencias/tabs/fallbacks-tab";
import { useEffect, useState } from "react";
import {
  hydrateDecisionModelPreferences,
  useSettingsStore,
  type DecisionModelProvider,
} from "@/lib/stores/settings-store";
import { useWorkspacesStore } from "@/lib/stores/workspaces-store";
import { m } from "@/lib/paraglide/messages";
import { mDyn } from "@/lib/i18n-dyn";
import { MemorySettings } from "@/components/workbench/settings/memory-settings";

const DECISION_PROVIDERS: Array<{ id: DecisionModelProvider; label: string }> =
  [
    { id: "clef-local", label: "ai_models_provider_clef_local" },
    { id: "laya-local", label: "ai_models_provider_laya_local" },
    { id: "clef-api", label: "ai_models_provider_clef_api" },
    { id: "jev-api", label: "ai_models_provider_jev_api" },
  ];

const DECISION_SURFACES = [
  ["hitl", "ai_models_surface_hitl"],
  ["subagents", "ai_models_surface_subagents"],
  ["plan_mode", "ai_models_surface_plan_mode"],
  ["kanban", "ai_models_surface_kanban"],
  ["browser", "ai_models_surface_browser"],
  ["mcp", "ai_models_surface_mcp"],
  ["terminal", "ai_models_surface_terminal"],
  ["network", "ai_models_surface_network"],
  ["background", "ai_models_surface_background"],
  ["connectors", "ai_models_surface_connectors"],
] as const;

interface DecisionStatus {
  model: string;
  ready: boolean;
  configured: boolean;
  credential_valid: boolean | null;
  healthy: boolean;
  diagnostic?: string | null;
}

interface SystemOneConfig {
  enabled: boolean;
  provider: string;
  local_base_url: string;
  local_model: string;
  laya_base_url: string;
  laya_model: string;
  laya_api_key_configured: boolean;
  clef_api_base_url: string;
  clef_account_id: string;
  clef_api_model: string;
  clef_api_key_configured: boolean;
  jev_api_base_url: string;
  jev_api_model: string;
  jev_api_key_configured: boolean;
  allow_remote_fallback: boolean;
  confidence_threshold_hitl: number;
  risk_assessment_timeout_s: number;
}

export function SystemOneConfiguration() {
  const [config, setConfig] = useState<SystemOneConfig | null>(null);
  const [secret, setSecret] = useState("");
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);
  const selected = config?.provider ?? "clef-local";

  useEffect(() => {
    let active = true;
    void fetch("/provider-routing/decision-models/status")
      .then(async (response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return (await response.json()) as SystemOneConfig;
      })
      .then((value) => {
        if (active) setConfig(value);
      })
      .catch(() => {
        if (active) setMessage(mDyn("ai_models_systemone_load_failed"));
      });
    return () => {
      active = false;
    };
  }, []);

  async function save(changes: Record<string, unknown>) {
    setSaving(true);
    setMessage("");
    try {
      const response = await fetch("/provider-routing/decision-models/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(changes),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const updated = (await response.json()) as SystemOneConfig;
      setConfig(updated);
      setSecret("");
      setMessage(mDyn("ai_models_configuration_saved"));
    } catch {
      setMessage(mDyn("ai_models_configuration_save_failed"));
    } finally {
      setSaving(false);
    }
  }

  if (!config)
    return (
      <section
        className="rounded-lg border p-3 text-xs text-muted-foreground"
        aria-label={mDyn("ai_models_systemone_configuration")}
      >
        {message || mDyn("ai_models_systemone_loading")}
      </section>
    );

  const baseUrlKey =
    selected === "clef-local"
      ? "local_base_url"
      : selected === "laya-local"
        ? "laya_base_url"
        : selected === "clef-api"
          ? "clef_api_base_url"
          : "jev_api_base_url";
  const modelKey =
    selected === "clef-local"
      ? "local_model"
      : selected === "laya-local"
        ? "laya_model"
        : selected === "clef-api"
          ? "clef_api_model"
          : "jev_api_model";
  const credentialConfigured =
    selected === "laya-local"
      ? config.laya_api_key_configured
      : selected === "clef-api"
        ? config.clef_api_key_configured
        : selected === "jev-api"
          ? config.jev_api_key_configured
          : false;
  const credentialKey =
    selected === "laya-local"
      ? "laya_api_key"
      : selected === "clef-api"
        ? "clef_api_key"
        : selected === "jev-api"
          ? "jev_api_key"
          : null;
  const accountKey = selected === "clef-api" ? "clef_account_id" : null;
  const accountValue = selected === "clef-api" ? config.clef_account_id : "";
  const baseUrl = config[baseUrlKey as keyof SystemOneConfig] as string;
  const model = config[modelKey as keyof SystemOneConfig] as string;

  return (
    <section
      className="space-y-3 rounded-lg border p-3"
      aria-label={mDyn("ai_models_systemone_provider_configuration")}
    >
      <div>
        <h4 className="text-sm font-medium">
          {mDyn("ai_models_systemone_runtime_credentials")}
        </h4>
        <p className="text-xs text-muted-foreground">
          {mDyn("ai_models_systemone_config_description")}
        </p>
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={config.enabled}
          onChange={(event) => void save({ enabled: event.target.checked })}
        />
        {mDyn("ai_models_systemone_enable_runtime")}
      </label>
      <label className="grid gap-1 text-xs">
        {mDyn("ai_models_systemone_default_provider")}
        <select
          className="h-8 rounded-md border bg-background px-2 text-sm"
          value={config.provider}
          onChange={(event) => void save({ provider: event.target.value })}
        >
          {DECISION_PROVIDERS.map((item) => (
            <option key={item.id} value={item.id}>
              {mDyn(item.label)}
            </option>
          ))}
        </select>
      </label>
      <label className="grid gap-1 text-xs">
        {mDyn("ai_models_base_url")}
        <input
          className="h-8 rounded-md border bg-background px-2 text-sm"
          value={baseUrl}
          onChange={(event) =>
            setConfig({ ...config, [baseUrlKey]: event.target.value })
          }
        />
      </label>
      <label className="grid gap-1 text-xs">
        {mDyn("ai_models_model")}
        <input
          className="h-8 rounded-md border bg-background px-2 text-sm"
          value={model}
          onChange={(event) =>
            setConfig({ ...config, [modelKey]: event.target.value })
          }
        />
      </label>
      {accountKey && (
        <label className="grid gap-1 text-xs">
          {mDyn("ai_models_cloudflare_account_id")}
          <input
            className="h-8 rounded-md border bg-background px-2 text-sm"
            value={accountValue}
            onChange={(event) =>
              setConfig({ ...config, clef_account_id: event.target.value })
            }
          />
        </label>
      )}
      {credentialKey && (
        <label className="grid gap-1 text-xs">
          {mDyn("ai_models_credential")}{" "}
          {credentialConfigured ? mDyn("ai_models_credential_keep_hint") : ""}
          <input
            className="h-8 rounded-md border bg-background px-2 text-sm"
            type="password"
            autoComplete="new-password"
            value={secret}
            onChange={(event) => setSecret(event.target.value)}
          />
        </label>
      )}
      <label className="grid gap-1 text-xs">
        {mDyn("ai_models_minimum_autonomous_confidence")}{" "}
        {config.confidence_threshold_hitl.toFixed(2)}
        <input
          type="range"
          min="0"
          max="1"
          step="0.01"
          value={config.confidence_threshold_hitl}
          onChange={(event) =>
            setConfig({
              ...config,
              confidence_threshold_hitl: Number(event.target.value),
            })
          }
        />
      </label>
      <label className="grid gap-1 text-xs">
        {mDyn("ai_models_risk_assessment_timeout")}{" "}
        {config.risk_assessment_timeout_s}
        <input
          type="number"
          min="0.1"
          max="120"
          step="0.1"
          value={config.risk_assessment_timeout_s}
          onChange={(event) =>
            setConfig({
              ...config,
              risk_assessment_timeout_s: Number(event.target.value),
            })
          }
        />
      </label>
      <label className="flex items-center gap-2 text-xs">
        <input
          type="checkbox"
          checked={config.allow_remote_fallback}
          onChange={(event) =>
            setConfig({
              ...config,
              allow_remote_fallback: event.target.checked,
            })
          }
        />
        {mDyn("ai_models_allow_remote_fallback")}
      </label>
      <button
        type="button"
        className="rounded-md border px-3 py-1.5 text-xs disabled:opacity-50"
        disabled={
          saving ||
          !Number.isFinite(config.risk_assessment_timeout_s) ||
          config.risk_assessment_timeout_s < 0.1 ||
          config.risk_assessment_timeout_s > 120
        }
        onClick={() =>
          void save({
            [baseUrlKey]: baseUrl,
            [modelKey]: model,
            ...(accountKey ? { [accountKey]: accountValue } : {}),
            ...(credentialKey && secret ? { [credentialKey]: secret } : {}),
            confidence_threshold_hitl: config.confidence_threshold_hitl,
            risk_assessment_timeout_s: config.risk_assessment_timeout_s,
            allow_remote_fallback: config.allow_remote_fallback,
          })
        }
      >
        {mDyn("ai_models_save_provider_configuration")}
      </button>
      {message && (
        <p role="status" className="text-xs text-muted-foreground">
          {message}
        </p>
      )}
    </section>
  );
}

function DecisionModelsSection() {
  const workspaceId = useWorkspacesStore((state) => state.active_id);
  const enabled = useSettingsStore((state) => state.decisionModelsEnabled);
  const provider = useSettingsStore((state) => state.decisionModelProvider);
  const surfaces = useSettingsStore((state) => state.decisionModelSurfaces);
  const setEnabled = useSettingsStore(
    (state) => state.setDecisionModelsEnabled,
  );
  const setProvider = useSettingsStore(
    (state) => state.setDecisionModelProvider,
  );
  const setSurfaces = useSettingsStore(
    (state) => state.setDecisionModelSurfaces,
  );
  const [status, setStatus] = useState<DecisionStatus | null>(null);
  const [statusError, setStatusError] = useState(false);

  useEffect(() => {
    void hydrateDecisionModelPreferences(workspaceId);
  }, [workspaceId]);

  useEffect(() => {
    let active = true;
    void fetch(
      `/provider-routing/decision-models/readiness?provider=${encodeURIComponent(provider)}`,
    )
      .then(async (response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return (await response.json()) as DecisionStatus;
      })
      .then((value) => {
        if (active) setStatus(value);
      })
      .catch(() => {
        if (active) setStatusError(true);
      });
    return () => {
      active = false;
    };
  }, [provider]);

  return (
    <section
      className="space-y-3 rounded-lg border p-3"
      aria-label={mDyn("ai_models_decision_models")}
    >
      <div>
        <h4 className="text-sm font-medium">
          {mDyn("ai_models_decision_models")}
        </h4>
        <p className="text-xs text-muted-foreground">
          {mDyn("ai_models_decision_models_description")}
        </p>
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(event) => setEnabled(event.target.checked)}
        />
        {mDyn("ai_models_enable_for_user")}
      </label>
      <label className="grid gap-1 text-xs">
        {mDyn("ai_models_selected_provider")}
        <select
          className="h-8 rounded-md border bg-background px-2 text-sm"
          value={provider}
          onChange={(event) =>
            setProvider(event.target.value as DecisionModelProvider)
          }
        >
          {DECISION_PROVIDERS.map((item) => (
            <option key={item.id} value={item.id}>
              {mDyn(item.label)}
            </option>
          ))}
        </select>
      </label>
      <p role="status" className="text-xs text-muted-foreground">
        {!enabled
          ? m.decision_models_readiness_no_guardrail()
          : statusError
            ? m.decision_models_readiness_unavailable()
            : status
              ? status.ready
                ? m.decision_models_readiness_ready()
                : !status.configured
                  ? m.decision_models_readiness_unconfigured()
                  : status.credential_valid === false
                    ? m.decision_models_readiness_no_credential()
                    : !status.healthy
                      ? m.decision_models_readiness_unhealthy()
                      : m.decision_models_readiness_no_guardrail()
              : m.decision_models_readiness_checking()}
      </p>
      {status?.model && (
        <p className="text-xs text-muted-foreground">
          {mDyn("ai_models_configured_model", { model: status.model })}
        </p>
      )}
      <fieldset className="space-y-2">
        <legend className="text-xs font-medium">
          {mDyn("ai_models_allowed_surfaces")}
        </legend>
        <div className="grid gap-2 sm:grid-cols-2">
          {DECISION_SURFACES.map(([id, label]) => (
            <label key={id} className="flex items-center gap-2 text-xs">
              <input
                type="checkbox"
                checked={surfaces[id] === true}
                onChange={(event) =>
                  setSurfaces({ ...surfaces, [id]: event.target.checked })
                }
              />
              {mDyn(label)}
            </label>
          ))}
        </div>
      </fieldset>
    </section>
  );
}

/**
 * Dedicated AI configuration surface. Provider Routing remains focused on
 * transport/provider endpoints; capability defaults such as vision and audio
 * live here with the decision-model controls that will follow in this area.
 */
export function TranscriptionSettings() {
  const transcriptionMode = useSettingsStore(
    (state) => state.transcriptionMode,
  );
  const setTranscriptionMode = useSettingsStore(
    (state) => state.setTranscriptionMode,
  );
  const [localReadiness, setLocalReadiness] = useState<{
    dependency_available: boolean;
    model_cached: boolean;
    runtime_verified?: boolean;
    probe_attempted?: boolean;
    probe_in_progress?: boolean;
    ready: boolean;
  } | null>(null);
  const [probingLocalRuntime, setProbingLocalRuntime] = useState(false);

  useEffect(() => {
    let active = true;
    void fetch("/vectora.chat.v1.ChatService/TranscriptionReadiness")
      .then(async (response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return (await response.json()) as {
          dependency_available: boolean;
          model_cached: boolean;
          ready: boolean;
        };
      })
      .then((value) => {
        if (active) setLocalReadiness(value);
      })
      .catch(() => {
        if (active) setLocalReadiness(null);
      });
    return () => {
      active = false;
    };
  }, []);

  const probeLocalRuntime = async () => {
    setProbingLocalRuntime(true);
    try {
      const response = await fetch(
        "/vectora.chat.v1.ChatService/TranscriptionReadiness?probe=true",
      );
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setLocalReadiness(
        (await response.json()) as {
          dependency_available: boolean;
          model_cached: boolean;
          runtime_verified?: boolean;
          probe_attempted?: boolean;
          probe_in_progress?: boolean;
          ready: boolean;
        },
      );
    } catch {
      setLocalReadiness(null);
    } finally {
      setProbingLocalRuntime(false);
    }
  };
  return (
    <section
      className="space-y-2 rounded-lg border p-3"
      aria-label={mDyn("ai_models_transcription")}
    >
      <h4 className="text-sm font-medium">
        {mDyn("ai_models_audio_transcription")}
      </h4>
      <label className="grid gap-1 text-xs text-muted-foreground">
        {mDyn("ai_models_transcription_mode_label")}
        <select
          aria-label={mDyn("ai_models_transcription_mode_label")}
          className="h-9 rounded-md border bg-background px-2 text-sm text-foreground"
          value={transcriptionMode}
          onChange={(event) =>
            setTranscriptionMode(event.target.value as "local" | "remote")
          }
        >
          <option value="remote">
            {mDyn("ai_models_transcription_remote")}
          </option>
          <option value="local">{mDyn("ai_models_transcription_local")}</option>
        </select>
      </label>
      <p className="text-xs text-muted-foreground">
        {mDyn("ai_models_transcription_description")}
      </p>
      {transcriptionMode === "local" && (
        <p role="status" className="text-xs text-muted-foreground">
          {!localReadiness
            ? mDyn("ai_models_transcription_readiness_unknown")
            : !localReadiness.dependency_available
              ? mDyn("ai_models_transcription_dependency_missing")
              : !localReadiness.model_cached
                ? mDyn("ai_models_transcription_model_download_required")
                : localReadiness.ready
                  ? mDyn("ai_models_transcription_local_ready")
                  : localReadiness.probe_in_progress
                    ? mDyn("ai_models_transcription_probe_in_progress")
                    : localReadiness.probe_attempted
                      ? mDyn("ai_models_transcription_probe_failed")
                      : mDyn("ai_models_transcription_probe_required")}
        </p>
      )}
      {transcriptionMode === "local" && localReadiness?.model_cached && (
        <button
          type="button"
          className="h-8 rounded-md border px-3 text-xs"
          onClick={() => void probeLocalRuntime()}
          disabled={probingLocalRuntime || localReadiness.probe_in_progress}
        >
          {probingLocalRuntime
            ? mDyn("ai_models_transcription_probe_running")
            : mDyn("ai_models_transcription_probe_button")}
        </button>
      )}
    </section>
  );
}

export function AiModelsTab() {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-sm font-medium text-foreground">
          {m.settings_category_ai_models()}
        </h3>
        <p className="mt-1 text-xs leading-5 text-muted-foreground">
          {m.ai_models_description()}
        </p>
      </div>
      <DecisionModelsSection />
      <SystemOneConfiguration />
      <section
        className="space-y-3 rounded-lg border p-3"
        aria-label={mDyn("ai_models_model_fallbacks")}
      >
        <h4 className="text-sm font-medium">
          {m.settings_category_fallbacks()}
        </h4>
        <FallbacksTab />
      </section>
      <MediaModelsSection />
      <section
        className="space-y-3 rounded-lg border p-3"
        aria-label={mDyn("ai_models_embeddings_and_reranker")}
      >
        <div>
          <h4 className="text-sm font-medium">
            {mDyn("ai_models_embeddings_reranker_retrieval")}
          </h4>
          <p className="text-xs text-muted-foreground">
            {mDyn("ai_models_embeddings_description")}
          </p>
        </div>
        <MemorySettings />
      </section>
      <TranscriptionSettings />
    </div>
  );
}
