/**
 * Stream Handler Hook — Vectora
 *
 * Gerencia streaming de respostas do agente Vectora via SSE, cliente
 * nativo.
 *
 * Eventos tratados:
 * - token      → acumula texto da resposta
 * - tool_call  → adiciona tool call inline na mensagem
 * - tool_result → atualiza output da tool call
 * - hitl       → pausa para aprovação humana
 * - done       → finaliza stream
 * - error      → propaga erro ao caller
 */

"use client";

import { useCallback, useRef } from "react";
import type { Message, ToolCall, ImageAttachment } from "../../types";
import {
  streamChat,
  resumeChat,
  getHistory,
  type StreamEvent,
  type ChatConfig,
  type ResumeChatRequest,
} from "../../api/vectora-client";
import {
  ensureMessageExists,
  updateMessageInList,
  toApiAttachments,
} from "../../utils/chat";
import { stripMarkdownEnvelope } from "../../utils/string/markdown-envelope";
import type { AgentConfig } from "@/components/layout/agent-settings";
import { useSettingsStore } from "@/lib/stores/settings-store";
import { useWorkspacesStore } from "@/lib/stores/workspaces-store";
import {
  consumeCreateNewWorkspace,
  markCreateNewWorkspace,
} from "@/lib/stores/workspace-choice-registry";
import { useWorkbenchStore } from "@/lib/stores/workbench-store";
import { useToastStore } from "@/lib/stores/toast-store";
import { useNetworkStore } from "@/lib/hooks/use-network-status";
import {
  markStreamStarted,
  markStreamEnded,
} from "@/lib/utils/stream-interruption";
import { m as msg } from "@/lib/paraglide/messages";
import type { RenderHint, ToolCategory } from "@/lib/types/render";

/** Únicos valores que o backend pode mandar em `render_hint`/`category` de
 * `tool_call` — um valor fora daqui vira fallback explícito (nunca cast
 * silencioso de string livre pro union type). */
const KNOWN_RENDER_HINTS = new Set<RenderHint>([
  "diff",
  "code_block",
  "terminal_output",
  "search_results",
  "table",
  "queue_progress",
  "queue_badge",
  "artifact",
  "image_preview",
  "browser_screenshot",
  "thinking_step",
  "json_tree",
  "chart_inline",
  "db_result",
  "json",
]);
const KNOWN_TOOL_CATEGORIES = new Set<ToolCategory>([
  "filesystem",
  "web",
  "rag",
  "memory",
  "workspace",
  "mcp",
  "artifacts",
  "general",
]);

// ============================================================================
// Streaming rendering
// ============================================================================

// Cede controle ao scheduler do browser para que React consiga commitar
// atualizações de estado e o browser pinte entre tokens.
//
// Problema raiz: reader.read() resolve como microtask quando há dados
// bufferizados — o loop for-await processa todos os tokens sem nunca ceder ao
// event loop. requestAnimationFrame não dispara enquanto microtasks estão
// rodando. scheduler.yield() (Chromium/Electron) cede sem delay artificial;
// MessageChannel é o fallback (sub-milissegundo, sem o delay mínimo de 4ms
// do setTimeout).
function yieldToBrowser(): Promise<void> {
  type Sched = { yield: () => Promise<void> };
  const sched = (globalThis as { scheduler?: Sched }).scheduler;
  if (typeof sched?.yield === "function") return sched.yield();
  return new Promise<void>((resolve) => {
    const { port1, port2 } = new MessageChannel();
    port1.addEventListener(
      "message",
      () => {
        port1.close();
        resolve();
      },
      { once: true },
    );
    port1.start();
    port2.postMessage(null);
    port2.close();
  });
}

// ============================================================================
// Resiliência de rede: status do SSE
// ============================================================================
//
// Não há `EventSource` aqui — o stream é lido via `fetch().body` (SSE manual,
// ver `vectora-client.ts::readSSEStream`). Por isso "onerror"/"onopen" são
// simulados: marcamos `connected` ao receber o primeiro evento do stream e
// `reconnecting` quando o erro capturado é de transporte (não uma falha de
// aplicação reportada pelo backend via evento `error`).

/** `true` para falhas de rede/conexão (fetch caiu, DNS, timeout de socket). */
export function isNetworkError(err: unknown): boolean {
  if (err instanceof TypeError) return true;
  const errMsg = err instanceof Error ? err.message : String(err);
  return /failed to fetch|network ?error|load failed|ECONNRESET|ECONNREFUSED/i.test(
    errMsg,
  );
}

/**
 * Mensagem amigável (localizada) para um erro de aplicação reportado pelo
 * backend via evento `error`. O backend classifica em códigos estáveis
 * (`RATE_LIMIT`, `AUTH`, `STREAM_ERROR`) — aqui mapeamos para i18n. Erros de
 * limite/quota do provedor (ex.: 429 do Gemini) não vazam o JSON cru.
 */
