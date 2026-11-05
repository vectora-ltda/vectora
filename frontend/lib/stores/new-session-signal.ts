import { useWorkspacesStore } from "./workspaces-store";

/**
 * Sinal one-shot: set antes de navegar para /session/new quando o workspace
 * já foi confirmado no diálogo, para que a rota destino não reabra o seletor.
 */
let preChosenFlag = false;

export function signalWorkspacePreChosen(): void {
  preChosenFlag = true;
}

export function consumeWorkspacePreChosen(): boolean {
  const val = preChosenFlag;
  preChosenFlag = false;
  return val;
}

/**
 * Mesmo padrão, pra "criar novo workspace": setado ao sair de uma sessão
 * existente pro modal "Nova conversa" → "criar novo", antes de navegar pra
 * /session/new — nesse ponto o id da conversa nova ainda não existe (só é
 * gerado no destino), então não dá pra marcar `markCreateNewWorkspace(id)`
 * direto (ver workspace-choice-registry.ts). Consumido no mesmo lugar que
 * `consumeWorkspacePreChosen`, assim que o id novo é gerado.
 */
let createNewPreNavFlag = false;

export function signalCreateNewWorkspacePreNav(): void {
  createNewPreNavFlag = true;
}

export function consumeCreateNewWorkspacePreNav(): boolean {
  const val = createNewPreNavFlag;
  createNewPreNavFlag = false;
  return val;
}

/**
 * Decisão única de "o que fazer com a escolha do modal Nova conversa" antes
 * de navegar pra /session/new — usada por TODO caller que sai de uma tela
 * sem thread ativa (tela inicial, ou uma sessão existente) rumo a uma nova.
 * Existiu duplicada em index.tsx e $threadId.tsx até um bug real: a cópia da
 * tela inicial nunca chamava signalCreateNewWorkspacePreNav, então "criar
 * novo workspace" ali tinha o mesmo efeito de não escolher nada. Centralizado
 * aqui pra não poder mais divergir — quem precisar desse fluxo importa isto,
 * não reimplementa.
 */
export function signalWorkspaceChoiceForNewSession(
  workspaceId: string | null,
): void {
  signalWorkspacePreChosen();
  if (workspaceId) {
    void useWorkspacesStore.getState().setActive(workspaceId);
  } else {
    signalCreateNewWorkspacePreNav();
  }
}
