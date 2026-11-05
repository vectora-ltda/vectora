/* oxlint-disable react/exhaustive-effect-dependencies, react-hooks/exhaustive-deps, unicorn/prefer-add-event-listener -- browser speech APIs own this lifecycle. */
import { useCallback, useEffect, useRef, useState } from "react";

type SpeechState = "idle" | "speaking" | "paused";
let activeCancel: (() => void) | null = null;
let activeOwner: symbol | null = null;

function cancelActiveSpeech(): void {
  const cancel = activeCancel;
  activeCancel = null;
  activeOwner = null;
  cancel?.();
}

function removeFencedCode(content: string): string {
  const lines = content.split(/\r?\n/);
  const output: string[] = [];
  let opening: { character: "`" | "~"; length: number } | null = null;

  for (const line of lines) {
    if (opening === null) {
      const match = line.match(/^[\t ]*(`{3,}|~{3,})/);
      if (match) {
        opening = {
          character: match[1][0] as "`" | "~",
          length: match[1].length,
        };
        output.push(" ");
      } else {
        output.push(line);
      }
      continue;
    }

    const closing = line.match(/^[\t ]*([`~]+)[\t ]*$/);
    if (
      closing &&
      closing[1][0] === opening.character &&
      closing[1].length >= opening.length
    ) {
      opening = null;
    }
    output.push(" ");
  }

  return output.join("\n");
}

export function spokenMessageText(content: string): string {
  return removeFencedCode(content)
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
    if (activeOwner === owner.current) cancelActiveSpeech();
    if (activeOwner === owner.current) {
      activeCancel = null;
      activeOwner = null;
    }
    setState("idle");
  }, []);
  const speak = useCallback(() => {
    if (!supported) return;
    cancelActiveSpeech();
    const value = spokenMessageText(text);
    if (!value) return;
    const utterance = new SpeechSynthesisUtterance(value);
    utterance.lang = document.documentElement.lang || "pt-BR";
    utterance.onend = () => {
      if (activeOwner === owner.current) setState("idle");
    };
    utterance.onerror = () => {
      if (activeOwner === owner.current) setState("idle");
    };
    activeCancel = () => {
      window.speechSynthesis.cancel();
      setState("idle");
    };
    activeOwner = owner.current;
    window.speechSynthesis.speak(utterance);
    setState("speaking");
  }, [supported, text]);
  const pause = useCallback(() => {
    if (supported && activeOwner === owner.current) {
      window.speechSynthesis.pause();
      setState("paused");
    }
  }, [supported]);
  const resume = useCallback(() => {
    if (supported && activeOwner === owner.current) {
      window.speechSynthesis.resume();
      setState("speaking");
    }
  }, [supported]);
  useEffect(() => () => stop(), [stop, threadId]);
  return { supported, state, speak, pause, resume, stop };
}
