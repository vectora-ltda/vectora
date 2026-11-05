/**
 * Slash commands — registry e parser.
 *
 * Apenas comandos cuja ação já existe no Vectora hoje. O dispatch real vive no
 * chat-interface; aqui ficam o registro (para o autocomplete) e helpers puros
 * de parsing, testáveis isoladamente.
 */

export interface SlashCommand {
  name: string;
  description: string;
  usage: string;
  takesArg?: boolean;
}

export interface ParsedSlash {
  name: string;
  arg: string;
}

export function isSlashQuery(input: string): boolean {
  return /^\/[a-z_]*$/i.test(input);
}

export function parseSlashCommand(input: string): ParsedSlash | null {
  const trimmed = input.trim();
  if (!trimmed.startsWith("/")) return null;
  const match = trimmed.match(/^\/([a-z_]+)(?:\s+([\s\S]*))?$/i);
  if (!match) return null;
  return { name: match[1].toLowerCase(), arg: (match[2] ?? "").trim() };
}

export function filterCommands(
  input: string,
  allCommands: SlashCommand[],
): SlashCommand[] {
  if (!input.startsWith("/")) return [];
  const term = input.slice(1).toLowerCase();
  return allCommands.filter((c) => c.name.startsWith(term));
}
