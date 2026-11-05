export type AppMode = "assistant" | "ide" | "kanban";
export type ShellSlot =
  "sessions" | "workbench" | "chat" | "canvas" | "kanban" | "empty";

export interface ModeComposition {
  left: ShellSlot;
  center: ShellSlot;
  right: ShellSlot | null;
}

export const MODE_COMPOSITIONS: Record<AppMode, ModeComposition> = {
  assistant: { left: "sessions", center: "chat", right: "workbench" },
  ide: { left: "workbench", center: "canvas", right: "chat" },
  kanban: { left: "sessions", center: "kanban", right: null },
};

export function getModeComposition(mode: AppMode): ModeComposition {
  return MODE_COMPOSITIONS[mode];
}
