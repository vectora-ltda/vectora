"use client";

import { memo } from "react";
import { useNetworkStatus } from "@/lib/hooks/use-network-status";
import { useSettingsStore } from "@/lib/stores/settings-store";
import { m } from "@/lib/paraglide/messages";

interface NewChatButtonProps {
  onClick: () => void;
  /** true quando a sessão atual é nova/vazia — destaca o botão como ativo. */
  active?: boolean;
}

export const NewChatButton = memo(function NewChatButton({
  onClick,
  active = false,
}: NewChatButtonProps) {
  const { offline } = useNetworkStatus();
  const sidebarOnRight = useSettingsStore((s) => s.sidebarPosition === "right");
  const label = offline ? m.network_disabled_offline() : m.sidebar_new_chat();

  return (
    <div className="px-3 py-1.5">
      <button
        onClick={onClick}
        disabled={offline}
        aria-label={label}
        aria-current={active ? "page" : undefined}
        className={`group w-full h-8 inline-flex items-center ${sidebarOnRight ? "justify-end" : "justify-start"} gap-1.5 pl-3 pr-3 border rounded-md text-xs transition-colors duration-150 whitespace-nowrap disabled:opacity-50 disabled:cursor-not-allowed ${
          active
            ? "bg-muted border-border text-foreground"
            : "bg-muted/30 hover:bg-muted/60 border-border/50 hover:border-border/80 text-foreground/80 hover:text-foreground"
        }`}
      >
        <svg
          xmlns="http://www.w3.org/2000/svg"
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          className={`shrink-0 text-muted-foreground ${sidebarOnRight ? "order-last" : ""}`}
        >
          <path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275L12 3Z" />
        </svg>
        {m.sidebar_new_chat()}
      </button>
    </div>
  );
});