export function streamErrorMessage(code?: string): string {
  switch (code) {
    case "RATE_LIMIT":
      return msg.chat_error_rate_limit();
    case "MISSING_KEYS":
      return msg.chat_error_missing_keys();
    case "AUTH":
      return msg.chat_error_auth();
    case "TIMEOUT":
      return msg.chat_error_timeout();
    case "MODEL_NO_VISION":
      return msg.chat_error_model_no_vision();
    case "MODEL_INCOMPATIBLE":
      return msg.chat_error_model_incompatible();
    case "MODEL_NOT_ALLOWED":
      return msg.chat_error_model_not_allowed();
    case "ACCOUNT_CREDIT":
      return msg.chat_error_account_credit();
    default:
      return msg.chat_error_generic();
  }
}

/** Marca o stream como conectado; se vínhamos de uma queda, avisa via toast. */
function announceSSEConnected(): void {
  const prev = useNetworkStore.getState().sseStatus;
  if (prev === "reconnecting" || prev === "failed") {
    useToastStore.getState().success(msg.network_sse_reconnected());
  }
  if (prev !== "connected")
    useNetworkStore.getState().setSSEStatus("connected");
}

/** Marca o stream como caído por erro de transporte (badge "Reconectando…"). */
function announceSSEDropped(err: unknown): void {
  // A UI sempre mostra uma mensagem genérica localizada (nunca o erro cru
  // pro usuário) — mas sem isso, a causa real (status HTTP, "failed to
  // fetch", etc.) some completamente. Loga no console pra dar pra
  // diagnosticar via DevTools (Ctrl+Shift+I, inclusive no build desktop).
  console.error("[chat] queda de transporte no stream:", err);
  if (isNetworkError(err)) {
    useNetworkStore.getState().setSSEStatus("reconnecting");
  }
}

/**
 * Reconcilia uma mensagem cujo stream terminou sem `done`/`error` explícito
 * — o async generator do SSE simplesmente esgotou (ex.: buffer final
 * descartado numa queda de conexão silenciosa). O backend já persistiu o
 * conteúdo completo no checkpoint da sessão independente do que chegou ao
 * vivo; busca o histórico e aplica só o conteúdo final da mensagem truncada,
 * SEM substituir a lista inteira de mensagens (evita reintroduzir a race que
 * o guard `hasSentMessageRef` de chat-interface.tsx existe para prevenir).
 * Nunca lança — best-effort, roda dentro do `finally` do stream.
 */
async function reconcileTruncatedMessage(
  threadId: string,
  assistantMessageId: string,
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>,
): Promise<void> {
  try {
    const { messages } = await getHistory(threadId);
    const lastAssistant = messages.findLast((m) => m.role === "assistant");
    if (!lastAssistant) return;
    setMessages((prev) =>
      updateMessageInList(prev, assistantMessageId, (m) => ({
        ...m,
        content: lastAssistant.content,
        isThinking: false,
      })),
    );
  } catch (err) {
    console.error("[chat] falha ao reconciliar mensagem truncada:", err);
  }
}

// ============================================================================
// Types
// ============================================================================

interface UseStreamHandlerProps {
  /** Não utilizado (mantido para compatibilidade com chat-interface.tsx) */
  client?: unknown;
  threadId: string;
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>;
  agentConfig?: AgentConfig;
  shouldInterruptRef?: React.MutableRefObject<boolean>;
  /** Não utilizado nesta função (mantido pra compatibilidade de assinatura) */
  userId?: string | null;
  userEmail?: string | null;
  userName?: string | null;
  /** Troca automática de provider por quota — atualiza model selector + toast. */
  onModelSwitched?: (fromModel: string, toModel: string) => void;
}

interface UseStreamHandlerReturn {
  processStream: (
    userContent: string,
    assistantMessageId: string,
    images?: ImageAttachment[],
    forkFromCheckpointId?: string,
  ) => Promise<{ assistantContent: string; runId: string | undefined }>;
  /** Retoma uma execução pausada por HITL (approve / reject / edit:<json>). */
  processResume: (
    request: ResumeChatRequest,
    assistantMessageId: string,
  ) => Promise<{ assistantContent: string }>;
  /**
   * Aborta o stream em andamento IMEDIATAMENTE (não espera o próximo evento
   * SSE chegar). Bug: `handleStop` só setava `shouldInterruptRef.current`,
   * checado unicamente dentro do `for await` do loop de eventos — se o
   * modelo está "pensando" sem produzir token nenhum, o cancelamento não
   * tinha efeito nenhum até o servidor mandar alguma coisa.
   */
  abort: () => void;
}

// ============================================================================
// Hook
// ============================================================================

