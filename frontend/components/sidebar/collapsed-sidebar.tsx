"use client";

import { memo } from "react";
import { PanelLeft, MessageSquare } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/components/ui/tooltip";
import type { Thread } from "@/lib/hooks/threads";
import { useNetworkStatus } from "@/lib/hooks/use-network-status";
import { m } from "@/lib/paraglide/messages";

interface CollapsedSidebarProps {
  threads: Thread[];
  currentThreadId: string;
  onToggle: () => void;
  onSelectThread: (threadId: string) => void;
  onNewChat?: () => void;
}

export const CollapsedSidebar = memo(function CollapsedSidebar({
  threads,
  currentThreadId,
  onToggle,
  onSelectThread,
  onNewChat,
}: CollapsedSidebarProps) {
  const { offline } = useNetworkStatus();

  return (
    <aside className="hidden md:flex w-16 bg-gradient-to-b from-sidebar via-sidebar-light to-sidebar border-r border-border/60 flex-col shadow-depth-sm">
      <div className="px-3 py-4 border-b border-border/60 h-16 flex items-center justify-center">
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              onClick={onToggle}
              aria-label={m.sidebar_expand()}
              className="hover:bg-sidebar-primary/10 hover:text-sidebar-primary transition-all duration-200 shadow-depth-xs hover:shadow-depth-hover rounded-lg"
            >
              <PanelLeft className="w-5 h-5" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="right">{m.sidebar_expand()}</TooltipContent>
        </Tooltip>
      </div>

      {onNewChat && (
        <div className="px-3 pt-2">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                onClick={onNewChat}
                disabled={offline}
                aria-label={
                  offline ? m.network_disabled_offline() : m.sidebar_new_chat()
                }
                className="w-full hover:bg-muted/40 hover:text-foreground transition-all duration-200 rounded-lg"
              >
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  className="w-5 h-5"
                >
                  <path d="M12 5v14M5 12h14" />
                </svg>
              </Button>
            </TooltipTrigger>
            <TooltipContent side="right">
              {offline ? m.network_disabled_offline() : m.sidebar_new_chat()}
            </TooltipContent>
          </Tooltip>
        </div>
      )}

      <div className="custom-scrollbar flex-1 overflow-y-auto px-3 py-2 flex flex-col gap-1">
        {threads.map((thread) => (
          <button
            key={thread.thread_id}
            onClick={() => onSelectThread(thread.thread_id)}
            title={thread.metadata?.title || m.sidebar_untitled_chat()}
            className={`flex items-center justify-center w-10 h-10 mx-auto rounded-lg transition-all duration-200 ${
              thread.thread_id === currentThreadId
                ? "bg-muted/70 text-foreground"
                : "text-muted-foreground hover:bg-muted/40 hover:text-foreground"
            }`}
          >
            <MessageSquare className="w-4 h-4" />
          </button>
        ))}
      </div>
    </aside>
  );
});
