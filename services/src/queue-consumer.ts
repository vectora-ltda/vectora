/**
 * Consumer das duas filas do Worker (`vectora-email` e `vectora-jobs`) —
 * chamado pelo `queue()` do ExportedHandler em src/index.ts. Despacha por
 * `batch.queue` e, dentro de `vectora-jobs`, por `message.body.type`.
 */
import type { Env } from "./gateway/types";
import type { EmailMessage, JobMessage } from "./lib/queue-types";
import { sendEmail } from "./lib/email";
import { hardDeleteOneUser } from "./gdpr/routes";
import { processUpdateTelemetry } from "./updates/worker";
import { recordTelemetryEvent } from "./telemetry/routes";
import { processRagReindex } from "./memory-buckets/routes";

async function handleJob(env: Env, job: JobMessage): Promise<void> {
  switch (job.type) {
    case "gdpr_delete_user":
      await hardDeleteOneUser(env, job.userId);
      return;
    case "update_telemetry":
      await processUpdateTelemetry(env, job);
      return;
    case "telemetry_ingest":
      await recordTelemetryEvent(env, {
        source: job.source,
        eventType: job.eventType,
        payload: job.payload,
      });
      return;
    case "rag_reindex":
      await processRagReindex(env, job.packageId);
      return;
  }
}

export async function handleQueue(
  batch: MessageBatch<unknown>,
  env: Env,
): Promise<void> {
  for (const message of batch.messages) {
    try {
      if (batch.queue === "vectora-email") {
        const email = message.body as EmailMessage;
        await sendEmail(env.RESEND_API_KEY, email);
      } else {
        await handleJob(env, message.body as JobMessage);
      }
      message.ack();
    } catch (err) {
      console.error(`queue-consumer: falha processando ${batch.queue}`, err);
      message.retry();
    }
  }
}