export function useStreamHandler({
  threadId,
  setMessages,
  agentConfig,
  shouldInterruptRef,
  onModelSwitched,
}: UseStreamHandlerProps): UseStreamHandlerReturn {
  // AbortController para interromper o stream quando shouldInterruptRef === true
  const abortRef = useRef<AbortController | null>(null);

  const processStream = useCallback(
    async (
      userContent: string,
      assistantMessageId: string,
      images?: ImageAttachment[],
      forkFromCheckpointId?: string,
    ): Promise<{ assistantContent: string; runId: string | undefined }> => {
      // Cancela stream anterior se ainda em andamento
      abortRef.current?.abort();
      const abort = new AbortController();
      abortRef.current = abort;

      // Garante que a mensagem do assistente existe no estado
      const thinkingStartTime = Date.now();
      const baseAssistantMessage: Message = {
        id: assistantMessageId,
        role: "assistant",
        content: "",
        timestamp: new Date(),
        isThinking: true,
        thinkingStartTime,
      };
      setMessages((prev) =>
        ensureMessageExists(prev, assistantMessageId, baseAssistantMessage),
      );

      // activeId rastreia a bolha corrente; muda ao receber message_break.
      let activeId = assistantMessageId;

      let assistantContent = "";
      let resolvedRunId: string | undefined;
      // Primeiro evento recebido = conexão SSE estabelecida
      let sseConnected = false;
      // true só quando o loop termina por done/error/abort explícitos — se o
      // async generator simplesmente esgotar sem nenhum desses (queda de
      // conexão silenciosa), fica false e dispara reconciliação com o
      // backend (ver reconcileTruncatedMessage) em vez de aceitar o
      // conteúdo parcial como definitivo.
      let streamCompletedNormally = false;

      // Monta config da request
      const config: ChatConfig = {};
      if (agentConfig?.model) config.model = agentConfig.model;
      const customSystemPrompt = useSettingsStore.getState().customSystemPrompt;
      if (customSystemPrompt) config.custom_system_prompt = customSystemPrompt;
      const settings = useSettingsStore.getState();
      // Modo Chat: conversacional puro, sem workspace/folders.
      config.chat_mode = settings.chatMode;
      // "criar novo workspace" (modal Nova conversa) — sinal one-shot,
      // consumido aqui: NÃO manda o active_id stale do store (workspace de
      // outra conversa), manda create_new_workspace pro backend criar um
      // dedicado. Ver workspace-choice-registry.ts. Se essa tentativa cair
      // (transporte ou erro de app) antes do evento `thread` confirmar o
      // workspace resolvido, o sinal é restaurado abaixo (catch/error) —
      // sem isso, um retry silenciosamente reusa o active_id stale (era o
      // bug: 1ª tentativa falha, sinal já consumido, retry cai no workspace
      // errado mesmo com "criar novo" marcado).
      const wantsNewWorkspace =
        !settings.chatMode && consumeCreateNewWorkspace(threadId);
      let newWorkspaceConfirmed = false;
      if (wantsNewWorkspace) {
        config.create_new_workspace = true;
      } else {
        const activeWorkspaceId = useWorkspacesStore.getState().active_id;
        if (!settings.chatMode && activeWorkspaceId)
          config.workspace_id = activeWorkspaceId;
      }
      config.permission_mode = settings.permissionMode;
      config.reasoning_effort = settings.reasoningEffort;
      // Editar mensagem / regenerar resposta: resume a partir do checkpoint
      // pai da mensagem alvo em vez do mais recente, fazendo o histórico
      // ramificar dali (histórico original preservado).
      if (forkFromCheckpointId) {
        config.fork_from_checkpoint_id = forkFromCheckpointId;
      }

      // Converte ImageAttachment[] → Attachment[] para a API (F1)
      const attachments =
        images && images.length > 0 ? toApiAttachments(images) : undefined;

      // needsSeparator: true após message_break — próximo token recebe "\n\n"
      // de separação (só quando há conteúdo prévio na bolha).
      let needsSeparator = false;

      // Marca início; `finally` desmarca por qualquer saída conhecida
      // (done/hitl/error/abort). Se a aba fechar/recarregar no meio, a marca
      // sobrevive e o próximo mount acusa "resposta pode ter sido interrompida".
      markStreamStarted(threadId);

      try {
        const events = streamChat(
          {
            thread_id: threadId,
            content: userContent,
            config,
            ...(attachments && attachments.length > 0 && { attachments }),
          },
          abort.signal,
        );

        for await (const event of events) {
          // Primeiro evento do stream = conexão SSE de fato estabelecida
          if (!sseConnected) {
            sseConnected = true;
            announceSSEConnected();
          }

          // Interrupção solicitada pelo usuário
          if (shouldInterruptRef?.current) {
            abort.abort();
            streamCompletedNormally = true;
            break;
          }

          if (event.type === "thread" && event.workspace_id) {
            // Sincroniza o workspace resolvido pelo backend de volta pro
            // store local — sem isso, um workspace criado via
            // create_new_workspace nunca aparece no seletor/chip da UI
            // (o backend já persistiu como ativo sozinho, isso só espelha
            // localmente, sem POST redundante — ver syncActiveLocal).
            useWorkspacesStore.getState().syncActiveLocal(event.workspace_id);
            newWorkspaceConfirmed = true;
          }

          if (event.type === "token") {
            // Guard defensivo: `content` ausente/não-string (drift de
            // contrato) não pode virar a string literal "undefined"
            // concatenada silenciosamente na resposta visível ao usuário.
            const token =
              typeof event.content === "string" ? event.content : "";
            if (token === "" && event.content !== "") {
              console.warn(
                "[SSE] evento token com `content` inválido, ignorado:",
                event.content,
              );
            }
            assistantContent += token;
            const sep = needsSeparator ? "\n\n" : "";
            needsSeparator = false;
            setMessages((prev) =>
              updateMessageInList(prev, activeId, (m) => {
                const cur = typeof m.content === "string" ? m.content : "";
                return { ...m, content: cur + (cur && sep ? sep : "") + token };
              }),
            );
            // Cede ao scheduler do browser para que o token apareça na tela
            // antes do próximo ser processado (streaming visível letra a letra).
            await yieldToBrowser();
            continue;
          }

          // Fallback automático de provider por quota: atualiza model selector + toast
          if (event.type === "model_switched") {
            onModelSwitched?.(event.from_model, event.to_model);
            continue;
          }

          // Quebra de segmento: o backend mudou de nó emissor de tokens.
          // stripMarkdownEnvelope aqui é defensivo (no-op na maioria das
          // respostas — o modelo não é mais instruído a envelopar em fence),
          // só entra em ação se algum provider insistir em envelopar por
          // conta própria. Seta separador para o próximo segmento. Continua
          // na MESMA bolha — sem nova mensagem.
          if (event.type === "message_break") {
            setMessages((prev) =>
              updateMessageInList(prev, activeId, (m) => {
                const current = typeof m.content === "string" ? m.content : "";
                return { ...m, content: stripMarkdownEnvelope(current) };
              }),
            );
            assistantContent = stripMarkdownEnvelope(assistantContent);
            needsSeparator = true;
            continue;
          }

          await handleEvent(event, activeId, setMessages, threadId);

          if (event.type === "hitl") {
            // Pausa deliberada pra aprovação humana — o backend encerra o
            // stream aqui de propósito (ver adapt_stream), não é
            // truncamento. Sem este break explícito, o loop só sairia por
            // esgotamento natural do generator, indistinguível do bug real.
            streamCompletedNormally = true;
            break;
          }
          if (event.type === "done") {
            resolvedRunId = event.run_id || undefined;
            streamCompletedNormally = true;
            break;
          }
          if (event.type === "error") {
            // Erro de aplicação reportado pelo backend (ex.: 429/quota do
            // provedor). Em vez de exibir o JSON cru como se fosse a resposta
            // da IA, mostramos uma mensagem limpa e localizada (por código) e
            // marcamos isError para habilitar o retry. Encerra o loop sem
            // throw — o catch fica reservado a quedas de transporte.
            const friendly = streamErrorMessage(event.code);
            setMessages((prev) =>
              updateMessageInList(prev, activeId, (m) => {
                // Preserva qualquer conteúdo parcial real já gerado antes do
                // erro (ex.: quota estourou no meio da execução de um
                // subagente, depois do orquestrador já ter respondido algo)
                // — nunca sobrescreve, senão o usuário só vê o aviso
                // genérico e perde o trabalho parcial visível.
                const existing = typeof m.content === "string" ? m.content : "";
                const content = existing.trim()
                  ? `${existing}\n\n---\n\n${friendly}`
                  : friendly;
                return {
                  ...m,
                  content,
                  isError: true,
                  isThinking: false,
                  thinkingDuration:
                    m.thinkingStartTime !== undefined
                      ? Date.now() - m.thinkingStartTime
                      : undefined,
                };
              }),
            );
            streamCompletedNormally = true;
            break;
          }
        }
        // O `for await` acima também termina "normalmente" (sem exceção,
        // sem break) quando o async generator simplesmente esgota — ex.:
        // readSSEStream perdeu o evento final numa queda de conexão
        // silenciosa (mesma classe de bug já mitigada na origem, mas
        // defesa em profundidade aqui). Sem done/error/abort explícitos, o
        // conteúdo acumulado no client não pode ser considerado definitivo
        // — reconcilia com o que o backend de fato persistiu.
        if (!streamCompletedNormally) {
          await reconcileTruncatedMessage(threadId, activeId, setMessages);
        }
      } catch (err: unknown) {
        if ((err as { name?: string }).name === "AbortError") {
          // Interrompido pelo usuário — não é um erro; encerra o thinking timer
          streamCompletedNormally = true;
          setMessages((prev) =>
            updateMessageInList(prev, activeId, (m) => ({
              ...m,
              isThinking: false,
              thinkingDuration:
                m.thinkingStartTime !== undefined
                  ? Date.now() - m.thinkingStartTime
                  : undefined,
            })),
          );
        } else {
          // Distingue queda de transporte (badge "Reconectando…") de
          // erro de aplicação reportado pelo próprio backend via evento `error`.
          announceSSEDropped(err);
          // Queda de transporte: mostra o conteúdo parcial já recebido de
          // imediato (feedback rápido) — sem conteúdo, mensagem genérica
          // localizada e isError (retry), nunca o texto cru da exceção.
          setMessages((prev) =>
            updateMessageInList(prev, activeId, (m) => ({
              ...m,
              content: assistantContent || streamErrorMessage(undefined),
              isError: !assistantContent,
              isThinking: false,
              thinkingDuration:
                m.thinkingStartTime !== undefined
                  ? Date.now() - m.thinkingStartTime
                  : undefined,
            })),
          );
          // O texto acumulado no client é só o que chegou até a exceção —
          // se o turno continuou rodando no backend depois da conexão
          // cair (comum: a queda é do transporte, não do processamento),
          // esse conteúdo parcial fica truncado no meio de uma frase
          // indefinidamente, sem o reload manual que hoje é o único jeito
          // de ver a versão completa. Reconcilia igual ao caminho de
          // esgotamento silencioso do loop, abaixo.
          await reconcileTruncatedMessage(threadId, activeId, setMessages);
        }
      } finally {
        // Restaura o sinal "criar novo workspace" se essa tentativa terminou
        // (erro de app, queda de transporte ou abort) sem o backend nunca
        // confirmar o workspace resolvido — sem isso, um retry reusaria o
        // active_id stale mesmo com "criar novo" marcado (ver comentário na
        // montagem do config, acima).
        if (wantsNewWorkspace && !newWorkspaceConfirmed) {
          markCreateNewWorkspace(threadId);
        }
        // Qualquer saída conhecida do loop desmarca a thread como
        // "streaming em andamento". Se terminou sem done/error/abort (loop
        // esgotou em silêncio), a marca fica — mesmo já reconciliado acima,
        // preserva o aviso "resposta pode ter sido interrompida" num
        // reload/mount futuro (defesa em profundidade caso a reconciliação
        // em si tenha falhado).
        if (streamCompletedNormally) {
          markStreamEnded(threadId);
        }
        // Defesa em profundidade: garante que o spinner sempre encerra na bolha ativa
        setMessages((prev) =>
          updateMessageInList(prev, activeId, (m) =>
            m.isThinking
              ? {
                  ...m,
                  isThinking: false,
                  thinkingDuration:
                    m.thinkingStartTime !== undefined
                      ? Date.now() - m.thinkingStartTime
                      : undefined,
                }
              : m,
          ),
        );
      }

      return { assistantContent, runId: resolvedRunId };
    },
    // shouldInterruptRef (.current) e onModelSwitched (?.()) só são acessados
    // via optional chaining — o linter de deps de memoização do oxlint não
    // enxerga esse uso e os marca como "extra", mas ambos são genuinamente
    // lidos no corpo do callback (ver linhas acima).
    // oxlint-disable-next-line react/memo-dependencies
    [threadId, setMessages, agentConfig, shouldInterruptRef, onModelSwitched],
  );

  // ---------------------------------------------------------------------------
  // processResume — retoma stream após aprovação/rejeição HITL
  // ---------------------------------------------------------------------------
  const processResume = useCallback(
    async (
      request: ResumeChatRequest,
      assistantMessageId: string,
    ): Promise<{ assistantContent: string }> => {
      // Limpa hitlPending e reativa o spinner de thinking
      setMessages((prev) =>
        updateMessageInList(prev, assistantMessageId, (m) => ({
          ...m,
          hitlPending: undefined,
          isThinking: true,
          thinkingStartTime: Date.now(),
        })),
      );

      let assistantContent = "";
      // Primeiro evento recebido = conexão SSE estabelecida
      let sseConnected = false;
      // Mesma defesa em profundidade de processStream — ver comentário lá.
      let streamCompletedNormally = false;

      // Mesma marca de "stream em andamento" do processStream
      markStreamStarted(threadId);

      try {
        const events = resumeChat(request, abortRef.current?.signal);

        for await (const event of events) {
          // Primeiro evento do stream = conexão SSE de fato estabelecida
          if (!sseConnected) {
            sseConnected = true;
            announceSSEConnected();
          }

          if (shouldInterruptRef?.current) {
            abortRef.current?.abort();
            streamCompletedNormally = true;
            break;
          }

          if (event.type === "token") {
            const token =
              typeof event.content === "string" ? event.content : "";
            if (token === "" && event.content !== "") {
              console.warn(
                "[SSE] evento token com `content` inválido, ignorado:",
                event.content,
              );
            }
            assistantContent += token;
            setMessages((prev) =>
              updateMessageInList(prev, assistantMessageId, (m) => ({
                ...m,
                content:
                  (typeof m.content === "string" ? m.content : "") + token,
              })),
            );
            await yieldToBrowser();
            continue;
          }

          await handleEvent(event, assistantMessageId, setMessages, threadId);

          if (event.type === "hitl") {
            // Pausa deliberada pra aprovação humana — ver comentário
            // equivalente em processStream.
            streamCompletedNormally = true;
            break;
          }
          if (event.type === "done") {
            streamCompletedNormally = true;
            break;
          }
          if (event.type === "error") {
            const friendly = streamErrorMessage(event.code);
            setMessages((prev) =>
              updateMessageInList(prev, assistantMessageId, (m) => ({
                ...m,
                content: friendly,
                isError: true,
                isThinking: false,
              })),
            );
            streamCompletedNormally = true;
            break;
          }
        }
        // Ver comentário equivalente em processStream: loop esgotado sem
        // done/error/abort explícitos → conteúdo acumulado não é
        // definitivo, reconcilia com o backend.
        if (!streamCompletedNormally) {
          await reconcileTruncatedMessage(
            threadId,
            assistantMessageId,
            setMessages,
          );
        }
      } catch (err: unknown) {
        if ((err as { name?: string }).name === "AbortError") {
          streamCompletedNormally = true;
        } else {
          // Mesma distinção transporte vs. aplicação do processStream
          announceSSEDropped(err);
          setMessages((prev) =>
            updateMessageInList(prev, assistantMessageId, (m) => ({
              ...m,
              content: assistantContent || streamErrorMessage(undefined),
              isError: !assistantContent,
              isThinking: false,
            })),
          );
          // Mesmo reconcile do processStream: o turno pode ter continuado
          // no backend depois da conexão cair.
          await reconcileTruncatedMessage(
            threadId,
            assistantMessageId,
            setMessages,
          );
        }
      } finally {
        // Qualquer saída conhecida do loop (done/hitl/error/abort)
        // desmarca a thread como "streaming em andamento"; só sobra marcado
        // o caso em que a aba fechou/recarregou no meio da resposta (mesma
        // lógica de processStream — ver comentário lá).
        if (streamCompletedNormally) {
          markStreamEnded(threadId);
        }
        setMessages((prev) =>
          updateMessageInList(prev, assistantMessageId, (m) =>
            m.isThinking
              ? {
                  ...m,
                  isThinking: false,
                  thinkingDuration:
                    m.thinkingStartTime !== undefined
                      ? Date.now() - m.thinkingStartTime
                      : undefined,
                }
              : m,
          ),
        );
      }

      return { assistantContent };
    },
    // shouldInterruptRef (.current) só é acessado via optional chaining — o
    // linter de deps de memoização do oxlint não enxerga esse uso e o marca
    // como "extra", mas ele é genuinamente lido no corpo do callback.
    // oxlint-disable-next-line react/memo-dependencies
    [threadId, setMessages, shouldInterruptRef],
  );

  const abort = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  return { processStream, processResume, abort };
}

