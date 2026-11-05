// @vitest-environment jsdom

import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, beforeEach, vi } from "vitest";

import { spokenMessageText, useSpeechSynthesis } from "../use-speech-synthesis";

describe("useSpeechSynthesis", () => {
  const speech = {
    speak: vi.fn(),
    pause: vi.fn(),
    resume: vi.fn(),
    cancel: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(window, "speechSynthesis", {
      configurable: true,
      value: speech,
    });
    class FakeUtterance {
      public text: string;
      public lang = "";
      public onend: (() => void) | null = null;
      public onerror: (() => void) | null = null;

      public constructor(text: string) {
        this.text = text;
      }
    }
    Object.defineProperty(window, "SpeechSynthesisUtterance", {
      configurable: true,
      value: FakeUtterance,
    });
  });

  it("removes fenced code before speaking", () => {
    expect(spokenMessageText("Olá\n```ts\nconst x = 1\n```\nTudo bem")).toBe(
      "Olá Tudo bem",
    );
  });

  it("removes fences longer than three delimiters without leaking code", () => {
    expect(
      spokenMessageText('Antes\n````ts\nconst template = "```"\n````\nDepois'),
    ).toBe("Antes Depois");
  });

  it("preserves prose after a longer closing fence", () => {
    expect(spokenMessageText("Antes\n```ts\nconst x = 1\n````\nDepois")).toBe(
      "Antes Depois",
    );
  });

  it("supports start, pause, resume and stop", () => {
    const { result } = renderHook(() => useSpeechSynthesis("Olá", "thread-1"));

    act(() => result.current.speak());
    expect(result.current.state).toBe("speaking");
    expect(speech.speak).toHaveBeenCalledTimes(1);

    act(() => result.current.pause());
    expect(result.current.state).toBe("paused");
    act(() => result.current.resume());
    expect(result.current.state).toBe("speaking");
    act(() => result.current.stop());
    expect(result.current.state).toBe("idle");
    expect(speech.cancel).toHaveBeenCalled();
  });

  it("invalidates the previous owner when another instance starts speaking", () => {
    const { result } = renderHook(() => ({
      first: useSpeechSynthesis("Primeiro", "thread-1"),
      second: useSpeechSynthesis("Segundo", "thread-2"),
    }));

    act(() => result.current.first.speak());
    act(() => result.current.second.speak());

    expect(result.current.first.state).toBe("idle");
    expect(result.current.second.state).toBe("speaking");
    act(() => result.current.first.pause());
    act(() => result.current.first.resume());
    expect(speech.pause).not.toHaveBeenCalled();
    expect(speech.resume).not.toHaveBeenCalled();
  });
});
