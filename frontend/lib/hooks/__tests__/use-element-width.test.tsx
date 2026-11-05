// @vitest-environment jsdom
/**
 * useElementWidth — largura real via ResizeObserver, não breakpoint de
 * viewport (o grupo encolhe pela largura que sobra no header, não pela
 * janela inteira).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";

import { useElementWidth } from "@/lib/hooks/use-element-width";

let observers: MockResizeObserver[] = [];

class MockResizeObserver {
  callback: ResizeObserverCallback;
  observed: Element | null = null;
  disconnected = false;

  constructor(callback: ResizeObserverCallback) {
    this.callback = callback;
    observers.push(this);
  }

  observe(target: Element) {
    this.observed = target;
  }

  unobserve() {}

  disconnect() {
    this.disconnected = true;
  }

  trigger(width: number) {
    this.callback(
      [{ contentRect: { width } } as ResizeObserverEntry],
      this as unknown as ResizeObserver,
    );
  }
}

function Sonda() {
  const [ref, width] = useElementWidth<HTMLDivElement>();
  return (
    <div ref={ref} data-testid="sonda">
      {width}
    </div>
  );
}

beforeEach(() => {
  observers = [];
  vi.stubGlobal("ResizeObserver", MockResizeObserver);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("useElementWidth", () => {
  it("atualiza a largura quando o ResizeObserver dispara", async () => {
    render(<Sonda />);
    expect(screen.getByTestId("sonda").textContent).toBe("0");

    await act(async () => {
      observers[0]?.trigger(240);
    });

    expect(screen.getByTestId("sonda").textContent).toBe("240");
  });

  it("desconecta o observer no unmount, sem lançar", async () => {
    const { unmount } = render(<Sonda />);
    const observer = observers[0];

    expect(() => unmount()).not.toThrow();
    expect(observer?.disconnected).toBe(true);
  });
});
