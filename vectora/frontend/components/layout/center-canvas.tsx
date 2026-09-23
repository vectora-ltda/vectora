import type { ReactNode } from "react";
import { mDyn } from "@/lib/i18n-dyn";

export interface CenterCanvasTab {
  /** Stable unique key for this tab within the CenterCanvas instance. */
  id: string;
  label: string;
  content: ReactNode;
}

const EMPTY_TABS: CenterCanvasTab[] = [];

function tabDomId(id: string, index: number): string {
  return `vectora-center-canvas-tab-${id.replace(/[^a-zA-Z0-9_-]/g, "-")}-${index}`;
}

interface CenterCanvasProps {
  tabs?: CenterCanvasTab[];
  activeTab?: string;
  onTabChange?: (id: string) => void;
  children?: ReactNode;
}

/** Host for editor, Git, plan and future document tabs in the IDE center. */
export function CenterCanvas({
  tabs = EMPTY_TABS,
  activeTab,
  onTabChange,
  children,
}: CenterCanvasProps) {
  const seenTabIds = new Set<string>();
  for (const tab of tabs) {
    if (seenTabIds.has(tab.id)) {
      throw new Error(`CenterCanvas tabs must have unique ids: "${tab.id}"`);
    }
    seenTabIds.add(tab.id);
  }
  const selected = tabs.find((tab) => tab.id === activeTab) ?? tabs[0];
  const selectedIndex = selected ? tabs.indexOf(selected) : -1;
  const tabpanelId = "vectora-center-canvas-panel";
  return (
    <section
      aria-label={mDyn("ide.canvas")}
      className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden"
    >
      {tabs.length > 0 && (
        <div
          role="tablist"
          aria-label={mDyn("ide.documents.open")}
          className="flex shrink-0 border-b border-border/60"
        >
          {tabs.map((tab, index) => (
            <button
              key={`${tab.id}-${index}`}
              type="button"
              role="tab"
              id={tabDomId(tab.id, index)}
              aria-controls={tabpanelId}
              aria-selected={selectedIndex === index}
              onClick={() => onTabChange?.(tab.id)}
              className="px-3 py-2 text-sm text-muted-foreground data-[active=true]:text-foreground"
              data-active={selectedIndex === index}
            >
              {tab.label}
            </button>
          ))}
        </div>
      )}
      <div
        role={tabs.length > 0 ? "tabpanel" : undefined}
        id={tabpanelId}
        aria-labelledby={
          selected ? tabDomId(selected.id, selectedIndex) : undefined
        }
        className="flex min-h-0 min-w-0 flex-1 overflow-hidden"
      >
        {selected?.content ?? children}
      </div>
    </section>
  );
}
