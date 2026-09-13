import { useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { ChevronDown, Plus } from "lucide-react";
import type { Thread } from "@/lib/hooks/threads";
import { m } from "@/lib/paraglide/messages";
import { MOTION_INSTANT, PANEL_TRANSITION } from "@/lib/motion/transitions";

interface SessionSwitcherProps {
  threads: Thread[];
  currentThreadId: string;
  onSelectThread: (id: string) => void;
  onNewSession: () => void;
}

export function SessionSwitcher({
  threads,
  currentThreadId,
  onSelectThread,
  onNewSession,
}: SessionSwitcherProps) {
  const [open, setOpen] = useState(false);
  const reducedMotion = useReducedMotion();

  const current = threads.find((t) => t.thread_id === currentThreadId);
  const label = current?.metadata?.title || m.ide_session_untitled();

  return (
    <div className="relative min-w-0 flex-1 min-h-[var(--app-header-height)] flex items-center">
      <button
        onClick={() => setOpen((v) => !v)}
        aria-label={m.ide_session_switcher_label()}
        aria-expanded={open}
        className="flex w-full items-center gap-1 rounded px-1.5 py-1 text-xs text-foreground/70 hover:bg-muted/50 hover:text-foreground transition-colors"
      >
        <span className="truncate flex-1 text-left">{label}</span>
        <ChevronDown className="w-3 h-3 shrink-0 opacity-60" />
      </button>

      <AnimatePresence initial={false}>
        {open && (
          <>
            <div
              aria-hidden="true"
              className="fixed inset-0 z-40"
              onClick={() => setOpen(false)}
            />
            <motion.div
              key="dropdown"
              role="listbox"
              aria-label={m.ide_session_switcher_label()}
              initial={{ opacity: 0, scale: 0.95, y: -4 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.95, y: -4 }}
              transition={reducedMotion ? MOTION_INSTANT : PANEL_TRANSITION}
              style={{ transformOrigin: "top left" }}
              className="absolute top-full left-0 mt-1 z-50 w-56 rounded-md border border-border/60 bg-popover shadow-md overflow-hidden"
            >
              <div className="max-h-60 overflow-y-auto">
                {threads.length === 0 ? (
                  <div className="px-3 py-2 text-xs text-muted-foreground">
                    {m.sidebar_no_conversations()}
                  </div>
                ) : (
                  threads.map((t) => (
                    <button
                      key={t.thread_id}
                      role="option"
                      aria-selected={t.thread_id === currentThreadId}
                      onClick={() => {
                        onSelectThread(t.thread_id);
                        setOpen(false);
                      }}
                      className={`w-full text-left px-3 py-2 text-xs truncate hover:bg-muted/50 transition-colors ${
                        t.thread_id === currentThreadId
                          ? "text-primary font-medium"
                          : "text-foreground/80"
                      }`}
                    >
                      {t.metadata?.title || m.ide_session_untitled()}
                    </button>
                  ))
                )}
              </div>
              <div className="border-t border-border/40">
                <button
                  onClick={() => {
                    onNewSession();
                    setOpen(false);
                  }}
                  className="w-full flex items-center gap-2 px-3 py-2 text-xs text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
                >
                  <Plus className="w-3 h-3" aria-hidden="true" />
                  {m.ide_session_new()}
                </button>
              </div>
            </motion.div>
          </>
        )}
      </AnimatePresence>
    </div>
  );
}
