"use client";

import { memo, useEffect, useRef, useState } from "react";
import { BookOpen, Flag } from "lucide-react";
import { m } from "@/lib/paraglide/messages";
import { useSettingsStore } from "@/lib/stores/settings-store";
import { useHydrated } from "@/lib/hooks/use-hydrated";
import { submitFeedback } from "@/lib/api/vectora-client";

const LABEL_THRESHOLD = 200;

export const SidebarFooter = memo(function SidebarFooter() {
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<"bug" | "suggestion">("bug");
  const [description, setDescription] = useState("");
  const [status, setStatus] = useState("");
  const [statusType, setStatusType] = useState<"status" | "alert">("status");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLFormElement>(null);
  const sidebarWidth = useSettingsStore((s) => s.sidebarWidth);
  const hydrated = useHydrated();
  const showLabel = hydrated && sidebarWidth >= LABEL_THRESHOLD;

  useEffect(() => {
    if (!open) return;
    dialogRef.current
      ?.querySelector<HTMLElement>("select, textarea, button")
      ?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
      if (event.key !== "Tab") return;
      const dialog = dialogRef.current;
      if (!dialog) return;
      const focusable = Array.from(
        dialog.querySelectorAll<HTMLElement>(
          'button, select, textarea, input, a[href], [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((element) => !element.hasAttribute("disabled"));
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open]);

  useEffect(() => {
    if (!open) triggerRef.current?.focus();
  }, [open]);

  return (
    <div className="bg-gradient-to-t from-sidebar-accent/10 via-sidebar-accent/5 to-transparent pt-1.5 pb-0">
      <div className="flex items-center gap-1 px-2 py-1.5">
        <a
          href="https://docs.vectora.company"
          target="_blank"
          rel="noopener noreferrer"
          title={m.sidebar_documentation()}
          className="flex-1 min-w-0 flex items-center gap-1.5 px-2 py-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-sidebar-accent/20 transition-colors duration-150"
        >
          <BookOpen className="w-3.5 h-3.5 shrink-0" />
          {showLabel && (
            <span className="text-xs truncate">{m.sidebar_docs()}</span>
          )}
        </a>
        <button
          type="button"
          ref={triggerRef}
          onClick={() => setOpen(true)}
          title={m.sidebar_feedback()}
          className="flex-1 min-w-0 flex items-center gap-1.5 px-2 py-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-sidebar-accent/20 transition-colors duration-150"
        >
          <Flag className="w-3.5 h-3.5 shrink-0" />
          {showLabel && (
            <span className="text-xs truncate">{m.sidebar_feedback()}</span>
          )}
        </button>
      </div>
      {open && (
        <div
          className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4"
          role="presentation"
        >
          <form
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="feedback-dialog-title"
            className="w-full max-w-md space-y-3 rounded-lg bg-background p-5 shadow-xl"
            onSubmit={async (event) => {
              event.preventDefault();
              if (!description.trim()) {
                setStatusType("alert");
                return setStatus(m.feedback_required());
              }
              setIsSubmitting(true);
              try {
                await submitFeedback({
                  kind,
                  description,
                  include_context: true,
                  context: {
                    route: window.location.pathname,
                    platform: navigator.platform,
                  },
                });
                setStatusType("status");
                setStatus(m.feedback_sent());
                setDescription("");
              } catch (error) {
                setStatusType("alert");
                setStatus(
                  error instanceof Error && error.message === "rate_limited"
                    ? m.feedback_rate_limited()
                    : m.feedback_error(),
                );
              } finally {
                setIsSubmitting(false);
              }
            }}
          >
            <h2 id="feedback-dialog-title" className="text-sm font-semibold">
              {m.feedback_title()}
            </h2>
            <label htmlFor="feedback-kind" className="sr-only">
              {m.feedback_title()}
            </label>
            <select
              id="feedback-kind"
              className="w-full rounded border bg-background p-2"
              value={kind}
              onChange={(event) =>
                setKind(event.target.value as "bug" | "suggestion")
              }
            >
              <option value="bug">{m.feedback_bug()}</option>
              <option value="suggestion">{m.feedback_suggestion()}</option>
            </select>
            <label htmlFor="feedback-description" className="sr-only">
              {m.feedback_required()}
            </label>
            <textarea
              id="feedback-description"
              className="min-h-28 w-full rounded border bg-background p-2"
              required
              maxLength={5000}
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
            {status && (
              <p
                role={statusType}
                aria-live={statusType === "alert" ? "assertive" : "polite"}
                className="text-xs text-muted-foreground"
              >
                {status}
              </p>
            )}
            <div className="flex justify-end gap-2">
              <button
                type="button"
                className="rounded px-3 py-1 text-sm"
                onClick={() => setOpen(false)}
              >
                {m.feedback_cancel()}
              </button>
              <button
                type="submit"
                disabled={isSubmitting}
                aria-busy={isSubmitting}
                className="rounded bg-primary px-3 py-1 text-sm text-primary-foreground"
              >
                {m.feedback_send()}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
});
