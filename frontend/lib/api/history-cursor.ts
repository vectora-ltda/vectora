/**
 * O cursor de histórico apontava para uma mensagem que já não existe (por
 * exemplo, removida por um rewind). O cliente descarta o cursor e volta a
 * paginar por posição.
 */
export class HistoryCursorGoneError extends Error {
  constructor() {
    super("cursor de histórico não existe mais");
    this.name = "HistoryCursorGoneError";
  }
}
