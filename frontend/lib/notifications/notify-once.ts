/**
 * Alertas idempotentes.
 *
 * Cada alerta tem uma chave ligada à thread, ao turno e ao evento
 * (`thread:turno:evento`). Reconexão, retomada do stream ou reenvio da fila com
 * o mesmo turno chegam à mesma chave, então o usuário vê no máximo um alerta.
 * O canal é explícito e a ausência dele nunca é registrada como entrega.
 */

export type NotificationChannel = "local" | "in-app" | "push";

export type NotificationOutcome =
  /** O canal confirmou a entrega e a chave ficou registrada. */
  | "delivered"
  /** A chave já tinha sido entregue; nada foi enviado de novo. */
  | "duplicate"
  /** O canal não está disponível ou recusou: não conta como entregue. */
  | "not_delivered";

const STORAGE_KEY = "vectora:delivered-notifications";
const MAX_KEYS = 200;

/** Fallback em memória quando o `sessionStorage` não está disponível. */
const memory: string[] = [];

function readKeys(): string[] {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    if (Array.isArray(parsed)) {
      return parsed.filter((item): item is string => typeof item === "string");
    }
  } catch {
    // sessionStorage indisponível ou corrompido: usa o fallback em memória.
  }
  return memory;
}

function writeKeys(keys: string[]): void {
  const bounded = keys.slice(-MAX_KEYS);
  memory.splice(0, memory.length, ...bounded);
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(bounded));
  } catch {
    // Só o fallback em memória vale nesta sessão.
  }
}

export function notificationKey(
  threadId: string,
  turnId: string,
  event: string,
): string {
  return `${threadId}:${turnId}:${event}`;
}

/** `true` se a chave já foi entregue nesta sessão. */
export function wasDelivered(key: string): boolean {
  return readKeys().includes(key);
}

/**
 * Entrega o alerta uma única vez por chave.
 *
 * `deliver` devolve `true` só quando o canal de fato entregou (por exemplo,
 * a permissão do navegador estava concedida). Só então a chave é registrada.
 */
export function notifyOnce(
  key: string,
  deliver: () => boolean,
): NotificationOutcome {
  if (wasDelivered(key)) return "duplicate";
  let delivered = false;
  try {
    delivered = deliver();
  } catch {
    delivered = false;
  }
  if (!delivered) return "not_delivered";
  writeKeys([...readKeys(), key]);
  return "delivered";
}

/** Alerta do sistema (canal `local`); não entrega sem permissão concedida. */
export function deliverLocalNotification(title: string, body: string): boolean {
  if (
    typeof Notification === "undefined" ||
    Notification.permission !== "granted"
  ) {
    return false;
  }
  const alert = new Notification(title, { body, icon: "/vectora.svg" });
  return alert !== undefined;
}
