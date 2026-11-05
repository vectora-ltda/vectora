// @vitest-environment jsdom
/**
 * Tests para VoiceInputButton: ícone/aria por estado, click e disabled.
 */

import { describe, expect, it, afterEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { TooltipProvider } from "@/components/ui/tooltip";
import { VoiceInputButton } from "../voice-input-button";

afterEach(cleanup);

function renderBtn(
  props: Partial<React.ComponentProps<typeof VoiceInputButton>>,
) {
  return render(
    <TooltipProvider>
      <VoiceInputButton isListening={false} onClick={() => {}} {...props} />
    </TooltipProvider>,
  );
}

describe("VoiceInputButton", () => {
  it("dispara onClick ao clicar", () => {
    const onClick = vi.fn();
    renderBtn({ onClick });
    fireEvent.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("fica desabilitado e não dispara onClick quando disabled", () => {
    const onClick = vi.fn();
    renderBtn({ onClick, disabled: true });
    const btn = screen.getByRole("button");
    expect(btn).toBeDisabled();
    fireEvent.click(btn);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("mostra o ícone de stop (rect) quando isListening", () => {
    const { container } = renderBtn({ isListening: true });
    expect(container.querySelector("rect")).not.toBeNull();
  });

  it("mostra o ícone de microfone (sem rect) quando idle", () => {
    const { container } = renderBtn({ isListening: false });
    expect(container.querySelector("rect")).toBeNull();
  });

  it("o aria-label muda entre idle e listening", () => {
    const { rerender } = renderBtn({ isListening: false });
    const idleLabel = screen.getByRole("button").getAttribute("aria-label");
    rerender(
      <TooltipProvider>
        <VoiceInputButton isListening onClick={() => {}} />
      </TooltipProvider>,
    );
    const listeningLabel = screen
      .getByRole("button")
      .getAttribute("aria-label");
    expect(idleLabel).not.toBe(listeningLabel);
  });
});
