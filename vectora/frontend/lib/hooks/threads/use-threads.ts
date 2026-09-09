/**
 * Tipos de thread — usados pela sidebar, session-switcher e
 * client-config. O hook `useThreads` que vivia aqui foi removido: a rota
 * principal usa `useThreadsQuery` (`lib/queries/threads.ts`), e nenhum
 * componente ativo consumia esta versão (só os tipos abaixo).
 */

export interface ClientProfile {
  id: string;
  label?: string;
  avatarColor?: string;
}

export interface ThreadMetadata {
  user_id: string;
  title?: string;
  lastMessage?: string;
  client?: ClientProfile;
  [key: string]: unknown;
}

/** Thread no formato esperado pelos componentes (compatível com a API antiga). */
export interface Thread {
  thread_id: string;
  created_at: string;
  updated_at: string;
  metadata: ThreadMetadata;
  values?: Record<string, unknown>;
  /** Workspace físico associado à sessão (P3 — sidebar pasta=workspace). */
  workspace_id?: string;
  /** Modo da sessão: "chat" | "dev" (default "dev"). */
  mode?: string;
  /** Sessão fixada — aparece no topo da lista da sidebar. */
  pinned?: boolean;
  /** Mensagens recebidas desde a última leitura confirmada. */
  unread_count?: number;
}
