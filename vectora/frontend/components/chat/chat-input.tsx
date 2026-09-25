/**
 * Chat Input Component
 *
 * Fixed input area at the bottom of the chat interface.
 * Includes file upload, drag & drop, and paste support.
 */

import {
  Children,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Send, TriangleAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/components/ui/tooltip";
import { FilePreviewGrid } from "./features/file-preview-grid";
import { UrlPreviewCard } from "./features/url-preview-card";
import { VoiceInputButton } from "./features/voice-input-button";
import { PermissionModeMenu } from "./features/permission-mode-menu";
import { EffortMenu } from "./features/effort-menu";
import { PlusMenu } from "./features/plus-menu";
import { UsagePopover } from "./features/usage-popover";
import { SlashCommandMenu } from "./features/slash-command-menu";
import { AtMentionMenu } from "./features/at-mention-menu";
import { ModelSelector } from "./model-selector";
import { VscodeIcon } from "@/components/icons/vscode-icon";
import { useWorkspacesStore } from "@/lib/stores/workspaces-store";
import { useSettingsStore } from "@/lib/stores/settings-store";
import type { SlashCommand } from "@/lib/constants/slash-commands";
import type { AgentConfig } from "@/components/layout/agent-settings";
import type { ImageAttachment } from "@/lib/types";
import { useNetworkStatus } from "@/lib/hooks/use-network-status";
import { useElementWidth } from "@/lib/hooks/use-element-width";
import { useToastStore } from "@/lib/stores/toast-store";
import {
  getModelProvider,
  isProviderVisionCapable,
  type ModelOption,
} from "@/lib/config/deployment-config";
import { checkOpenRouterModelSupportsImage } from "@/lib/api/openrouter-vision";
import { m } from "@/lib/paraglide/messages";
import { classifySmartPaste } from "@/lib/utils/chat/smart-paste";
import { balanceCompactControlWidths } from "@/lib/layout/compact-control-layout";

interface VscodeOption {
  strategy: string;
  label: string;
  url: string;
}

/**
 * Botão "Abrir no VS Code" — lança o VS Code direto, sem popup intermediário.
 * Prefere a estratégia "local" (vscode://file/...) e cai para a primeira
 * opção disponível (ssh/devcontainer) quando o workspace não é local.
 */
function VscodeMenu({
  workspaceId,
  compact = false,
}: {
  workspaceId: string;
  compact?: boolean;
}) {
  const handleLaunch = useCallback(async () => {
    if (!workspaceId) return;
    const res = await fetch(
      `/workspaces/${encodeURIComponent(workspaceId)}/vscode-options`,
    );
    const options: VscodeOption[] = res.ok
      ? ((await res.json()).options ?? [])
      : [];
    const opt =
      options.find((o) => o.strategy === "local") ?? options[0] ?? null;
    if (!opt) {
      useToastStore.getState().error(m.workbench_open_vscode_unavailable());
      return;
    }
    window.location.href = opt.url;
  }, [workspaceId]);

  return (
    <button
      onClick={handleLaunch}
      className={`shrink-0 rounded-md text-muted-foreground hover:bg-muted/50 hover:text-foreground ${compact ? "h-6 w-auto p-0" : "p-1.5"}`}
      title={m.workbench_open_vscode()}
      aria-label={m.workbench_open_vscode()}
    >
      <VscodeIcon className={compact ? "size-3" : "size-4"} />
    </button>
  );
}

interface ChatInputProps {
  input: string;
  onInputChange: (value: string) => void;
  onSend: () => void;
  onKeyDown: (e: React.KeyboardEvent<HTMLTextAreaElement>) => void;
  isLoading: boolean;
  isStopping: boolean;
  onStop: () => void;
  userId?: string | null;

  // File upload
  attachedFiles: ImageAttachment[];
  uploadError: string | null;
  inputError: string | null;
  isDragging: boolean;
  onDragOver: (e: React.DragEvent) => void;
  onDragLeave: (e: React.DragEvent) => void;
  onDrop: (e: React.DragEvent) => void;
  onPaste: (e: React.ClipboardEvent<HTMLTextAreaElement>) => void;
  onRemoveFile: (fileId: string) => void;
  onFileButtonClick: (e: React.MouseEvent) => void;
  onCaptureScreenshot?: () => void;
  fileInputRef: React.RefObject<HTMLInputElement | null>;
  onFileSelect: (e: React.ChangeEvent<HTMLInputElement>) => void;
  textareaRef?: React.RefObject<HTMLTextAreaElement | null>;

  // Voice input
  isVoiceListening?: boolean;
  isVoiceSupported?: boolean;
  onVoiceToggle?: () => void;
  voiceError?: string | null;

  // Queued messages
  queuedMessages?: { content: string; id: string }[];

  // Context meter
  tokensUsed?: number;
  modelId?: string;

  // Modelo (seletor permanente no rodapé)
  agentConfig?: AgentConfig;
  onAgentConfigChange?: (config: AgentConfig) => void;

  // Overlay de drop zone mais explícito no estado vazio
  dropHintExpanded?: boolean;
  /** Callback de seleção de arquivo via @mention. */
  onAtMentionSelect?: (path: string, startIdx: number, endIdx: number) => void;
  /** IDE sidebar: usa bg-sidebar em vez de bg-background. */
  compact?: boolean;
  structuredPaste?: {
    content: string;
    extension: string;
    mimeType: string;
  } | null;
  onStructuredPasteAttach?: () => void;
  onStructuredPasteText?: () => void;
  onStructuredPasteCancel?: () => void;
}

const EMPTY_QUEUED_MESSAGES: NonNullable<ChatInputProps["queuedMessages"]> = [];

// O breakpoint @md dos container queries do Tailwind usa 28rem (448px),
// ficando ligeiramente acima do alvo de 420px pedido para o composer.
const COMPOSER_COMPACT_BREAKPOINT_REM = 28;

function remToCssPixels(rem: number): number {
  if (typeof document === "undefined") return rem * 16;
  const rootFontSize = Number.parseFloat(
    getComputedStyle(document.documentElement).fontSize,
  );
  return rem * (Number.isFinite(rootFontSize) ? rootFontSize : 16);
}

export function isComposerCompactWidth(
  width: number,
  rootFontSize: number,
): boolean {
  return width > 0 && width < COMPOSER_COMPACT_BREAKPOINT_REM * rootFontSize;
}

function ControlGroup({
  children,
  compact,
}: {
  children: ReactNode;
  compact: boolean;
}) {
  const groupRef = useRef<HTMLDivElement>(null);
  const itemRefs = useRef<Array<HTMLDivElement | null>>([]);

  const measureAndBalance = useCallback(() => {
    const group = groupRef.current;
    const items = itemRefs.current.filter(
      (item): item is HTMLDivElement => item !== null,
    );
    if (!group || items.length === 0 || group.clientWidth <= 0) return;

    // Os controles podem conter um botão aninhado com `max-w-full`/`w-fit`
    // (principalmente o seletor de modelo). Remover essas restrições também
    // durante a medição evita registrar um modelo já truncado como natural.
    const measurableElements = items.flatMap((item) => [
      item,
      ...Array.from(item.querySelectorAll<HTMLElement>("button, div, span")),
    ]);
    const previousStyles = measurableElements.map(
      (element) => element.style.cssText,
    );
    measurableElements.forEach((element) => {
      element.style.flex = "0 0 auto";
      element.style.width = "max-content";
      element.style.maxWidth = "none";
      element.style.overflow = "visible";
    });
    const naturalWidths = items.map((item) =>
      Math.ceil(item.getBoundingClientRect().width),
    );
    const computedGap = compact
      ? 0
      : Number.parseFloat(getComputedStyle(group).columnGap || "0") || 0;
    const availableWidth = Math.max(
      0,
      group.clientWidth - computedGap * Math.max(0, items.length - 1),
    );
    measurableElements.forEach((element, index) => {
      element.style.cssText = previousStyles[index] ?? "";
    });

    const balancedWidths = balanceCompactControlWidths(
      naturalWidths,
      availableWidth,
    );
    items.forEach((item, index) => {
      item.style.width = `${balancedWidths[index]}px`;
    });
  }, [compact]);

  useLayoutEffect(() => {
    measureAndBalance();
    const group = groupRef.current;
    if (!group || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measureAndBalance);
    observer.observe(group);
    const mutationObserver =
      typeof MutationObserver === "undefined"
        ? null
        : new MutationObserver(measureAndBalance);
    mutationObserver?.observe(group, {
      subtree: true,
      childList: true,
      characterData: true,
    });
    return () => {
      observer.disconnect();
      mutationObserver?.disconnect();
    };
  }, [measureAndBalance]);

  return (
    <div
      ref={groupRef}
      data-testid={compact ? "compact-control-group" : "wide-control-group"}
      className={`flex min-w-0 flex-1 items-center overflow-hidden ${compact ? "justify-between" : "gap-2"}`}
    >
      {Children.toArray(children).map((child, index) => (
        <div
          key={index}
          ref={(item) => {
            itemRefs.current[index] = item;
          }}
          className="min-w-0 shrink-0 overflow-hidden"
        >
          {child}
        </div>
      ))}
    </div>
  );
}

/**
 * Chat input area with file upload support.
 * Displays at the bottom of the chat interface when there are existing messages.
 */
export function ChatInput({
  input,
  onInputChange,
  onSend,
  onKeyDown,
  isLoading,
  isStopping,
  onStop,
  userId,
  attachedFiles,
  uploadError,
  inputError,
  isDragging,
  onDragOver,
  onDragLeave,
  onDrop,
  onPaste,
  onRemoveFile,
  onFileButtonClick,
  onCaptureScreenshot,
  fileInputRef,
  onFileSelect,
  textareaRef,
  isVoiceListening,
  isVoiceSupported,
  onVoiceToggle,
  voiceError,
  queuedMessages = EMPTY_QUEUED_MESSAGES,
  tokensUsed,
  modelId,
  agentConfig,
  onAgentConfigChange,
  dropHintExpanded = false,
  onAtMentionSelect,
  compact = false,
  structuredPaste,
  onStructuredPasteAttach,
  onStructuredPasteText,
  onStructuredPasteCancel,
}: ChatInputProps) {
  const [composerRef, composerWidth] = useElementWidth<HTMLDivElement>();
  const compactByContainer = isComposerCompactWidth(
    composerWidth,
    remToCssPixels(1),
  );
  const compactMode = compact || compactByContainer;
  const wsId = useWorkspacesStore((s) => s.getActive())?.id ?? "";
  const chatMode = useSettingsStore((s) => s.chatMode);
  // Sem rede não há para onde enviar; desabilita entrada e ações
  // que dependem do backend (anexos, voz) em vez de deixar o usuário digitar
  // para uma falha certa.
  const { offline } = useNetworkStatus();
  const handleModelChange = (model: string) => {
    if (agentConfig && onAgentConfigChange) {
      onAgentConfigChange({ ...agentConfig, model });
    }
  };

  const hasImage = attachedFiles.some((f) => f.mimeType.startsWith("image/"));
  const provider = agentConfig?.model
    ? getModelProvider(agentConfig.model as ModelOption)
    : undefined;

  // OpenRouter serve modelos de vários fornecedores com capability variável
  // por modelo — consulta o catálogo (cacheado no backend) em vez de tratar
  // o provedor inteiro como sem suporte a imagem.
  const [openRouterSupportsImage, setOpenRouterSupportsImage] = useState(true);
  const [dismissedPreviewUrl, setDismissedPreviewUrl] = useState<string | null>(
    null,
  );
  const previousPreviewUrl = useRef<string | null>(null);
  const previewUrl = useMemo(() => {
    const value = input.trim();
    return classifySmartPaste(value).kind === "url" ? value : null;
  }, [input]);
  useEffect(() => {
    if (previousPreviewUrl.current === previewUrl) return;
    previousPreviewUrl.current = previewUrl;
    // A changed URL should always be eligible for a fresh preview.
    // oxlint-disable-next-line react/set-state-in-effect
    setDismissedPreviewUrl(null);
  }, [previewUrl]);
  useEffect(() => {
    if (provider !== "openrouter" || !hasImage || !agentConfig?.model) {
      // Consulta o catálogo de modelos OpenRouter (I/O de rede, cacheado no
      // backend) — não é estado derivado de prop.
      // oxlint-disable-next-line react/set-state-in-effect
      setOpenRouterSupportsImage(true);
      return;
    }
    let cancelled = false;
    const openRouterModelId = agentConfig.model.slice(
      agentConfig.model.indexOf(":") + 1,
    );
    checkOpenRouterModelSupportsImage(openRouterModelId).then((supported) => {
      if (!cancelled) setOpenRouterSupportsImage(supported);
    });
    return () => {
      cancelled = true;
    };
  }, [provider, hasImage, agentConfig?.model]);

  // Cohere (e Ollama) não aceitam imagem na mensagem; avisa
  // cedo em vez de deixar o envio estourar com o erro cru do provedor.
  const hasImageWithoutVisionSupport = useMemo(() => {
    if (!hasImage || !provider) return false;
    if (provider === "openrouter") return !openRouterSupportsImage;
    return !isProviderVisionCapable(provider);
  }, [hasImage, provider, openRouterSupportsImage]);

  // Auto-grow do textarea: ajusta a altura ao conteúdo até o teto de 240px;
  // depois disso o próprio textarea passa a scrollar verticalmente. Mesmo no
  // modo compacto, o texto quebra linha e faz o composer crescer antes do
  // limite, em vez de virar uma caixa horizontal com scroll.
  // Recalcula quando o texto ou o modo do composer muda (digitação, @mention,
  // limpeza após envio ou uma troca de largura que ativa o modo compacto).
  // O cálculo usa o `scrollHeight` real do DOM, já refletindo o valor atual.
  useLayoutEffect(() => {
    const el = textareaRef?.current;
    if (!el) return;
    // Mutação imperativa do DOM via ref encaminhado (padrão do próprio
    // React para refs) — não é o objeto prop sendo reatribuído, só o nó
    // DOM que ele aponta.
    // oxlint-disable-next-line react/immutability
    el.style.height = "auto";
    // Keep the composer free of a scrollbar while it is growing. The
    // vertical scrollbar only becomes useful after the 240px ceiling.
    el.style.overflowY = "hidden";
    el.style.overflowX = "hidden";
    const next = Math.max(38, Math.min(240, el.scrollHeight));
    // oxlint-disable-next-line react/immutability
    el.style.height = `${next}px`;
    // oxlint-disable-next-line react/immutability
    el.style.overflowY = next >= 240 ? "auto" : "hidden";
  }, [compactMode, input]);
  return (
    <div className="relative">
      {/* Enhanced visibility layer */}
      <div className="absolute inset-0 pointer-events-none" />

      <div
        className={`relative z-[50] backdrop-blur-sm ${compactMode ? "bg-sidebar" : "bg-background"}`}
      >
        {/* @container/composer: o rodapé de controles reage à largura do
            PRÓPRIO composer, não da viewport. No modo IDE o ChatInput vive numa
            sidebar de chat estreita enquanto a janela segue larga — breakpoints
            de viewport (sm:) nunca disparavam ali e os controles transbordavam.
            Container queries resolvem chat largo e IDE estreito com uma regra. */}
        <div
          ref={composerRef}
          className="@container/composer w-full max-w-4xl min-w-0 mx-auto"
        >
          {structuredPaste && (
            <div
              className="mb-2 rounded-lg border border-border bg-muted/40 p-3"
              role="dialog"
              aria-label={m.chat_structured_paste_title()}
            >
              <p className="text-sm font-medium text-foreground">
                {m.chat_structured_paste_title()}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                {m.chat_structured_paste_description()}
              </p>
              <div className="mt-3 flex flex-wrap gap-2">
                <Button
                  type="button"
                  size="sm"
                  onClick={onStructuredPasteAttach}
                >
                  {m.chat_structured_paste_attach()}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={onStructuredPasteText}
                >
                  {m.chat_structured_paste_text()}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={onStructuredPasteCancel}
                >
                  {m.chat_structured_paste_cancel()}
                </Button>
              </div>
            </div>
          )}
          {previewUrl && previewUrl !== dismissedPreviewUrl && (
            <UrlPreviewCard
              url={previewUrl}
              onDismiss={() => setDismissedPreviewUrl(previewUrl)}
            />
          )}
          {/* File Previews */}
          <FilePreviewGrid files={attachedFiles} onRemove={onRemoveFile} />

          {/* Upload Error */}
          {uploadError && (
            <div className="mb-2 text-sm text-destructive bg-destructive/10 px-3 py-2 rounded-md">
              {uploadError}
            </div>
          )}

          {/* Voice Error */}
          {voiceError && (
            <div className="mb-2 text-sm text-destructive bg-destructive/10 px-3 py-2 rounded-md">
              {voiceError}
            </div>
          )}

          {/* Aviso: modelo atual não processa a imagem anexada */}
          {hasImageWithoutVisionSupport && (
            <div className="mb-2 flex items-center gap-2 text-xs text-amber-600 bg-amber-500/10 px-3 py-2 rounded-md">
              <TriangleAlert className="w-3.5 h-3.5 shrink-0" />
              {m.chat_input_no_vision_warning()}
            </div>
          )}

          {/* Queued Messages */}
          {queuedMessages.length > 0 && (
            <div className="mb-2 space-y-1.5">
              {queuedMessages.map((msg) => (
                <div
                  key={msg.id}
                  className="flex items-center gap-2 px-3 py-2 bg-muted/50 border border-border/50 rounded-lg text-sm"
                >
                  <div className="flex items-center gap-1.5 text-muted-foreground flex-shrink-0">
                    <svg
                      className="w-3 h-3 animate-pulse"
                      xmlns="http://www.w3.org/2000/svg"
                      viewBox="0 0 24 24"
                      fill="currentColor"
                    >
                      <circle cx="12" cy="12" r="10" />
                    </svg>
                    <span className="text-xs font-medium">
                      {m.input_queued()}
                    </span>
                  </div>
                  <span className="text-foreground/80 truncate">
                    {msg.content}
                  </span>
                </div>
              ))}
            </div>
          )}

          <div className="relative group">
            {/* Autocomplete de @arquivo — aparece antes do slash para não conflitar */}
            {onAtMentionSelect && (
              <AtMentionMenu input={input} onSelect={onAtMentionSelect} />
            )}

            {/* Autocomplete de slash commands */}
            <SlashCommandMenu
              input={input}
              onSelect={(cmd: SlashCommand) => {
                onInputChange(cmd.takesArg ? `/${cmd.name} ` : `/${cmd.name}`);
                textareaRef?.current?.focus();
              }}
            />

            {/* Input container — mantém o fundo e o comportamento de drop; o
                contorno visual pertence ao próprio textarea. */}
            <div className="relative">
              <div
                className={`relative transition-colors duration-200 ${compactMode ? "bg-sidebar" : "bg-background"}`}
                onDragOver={onDragOver}
                onDragLeave={onDragLeave}
                onDrop={onDrop}
              >
                {isDragging && (
                  <div
                    className={
                      dropHintExpanded
                        ? "absolute inset-0 bg-primary/15 rounded-xl flex items-center justify-center z-20 pointer-events-none border-2 border-dashed border-primary"
                        : "absolute inset-0 bg-primary/10 rounded-xl flex items-center justify-center z-20 pointer-events-none"
                    }
                  >
                    <div className="text-primary font-medium">
                      {m.welcome_drop_files()}
                    </div>
                  </div>
                )}
                <div className="flex items-center gap-2 px-2 py-1.5">
                  {/* Hidden File Input */}
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept="image/*,audio/*,.py,.js,.ts,.tsx,.jsx,.java,.cpp,.c,.h,.cs,.go,.rs,.rb,.php,.sh,.bash,.yaml,.yml,.json,.xml,.html,.css,.md,.txt,.log,.sql,.graphql,.r,.swift,.kt,.scala,.har,.mp3,.wav,.m4a,.ogg,.webm"
                    multiple
                    onChange={onFileSelect}
                    className="hidden"
                  />

                  <Textarea
                    ref={textareaRef}
                    data-testid="chat-input"
                    value={input}
                    onChange={(e) => onInputChange(e.target.value)}
                    onKeyDown={onKeyDown}
                    onPaste={onPaste}
                    placeholder={
                      !userId
                        ? m.input_initializing()
                        : offline
                          ? m.network_disabled_offline()
                          : isLoading
                            ? m.input_loading_placeholder()
                            : m.input_placeholder()
                    }
                    title={offline ? m.network_disabled_offline() : undefined}
                    className="relative z-10 min-h-[38px] max-h-60 min-w-0 flex-1 basis-0 resize-none [field-sizing:fixed] overflow-x-hidden overflow-y-hidden whitespace-pre-wrap break-words [overflow-wrap:anywhere] custom-scrollbar rounded-md border border-[#2a2a2a]/60 bg-[#252525]/30 w-full px-3 py-2 text-sm leading-relaxed text-foreground placeholder:text-muted-foreground shadow-sm focus:outline-none focus-visible:outline-none focus-visible:border-[#2a2a2a]/60 focus:ring-1 focus:ring-primary/50 focus-visible:ring-1 focus-visible:ring-primary/50 focus-visible:ring-offset-0 transition-[height] duration-150"
                    disabled={!userId || offline}
                    rows={1}
                    wrap="soft"
                  />

                  {!isLoading && (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button
                          onClick={onSend}
                          data-testid="chat-send"
                          variant="ghost"
                          size="sm"
                          disabled={!input.trim() || !userId || offline}
                          className="h-[38px] w-[38px] p-0 shrink-0 rounded-full bg-muted/40 text-primary hover:bg-muted disabled:opacity-40 disabled:bg-muted disabled:text-muted-foreground"
                          type="button"
                          aria-label={m.tooltip_chat_send()}
                        >
                          <Send className="w-3.5 h-3.5" />
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent side="top">
                        {m.tooltip_chat_send()}
                      </TooltipContent>
                    </Tooltip>
                  )}

                  {isLoading && (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button
                          onClick={onStop}
                          variant="ghost"
                          size="sm"
                          disabled={isStopping}
                          className={`
                            h-9 px-4 mb-0.5 rounded-full flex-shrink-0
                            transition-all duration-200 hover:scale-105 active:scale-95
                            bg-muted text-primary hover:text-primary hover:bg-muted/80 border-2 border-primary
                            ${isStopping ? "opacity-60 cursor-not-allowed" : ""}
                          `}
                          type="button"
                          aria-label={
                            isStopping
                              ? m.input_stopping()
                              : m.tooltip_chat_stop()
                          }
                        >
                          <span className="text-xs font-medium">
                            {isStopping ? m.input_stopping() : m.input_stop()}
                          </span>
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent side="top">
                        {isStopping
                          ? m.input_stopping()
                          : m.tooltip_chat_stop()}
                      </TooltipContent>
                    </Tooltip>
                  )}
                </div>
              </div>
            </div>
          </div>

          {inputError && (
            <div className="mt-1 px-2 text-xs text-destructive">
              {inputError}
            </div>
          )}

          {/* Rodapé do input — todos os controles compartilham uma única linha
              flexível e cedem espaço aos rótulos antes de serem ocultados. */}
          <div
            data-testid="chat-input-footer"
            className={`flex min-h-10 w-full min-w-0 items-center border-t border-border/60 ${compactMode ? "flex-nowrap gap-x-1 overflow-visible px-1.5 py-2" : "flex-nowrap gap-x-2 overflow-hidden px-4 py-2"}`}
          >
            <div
              data-testid="chat-input-icon-group"
              className="flex shrink-0 items-center gap-1"
            >
              <PlusMenu
                compact={compactMode}
                disabled={!userId || offline}
                onAddFiles={onFileButtonClick}
                onCaptureScreenshot={onCaptureScreenshot}
              />
              {isVoiceSupported && onVoiceToggle && (
                <VoiceInputButton
                  isListening={isVoiceListening ?? false}
                  disabled={!userId || offline}
                  onClick={onVoiceToggle}
                  size="sm"
                  compact={compactMode}
                />
              )}
              <div
                className={`${compactMode ? "hidden" : "hidden @sm/composer:block"} h-4 w-px shrink-0 bg-border/60`}
              />
              {/* O workspace é escolhido só no modal de nova conversa e é imutável
                  depois disso — por isso não há seletor de workspace na appbar. */}
              {!chatMode && wsId && (
                <>
                  <VscodeMenu workspaceId={wsId} compact={compactMode} />
                  <div
                    className={`${compactMode ? "hidden" : "hidden @sm/composer:block"} h-4 w-px shrink-0 bg-border/60`}
                  />
                </>
              )}
            </div>
            {compactMode ? (
              <ControlGroup compact>
                <PermissionModeMenu compact />
                <EffortMenu compact />
                <div className="flex min-w-0 items-center gap-1">
                  {agentConfig && onAgentConfigChange && (
                    <ModelSelector
                      value={agentConfig.model}
                      onChange={handleModelChange}
                      compact
                      codeMode={!chatMode && !!wsId}
                    />
                  )}
                  {modelId && (
                    <UsagePopover
                      tokensUsed={tokensUsed ?? 0}
                      modelId={modelId}
                    />
                  )}
                </div>
              </ControlGroup>
            ) : (
              <ControlGroup compact={false}>
                <PermissionModeMenu />
                <EffortMenu />
                <div
                  data-testid="wide-model-controls"
                  className="flex min-w-0 w-fit max-w-full flex-[0_1_auto] items-center gap-1 overflow-hidden"
                >
                  {agentConfig && onAgentConfigChange && (
                    <ModelSelector
                      value={agentConfig.model}
                      onChange={handleModelChange}
                      codeMode={!chatMode && !!wsId}
                    />
                  )}
                  {modelId && (
                    <div className="shrink-0">
                      <UsagePopover
                        tokensUsed={tokensUsed ?? 0}
                        modelId={modelId}
                      />
                    </div>
                  )}
                </div>
              </ControlGroup>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
