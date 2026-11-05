/**
 * fetchJsonWithRetry — leitura JSON com retry exponencial para chamadas
 * não-destrutivas (GET / revalidação em background).
 *
 * Política:
 *   - até `retries` tentativas extras (default 2 → 3 tentativas no total);
 *   - backoff exponencial a partir de `backoffMs` (default 300ms: 300, 600,
 *     1200…) com jitter de até 100ms para evitar thundering herd;
 *   - **não** retenta 4xx (erro do cliente — repetir não muda o resultado),
 *     nem `AbortError` (cancelamento intencional);
 *   - retenta 5xx, falhas de rede (`TypeError`/fetch lançou) e timeouts.
 *
 * Uso recomendado: apenas para leituras (`GET`/RPCs idempotentes de listagem).
 * Ações que mutam estado (`POST` de criação/exclusão) não devem usar retry
 * automático — duplicar a tentativa pode duplicar o efeito colateral.
 *
 * @example
 *   const data = await fetchJsonWithRetry<{ workspaces: WorkspaceInfo[] }>(
 *     "/workspaces",
 *   );
 */

export interface FetchRetryOptions {
  /** Tentativas extras após a primeira falha (default: 2). */
  retries?: number;
  /** Atraso base do backoff exponencial em ms (default: 300). */
  backoffMs?: number;
  /** Sinal de cancelamento — propagado ao fetch; aborts não são retentados. */
  signal?: AbortSignal;
}

/** HTTP error com status preservado — permite ao chamador inspecionar 4xx/5xx. */
export class FetchHttpError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "FetchHttpError";
    this.status = status;
  }
}

function isAbort(err: unknown): boolean {
  return (err as { name?: string } | null)?.name === "AbortError";
}

/** 4xx não é retentável — o servidor já disse que o pedido está errado. */
function isClientError(err: unknown): boolean {
  return err instanceof FetchHttpError && err.status >= 400 && err.status < 500;
}

/**
 * Tenta extrair um status HTTP de mensagens no formato
 * `"<path> failed (<status>): <body>"` — convenção usada por `postRpc` em
 * `vectora-client.ts`. Retorna `null` quando a mensagem não segue o padrão
 * (ex.: erro de rede, onde não há status — esses devem ser retentados).
 */
function statusFromMessage(message: string): number | null {
  const match = /\((\d{3})\)/.exec(message);
  return match ? Number(match[1]) : null;
}

/** `true` quando o erro tem status 4xx embutido na mensagem (não retentar). */
function isClientErrorMessage(err: unknown): boolean {
  if (!(err instanceof Error)) return false;
  const status = statusFromMessage(err.message);
  return status !== null && status >= 400 && status < 500;
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        reject(new DOMException("Aborted", "AbortError"));
      },
      { once: true },
    );
  });
}

/**
 * `fetch` + parse JSON com retry exponencial. Lança `FetchHttpError` em
 * respostas não-OK (preservando `status`) e propaga erros de rede/abort.
 */
export async function fetchJsonWithRetry<T = unknown>(
  url: string,
  init?: RequestInit,
  opts?: FetchRetryOptions,
): Promise<T> {
  const retries = opts?.retries ?? 2;
  const backoffMs = opts?.backoffMs ?? 300;
  const signal = opts?.signal ?? init?.signal ?? undefined;

  let attempt = 0;
  // Retry sequencial intencional — cada tentativa só faz sentido depois que a
  // anterior falhou (não há nada para paralelizar com Promise.all aqui).
  /* eslint-disable no-await-in-loop */
  // eslint-disable-next-line no-constant-condition
  while (true) {
    try {
      const res = await fetch(url, { ...init, signal });
      if (!res.ok) {
        const text = await res.text().catch(() => "");
        throw new FetchHttpError(res.status, text || `HTTP ${res.status}`);
      }
      return (await res.json()) as T;
    } catch (err) {
      if (isAbort(err) || isClientError(err) || attempt >= retries) {
        throw err;
      }
      // Backoff exponencial com jitter — evita que clientes re-sincronizem
      // e batam no servidor todos juntos após uma instabilidade comum.
      const delay = backoffMs * 2 ** attempt + Math.random() * 100;
      attempt += 1;
      await sleep(delay, signal);
    }
  }
  /* eslint-enable no-await-in-loop */
}

/**
 * Variante genérica de `fetchJsonWithRetry` para chamadas que não usam
 * `fetch` diretamente — ex.: RPCs em `vectora-client.ts::postRpc`, cujo
 * erro chega como `Error("<path> failed (<status>): <body>")`.
 *
 * Mesma política (retries/backoff/jitter); 4xx detectado via regex no
 * texto do erro não é retentado. Use apenas para operações idempotentes
 * (listagens, leituras) — nunca para criação/mutação.
 *
 * @example
 *   const { threads } = await withRetry(() => listThreads(50));
 */
export async function withRetry<T>(
  fn: () => Promise<T>,
  opts?: Omit<FetchRetryOptions, "signal"> & { signal?: AbortSignal },
): Promise<T> {
  const retries = opts?.retries ?? 2;
  const backoffMs = opts?.backoffMs ?? 300;
  const signal = opts?.signal;

  let attempt = 0;
  // Mesma justificativa de fetchJsonWithRetry — tentativas são sequenciais
  // por natureza (a próxima só ocorre se a anterior falhar).
  /* eslint-disable no-await-in-loop */
  // eslint-disable-next-line no-constant-condition
  while (true) {
    try {
      return await fn();
    } catch (err) {
      if (isAbort(err) || isClientErrorMessage(err) || attempt >= retries) {
        throw err;
      }
      const delay = backoffMs * 2 ** attempt + Math.random() * 100;
      attempt += 1;
      await sleep(delay, signal);
    }
  }
  /* eslint-enable no-await-in-loop */
}
