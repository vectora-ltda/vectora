"use client";

/**
 * PreAuthWizard — primeiro acesso, antes de qualquer conta existir.
 *
 * Único welcome do produto (tela cheia, multi-step): identidade (nome +
 * username + empresa + idioma + tema) → modo (Local vs VPS) → continuação
 * compartilhada pelos dois caminhos (token, storage, chaves de API,
 * workspace, memória, capacidades) → concluir.
 *
 * - Local: chama POST /auth/setup-local (persiste VECTORA_AUTH_REQUIRED=false
 *   + nome/username/empresa) e segue direto pros passos de continuação na
 *   mesma SPA — o AuthMiddleware já trata qualquer request como o usuário
 *   virtual "local" assim que auth_required=false, sem depender de cookie.
 *   O reload real só acontece uma vez, no fim (StepDone → Concluir).
 * - VPS: recurso Pro — pede um VECTORA_TOKEN, valida via
 *   POST /license/validate-token (só funciona no primeiro acesso, mesma
 *   guarda do setup-local) e, se válido e tier=pro, segue pro /auth/signup
 *   de verdade (conta real, multi-usuário) com nome/username pré-preenchidos;
 *   de lá, o signup bem-sucedido volta pra cá com `?continue=1` pra entrar
 *   nos mesmos passos de continuação antes do reload final.
 */

import { useEffect, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import {
  Loader2,
  Check,
  CheckCircle2,
  XCircle,
  Monitor,
  Server,
  Moon,
  Sun,
  Laptop,
  Database,
  Upload,
} from "lucide-react";
import { m } from "@/lib/paraglide/messages";
import { mDyn } from "@/lib/i18n-dyn";
import { signalVpsGatePassed } from "@/lib/stores/onboarding-signal";
import { useOnboardingDraftStore } from "@/lib/stores/onboarding-draft-store";
import {
  checkUsername,
  slugifyUsername,
  type UsernameStatus,
} from "@/lib/api/username";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  useSettingsStore,
  SUPPORTED_LANGS,
  type Lang,
  type Theme,
} from "@/lib/stores/settings-store";
import {
  ONBOARDING_CONTINUATION_STEPS,
  ONBOARDING_CONTINUATION_TITLE_KEYS,
  StepIndicator,
} from "./setup-wizard";

type Step = "identity" | "mode" | "vps-token" | "restore" | "continuation";

type BackupPreview = {
  categories: Record<string, number>;
  size_bytes: number;
  storage_mode: string;
  results?: Record<string, { status: string; count: number }>;
};

const VPS_FEATURE_LABELS = [
  m.onboarding_pre_vps_feature_1,
  m.onboarding_pre_vps_feature_2,
  m.onboarding_pre_vps_feature_3,
  m.onboarding_pre_vps_feature_4,
  m.onboarding_pre_vps_feature_5,
];

function openExternal(url: string): void {
  if (typeof window !== "undefined" && window.vectora?.openExternal) {
    void window.vectora.openExternal(url);
    return;
  }
  window.open(url, "_blank", "noopener,noreferrer");
}

const THEME_OPTIONS: {
  value: Theme;
  label: () => string;
  icon: typeof Laptop;
}[] = [
  { value: "system", label: m.onboarding_pre_theme_system, icon: Laptop },
  { value: "dark", label: m.onboarding_pre_theme_dark, icon: Moon },
  { value: "light", label: m.onboarding_pre_theme_light, icon: Sun },
];

interface PreAuthWizardProps {
  /** `true` quando chega aqui depois de um `/auth/signup` (VPS) bem-sucedido
   * — pula identity/mode/vps-token direto pros passos compartilhados. */
  startAtContinuation?: boolean;
}

