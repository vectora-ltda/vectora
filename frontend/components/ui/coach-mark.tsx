"use client";

import * as React from "react";
import * as PopoverPrimitive from "@radix-ui/react-popover";
import { X } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

interface CoachMarkProps {
  isOpen: boolean;
  targetRef: React.RefObject<HTMLElement | null>;
  title: string;
  description: React.ReactNode;
  onDismiss: () => void;
  nextLabel?: string;
  onNext?: () => void;
  showOverlay?: boolean;
  position?: "top" | "bottom" | "left" | "right";
}

export function CoachMark({
  isOpen,
  targetRef,
  title,
  description,
  onDismiss,
  nextLabel = "Próximo",
  onNext,
  showOverlay = true,
  position = "bottom",
}: CoachMarkProps) {
  const [targetRect, setTargetRect] = React.useState<DOMRect | null>(null);

  React.useEffect(() => {
    if (!isOpen || !targetRef.current) return;

    const updateRect = () => {
      setTargetRect(targetRef.current?.getBoundingClientRect() ?? null);
    };

    updateRect();
    window.addEventListener("resize", updateRect);
    window.addEventListener("scroll", updateRect);

    return () => {
      window.removeEventListener("resize", updateRect);
      window.removeEventListener("scroll", updateRect);
    };
  }, [isOpen, targetRef]);

  if (!isOpen) return null;

  const align =
    position === "left" ? "end" : position === "right" ? "start" : "center";

  return (
    <>
      {showOverlay && (
        <div
          className="fixed inset-0 z-40 bg-black/30 backdrop-blur-sm"
          onClick={onDismiss}
          aria-hidden
        />
      )}

      {targetRect && (
        <div
          className="fixed z-50 rounded-lg border-2 border-primary bg-transparent pointer-events-none"
          style={{
            left: `${targetRect.left - 2}px`,
            top: `${targetRect.top - 2}px`,
            width: `${targetRect.width + 4}px`,
            height: `${targetRect.height + 4}px`,
          }}
          aria-hidden
        />
      )}

      <PopoverPrimitive.Root open={isOpen}>
        <PopoverPrimitive.Anchor
          ref={targetRef as React.RefObject<HTMLDivElement | null>}
          style={{
            pointerEvents: "none",
          }}
        />
        <PopoverPrimitive.Portal>
          <PopoverPrimitive.Content
            align={align}
            side={position}
            sideOffset={16}
            className={cn(
              "z-50 w-80 rounded-lg border bg-popover p-4 text-popover-foreground shadow-lg outline-none animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95",
            )}
          >
            <div className="space-y-3">
              <div className="flex items-start justify-between gap-2">
                <h3 className="font-semibold leading-tight">{title}</h3>
                <button
                  type="button"
                  onClick={onDismiss}
                  className="rounded p-0.5 opacity-60 hover:opacity-100 focus:outline-none focus:ring-2 focus:ring-primary"
                  aria-label="Fechar"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>

              <p className="text-sm opacity-80 leading-relaxed">
                {description}
              </p>

              <div className="flex items-center justify-between gap-2 pt-1">
                <button
                  type="button"
                  onClick={onDismiss}
                  className="text-xs opacity-60 hover:opacity-100 underline underline-offset-2"
                >
                  Pular
                </button>
                {onNext && (
                  <Button size="sm" onClick={onNext}>
                    {nextLabel}
                  </Button>
                )}
              </div>
            </div>
          </PopoverPrimitive.Content>
        </PopoverPrimitive.Portal>
      </PopoverPrimitive.Root>
    </>
  );
}
