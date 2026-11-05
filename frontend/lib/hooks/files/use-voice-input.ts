/**
 * Voice Input Hook
 *
 * Ditado de voz pro composer. Usa a Web Speech API do navegador quando ela
 * está disponível e funciona (Chrome, Edge, e qualquer Chromium com a chave
 * de voz do Google embutida). Quando não está disponível — Firefox/Zen (não
 * implementam a API) ou Electron/Chromium vendored (implementa a API mas
 * sempre falha com erro de rede por não ter a chave proprietária do Google)
 * — cai pra gravação via MediaRecorder + transcrição no backend (Whisper ou
 * Gemini, ver `backend/llm/transcription.py`).
 */

import { useState, useCallback, useRef, useEffect } from "react";
import { transcribeAudio } from "@/lib/api/vectora-client";
import { useSettingsStore } from "@/lib/stores/settings-store";

// ============================================================================
// Types
// ============================================================================

interface SpeechRecognitionEvent extends Event {
  results: SpeechRecognitionResultList;
  resultIndex: number;
}

interface SpeechRecognitionErrorEvent extends Event {
  error: string;
  message?: string;
}

interface SpeechRecognition extends EventTarget {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start: () => void;
  stop: () => void;
  abort: () => void;
  onresult: ((event: SpeechRecognitionEvent) => void) | null;
  onerror: ((event: SpeechRecognitionErrorEvent) => void) | null;
  onend: (() => void) | null;
  onstart: (() => void) | null;
}

declare global {
  interface Window {
    SpeechRecognition: new () => SpeechRecognition;
    webkitSpeechRecognition: new () => SpeechRecognition;
  }
}

export interface UseVoiceInputReturn {
  isListening: boolean;
  isSupported: boolean;
  error: string | null;
  interimTranscript: string;
  startListening: () => void;
  stopListening: () => void;
  toggleListening: () => void;
}

function hasBackendRecordingSupport(): boolean {
  return (
    typeof navigator !== "undefined" &&
    Boolean(navigator.mediaDevices?.getUserMedia) &&
    typeof MediaRecorder !== "undefined"
  );
}

function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => {
      const result = reader.result as string;
      resolve(result.split(",")[1] ?? "");
    });
    reader.addEventListener("error", () => reject(reader.error));
    reader.readAsDataURL(blob);
  });
}

// ============================================================================
// Hook
// ============================================================================

/**
 * Hook de ditado de voz. Mesma interface (`UseVoiceInputReturn`) independente
 * do motor usado — quem consome (`VoiceInputButton`, `chat-interface.tsx`)
 * não precisa saber se é Web Speech API ou gravação + transcrição via backend.
 *
 * @param onTranscript - Called with finalized transcript text
 * @returns Voice input state and controls
 */
