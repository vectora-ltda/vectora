"use client";

/**
 * AgentConfig — tipo compartilhado de configuração do agente (modelo, repos
 * etc). O dialog "Configurações do Chat" foi removido: suas opções
 * (modelo, tema, idioma, tool calls, atalhos) agora vivem no Settings
 * completo (Settings → Preferências), acessível pelo ícone de engrenagem
 * no header.
 */

export interface AgentConfig {
  model: string;
  /** @deprecated — não tem efeito; remover dos consumidores */
  recursionLimit?: number;
  /** @deprecated — não tem efeito; remover dos consumidores */
  agentType?: string;
  repos?: string[];
}
