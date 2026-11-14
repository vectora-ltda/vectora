/**
 * Fila de envio do composer: mensagens digitadas enquanto o agente responde.
 *
 * Cada item tem um ID estável (o mesmo `turn_id` enviado ao backend, que
 * deduplica por ele: repetir o envio não duplica a mensagem) e um estado:
 * `waiting` (aguardando a vez), `sending` (em envio), `failed` (a requisição
 * não chegou ao servidor) ou `cancelled` (o usuário interrompeu o envio). Um item só sai da fila quando o servidor confirma;
 * falha preserva o item, e a ordem é mantida: um item `failed` na frente
 * bloqueia os seguintes até ser reenviado ou removido. Um item `cancelled` foi
 * uma escolha do usuário: fica visível para reenviar ou remover, mas não
 * bloqueia os seguintes.
 */
export type QueueStatus = "waiting" | "sending" | "failed" | "cancelled";

interface QueueItem<T> {
  id: string;
  status: QueueStatus;
  payload: T;
}

export interface SendQueue<T> {
  /** Itens na ordem de envio (cópia somente leitura). */
  items(): readonly QueueItem<T>[];
  /** Enfileira; `false` se já existe item com esse ID (idempotente). */
  enqueue(id: string, payload: T): boolean;
  /**
   * Próximo a enviar: o primeiro item que não está cancelado, se estiver
   * `waiting` e nada estiver em envio.
   */
  next(): QueueItem<T> | undefined;
  /** `waiting` → `sending`. */
  start(id: string): boolean;
  /** Servidor confirmou: remove. */
  succeed(id: string): boolean;
  /** Requisição não chegou ao servidor: mantém o item como `failed`. */
  fail(id: string): boolean;
  /** O usuário interrompeu o envio: `sending` → `cancelled`. */
  cancel(id: string): boolean;
  /** `failed` ou `cancelled` → `waiting`, na mesma posição e com o mesmo ID. */
  retry(id: string): boolean;
  /** Remove um item que não está em envio. */
  remove(id: string): boolean;
  /** Move um item `waiting` para a frente da fila. */
  promote(id: string): boolean;
}

export function createSendQueue<T>(): SendQueue<T> {
  let list: QueueItem<T>[] = [];

  const find = (id: string) => list.find((item) => item.id === id);

  return {
    items: () => list.map((item) => ({ ...item })),
    enqueue(id, payload) {
      if (find(id)) return false;
      list = [...list, { id, status: "waiting", payload }];
      return true;
    },
    next() {
      if (list.some((item) => item.status === "sending")) return undefined;
      const head = list.find((item) => item.status !== "cancelled");
      return head?.status === "waiting" ? { ...head } : undefined;
    },
    start(id) {
      const item = find(id);
      if (item?.status !== "waiting") return false;
      item.status = "sending";
      return true;
    },
    succeed(id) {
      const before = list.length;
      list = list.filter((item) => item.id !== id);
      return list.length !== before;
    },
    fail(id) {
      const item = find(id);
      if (item?.status !== "sending") return false;
      item.status = "failed";
      return true;
    },
    cancel(id) {
      const item = find(id);
      if (item?.status !== "sending") return false;
      item.status = "cancelled";
      return true;
    },
    retry(id) {
      const item = find(id);
      if (item?.status !== "failed" && item?.status !== "cancelled") {
        return false;
      }
      item.status = "waiting";
      return true;
    },
    remove(id) {
      const item = find(id);
      if (!item || item.status === "sending") return false;
      list = list.filter((entry) => entry.id !== id);
      return true;
    },
    promote(id) {
      const item = find(id);
      if (item?.status !== "waiting") return false;
      list = [item, ...list.filter((entry) => entry.id !== id)];
      return true;
    },
  };
}