export function useVoiceInput({
  onTranscript,
  lang = "en-US",
}: {
  onTranscript: (text: string) => void;
  /** Idioma do reconhecimento em BCP-47 (ex: "pt-BR"). */
  lang?: string;
}): UseVoiceInputReturn {
  const transcriptionMode = useSettingsStore(
    (state) => state.transcriptionMode,
  );
  const [isListening, setIsListening] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isSupported, setIsSupported] = useState(false);
  const [interimTranscript, setInterimTranscript] = useState("");

  const onTranscriptRef = useRef(onTranscript);
  useEffect(() => {
    onTranscriptRef.current = onTranscript;
  }, [onTranscript]);

  const langRef = useRef(lang);
  useEffect(() => {
    langRef.current = lang;
  }, [lang]);

  // ── Gravação + transcrição via backend — fallback quando o navegador não
  //    tem (ou não consegue de fato usar) a Web Speech API ────────────────
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  /** true a partir do momento em que sabemos que a Web Speech API não
   * está disponível ou não funciona neste ambiente (setado no mount se o
   * construtor nem existe, ou em runtime no primeiro erro "network"). */
  const useBackendRef = useRef(false);

  const startBackendRecording = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: true,
      });
      mediaStreamRef.current = stream;
      const mimeType = MediaRecorder.isTypeSupported("audio/webm")
        ? "audio/webm"
        : "";
      const recorder = mimeType
        ? new MediaRecorder(stream, { mimeType })
        : new MediaRecorder(stream);
      chunksRef.current = [];

      recorder.ondataavailable = (e: BlobEvent) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };

      recorder.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        mediaStreamRef.current = null;
        setIsListening(false);

        const blob = new Blob(chunksRef.current, {
          type: recorder.mimeType || "audio/webm",
        });
        chunksRef.current = [];
        if (blob.size === 0) return;

        void (async () => {
          try {
            const base64 = await blobToBase64(blob);
            const { text } = await transcribeAudio(
              base64,
              blob.type || "audio/webm",
              "recording.webm",
              transcriptionMode,
            );
            if (text) onTranscriptRef.current(text);
          } catch {
            setError(
              "Falha ao transcrever o áudio. Serviço temporariamente indisponível — tente novamente em instantes.",
            );
            setTimeout(() => setError(null), 5000);
          }
        })();
      };

      recorder.start();
      mediaRecorderRef.current = recorder;
      setError(null);
      setIsListening(true);
    } catch {
      setError(
        "Acesso ao microfone negado. Permita o acesso ao microfone nas configurações do sistema.",
      );
      setTimeout(() => setError(null), 5000);
    }
  }, [transcriptionMode]);

  const stopBackendRecording = useCallback(() => {
    mediaRecorderRef.current?.stop();
    mediaRecorderRef.current = null;
  }, []);

  // ── Web Speech API — navegador ───────────────────────────────────────────
  const recognitionRef = useRef<SpeechRecognition | null>(null);
  const isStartingRef = useRef(false);

  useEffect(() => {
    const SpeechRecognitionAPI =
      typeof window !== "undefined"
        ? window.SpeechRecognition || window.webkitSpeechRecognition
        : null;

    const backendSupported = hasBackendRecordingSupport();
    setIsSupported(Boolean(SpeechRecognitionAPI) || backendSupported);

    if (!SpeechRecognitionAPI || transcriptionMode === "local") {
      // Firefox/Zen — a API nem existe. Vai direto pro fallback via backend.
      useBackendRef.current = true;
      return () => {
        mediaRecorderRef.current?.stop();
        mediaStreamRef.current?.getTracks().forEach((t) => t.stop());
      };
    }

    const recognition = new SpeechRecognitionAPI();
    // Use non-continuous mode for better compatibility
    // User can click again to continue recording
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.lang = langRef.current;
    // @ts-expect-error - maxAlternatives exists but not in types
    recognition.maxAlternatives = 1;

    recognition.onstart = () => {
      isStartingRef.current = false;
      setIsListening(true);
      setError(null);
      setInterimTranscript("");
    };

    recognition.onresult = (event: SpeechRecognitionEvent) => {
      let interim = "";
      let final = "";

      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i];
        const transcript = result[0].transcript;

        if (result.isFinal) {
          final += transcript;
        } else {
          interim += transcript;
        }
      }

      setInterimTranscript(interim);

      if (final) {
        onTranscriptRef.current(final);
        setInterimTranscript("");
      }
    };

    recognition.addEventListener("error", (event: Event) => {
      const errEvent = event as SpeechRecognitionErrorEvent;
      isStartingRef.current = false;

      // "aborted" is expected when user stops listening or component unmounts
      if (errEvent.error === "aborted") {
        setIsListening(false);
        return;
      }

      if (errEvent.error === "network" && backendSupported) {
        // Chromium/Electron sem a chave de voz do Google — a API existe mas
        // nunca funciona. Marca o fallback permanente pra esta sessão e já
        // retoma via backend, sem incomodar o usuário com erro.
        useBackendRef.current = true;
        setIsListening(false);
        void startBackendRecording();
        return;
      }

      console.error("Speech recognition error:", errEvent.error);

      let errorMessage: string;
      switch (errEvent.error) {
        case "no-speech":
          errorMessage = "No speech detected. Please try again.";
          break;
        case "audio-capture":
          errorMessage = "No microphone found. Please check your microphone.";
          break;
        case "not-allowed":
          errorMessage =
            "Microphone access denied. Please allow microphone access.";
          break;
        case "network":
          errorMessage =
            "Speech recognition unavailable. Try Chrome or Edge, or check browser privacy settings.";
          break;
        default:
          errorMessage = `Error: ${errEvent.error}`;
      }

      setError(errorMessage);
      setIsListening(false);

      // Auto-dismiss error after 5 seconds
      setTimeout(() => setError(null), 5000);
    });

    recognition.onend = () => {
      isStartingRef.current = false;
      setIsListening(false);
    };

    recognitionRef.current = recognition;

    return () => {
      recognitionRef.current?.abort();
      mediaRecorderRef.current?.stop();
      mediaStreamRef.current?.getTracks().forEach((t) => t.stop());
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [transcriptionMode]); // Callbacks are handled via refs

  // ── API pública — despacha pro motor certo a cada chamada ────────────────

  const startListening = useCallback(() => {
    if (useBackendRef.current || !recognitionRef.current) {
      if (!isListening && !isStartingRef.current) {
        isStartingRef.current = true;
        void startBackendRecording().finally(() => {
          isStartingRef.current = false;
        });
      }
      return;
    }

    // Prevent double-start with ref (more reliable than state for rapid clicks)
    if (!isListening && !isStartingRef.current) {
      isStartingRef.current = true;
      setError(null);
      // Reaplica o idioma atual — o usuário pode tê-lo trocado desde o mount.
      recognitionRef.current.lang = langRef.current;
      try {
        recognitionRef.current.start();
      } catch {
        // Recognition might already be running - silently ignore
        isStartingRef.current = false;
      }
    }
  }, [isListening, startBackendRecording]);

  const stopListening = useCallback(() => {
    if (useBackendRef.current || !recognitionRef.current) {
      stopBackendRecording();
      return;
    }

    isStartingRef.current = false;
    try {
      recognitionRef.current.stop();
    } catch {
      // Ignore errors when stopping
    }
  }, [stopBackendRecording]);

  const toggleListening = useCallback(() => {
    if (isListening || isStartingRef.current) {
      stopListening();
    } else {
      startListening();
    }
  }, [isListening, startListening, stopListening]);

  return {
    isListening,
    isSupported,
    error,
    interimTranscript,
    startListening,
    stopListening,
    toggleListening,
  };
}
