/**
 * ID local de uma sessão ainda não persistida (/session/new).
 *
 * TanStack Router reaproveita a MESMA instância de componente entre
 * /session/<uuid-real> e /session/new (mesmo padrão de rota `$threadId`) —
 * um `useState` com inicializador preguiçoso só gera o id na primeira
 * montagem. Clicar em "Nova sessão" a partir de uma sessão real navega o
 * param de volta pra "new" sem remontar o componente, então esse id nunca
 * era regerado: a "nova" sessão reciclava o uuid antigo (já promovido a
 * thread real por clearNew()), fazendo a mensagem seguinte ser enviada pra
 * thread errada — e, em outros casos, deixando o usuário preso num id
 * client-side que o backend nunca viu (getHistory 404 → tela "Not Found").
 */

import { useMemo } from "react";
import { markAsNew } from "@/lib/stores/new-thread-registry";
import {
  markWorkspaceChosen,
  markCreateNewWorkspace,
} from "@/lib/stores/workspace-choice-registry";
import {
  consumeWorkspacePreChosen,
  consumeCreateNewWorkspacePreNav,
} from "@/lib/stores/new-session-signal";
import { safeRandomUUID } from "@/lib/utils/uuid";

/**
 * Gera um novo id de sessão local e consome os sinais one-shot de
 * workspace pré-escolhido antes da navegação (handleConfirmNewChat).
 */
export function generateLocalNewId(): string {
  const id = safeRandomUUID();
  markAsNew(id);
  if (consumeWorkspacePreChosen()) markWorkspaceChosen(id);
  if (consumeCreateNewWorkspacePreNav()) markCreateNewWorkspace(id);
  return id;
}

/**
 * Devolve o id local da sessão nova, regerando sempre que ``routeParam``
 * transiciona de volta pra ``"new"`` a partir de outro valor (não só na
 * primeira montagem). Fora do modo "new", devolve string vazia.
 */
export function useNewSessionId(routeParam: string): string {
  // Router mantém a instância da rota entre /session/:id e /session/new.
  // A dependência no parâmetro gera um id novo ao voltar para "new" e mantém
  // o mesmo id enquanto a rota não muda, sem ler refs durante o render.
  return useMemo(
    () => (routeParam === "new" ? generateLocalNewId() : ""),
    [routeParam],
  );
}