export function PreAuthWizard({
  startAtContinuation = false,
}: PreAuthWizardProps) {
  const navigate = useNavigate();
  const language = useSettingsStore((s) => s.language);
  const setLanguage = useSettingsStore((s) => s.setLanguage);
  const theme = useSettingsStore((s) => s.theme);
  const setTheme = useSettingsStore((s) => s.setTheme);

  const name = useOnboardingDraftStore((s) => s.name);
  const setName = useOnboardingDraftStore((s) => s.setName);
  const username = useOnboardingDraftStore((s) => s.username);
  const setUsername = useOnboardingDraftStore((s) => s.setUsername);
  const company = useOnboardingDraftStore((s) => s.company);
  const setCompany = useOnboardingDraftStore((s) => s.setCompany);
  const resetDraft = useOnboardingDraftStore((s) => s.reset);

  const [step, setStep] = useState<Step>(
    startAtContinuation ? "continuation" : "identity",
  );
  const [continuationIndex, setContinuationIndex] = useState(0);
  const [continuationValid, setContinuationValid] = useState(true);
  const [selectedWorkspace, setSelectedWorkspace] = useState<string | null>(
    null,
  );

  const [nameError, setNameError] = useState<string | null>(null);
  const [usernameError, setUsernameError] = useState<string | null>(null);
  // Enquanto o usuário não editar o username manualmente, ele segue o slug
  // do nome (mesmo padrão do /auth/signup real) — um username já restaurado
  // do rascunho que diverge do slug atual conta como "editado", pra não
  // sobrescrever uma customização ao voltar de um reload (troca de idioma).
  const [usernameEdited, setUsernameEdited] = useState(
    () => !!username.trim() && username.trim() !== slugifyUsername(name),
  );
  const [usernameStatus, setUsernameStatus] = useState<UsernameStatus | null>(
    null,
  );
  const [checkingUsername, setCheckingUsername] = useState(false);
  const [token, setToken] = useState("");
  const [tokenError, setTokenError] = useState<string | null>(null);
  const [validating, setValidating] = useState(false);
  const [settingUpLocal, setSettingUpLocal] = useState(false);
  const [backupPreview, setBackupPreview] = useState<BackupPreview | null>(
    null,
  );
  const [backupSelection, setBackupSelection] = useState<string[]>([]);
  const [backupBusy, setBackupBusy] = useState(false);
  const [backupError, setBackupError] = useState(false);
  const [backupImported, setBackupImported] = useState(false);
  const [backupResult, setBackupResult] = useState<BackupPreview | null>(null);

  // Auto-preenche o username a partir do nome enquanto não houver edição
  // manual — mesmo padrão do /auth/signup real. Comparação durante o render
  // (não num effect) é o padrão que o React recomenda pra "ajustar estado
  // quando uma prop muda":
  // https://react.dev/learn/you-might-not-need-an-effect#adjusting-some-state-when-a-prop-changes
  const [prevName, setPrevName] = useState<string | null>(null);
  if (name !== prevName) {
    setPrevName(name);
    if (!usernameEdited) setUsername(slugifyUsername(name));
  }

  // Checagem de disponibilidade com debounce — mesmo padrão do /auth/signup
  // real (lib/api/username.ts::checkUsername), reaproveitado aqui.
  useEffect(() => {
    const u = username.trim();
    if (!u) {
      // Busca de disponibilidade (I/O de rede) com debounce — não é estado
      // derivado de prop.
      // oxlint-disable-next-line react/set-state-in-effect
      setUsernameStatus(null);
      setCheckingUsername(false);
      return;
    }
    setCheckingUsername(true);
    let cancelled = false;
    const handle = setTimeout(() => {
      void checkUsername(u).then((status) => {
        if (cancelled) return;
        setUsernameStatus(status);
        setCheckingUsername(false);
      });
    }, 350);
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [username]);

  // Reseta a validade do passo de continuação sempre que o índice muda —
  // comparação durante o render (não num effect), mesmo padrão do
  // auto-preenchimento de username acima.
  const [prevContinuationIndex, setPrevContinuationIndex] =
    useState(continuationIndex);
  if (continuationIndex !== prevContinuationIndex) {
    setPrevContinuationIndex(continuationIndex);
    setContinuationValid(true);
  }

  function handleIdentityNext() {
    let hasError = false;
    if (!name.trim()) {
      setNameError(m.onboarding_pre_name_required());
      hasError = true;
    } else {
      setNameError(null);
    }
    if (!username.trim()) {
      setUsernameError(m.auth_signup_username_required());
      hasError = true;
    } else if (usernameStatus && !usernameStatus.available) {
      hasError = true;
    } else {
      setUsernameError(null);
    }
    if (hasError) return;
    setStep("mode");
  }

  async function handleSelectLocal() {
    setSettingUpLocal(true);
    setNameError(null);
    try {
      const res = await fetch("/auth/setup-local", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: name.trim(),
          company: company.trim(),
          username: username.trim(),
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setNameError(
          (data as { detail?: string }).detail ??
            m.onboarding_pre_setup_error(),
        );
        setSettingUpLocal(false);
        return;
      }
      setSettingUpLocal(false);
      const preview = await window.vectora?.backupPickAndInspect?.();
      if (preview?.compatible && preview.storage_mode === "lite") {
        setBackupPreview(preview);
        setBackupSelection(Object.keys(preview.categories));
        setStep("restore");
      } else {
        setStep("continuation");
        setContinuationIndex(0);
      }
    } catch {
      setNameError(m.onboarding_pre_setup_error());
      setSettingUpLocal(false);
    }
  }

  async function handleBackupRestore(): Promise<void> {
    setBackupBusy(true);
    setBackupError(false);
    try {
      const result = await window.vectora?.backupRestore?.(backupSelection);
      if (!result) {
        setBackupError(true);
        return;
      }
      setBackupResult(result);
      setBackupImported(true);
    } catch {
      setBackupError(true);
    } finally {
      setBackupBusy(false);
    }
  }

  async function handleValidateVpsToken() {
    const value = token.trim();
    if (!value) return;
    setValidating(true);
    setTokenError(null);
    try {
      const res = await fetch("/license/validate-token", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: value }),
      });
      const data = (await res.json()) as { valid: boolean; error?: string };
      if (!data.valid) {
        setTokenError(
          data.error === "not_pro_tier"
            ? m.onboarding_pre_vps_token_invalid()
            : (data.error ?? m.onboarding_pre_vps_token_invalid()),
        );
        return;
      }
      signalVpsGatePassed();
      void navigate({
        to: "/auth/signup",
        search: { name: name.trim(), username: username.trim() },
      });
    } catch {
      setTokenError(m.onboarding_pre_vps_token_invalid());
    } finally {
      setValidating(false);
    }
  }

  function handleContinuationNext() {
    if (continuationIndex < ONBOARDING_CONTINUATION_STEPS.length - 1) {
      setContinuationIndex((i) => i + 1);
      return;
    }
    resetDraft();
    window.location.href = "/";
  }

  function handleContinuationBack() {
    setContinuationIndex((i) => Math.max(0, i - 1));
  }

  const ContinuationStep = ONBOARDING_CONTINUATION_STEPS[continuationIndex]!;
  const continuationTotal = ONBOARDING_CONTINUATION_STEPS.length;
  const isFirstContinuationStep = continuationIndex === 0;
  const isLastContinuationStep = continuationIndex === continuationTotal - 1;

  if (step === "continuation") {
    return (
      <div className="min-h-full flex items-center justify-center bg-background px-4">
        <div className="w-full max-w-sm space-y-4">
          <div className="flex flex-col items-center gap-2">
            <div className="flex items-center gap-2.5">
              <img
                src="/vectora.svg"
                alt={m.onboarding_brand_name()}
                width={36}
                height={36}
              />
              <h1
                className="text-2xl font-semibold tracking-tight text-foreground"
                style={{ fontFamily: "var(--font-aeonik-mono)" }}
              >
                {m.onboarding_brand_name()}
              </h1>
            </div>
            <p className="text-sm font-medium text-foreground text-center">
              {mDyn(ONBOARDING_CONTINUATION_TITLE_KEYS[continuationIndex]!)}
            </p>
          </div>

          <div data-testid="step-content-area">
            <ContinuationStep
              onValidityChange={setContinuationValid}
              onWorkspaceSelect={setSelectedWorkspace}
            />
          </div>

          <StepIndicator step={continuationIndex} total={continuationTotal} />

          <div className="flex items-center justify-between gap-2">
            <button
              type="button"
              onClick={handleContinuationBack}
              disabled={isFirstContinuationStep}
              className={`text-xs text-muted-foreground hover:text-foreground transition-colors ${
                isFirstContinuationStep ? "invisible" : ""
              }`}
            >
              {m.onboarding_back()}
            </button>
            <button
              type="button"
              onClick={handleContinuationNext}
              disabled={!continuationValid}
              className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-60 transition-colors"
            >
              {isLastContinuationStep
                ? m.onboarding_finish()
                : m.onboarding_next()}
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (step === "restore" && backupPreview) {
    const labels: Record<string, () => string> = {
      workspaces: m.onboarding_backup_category_workspaces,
      threads: m.onboarding_backup_category_threads,
      memories: m.onboarding_backup_category_memories,
    };
    return (
      <div className="min-h-full flex items-center justify-center bg-background px-4">
        <div className="w-full max-w-sm space-y-5">
          <div className="flex flex-col items-center gap-2 text-center">
            <Upload className="h-8 w-8 text-primary" aria-hidden="true" />
            <h1 className="text-xl font-semibold text-foreground">
              {m.onboarding_backup_title()}
            </h1>
            <p className="text-sm text-muted-foreground">
              {m.onboarding_backup_body()}
            </p>
          </div>
          <div
            className="space-y-2"
            role="group"
            aria-label={m.onboarding_backup_title()}
          >
            {Object.entries(backupPreview.categories)
              .filter(([category]) => category !== "database")
              .map(([category, count]) => (
                <label
                  key={category}
                  className="flex items-center gap-3 rounded-md border border-border p-3 text-sm"
                >
                  <input
                    type="checkbox"
                    checked={backupSelection.includes(category)}
                    onChange={(event) =>
                      setBackupSelection((current) =>
                        event.target.checked
                          ? [...current, category]
                          : current.filter((item) => item !== category),
                      )
                    }
                  />
                  <Database
                    className="h-4 w-4 text-muted-foreground"
                    aria-hidden="true"
                  />
                  <span className="flex-1">
                    {labels[category]?.() ?? category}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {m.onboarding_backup_count({ n: count })}
                  </span>
                </label>
              ))}
          </div>
          {backupError && (
            <p role="alert" className="text-xs text-destructive">
              {m.onboarding_backup_error()}
            </p>
          )}
          {backupImported && (
            <div role="status" className="space-y-1 text-xs text-green-600">
              <p>{m.onboarding_backup_success()}</p>
              {Object.entries(backupResult?.results ?? {}).map(
                ([category, item]) => (
                  <p key={category}>
                    {category}: {item.status} ({item.count})
                  </p>
                ),
              )}
            </div>
          )}
          <div className="flex justify-between gap-2">
            <button
              type="button"
              className="text-xs text-muted-foreground hover:text-foreground"
              onClick={() => {
                setStep("continuation");
                setContinuationIndex(0);
              }}
              disabled={backupBusy}
            >
              {m.onboarding_backup_skip()}
            </button>
            <button
              type="button"
              className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-60"
              onClick={() => void handleBackupRestore()}
              disabled={backupBusy || backupImported}
            >
              {m.onboarding_backup_import()}
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-full flex items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm space-y-6">
        <div className="flex flex-col items-center gap-2">
          <div className="flex items-center gap-2.5">
            <img
              src="/vectora.svg"
              alt={m.onboarding_brand_name()}
              width={36}
              height={36}
            />
            <h1
              className="text-2xl font-semibold tracking-tight text-foreground"
              style={{ fontFamily: "var(--font-aeonik-mono)" }}
            >
              {m.onboarding_brand_name()}
            </h1>
          </div>
        </div>

        {step === "identity" && (
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground text-center">
              {m.onboarding_pre_identity_title()}
            </p>
            <div className="space-y-1">
              <label
                className="text-sm font-medium text-foreground"
                htmlFor="pre-name"
              >
                {m.onboarding_pre_name_label()}
              </label>
              <input
                id="pre-name"
                type="text"
                autoComplete="name"
                autoFocus
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={m.onboarding_pre_name_ph()}
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/60"
              />
              {nameError && (
                <p className="text-xs text-destructive">{nameError}</p>
              )}
            </div>
            <div className="space-y-1">
              <label
                className="text-sm font-medium text-foreground"
                htmlFor="pre-username"
              >
                {m.auth_signup_username()}
              </label>
              <div className="flex items-center rounded-md border border-border bg-background px-3 focus-within:ring-2 focus-within:ring-primary/60">
                <span className="text-sm text-muted-foreground select-none">
                  @
                </span>
                <input
                  id="pre-username"
                  type="text"
                  autoComplete="off"
                  value={username}
                  onChange={(e) => {
                    setUsernameEdited(true);
                    setUsername(e.target.value);
                  }}
                  placeholder={m.auth_signup_username_ph()}
                  className="w-full bg-transparent py-2 pl-1 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none"
                />
              </div>
              {usernameError && (
                <p className="text-xs text-destructive">{usernameError}</p>
              )}
              {checkingUsername ? (
                <p className="text-xs text-muted-foreground">
                  {m.auth_signup_username_checking()}
                </p>
              ) : usernameStatus && username.trim() ? (
                usernameStatus.available ? (
                  <p className="flex items-center gap-1 text-xs text-green-600 dark:text-green-400">
                    <CheckCircle2 className="w-3.5 h-3.5 shrink-0" />
                    {m.auth_signup_username_available()}
                  </p>
                ) : (
                  <p className="flex items-center gap-1 text-xs text-destructive">
                    <XCircle className="w-3.5 h-3.5 shrink-0" />
                    {m.auth_signup_username_taken()}{" "}
                    <button
                      type="button"
                      onClick={() => {
                        setUsernameEdited(true);
                        setUsername(usernameStatus.suggestion);
                      }}
                      className="text-primary hover:underline"
                    >
                      {m.auth_signup_username_use_suggestion({
                        suggestion: usernameStatus.suggestion,
                      })}
                    </button>
                  </p>
                )
              ) : null}
            </div>
            <div className="space-y-1">
              <label
                className="text-sm font-medium text-foreground"
                htmlFor="pre-company"
              >
                {m.onboarding_pre_company_label()}
              </label>
              <input
                id="pre-company"
                type="text"
                autoComplete="organization"
                value={company}
                onChange={(e) => setCompany(e.target.value)}
                placeholder={m.onboarding_pre_company_ph()}
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/60"
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <label className="text-sm font-medium text-foreground">
                  {m.onboarding_pre_language_label()}
                </label>
                <Select
                  value={language}
                  onValueChange={(v) => setLanguage(v as Lang)}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue>
                      {SUPPORTED_LANGS.find((l) => l.value === language)
                        ?.label ?? language}
                    </SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    {SUPPORTED_LANGS.map(({ value, label }) => (
                      <SelectItem key={value} value={value}>
                        {label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <label className="text-sm font-medium text-foreground">
                  {m.onboarding_pre_theme_label()}
                </label>
                <div className="flex rounded-md border border-border overflow-hidden">
                  {THEME_OPTIONS.map(({ value, label, icon: Icon }) => (
                    <button
                      key={value}
                      type="button"
                      onClick={() => setTheme(value)}
                      title={label()}
                      aria-label={label()}
                      aria-pressed={theme === value}
                      className={`flex-1 flex items-center justify-center py-2 transition-colors ${
                        theme === value
                          ? "bg-primary/15 text-primary"
                          : "text-muted-foreground hover:bg-muted/50 hover:text-foreground"
                      }`}
                    >
                      <Icon className="w-4 h-4" />
                    </button>
                  ))}
                </div>
              </div>
            </div>
            <button
              type="button"
              onClick={handleIdentityNext}
              className="w-full rounded-md bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
            >
              {m.onboarding_next()}
            </button>
          </div>
        )}

        {step === "mode" && (
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground text-center">
              {m.onboarding_pre_mode_body()}
            </p>
            <div className="grid grid-cols-1 gap-2">
              <button
                type="button"
                onClick={() => void handleSelectLocal()}
                disabled={settingUpLocal}
                className="flex items-start gap-3 text-left p-3 rounded-md border border-border hover:bg-muted/50 transition-colors disabled:opacity-60"
              >
                {settingUpLocal ? (
                  <Loader2 className="w-4 h-4 mt-0.5 shrink-0 animate-spin text-muted-foreground" />
                ) : (
                  <Monitor className="w-4 h-4 mt-0.5 shrink-0 text-muted-foreground" />
                )}
                <span>
                  <span className="block text-sm font-medium text-foreground">
                    {m.onboarding_pre_mode_local_title()}
                  </span>
                  <span className="block text-xs text-muted-foreground mt-0.5">
                    {m.onboarding_pre_mode_local_desc()}
                  </span>
                </span>
              </button>
              <button
                type="button"
                onClick={() => setStep("vps-token")}
                className="flex items-start gap-3 text-left p-3 rounded-md border border-border hover:bg-muted/50 transition-colors"
              >
                <Server className="w-4 h-4 mt-0.5 shrink-0 text-muted-foreground" />
                <span>
                  <span className="block text-sm font-medium text-foreground">
                    {m.onboarding_pre_mode_vps_title()}
                  </span>
                  <span className="block text-xs text-muted-foreground mt-0.5">
                    {m.onboarding_pre_mode_vps_desc()}
                  </span>
                </span>
              </button>
            </div>
            {nameError && (
              <p className="text-xs text-destructive text-center">
                {nameError}
              </p>
            )}
            <button
              type="button"
              onClick={() => setStep("identity")}
              className="w-full text-xs text-muted-foreground hover:text-foreground transition-colors"
            >
              {m.onboarding_back()}
            </button>
          </div>
        )}

        {step === "vps-token" && (
          <div className="space-y-4">
            <p className="text-sm font-medium text-foreground text-center">
              {m.onboarding_pre_vps_title()}
            </p>
            <p className="text-sm text-muted-foreground">
              {m.onboarding_pre_vps_body()}
            </p>
            <div className="rounded-md border border-border/60 p-3 space-y-1.5">
              <p className="text-xs font-medium text-muted-foreground">
                {m.onboarding_pre_vps_features_title()}
              </p>
              <ul className="space-y-1">
                {VPS_FEATURE_LABELS.map((label, idx) => (
                  <li
                    key={idx}
                    className="flex items-center gap-1.5 text-xs text-foreground"
                  >
                    <Check className="w-3.5 h-3.5 shrink-0 text-primary" />
                    {label()}
                  </li>
                ))}
              </ul>
            </div>
            <div className="space-y-1">
              <label
                className="text-sm font-medium text-foreground"
                htmlFor="pre-vps-token"
              >
                {m.onboarding_pre_vps_token_label()}
              </label>
              <input
                id="pre-vps-token"
                type="password"
                autoComplete="off"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder={m.onboarding_pre_vps_token_ph()}
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm font-mono text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/60"
              />
              {tokenError && (
                <p className="text-xs text-destructive">{tokenError}</p>
              )}
            </div>
            <button
              type="button"
              onClick={() => void handleValidateVpsToken()}
              disabled={validating || !token.trim()}
              className="w-full rounded-md bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-60 transition-colors flex items-center justify-center gap-1.5"
            >
              {validating && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
              {validating
                ? m.onboarding_pre_vps_validating()
                : m.onboarding_pre_vps_continue()}
            </button>
            <p className="text-center text-xs text-muted-foreground">
              {m.onboarding_pre_vps_subscribe_hint()}{" "}
              <button
                type="button"
                onClick={() => openExternal("https://vectora.company/")}
                className="text-primary hover:underline"
              >
                {m.onboarding_pre_vps_subscribe_cta()}
              </button>
            </p>
            <button
              type="button"
              onClick={() => setStep("mode")}
              className="w-full text-xs text-muted-foreground hover:text-foreground transition-colors"
            >
              {m.onboarding_back()}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
