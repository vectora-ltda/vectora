"use client";

/**
 * ModelSelector
 *
 * Chip de modelo — vive ao lado do `WorkspaceSelector` no rodapé do
 * composer. Reaproveita o MESMO padrão visual do seletor de workspace
 * (botão compacto + dropdown em popover com lista rolável, item ativo
 * marcado com `Check`, descrição secundária abaixo do nome) — o Vectora
 * não deve ter dois "estilos" de seletor coexistindo (ver
 * `workspace-selector.tsx`).
 */

import { useEffect, useState } from "react";
import { Check, ChevronDown } from "lucide-react";

import { ProviderIcon } from "@/components/icons/provider-icons";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  getAllowedModels,
  getModelDisplayName,
  getModelProvider,
  type ModelOption,
} from "@/lib/config/deployment-config";
import { m } from "@/lib/paraglide/messages";

interface ModelSelectorProps {
  value: string;
  onChange: (model: string) => void;
  compact?: boolean;
  // Code mode (workspace + workbenches, ALL_TOOLS) sempre usa tools — modelos
  // que rejeitam replay de tool_calls no histórico (ver
  // TOOL_CALLING_INCOMPATIBLE_MODELS no backend) somem da lista. Chat mode
  // não filtra: decisão de produto, o risco é menor lá.
  codeMode?: boolean;
}

interface DynamicModel {
  id: string;
  label: string;
  available?: boolean;
}

export function ModelSelector({
  value,
  onChange,
  compact = false,
  codeMode = false,
}: ModelSelectorProps) {
  const allowedModels = getAllowedModels();
  const [open, setOpen] = useState(false);
  // Providers com API key configurada (backend). null = ainda não carregado →
  // mostra todos; lista vazia/erro também cai no fallback de mostrar todos para
  // nunca travar o usuário sem opções.
  const [configuredProviders, setConfiguredProviders] = useState<
    string[] | null
  >(null);
  // Modelos registrados via gateway (Ollama local, hoje) — nunca fazem parte
  // do catálogo estático de deployment-config.ts, só existem em runtime.
  const [dynamicModels, setDynamicModels] = useState<DynamicModel[]>([]);
  const [toolIncompatibleModels, setToolIncompatibleModels] = useState<
    string[]
  >([]);

  useEffect(() => {
    if (typeof fetch === "undefined") return;
    let alive = true;

    const loadProviders = async () => {
      try {
        const response = await fetch("/models/providers");
        if (!response.ok) return;
        const data: {
          providers?: string[];
          dynamic_models?: DynamicModel[];
          tool_incompatible_models?: string[];
        } = await response.json();
        if (!alive) return;
        if (Array.isArray(data.providers))
          setConfiguredProviders(data.providers);
        if (Array.isArray(data.dynamic_models))
          setDynamicModels(data.dynamic_models);
        if (Array.isArray(data.tool_incompatible_models))
          setToolIncompatibleModels(data.tool_incompatible_models);
      } catch {
        /* fallback silencioso: mantém a lista estática */
      }
    };

    void loadProviders();
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (!open || typeof fetch === "undefined") return;
    let alive = true;

    const refreshProviders = async () => {
      try {
        const response = await fetch("/models/providers");
        if (!response.ok) return;
        const data: {
          providers?: string[];
          dynamic_models?: DynamicModel[];
          tool_incompatible_models?: string[];
        } = await response.json();
        if (!alive) return;
        if (Array.isArray(data.providers))
          setConfiguredProviders(data.providers);
        if (Array.isArray(data.dynamic_models))
          setDynamicModels(data.dynamic_models);
        if (Array.isArray(data.tool_incompatible_models))
          setToolIncompatibleModels(data.tool_incompatible_models);
      } catch {
        /* fallback silencioso: mantém a lista atual */
      }
    };

    void refreshProviders();
    return () => {
      alive = false;
    };
  }, [open]);

  // Esconde modelos cujo provider não tem credencial. Mantém sempre o modelo
  // ativo visível (mesmo sem key) para não sumir com a seleção atual.
  // Modelos dinâmicos (Ollama) não passam por esse filtro — não exigem key,
  // só existem na lista se o usuário já os registrou explicitamente.
  const visibleModels: string[] = [
    ...(configuredProviders && configuredProviders.length > 0
      ? allowedModels.filter(
          (model) =>
            model === value ||
            configuredProviders.includes(getModelProvider(model)),
        )
      : allowedModels),
    ...dynamicModels
      .filter((dm) => dm.available !== false || dm.id === value)
      .map((dm) => dm.id),
  ].filter(
    (model) =>
      model === value || !codeMode || !toolIncompatibleModels.includes(model),
  );

  // Troca sozinho pro primeiro modelo compatível quando entra em code mode
  // com um modelo incompatível já selecionado (herdado de sessão anterior) —
  // nunca deixa o usuário preso num modelo que vai falhar no próximo tool
  // call. Não roda em chat mode (o valor atual permanece na lista ali).
  useEffect(() => {
    if (!codeMode || !toolIncompatibleModels.includes(value)) return;
    const fallback = visibleModels.find((model) => model !== value);
    if (fallback) onChange(fallback);
  }, [codeMode, toolIncompatibleModels, value, visibleModels, onChange]);

  const activeLabel = getModelDisplayName(value as ModelOption) || value;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          className={
            compact
              ? "flex h-7 w-max min-w-0 max-w-full shrink items-center gap-1 overflow-hidden rounded-md px-1.5 text-xs leading-4 font-medium text-muted-foreground hover:bg-muted/50 hover:text-foreground transition-colors select-none"
              : "flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs leading-4 font-medium text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors select-none"
          }
          title={m.model_select_title()}
          aria-label={`${m.model_select_title()}: ${activeLabel}`}
        >
          <ProviderIcon
            provider={getModelProvider(value as ModelOption)}
            className={`shrink-0 text-muted-foreground ${compact ? "w-3.5 h-3.5" : "w-4 h-4"}`}
          />
          <span
            className={`${compact ? "inline" : "inline"} min-w-0 truncate font-medium`}
          >
            {activeLabel}
          </span>
          {!compact && (
            <ChevronDown className="hidden h-3.5 w-3.5 shrink-0 text-muted-foreground @sm/composer:block [&_svg]:opacity-70" />
          )}
        </button>
      </PopoverTrigger>

      {/* Portal (via PopoverContent) — escapa de qualquer ancestral com
          overflow/stacking context próprio (composer, split-pane do
          workbench). Sem isso o dropdown ficava cortado/atrás da sidebar
          de ícones do workbench quando o chat renderizava num painel
          estreito. */}
      <PopoverContent
        align="start"
        side={compact ? "top" : "bottom"}
        sideOffset={6}
        className="z-50 w-72 rounded-lg border border-border bg-background shadow-xl p-0 py-1"
      >
        <div className="px-3 py-2 text-xs font-medium text-muted-foreground uppercase tracking-wide">
          {m.model_select_title()}
        </div>

        <div className="max-h-72 overflow-y-auto">
          {visibleModels.map((model) => (
            <button
              key={model}
              className="w-full flex items-center gap-2 px-3 py-2 text-sm hover:bg-accent text-left transition-colors"
              onClick={() => {
                onChange(model);
                setOpen(false);
              }}
            >
              {model === value ? (
                <Check className="w-4 h-4 shrink-0 text-primary" />
              ) : (
                <ProviderIcon
                  provider={getModelProvider(model)}
                  className="w-4 h-4 shrink-0 text-muted-foreground"
                />
              )}
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium text-foreground">
                  {getModelDisplayName(model)}
                </span>
              </span>
            </button>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
}
