"use client";

/**
 * SlashCommandMenu
 *
 * Popup de autocomplete exibido ao digitar "/" no início do input. Lista os
 * comandos registrados; selecionar preenche o input com o comando.
 */

import { Slash } from "lucide-react";
import { useEffect, useState } from "react";

import {
  filterCommands,
  isSlashQuery,
  type SlashCommand,
} from "@/lib/constants/slash-commands";
import { m } from "@/lib/paraglide/messages";
import { getTools } from "@/lib/api/vectora-client";

interface SlashCommandMenuProps {
  input: string;
  onSelect: (command: SlashCommand) => void;
}

export function SlashCommandMenu({ input, onSelect }: SlashCommandMenuProps) {
  const [allCommands, setAllCommands] = useState<SlashCommand[]>([]);

  useEffect(() => {
    getTools()
      .then((res) => {
        const cmds: SlashCommand[] = res.tools.map((t) => ({
          name: t.name,
          description: t.description,
          usage: `/${t.name}`,
          takesArg: true,
        }));
        setAllCommands(cmds);
      })
      .catch((err) => {
        console.warn("Failed to fetch tools for slash commands", err);
      });
  }, []);

  // Só aparece enquanto o usuário ainda escolhe o comando (sem espaço/arg).
  if (!isSlashQuery(input)) return null;
  const commands = filterCommands(input, allCommands);
  if (commands.length === 0) return null;

  return (
    <div className="absolute bottom-full left-0 mb-2 w-80 rounded-lg border border-border bg-background shadow-xl py-1 z-50 animate-in fade-in slide-in-from-bottom-2">
      <div className="px-3 py-1.5 text-xs font-medium text-muted-foreground uppercase tracking-wide flex items-center gap-1.5">
        <Slash className="w-3 h-3" />
        {m.slash_title()}
      </div>
      <div className="max-h-60 overflow-y-auto">
        {commands.map((cmd) => (
          <button
            key={cmd.name}
            type="button"
            onMouseDown={(e) => {
              // onMouseDown (não onClick) evita blur do textarea antes da seleção.
              e.preventDefault();
              onSelect(cmd);
            }}
            className="w-full flex flex-col gap-1 px-3 py-2 text-sm hover:bg-accent text-left transition-colors"
          >
            <span className="font-mono text-foreground font-semibold">
              {cmd.usage}
            </span>
            <span className="text-xs text-muted-foreground line-clamp-2">
              {cmd.description}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
