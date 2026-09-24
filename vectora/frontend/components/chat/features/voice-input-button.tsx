/**
 * Voice Input Button Component
 *
 * Microphone button for voice-to-text input.
 * Shows microphone icon when idle, stop square when listening.
 */

import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/components/ui/tooltip";
import { m } from "@/lib/paraglide/messages";
interface VoiceInputButtonProps {
  isListening: boolean;
  disabled?: boolean;
  onClick: () => void;
  size?: "sm" | "md";
  compact?: boolean;
}

export function VoiceInputButton({
  isListening,
  disabled,
  onClick,
  size = "sm",
  compact = false,
}: VoiceInputButtonProps) {
  const dimensions = compact
    ? "h-7 w-7"
    : size === "sm"
      ? "h-9 w-9"
      : "h-10 w-10";
  const iconSize = compact
    ? "w-3.5 h-3.5"
    : size === "sm"
      ? "w-4 h-4"
      : "w-4.5 h-4.5";
  const tooltipText = isListening ? m.input_stop() : m.tooltip_chat_audio();

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          onClick={onClick}
          variant="ghost"
          size="sm"
          disabled={disabled}
          className={`
            group ${dimensions} p-0 rounded-full flex-shrink-0
            transition-all duration-200 hover:scale-105 active:scale-95 border-0
            ${isListening ? "bg-muted text-primary hover:text-primary hover:bg-muted/80 border-2 border-primary" : "text-muted-foreground hover:text-foreground hover:bg-muted/60"}
          `}
          type="button"
          aria-label={tooltipText}
        >
          {isListening ? (
            <StopIcon className={iconSize} />
          ) : (
            <MicrophoneIcon className={iconSize} />
          )}
        </Button>
      </TooltipTrigger>
      <TooltipContent side="top">{tooltipText}</TooltipContent>
    </Tooltip>
  );
}

function MicrophoneIcon({ className }: { className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
    >
      <path d="M12 2a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z" />
      <path d="M19 10v1a7 7 0 0 1-14 0v-1" />
      <line x1="12" y1="19" x2="12" y2="22" />
    </svg>
  );
}

function StopIcon({ className }: { className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="currentColor"
      className={className}
    >
      <rect x="6" y="6" width="12" height="12" rx="2" />
    </svg>
  );
}
