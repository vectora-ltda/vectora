export interface VextTheme {
  colorScheme: "light" | "dark";
  reducedMotion: boolean;
}
export type VextCommand = { id: string; title: string; run(): Promise<void> };

export function createCommandRegistry() {
  const commands = new Map<string, VextCommand>();
  return {
    register(command: VextCommand): () => void {
      commands.set(command.id, command);
      return () => commands.delete(command.id);
    },
    get(id: string): VextCommand | undefined {
      return commands.get(id);
    },
    list(): readonly VextCommand[] {
      return [...commands.values()];
    },
  };
}
