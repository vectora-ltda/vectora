import type { ReactNode } from "react";
import { X } from "lucide-react";
import { mDyn } from "@/lib/i18n-dyn";
import { m } from "@/lib/paraglide/messages";
import type { CanvasDocumentDescriptor } from "@/lib/stores/windows-store";

export interface CenterCanvasTab {
  /** Stable unique key for this tab within the CenterCanvas instance. */
  id: string;
  label: string;
  content: ReactNode;
  /** Optional serializable source document shared by IDE and assistant. */
  document?: CanvasDocumentDescriptor;
}

const EMPTY_TABS: CenterCanvasTab[] = [];
const EMPTY_DOCUMENTS: CanvasDocumentDescriptor[] = [];

function tabDomId(id: string, index: number): string {
  return `vectora-center-canvas-tab-${id.replace(/[^a-zA-Z0-9_-]/g, "-")}-${index}`;
}

interface CenterCanvasProps {
  tabs?: CenterCanvasTab[];
  /** Render shared document descriptors without storing React nodes in state. */
  documents?: CanvasDocumentDescriptor[];
  renderDocument?: (document: CanvasDocumentDescriptor) => ReactNode;
  activeTab?: string;
  onTabChange?: (id: string) => void;
  onTabClose?: (id: string) => void;
  children?: ReactNode;
}

/** Host for editor, Git, plan and future document tabs in the IDE center. */
export function CenterCanvas({
  tabs = EMPTY_TABS,
  documents = EMPTY_DOCUMENTS,
  renderDocument,
  activeTab,
  onTabChange,
  onTabClose,
  children,
}: CenterCanvasProps) {
  const documentTabs: CenterCanvasTab[] = documents.map((document) => ({
    id: document.id,
    label: document.title,
    document,
    content: renderDocument?.(document) ?? null,
  }));
  const resolvedTabs = [...tabs, ...documentTabs];
  const seenTabIds = new Set<string>();
  for (const tab of resolvedTabs) {
    if (seenTabIds.has(tab.id)) {
      throw new Error(`CenterCanvas tabs must have unique ids: "${tab.id}"`);
    }
    seenTabIds.add(tab.id);
  }
  const editorIsImplicit =
    activeTab === "editor" && !resolvedTabs.some((tab) => tab.id === "editor");
  const selected = editorIsImplicit
    ? undefined
    : (resolvedTabs.find((tab) => tab.id === activeTab) ?? resolvedTabs[0]);
  const selectedIndex = selected ? resolvedTabs.indexOf(selected) : -1;
  const tabpanelId = "vectora-center-canvas-panel";
  return (
    <section
      aria-label={mDyn("ide.canvas")}
      className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden"
    >
      {resolvedTabs.length > 0 && (
        <div
          role="tablist"
          aria-label={mDyn("ide.documents.open")}
          className="flex h-11 shrink-0 items-stretch border-b border-border/60"
        >
          {resolvedTabs.map((tab, index) => {
            const active = selectedIndex === index;
            return (
              <div
                key={`${tab.id}-${index}`}
                className={`group flex h-full shrink-0 items-center transition-colors ${
                  active
                    ? "bg-muted text-foreground"
                    : "bg-transparent text-muted-foreground hover:bg-muted/40"
                }`}
                data-active={active}
              >
                <button
                  type="button"
                  role="tab"
                  id={tabDomId(tab.id, index)}
                  aria-controls={tabpanelId}
                  aria-selected={selectedIndex === index}
                  onClick={() => onTabChange?.(tab.id)}
                  className="h-full min-w-0 max-w-64 truncate bg-transparent px-3 text-[14px] font-normal leading-4 transition-colors"
                  data-active={active}
                >
                  {tab.label}
                </button>
                {onTabClose && (
                  <button
                    type="button"
                    aria-label={`${m.workbench_browser_close_tab()} ${tab.label}`}
                    title={m.workbench_browser_close_tab()}
                    onClick={(event) => {
                      event.stopPropagation();
                      onTabClose(tab.id);
                    }}
                    className="mr-1 flex h-7 w-7 shrink-0 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-muted/80 hover:text-foreground group-data-[active=true]:text-foreground"
                  >
                    <X className="h-3 w-3" />
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}
      <div
        role={resolvedTabs.length > 0 ? "tabpanel" : undefined}
        id={tabpanelId}
        aria-labelledby={
          selected ? tabDomId(selected.id, selectedIndex) : undefined
        }
        className="flex min-h-0 min-w-0 flex-1 overflow-hidden"
      >
        <div className="flex min-h-0 min-w-0 flex-1 overflow-hidden">
          {selected?.content ?? children}
        </div>
      </div>
    </section>
  );
}
