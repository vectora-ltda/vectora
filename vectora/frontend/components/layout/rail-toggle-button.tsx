import { forwardRef } from "react";
import {
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
} from "lucide-react";

interface RailToggleButtonProps {
  side: "left" | "right";
  onClick?: () => void;
  ariaLabel: string;
  ariaExpanded: boolean;
  "data-testid"?: string;
}

/** Shared rail control used by session and workbench sidebars. */
export const RailToggleButton = forwardRef<
  HTMLButtonElement,
  RailToggleButtonProps
>(function RailToggleButton(
  { side, onClick, ariaLabel, ariaExpanded, "data-testid": dataTestId },
  ref,
) {
  const Icon =
    side === "left"
      ? ariaExpanded
        ? PanelLeftClose
        : PanelLeftOpen
      : ariaExpanded
        ? PanelRightClose
        : PanelRightOpen;

  return (
    <button
      ref={ref}
      type="button"
      onClick={onClick}
      data-testid={dataTestId}
      aria-label={ariaLabel}
      aria-expanded={ariaExpanded}
      className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground"
    >
      <Icon className="size-4" aria-hidden="true" />
    </button>
  );
});
