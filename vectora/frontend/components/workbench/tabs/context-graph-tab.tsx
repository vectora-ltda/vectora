"use client";

import { useState } from "react";
import { Loader2, RefreshCw, X, Settings2, Waypoints } from "lucide-react";

import { useContextGraph } from "@/lib/hooks/use-context-graph";
import { useWorkspacesStore } from "@/lib/stores/workspaces-store";
import { useContextGraphSettingsStore } from "@/lib/stores/context-graph-settings-store";
import { WorkbenchSettingsSurface } from "@/components/workbench/settings/workbench-settings-surface";
import { contextGraphSettings } from "@/components/workbench/settings/workbench-settings-registry";
import { ContextGraphViewer } from "@/components/workbench/tabs/context-graph-viewer";
import { m } from "@/lib/paraglide/messages";

interface ContextGraphTabProps {
  threadId: string;
  onSendPrompt?: (text: string) => void;
}

/**
 * Cor (CSS var do theme) da bolinha de um arquivo concluído, conforme a etapa
 * atual do build: AST (≤2) → semântica (3) → concluído/pós (≥4). Torna visível
 * por qual estágio o build está passando, em vez de uma cor única.
 */
export function graphStageColor(step: number | null | undefined): string {
  if (step != null && step >= 4) return "var(--color-graph-stage-done)";
  if (step === 3) return "var(--color-graph-stage-semantic)";
  return "var(--color-graph-stage-ast)";
}

