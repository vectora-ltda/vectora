import { useCallback, useEffect, useRef, useState } from "react";

type SpeechState = "idle" | "speaking" | "paused";
let activeCancel: (() => void) | null = null;
let activeOwner: symbol | null = null;

export function spokenMessageText(content: string): string {
  return content
    .replace(/(?:```|~~~)[\s\S]*?(?:```|~~~|$)/g, " ")
    .replace(/`[^`]*`/g, " ")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, " ")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/[#>*_~-]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export function useSpeechSynthesis(
  text: string,
  threadId: string,
): {
  supported: boolean;
  state: SpeechState;
  speak: () => void;
  pause: () => void;
  resume: () => void;
  stop: () => void;
} {
  const supported =
    typeof window !== "undefined" &&
    "speechSynthesis" in window &&
    "SpeechSynthesisUtterance" in window;
  const [state, setState] = useState<SpeechState>("idle");
  const owner = useRef(Symbol());
  const stop = useCallback(() => {
    if (
      activeOwner === owner.current &&
      typeof window !== "undefined" &&
      "speechSynthesis" in window
    )
      window.speechSynthesis.cancel();
    if (activeOwner === owner.current) {
      activeCancel = null;
      activeOwner = null;
    }
    setState("idle");
  }, []);
  const speak = useCallback(() => {
    if (!supported) return;
    activeCancel?.();
    const value = spokenMessageText(text);
    if (!value) return;
    const utterance = new SpeechSynthesisUtterance(value);
    utterance.lang = document.documentElement.lang || "pt-BR";
    utterance.onend = () => setState("idle");
    utterance.onerror = () => setState("idle");
    activeCancel = () => window.speechSynthesis.cancel();
    activeOwner = owner.current;
    window.speechSynthesis.speak(utterance);
    setState("speaking");
  }, [supported, text]);
  const pause = useCallback(() => {
    if (supported) {
      window.speechSynthesis.pause();
      setState("paused");
    }
  }, [supported]);
  const resume = useCallback(() => {
    if (supported) {
      window.speechSynthesis.resume();
      setState("speaking");
    }
  }, [supported]);
  useEffect(() => () => stop(), [stop, threadId]);
  return { supported, state, speak, pause, resume, stop };
}
