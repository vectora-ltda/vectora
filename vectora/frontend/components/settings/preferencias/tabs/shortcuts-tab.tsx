"use client";

import { useEffect } from "react";

import {
  formatShortcut,
  useAvailableShortcuts,
} from "@/hooks/useKeyboardShortcuts";
import { m } from "@/lib/paraglide/messages";
import { useLibraryStore } from "@/lib/stores/library-store";

/** Dedicated shortcut settings surface for native and VEXT-contributed commands. */
export function ShortcutsTab() {
  const groups = useAvailableShortcuts();
  const extensions = useLibraryStore((s) => s.extensionItems);
  const ensureExtensions = useLibraryStore((s) => s.ensureExtensionsLoaded);
  useEffect(() => {
    void ensureExtensions();
  }, [ensureExtensions]);
  const extensionShortcuts = extensions.flatMap((extension) =>
    (extension.contributions?.shortcuts ?? []).map((shortcut) => ({
      category: extension.name,
      description: shortcut.title,
      key: shortcut.keybinding ?? "",
      extension: extension.name,
    })),
  );
  return (
    <div className="space-y-5">
      <div>
        <h3 className="text-base font-medium">
          {m.settings_shortcuts_title()}
        </h3>
        <p className="mt-1 text-sm text-muted-foreground">
          {m.settings_shortcuts_description()}
        </p>
      </div>
      {Object.entries(groups).map(([category, shortcuts]) => (
        <section key={category} className="space-y-2">
          <h4 className="text-sm font-medium">{category}</h4>
          <div className="divide-y rounded-md border border-border/60">
            {shortcuts.map((shortcut) => (
              <div
                key={`${shortcut.category}:${shortcut.key}:${shortcut.description}`}
                className="flex items-center justify-between gap-4 px-3 py-2"
              >
                <span className="min-w-0 truncate text-sm">
                  {shortcut.description}
                </span>
                <kbd className="shrink-0 rounded border bg-muted px-2 py-0.5 text-xs">
                  {formatShortcut(shortcut)}
                </kbd>
              </div>
            ))}
          </div>
        </section>
      ))}
      {extensionShortcuts.length > 0 && (
        <section className="space-y-2">
          <h4 className="text-sm font-medium">
            {m.settings_shortcuts_extensions()}
          </h4>
          <div className="divide-y rounded-md border border-border/60">
            {extensionShortcuts.map((shortcut) => (
              <div
                key={`${shortcut.extension}:${shortcut.description}`}
                className="flex items-center justify-between gap-4 px-3 py-2"
              >
                <span className="min-w-0 truncate text-sm">
                  {shortcut.description}
                </span>
                <kbd className="shrink-0 rounded border bg-muted px-2 py-0.5 text-xs">
                  {shortcut.key || "—"}
                </kbd>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