export function ContextGraphTab({
  threadId,
  onSendPrompt,
}: ContextGraphTabProps) {
  const workspaceId = useWorkspacesStore((s) => s.active_id);
  const {
    status,
    report,
    loading,
    build,
    update,
    resume,
    cancel,
    queryAffected,
    fetchGraphData,
    pathBetween,
  } = useContextGraph(workspaceId);
  const [showReport, setShowReport] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const fileTypes = useContextGraphSettingsStore((s) => s.fileTypes);
  const graphMode = useContextGraphSettingsStore((s) => s.mode);

  const isBuilt = status.status === "done";
  const isRunning = status.status === "running" || status.status === "queued";
  const isPaused = status.status === "paused";
  const hasGraphData = (status.node_count ?? 0) > 0;

  function handleBuild() {
    build({ mode: graphMode, fileTypes });
  }

  function handleQuestion(q: string) {
    onSendPrompt?.(q);
  }

  const godNodeMatches = report?.match(
    /\*\*God nodes[^*]*\*\*[^\n]*\n([\s\S]*?)(?=\n##|\n\*\*|$)/i,
  );
  const godNodes = godNodeMatches
    ? godNodeMatches[1]
        .split("\n")
        .filter((l) => l.trim().startsWith("-"))
        .map((l) => l.replace(/^-\s*/, "").trim())
        .slice(0, 8)
    : [];

  const questionMatches = report?.match(
    /sugeridas?[^\n]*\n([\s\S]*?)(?=\n##|\n\*\*|$)/i,
  );
  const questions = questionMatches
    ? questionMatches[1]
        .split("\n")
        .filter((l) => l.trim().startsWith("-") || l.trim().match(/^\d+\./))
        .map((l) => l.replace(/^[-\d.]\s*/, "").trim())
        .filter(Boolean)
        .slice(0, 5)
    : [];

  return (
    <div className="relative flex flex-col h-full overflow-hidden">
      {/* Barra de ação — alinhada à esquerda (padrão dos workbenches). */}
      <div className="relative flex min-w-0 items-center gap-2 overflow-hidden px-3 py-2 border-b border-border/60 shrink-0">
        {isBuilt && !isRunning && (
          <button
            onClick={() => update({ mode: graphMode, fileTypes })}
            disabled={loading}
            className="flex shrink-0 items-center gap-1.5 whitespace-nowrap text-xs px-2.5 py-1 rounded border border-border text-muted-foreground hover:text-foreground hover:border-border/80 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <RefreshCw className="h-3 w-3" />
            {m.graph_update_button()}
          </button>
        )}
        <button
          onClick={isRunning ? () => void cancel() : handleBuild}
          disabled={!isRunning && loading}
          data-testid="graph-build-btn"
          className={
            isRunning
              ? "flex min-w-0 flex-1 items-center gap-1.5 overflow-hidden whitespace-nowrap text-xs px-2.5 py-1 rounded border border-border text-muted-foreground hover:text-foreground hover:border-border/80"
              : "flex min-w-0 flex-1 items-center gap-1.5 overflow-hidden whitespace-nowrap text-xs px-2.5 py-1 rounded bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed"
          }
        >
          {isRunning ? (
            <>
              <X className="h-3 w-3" />
              <span className="truncate">{m.graph_cancel_button()}</span>
            </>
          ) : (
            <>
              <RefreshCw className="h-3 w-3" />
              <span className="truncate">
                {isBuilt ? m.graph_rebuild_button() : m.graph_build_button()}
              </span>
            </>
          )}
        </button>

        {/* Configurações do grafo: tipos de arquivo + modo. */}
        <button
          onClick={() => setShowSettings((v) => !v)}
          disabled={isRunning}
          aria-label={m.graph_settings_title()}
          title={m.graph_settings_title()}
          data-testid="graph-settings-btn"
          aria-expanded={showSettings}
          className="ml-auto flex h-6 w-6 shrink-0 items-center justify-center rounded text-muted-foreground hover:text-foreground hover:bg-muted/50 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <Settings2 className="h-3.5 w-3.5" />
        </button>
      </div>

      <WorkbenchSettingsSurface
        descriptor={contextGraphSettings}
        context={{ threadId, workspaceId }}
        open={showSettings}
        onOpenChange={setShowSettings}
        testId="graph-settings-panel"
      />

      <div className="flex-1 min-h-0 flex flex-col">
        {/* Status: error */}
        {status.status === "error" && (
          <div className="overflow-y-auto px-3 py-2 text-sm text-destructive">
            {status.error ?? m.workbench_settings_unknown_error()}
          </div>
        )}

        {/* Status: paused (quota esgotada em todos os providers) */}
        {isPaused && (
          <div
            data-testid="graph-paused"
            className="overflow-y-auto px-3 py-4 text-sm text-center space-y-3"
          >
            <p className="text-amber-500">{m.graph_paused()}</p>
            {status.step != null && status.step_total != null && (
              <div className="space-y-1">
                <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden">
                  <div
                    className="h-full rounded-full bg-amber-500 transition-all"
                    style={{
                      width: `${Math.round((status.step / status.step_total) * 100)}%`,
                    }}
                  />
                </div>
                <p className="text-xs text-muted-foreground">
                  {status.step}/{status.step_total}
                </p>
              </div>
            )}
            {status.error && (
              <p className="text-xs text-muted-foreground">{status.error}</p>
            )}
            <button
              onClick={() => resume()}
              disabled={loading}
              className="flex items-center gap-1.5 text-xs px-2.5 py-1 rounded bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 mx-auto"
            >
              <RefreshCw className="h-3 w-3" />
              {m.graph_continue_button()}
            </button>
          </div>
        )}

        {/* Status: not built — texto descritivo; o CTA de build já está na
            barra de ação acima (data-testid="graph-build-btn"), sem
            duplicar botão aqui embaixo. */}
        {!isBuilt && !isRunning && !isPaused && status.status !== "error" && (
          <div className="overflow-y-auto px-3 py-6 text-sm text-muted-foreground text-center space-y-2">
            <Waypoints className="h-8 w-8 mx-auto text-muted-foreground/50" />
            <p className="font-medium text-foreground">{m.graph_not_built()}</p>
            <p className="text-xs">{m.graph_build_description()}</p>
          </div>
        )}

        {/* Status: running */}
        {isRunning && (
          <div className="overflow-y-auto flex flex-col gap-3 px-3 py-4">
            {/* Spinner + label */}
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin shrink-0" />
              <span>{status.step_label ?? m.graph_building()}</span>
            </div>

            {/* Barra de progresso por etapa */}
            {status.step != null && status.step_total != null && (
              <div className="space-y-1">
                <div className="h-1 bg-muted rounded-full overflow-hidden">
                  <div
                    className="h-full bg-primary/70 rounded-full transition-all duration-500"
                    style={{
                      width: `${Math.round((status.step / status.step_total) * 100)}%`,
                    }}
                  />
                </div>
                <p className="text-xs text-muted-foreground/50 text-right">
                  {status.step}/{status.step_total}
                </p>
              </div>
            )}

            {/* Contador de arquivos */}
            {status.files_total != null && status.files_total > 0 && (
              <p className="text-xs text-muted-foreground/60">
                {m.graph_files_progress({
                  done: status.files_done ?? 0,
                  total: status.files_total,
                })}
              </p>
            )}

            {/* Lista de arquivos — preenche o espaço vertical disponível em vez
                de cortar cedo; mostra todos os arquivos detectados. A bolinha de
                cada arquivo concluído usa a cor da etapa atual (AST → semântica →
                concluído), tornando visível por qual estágio o build está passando. */}
            {status.files_list && status.files_list.length > 0 && (
              <div className="max-h-[55vh] overflow-y-auto space-y-0.5">
                {status.files_list.map((file, i) => {
                  const done = i < (status.files_done ?? 0);
                  return (
                    <div
                      key={i}
                      className={`text-[10px] flex items-center gap-1.5 transition-opacity ${
                        done
                          ? "text-muted-foreground"
                          : "text-muted-foreground/30"
                      }`}
                    >
                      <span
                        className="w-1.5 h-1.5 rounded-full shrink-0 transition-colors"
                        style={{
                          backgroundColor: done
                            ? graphStageColor(status.step)
                            : "var(--color-muted-foreground)",
                          opacity: done ? 0.85 : 0.2,
                        }}
                      />
                      <span className="truncate">{file}</span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {/* Built + grafo com dados: a própria aba é o visualizador — canvas
            React nativo (reagraph), com o tema/design system do workbench
            e sem depender de iframe/link externo. */}
        {isBuilt && hasGraphData && (
          <div className="flex-1 min-h-0 flex flex-col">
            {/* Métricas — faixa compacta acima do grafo */}
            <div className="flex gap-4 text-xs text-muted-foreground px-3 py-1.5 border-b border-border/40 shrink-0">
              {status.node_count != null && (
                <span>{m.graph_nodes_count({ n: status.node_count })}</span>
              )}
              {status.edge_count != null && (
                <span>{m.graph_edges_count({ n: status.edge_count })}</span>
              )}
            </div>

            <ContextGraphViewer
              fetchGraphData={fetchGraphData}
              pathBetween={pathBetween}
              onExplainNode={(label) =>
                handleQuestion(`Explique o nó "${label}" no grafo de contexto`)
              }
              onAffectedNode={(label) =>
                queryAffected(label).then((text) => {
                  if (text) handleQuestion(text);
                })
              }
            />

            {/* God nodes + perguntas + relatório — faixa compacta abaixo
                do grafo, rolável, não compete pelo espaço principal. */}
            {(godNodes.length > 0 || questions.length > 0 || report) && (
              <div className="shrink-0 max-h-[30%] overflow-y-auto space-y-3 px-3 py-2 border-t border-border/40">
                {godNodes.length > 0 && (
                  <div>
                    <p className="text-xs font-medium mb-1.5">
                      {m.graph_god_nodes_title()}
                    </p>
                    <div className="flex flex-wrap gap-1.5">
                      {godNodes.map((node) => (
                        <span key={node} className="flex items-center gap-0.5">
                          <button
                            onClick={() =>
                              handleQuestion(
                                `Explique o nó "${node}" no grafo de contexto`,
                              )
                            }
                            className="text-xs px-2 py-0.5 rounded-l-full bg-muted hover:bg-muted/80 text-muted-foreground hover:text-foreground transition-colors"
                          >
                            {node}
                          </button>
                          <button
                            title={m.graph_affected_button()}
                            onClick={() =>
                              queryAffected(node).then((text) => {
                                if (text) handleQuestion(text);
                              })
                            }
                            className="text-xs px-1.5 py-0.5 rounded-r-full bg-muted hover:bg-primary/20 text-muted-foreground hover:text-primary transition-colors border-l border-border/40"
                          >
                            ↯
                          </button>
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                {questions.length > 0 && (
                  <div>
                    <p className="text-xs font-medium mb-1.5">
                      {m.graph_questions_title()}
                    </p>
                    <ul className="space-y-1.5">
                      {questions.map((q) => (
                        <li key={q}>
                          <button
                            onClick={() => handleQuestion(q)}
                            className="text-xs text-left text-muted-foreground hover:text-foreground underline-offset-2 hover:underline transition-colors w-full"
                          >
                            {q}
                          </button>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {report && (
                  <div>
                    <button
                      onClick={() => setShowReport((v) => !v)}
                      className="text-xs text-muted-foreground hover:text-foreground underline-offset-2 hover:underline"
                    >
                      {showReport ? "Ocultar" : m.graph_report_title()}
                    </button>
                    {showReport && (
                      <pre className="mt-2 text-xs whitespace-pre-wrap text-muted-foreground bg-muted/50 rounded p-2 max-h-64 overflow-y-auto">
                        {report}
                      </pre>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* Built, mas nenhum arquivo compatível foi indexado (grafo vazio) —
            não tenta carregar o iframe de um HTML sem grafo nenhum. */}
        {isBuilt && !hasGraphData && (
          <div
            data-testid="graph-empty-result"
            className="overflow-y-auto px-3 py-6 text-sm text-muted-foreground text-center space-y-2"
          >
            <Waypoints className="h-8 w-8 mx-auto text-muted-foreground/50" />
            <p>{m.graph_empty_result()}</p>
          </div>
        )}
      </div>

      {/* Footer: crédito */}
      <div className="px-3 py-1.5 border-t border-border/60 shrink-0">
        <a
          href="https://github.com/safishamsi/graphify"
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs text-muted-foreground/60 hover:text-muted-foreground hover:underline underline-offset-2 transition-colors"
        >
          {m.graph_credit()}
        </a>
      </div>
    </div>
  );
}
