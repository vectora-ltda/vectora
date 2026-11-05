/**
 * terminals-store — wrapper de retro-compatibilidade.
 *
 * O estado dos terminais foi unificado ao do workbench em T5.
 * Este módulo agora apenas re-exporta para não quebrar imports antigos
 * (`useTerminalsStore`). Novos consumidores devem usar `useWorkbenchStore`
 * diretamente.
 */

export { useTerminalsStore, type TerminalInstance } from "./workbench-store";
