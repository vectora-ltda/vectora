"use client";

import {
  ALL_GRAPH_FILE_TYPES,
  type GraphFileType,
  useContextGraphSettingsStore,
} from "@/lib/stores/context-graph-settings-store";
import { m } from "@/lib/paraglide/messages";

/** Formulário de preferências do Context Graph, reutilizável em duas superfícies. */
export function ContextGraphSettingsForm() {
  const fileTypes = useContextGraphSettingsStore((s) => s.fileTypes);
  const graphMode = useContextGraphSettingsStore((s) => s.mode);
  const toggleFileType = useContextGraphSettingsStore((s) => s.toggleFileType);
  const setGraphMode = useContextGraphSettingsStore((s) => s.setMode);

  return (
    <div className="flex min-w-0 flex-col gap-4 text-xs">
      <div>
        <p className="font-medium text-foreground">
          {m.graph_settings_filetypes()}
        </p>
        <p className="mt-0.5 text-[10px] text-muted-foreground">
          {m.graph_settings_filetypes_help()}
        </p>
        <div className="mt-1.5 flex flex-col gap-1">
          {ALL_GRAPH_FILE_TYPES.map((type: GraphFileType) => (
            <label
              key={type}
              className="flex min-w-0 items-center gap-2 cursor-pointer select-none"
            >
              <input
                type="checkbox"
                checked={fileTypes.includes(type)}
                onChange={() => toggleFileType(type)}
                className="accent-[var(--color-primary)]"
              />
              <span className="break-words text-foreground">
                {type === "code"
                  ? m.graph_filetype_code()
                  : type === "document"
                    ? m.graph_filetype_document()
                    : m.graph_filetype_paper()}
              </span>
            </label>
          ))}
        </div>
      </div>
      <div>
        <p className="font-medium text-foreground">{m.graph_settings_mode()}</p>
        <div className="mt-1.5 flex flex-col gap-1">
          {(["semantic", "ast"] as const).map((mode) => (
            <label
              key={mode}
              className="flex min-w-0 items-center gap-2 cursor-pointer select-none"
            >
              <input
                type="radio"
                name="graph-mode"
                checked={graphMode === mode}
                onChange={() => setGraphMode(mode)}
                className="accent-[var(--color-primary)]"
              />
              <span className="break-words text-foreground">
                {mode === "semantic"
                  ? m.graph_mode_semantic()
                  : m.graph_mode_ast()}
              </span>
            </label>
          ))}
        </div>
      </div>
    </div>
  );
}
