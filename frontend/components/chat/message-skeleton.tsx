/**
 * MessageSkeleton — Loading skeleton
 *
 * Exibido enquanto o histórico de uma thread está carregando.
 * Simula a forma visual de 3 mensagens alternadas (user + assistant + assistant).
 */

import { memo } from "react";

function SkeletonLine({
  width,
  height = "h-3",
}: {
  width: string;
  height?: string;
}) {
  return (
    <div
      className={`${height} ${width} rounded-full bg-muted/60 animate-pulse`}
    />
  );
}

/** Skeleton de uma mensagem do usuário (alinhada à direita) */
function UserMessageSkeleton() {
  return (
    <div className="flex justify-end">
      <div className="max-w-[70%] space-y-2 rounded-2xl rounded-tr-sm bg-muted/40 px-4 py-3">
        <SkeletonLine width="w-48" />
        <SkeletonLine width="w-32" />
      </div>
    </div>
  );
}

/** Skeleton de uma mensagem do assistente (alinhada à esquerda) */
function AssistantMessageSkeleton({ lines = 3 }: { lines?: number }) {
  const widths = ["w-full", "w-4/5", "w-3/5", "w-2/3", "w-1/2"];
  return (
    <div className="flex items-start gap-3">
      {/* Avatar placeholder */}
      <div className="h-7 w-7 shrink-0 rounded-full bg-muted/60 animate-pulse" />
      <div className="flex-1 space-y-2 pt-1">
        {Array.from({ length: lines }).map((_, i) => (
          <SkeletonLine key={i} width={widths[i % widths.length] ?? "w-full"} />
        ))}
      </div>
    </div>
  );
}

/**
 * Conjunto de skeletons que simula 3 mensagens de uma conversa.
 * Sempre exibe: user → assistant (longa) → assistant (curta).
 */
export const MessageSkeletons = memo(function MessageSkeletons() {
  return (
    <div className="w-full max-w-4xl mx-auto px-4 sm:px-6 py-6 sm:py-8 space-y-6">
      <UserMessageSkeleton />
      <AssistantMessageSkeleton lines={4} />
      <UserMessageSkeleton />
      <AssistantMessageSkeleton lines={3} />
      <UserMessageSkeleton />
      <AssistantMessageSkeleton lines={2} />
    </div>
  );
});

/**
 * Alias — padroniza o nome `MessageListSkeleton` entre os skeletons do
 * produto (`ThreadListSkeleton`, `FileTreeSkeleton`, `GitSkeleton`,
 * `MessageListSkeleton`). Mantém `MessageSkeletons` para não quebrar imports
 * existentes.
 */
export const MessageListSkeleton = MessageSkeletons;