// ============================================================================
// Event handler
// ============================================================================

// handleEvent processa todos os eventos exceto "token"
// (tokens são aplicados via setMessages diretamente nos loops)
async function handleEvent(
  event: StreamEvent,
  assistantMessageId: string,
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>,
  threadId?: string,
): Promise<void> {
  switch (event.type) {
    case "token":
    case "thread":
    case "done":
    case "error":
    case "model_switched":
    case "message_break":
      // Tratados pelo loop externo (processStream/processResume) antes ou
      // depois de chamar handleEvent — no-op aqui de propósito, não são
      // "desconhecidos" (cairiam no default e disparariam o warning de
      // drift de contrato por engano).
      break;

    case "tool_call": {
      let args: Record<string, unknown> = {};
      try {
        args = JSON.parse(event.args_json);
      } catch {
        args = { _raw: event.args_json };
      }

      const toolCall: ToolCall = {
        id: event.tool_call_id,
        name: event.tool_name,
        args,
        output: undefined,
        renderHint: KNOWN_RENDER_HINTS.has(event.render_hint as RenderHint)
          ? (event.render_hint as RenderHint)
          : "json",
        category: KNOWN_TOOL_CATEGORIES.has(event.category as ToolCategory)
          ? (event.category as ToolCategory)
          : "general",
        destructive: event.destructive ?? false,
        icon: event.icon ?? "tool",
      };

      setMessages((prev) =>
        updateMessageInList(prev, assistantMessageId, (m) => ({
          ...m,
          toolCalls: [...(m.toolCalls ?? []), toolCall],
        })),
      );
      break;
    }

    // Streaming ao vivo da tool `terminal` — chega enquanto o comando ainda
    // roda (antes do tool_result). Só existe uma tool `terminal` ativa por
    // vez (backend/services/terminal_stream.py garante isso); anexa na
    // última tool call desse tipo que ainda não tem output.
    case "terminal_line": {
      setMessages((prev) =>
        updateMessageInList(prev, assistantMessageId, (m) => {
          const calls = m.toolCalls ?? [];
          const idx = calls.findLastIndex(
            (tc) =>
              tc.renderHint === "terminal_output" && tc.output === undefined,
          );
          if (idx === -1) return m;
          const updated = [...calls];
          updated[idx] = {
            ...updated[idx],
            liveOutputLines: [
              ...(updated[idx].liveOutputLines ?? []),
              event.line,
            ],
          };
          return { ...m, toolCalls: updated };
        }),
      );
      break;
    }

    case "tool_result": {
      let output: unknown;
      try {
        output = JSON.parse(event.content_json);
      } catch {
        output = event.content_json;
      }

      let resolvedToolName: string | undefined;
      setMessages((prev) =>
        updateMessageInList(prev, assistantMessageId, (m) => ({
          ...m,
          toolCalls: (m.toolCalls ?? []).map((tc) => {
            if (tc.id !== event.tool_call_id) return tc;
            resolvedToolName = tc.name;
            return { ...tc, output, isError: event.is_error };
          }),
        })),
      );

      // Invalidate cache do workbench só quando a tool de fato terminou
      // (arquivo já em disco) — invalidar em "tool_call" (on_tool_start)
      // é cedo demais e causava o painel Plan/Files ficar vazio até um
      // novo pedido do usuário disparar outro ciclo de invalidação.
      if (!event.is_error && resolvedToolName) {
        invalidateWorkbenchFor(resolvedToolName, threadId);
      }
      break;
    }

    // Resultado de delegação via task() — popula o card "Subagent Outputs"
    // com identidade (coder/search), status ao vivo e conteúdo. Dedupe por
    // tool_call_id: o "running" (content vazio) vira "complete"/"error" com o
    // resultado no mesmo card, sem duplicar.
    case "subagent_output": {
      setMessages((prev) =>
        updateMessageInList(prev, assistantMessageId, (m) => {
          const existing = m.subgraphOutputs ?? [];
          const entry = {
            name: event.subagent_type,
            output: event.content,
            timestamp: Date.now(),
            toolCallId: event.tool_call_id,
            isStreaming: event.status === "running",
            isComplete: event.status !== "running",
          };
          const idx = existing.findIndex(
            (s) => s.toolCallId === event.tool_call_id,
          );
          const subgraphOutputs =
            idx >= 0
              ? existing.map((s, i) => {
                  if (i !== idx) return s;
                  const output = event.is_delta
                    ? `${s.output ?? ""}${event.content ?? ""}`
                    : event.content || s.output;
                  return { ...s, ...entry, output };
                })
              : [...existing, entry];
          return { ...m, subgraphOutputs };
        }),
      );
      break;
    }

    // D2/D3 — NodeEvent: label semântico + duração por nó
    case "node": {
      if (event.status === "started" && event.node_label) {
        setMessages((prev) =>
          updateMessageInList(prev, assistantMessageId, (m) => ({
            ...m,
            currentNodeLabel: event.node_label,
          })),
        );
      } else if (
        event.status === "finished" &&
        event.duration_ms != null &&
        event.duration_ms > 0
      ) {
        setMessages((prev) =>
          updateMessageInList(prev, assistantMessageId, (m) => ({
            ...m,
            currentNodeLabel: undefined,
            nodeDurations: [
              ...(m.nodeDurations ?? []),
              {
                node: event.node,
                label: event.node_label ?? event.node,
                duration_ms: event.duration_ms!,
              },
            ],
          })),
        );
      }
      break;
    }

    case "ui_metrics":
      break;

    // C.28 — RAG citations: armazena fontes para renderizar referências [N]
    case "rag_citations": {
      setMessages((prev) =>
        updateMessageInList(prev, assistantMessageId, (m) => ({
          ...m,
          ragCitations: event.citations,
        })),
      );
      break;
    }

    // E1 — HITLEvent: pausa do stream para aprovação humana
    case "hitl": {
      setMessages((prev) =>
        updateMessageInList(prev, assistantMessageId, (m) => ({
          ...m,
          isThinking: false,
          thinkingDuration:
            m.thinkingStartTime !== undefined
              ? Date.now() - m.thinkingStartTime
              : undefined,
          hitlPending: {
            toolName: event.tool_name,
            argsJson: event.args_json,
            interruptId: event.interrupt_id,
            reasoning: event.reasoning,
            diffPreview: event.diff_preview,
            affectedPaths: event.affected_paths,
            permissionMode: event.permission_mode,
            preApproved: event.pre_approved,
            workspaceId: useWorkspacesStore.getState().active_id ?? undefined,
          },
        })),
      );
      break;
    }

    case "tool_activity": {
      if (event.elapsed_ms === null) {
        // Tool iniciou: mostrar na status line
        setMessages((prev) =>
          updateMessageInList(prev, assistantMessageId, (m) => ({
            ...m,
            activeTool: {
              name: event.tool_name,
              argsPreview: event.args_preview,
            },
          })),
        );
      } else {
        const elapsedMs = event.elapsed_ms;
        const tcId = event.tool_call_id;
        // Tool terminou: enriquecer ToolCall com elapsed + atualizar status line
        setMessages((prev) =>
          updateMessageInList(prev, assistantMessageId, (m) => ({
            ...m,
            activeTool: {
              name: event.tool_name,
              argsPreview: event.args_preview,
              elapsedMs,
            },
            // Enriquece o ToolCall correspondente com o elapsed_ms
            toolCalls: (m.toolCalls ?? []).map((tc) =>
              tc.id === tcId ? { ...tc, elapsedMs } : tc,
            ),
          })),
        );
        // Limpa o indicador após 800ms para o usuário ver o tempo
        setTimeout(() => {
          setMessages((prev) =>
            updateMessageInList(prev, assistantMessageId, (m) =>
              m.activeTool?.name === event.tool_name
                ? { ...m, activeTool: null }
                : m,
            ),
          );
        }, 800);
      }
      break;
    }

    case "workbench_invalidate": {
      const ws = useWorkspacesStore.getState().getActive();
      // `tabs` malformado (ausente/tipo errado) não pode virar TypeError
      // aqui dentro — esse handler roda dentro do loop de streaming, e uma
      // exceção não tratada seria capturada pelo catch de nível superior e
      // mal-classificada como queda de conexão SSE (announceSSEDropped),
      // escondendo um bug de contrato atrás de um badge de "Reconectando…".
      if (ws && Array.isArray(event.tabs)) {
        const tabs = event.tabs as string[];
        if (tabs.includes("files"))
          useWorkbenchStore.getState().invalidateFiles(ws.id);
        if (tabs.includes("diff"))
          useWorkbenchStore.getState().invalidateDiff(ws.id);
        if (tabs.includes("plan") && threadId)
          useWorkbenchStore.getState().invalidatePlan(threadId);
        if (tabs.includes("tasks") || tabs.includes("files"))
          useWorkbenchStore.getState().markPending(ws.id);
      } else if (ws) {
        console.warn(
          "[SSE] workbench_invalidate com `tabs` malformado, ignorado:",
          event.tabs,
        );
      }
      break;
    }

    // Plan Mode real (write_todos/TodoListMiddleware) — substitui a lista
    // inteira a cada chamada (não incremental); alimenta a seção "Tasks"
    // do Plan tab ao vivo, sem round-trip HTTP extra.
    case "todos_updated": {
      if (threadId) {
        useWorkbenchStore.getState().setTodos(threadId, event.todos);
      }
      break;
    }

    default:
      // `type` desconhecido — nunca descartar silenciosamente. Paridade com
      // o log já existente em erro de parse JSON (parseSSELine): um evento
      // fora do vocabulário conhecido é sinal de drift de contrato entre
      // backend e frontend, não ruído a ignorar.
      console.warn(
        "[SSE] evento com type desconhecido, ignorado:",
        (event as { type?: unknown }).type,
      );
      break;
  }
}

// ============================================================================
// Workbench cache invalidation
// ============================================================================
//
// Mapeamento tool_name → caches a invalidar. As tools de filesystem/git/terminal
// mexem no workspace e podem mudar a árvore, o diff e os arquivos abertos.
// `create_artifact` mexe nos artifacts da sessão.

const FILES_DIFF_TOOLS = new Set([
  "file_write",
  "file_edit",
  "terminal",
  "git_commit",
  "git_checkout",
  "git_pull",
  "git_stash",
  "git_worktree",
]);

function invalidateWorkbenchFor(
  toolName: string,
  threadId: string | undefined,
): void {
  if (toolName === "create_artifact" && threadId) {
    useWorkbenchStore.getState().invalidatePlan(threadId);
    return;
  }

  if (FILES_DIFF_TOOLS.has(toolName)) {
    const ws = useWorkspacesStore.getState().getActive();
    if (ws) {
      useWorkbenchStore.getState().invalidateFiles(ws.id);
      useWorkbenchStore.getState().invalidateDiff(ws.id);
      // Sinaliza pendência para a aba que não está montada no momento.
      useWorkbenchStore.getState().markPending(ws.id);
    }
  }
}
