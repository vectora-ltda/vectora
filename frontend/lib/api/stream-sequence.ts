/**
 * Guarda de ordem dos eventos do stream de chat.
 *
 * O backend numera cada evento de um run com `seq` (a partir de 1) e
 * `event_id` (`<run_id>:<seq>`). O guarda descarta o que não deve alterar a
 * tela: evento repetido, evento reordenado (`seq` menor ou igual ao último já
 * aceito) e qualquer evento depois do terminal (`done`), para uma execução
 * concluída nunca ser reaberta por um retransmitido atrasado. Eventos sem
 * `seq` (backend antigo) passam sempre, até o terminal.
 */
interface SequencedEvent {
  type: string;
  seq?: number;
  event_id?: string;
}

export interface SequenceGuard {
  /** true quando o evento deve ser processado; false quando deve ser ignorado. */
  accept(event: SequencedEvent): boolean;
  /** `seq` do último evento aceito (0 se nenhum trouxe `seq`). */
  cursor(): number;
  /** true depois de aceitar o evento terminal. */
  finished(): boolean;
}

export function createSequenceGuard(): SequenceGuard {
  let lastSeq = 0;
  let terminal = false;
  const seenIds = new Set<string>();

  return {
    accept(event) {
      if (terminal) return false;
      if (typeof event.seq === "number") {
        if (event.seq <= lastSeq) return false;
        lastSeq = event.seq;
      } else if (event.event_id) {
        if (seenIds.has(event.event_id)) return false;
        seenIds.add(event.event_id);
      }
      if (event.type === "done") terminal = true;
      return true;
    },
    cursor: () => lastSeq,
    finished: () => terminal,
  };
}
