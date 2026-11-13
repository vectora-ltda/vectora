/**
 * Desfecho de um run, a partir do `stopped_reason` do evento terminal `done`.
 *
 * `completed` (e vazio, de backend antigo) não mostra indicador; `error` já é
 * exibido pelo próprio erro da resposta, então também não duplica.
 */
export type RunOutcome = "cancelled" | "limit" | null;

export function runOutcomeOf(stoppedReason: string | undefined): RunOutcome {
  switch (stoppedReason) {
    case "cancelled":
    case "interrupted":
      return "cancelled";
    case "max_iterations":
    case "loop_cap_exceeded":
      return "limit";
    default:
      return null;
  }
}
