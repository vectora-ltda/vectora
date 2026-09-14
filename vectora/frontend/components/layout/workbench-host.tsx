import type { ReactNode } from "react";
import { mDyn } from "@/lib/i18n-dyn";

interface WorkbenchHostProps {
  rail: ReactNode;
  panel: ReactNode;
  side?: "left" | "right";
}

/** Composes the workbench rail and active panel without owning their state. */
export function WorkbenchHost({
  rail,
  panel,
  side = "left",
}: WorkbenchHostProps) {
  return (
    <section
      aria-label={mDyn("workbench.title")}
      className="flex min-h-0 min-w-0 flex-1 overflow-hidden"
    >
      {side === "left" ? rail : panel}
      {side === "left" ? panel : rail}
    </section>
  );
}
