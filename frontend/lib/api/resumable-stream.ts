/**
 * Retomada do stream de chat depois de uma queda de conexão.
 *
 * Embrulha o stream original: se ele termina sem o evento terminal (`done`)
 * ou falha por transporte, reabre o run pelo cursor do último evento recebido
 * (`ResumeStream`) e segue entregando os eventos que faltavam. Se o servidor
 * não guarda mais os eventos, o comportamento anterior continua: o fim
 * silencioso termina o stream (o chamador reconcilia com o histórico) e a
 * falha de transporte é relançada.
 */
export interface ResumableEvent {
  type: string;
  event_id?: string;
}

export interface ResumeOptions<T extends ResumableEvent> {
  open: (lastEventId: string, signal?: AbortSignal) => AsyncGenerator<T>;
  /** Erro que significa "o servidor não tem mais esses eventos". */
  isUnavailable: (error: unknown) => boolean;
  signal?: AbortSignal;
  maxAttempts?: number;
  delayMs?: (attempt: number) => number;
}

const DEFAULT_ATTEMPTS = 3;
const defaultDelay = (attempt: number) => 400 * 2 ** (attempt - 1);

function isAbort(error: unknown): boolean {
  return (error as { name?: string } | null)?.name === "AbortError";
}

function wait(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal?.aborted) return resolve();
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });
}

export async function* withResume<T extends ResumableEvent>(
  source: AsyncGenerator<T>,
  options: ResumeOptions<T>,
): AsyncGenerator<T> {
  const maxAttempts = options.maxAttempts ?? DEFAULT_ATTEMPTS;
  const delayMs = options.delayMs ?? defaultDelay;
  let lastEventId = "";
  let finished = false;

  const track = (event: T) => {
    if (event.event_id) lastEventId = event.event_id;
    if (event.type === "done") finished = true;
  };

  let failure: unknown;
  try {
    for await (const event of source) {
      track(event);
      yield event;
    }
  } catch (error) {
    if (isAbort(error)) throw error;
    failure = error;
  }

  for (
    let attempt = 1;
    !finished && lastEventId && attempt <= maxAttempts;
    attempt++
  ) {
    if (options.signal?.aborted) break;
    // eslint-disable-next-line no-await-in-loop
    await wait(delayMs(attempt), options.signal);
    if (options.signal?.aborted) break;
    try {
      for await (const event of options.open(lastEventId, options.signal)) {
        track(event);
        yield event;
      }
      failure = undefined;
    } catch (error) {
      if (isAbort(error)) throw error;
      if (options.isUnavailable(error)) break;
      failure ??= error;
    }
  }

  if (failure !== undefined && !finished) throw failure;
}
