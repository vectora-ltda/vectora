// @vitest-environment jsdom
/**
 * useVoiceInput: usa a Web Speech API quando o construtor existe E funciona.
 * Cai pra MediaRecorder + backend quando a API nem existe (Firefox/Zen) ou
 * quando existe mas falha com erro "network" em runtime (Electron/Chromium
 * sem a chave de voz do Google).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";

import { transcribeAudio } from "@/lib/api/vectora-client";
import { useVoiceInput } from "../use-voice-input";

vi.mock("@/lib/api/vectora-client", () => ({
  transcribeAudio: vi.fn(),
}));

const mockedTranscribeAudio = vi.mocked(transcribeAudio);

class FakeMediaRecorder {
  static isTypeSupported = vi.fn(() => true);
  ondataavailable: ((e: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  mimeType = "audio/webm";
  start = vi.fn();
  stop = vi.fn(() => {
    this.ondataavailable?.({
      data: new Blob(["chunk"], { type: "audio/webm" }),
    });
    this.onstop?.();
  });
  constructor(public stream: MediaStream) {}
}

class FakeSpeechRecognition extends EventTarget {
  continuous = false;
  interimResults = false;
  lang = "en-US";
  onresult: ((event: unknown) => void) | null = null;
  onend: (() => void) | null = null;
  onstart: (() => void) | null = null;
  start = vi.fn(() => this.onstart?.());
  stop = vi.fn(() => this.onend?.());
  abort = vi.fn();
  emitError(error: string) {
    const ev = new Event("error") as Event & { error: string };
    ev.error = error;
    this.dispatchEvent(ev);
  }
}

function fakeStream(): MediaStream {
  const stop = vi.fn();
  return { getTracks: () => [{ stop }] } as unknown as MediaStream;
}

function setMediaDevices(value: unknown) {
  Object.defineProperty(navigator, "mediaDevices", {
    value,
    configurable: true,
  });
}

// jsdom 30.0.1 tem uma incompatibilidade interna real com @exodus/bytes na
// conversão de FileReader pra base64 (assertU8: "Expected an Uint8Array"),
// que dispara de forma assíncrona (fora do ciclo de vida do teste que a
// originou) e vaza como "Unhandled Error" pro Vitest — falha o job de CI
// inteiro mesmo com todos os 1944 testes passando. blobToBase64() (hook)
// só precisa do contrato load/error do FileReader — um fake evita depender
// da implementação binária real do jsdom, que não é o que este teste
// pretende exercitar.
class FakeFileReader extends EventTarget {
  result: string | ArrayBuffer | null = null;
  error: DOMException | null = null;
  readAsDataURL(blob: Blob): void {
    queueMicrotask(() => {
      this.result = `data:${blob.type};base64,ZmFrZQ==`;
      this.dispatchEvent(new Event("load"));
    });
  }
}

function stubBackendRecordingSupport() {
  vi.stubGlobal("MediaRecorder", FakeMediaRecorder);
  vi.stubGlobal("FileReader", FakeFileReader);
  setMediaDevices({ getUserMedia: vi.fn().mockResolvedValue(fakeStream()) });
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  setMediaDevices(undefined);
  delete (window as { webkitSpeechRecognition?: unknown })
    .webkitSpeechRecognition;
});

describe("useVoiceInput — sem Web Speech API (Firefox/Zen)", () => {
  beforeEach(() => {
    stubBackendRecordingSupport();
    mockedTranscribeAudio.mockReset();
  });

  it("isSupported é true via fallback de gravação, mesmo sem SpeechRecognition", () => {
    const { result } = renderHook(() =>
      useVoiceInput({ onTranscript: vi.fn() }),
    );
    expect(result.current.isSupported).toBe(true);
  });

  it("startListening grava e stopListening transcreve via backend", async () => {
    mockedTranscribeAudio.mockResolvedValue({ text: "olá mundo" });
    const onTranscript = vi.fn();
    const { result } = renderHook(() => useVoiceInput({ onTranscript }));

    await act(async () => {
      result.current.startListening();
      await Promise.resolve();
    });
    expect(result.current.isListening).toBe(true);

    await act(async () => {
      result.current.stopListening();
      await Promise.resolve();
    });

    await waitFor(() => {
      expect(onTranscript).toHaveBeenCalledWith("olá mundo");
    });
    expect(result.current.isListening).toBe(false);
  });

  it("erro do backend na transcrição vira mensagem de erro, sem chamar onTranscript", async () => {
    mockedTranscribeAudio.mockRejectedValue(new Error("502"));
    const onTranscript = vi.fn();
    const { result } = renderHook(() => useVoiceInput({ onTranscript }));

    await act(async () => {
      result.current.startListening();
      await Promise.resolve();
    });
    await act(async () => {
      result.current.stopListening();
      await Promise.resolve();
    });

    await waitFor(() => {
      expect(result.current.error).not.toBeNull();
    });
    expect(onTranscript).not.toHaveBeenCalled();
  });

  it("negar acesso ao microfone seta erro e não entra em isListening", async () => {
    setMediaDevices({
      getUserMedia: vi.fn().mockRejectedValue(new Error("denied")),
    });
    const { result } = renderHook(() =>
      useVoiceInput({ onTranscript: vi.fn() }),
    );

    await act(async () => {
      result.current.startListening();
      await Promise.resolve();
    });

    await waitFor(() => {
      expect(result.current.error).not.toBeNull();
    });
    expect(result.current.isListening).toBe(false);
  });

  it("sem SpeechRecognition e sem getUserMedia, isSupported é false", () => {
    setMediaDevices(undefined);
    const { result } = renderHook(() =>
      useVoiceInput({ onTranscript: vi.fn() }),
    );
    expect(result.current.isSupported).toBe(false);
  });
});

describe("useVoiceInput — Web Speech API funcionando (Chrome/Edge)", () => {
  let recognitionInstance: FakeSpeechRecognition;

  beforeEach(() => {
    recognitionInstance = new FakeSpeechRecognition();
    // vi.fn() não é utilizável com `new` no Vitest (ao contrário do Jest) —
    // precisa de uma function declaration de verdade, que retorna o objeto
    // explicitamente (semântica padrão de JS: `new` usa o retorno quando é
    // um objeto, em vez do `this` recém-criado).
    vi.stubGlobal("webkitSpeechRecognition", function MockSpeechRecognition() {
      return recognitionInstance;
    });
  });

  it("isSupported é true quando webkitSpeechRecognition existe", () => {
    const { result } = renderHook(() =>
      useVoiceInput({ onTranscript: vi.fn() }),
    );
    expect(result.current.isSupported).toBe(true);
  });

  it("startListening chama recognition.start()", () => {
    const { result } = renderHook(() =>
      useVoiceInput({ onTranscript: vi.fn() }),
    );
    act(() => {
      result.current.startListening();
    });
    expect(recognitionInstance.start).toHaveBeenCalledTimes(1);
    expect(result.current.isListening).toBe(true);
  });

  it("resultado final chama onTranscript com o texto transcrito", () => {
    const onTranscript = vi.fn();
    renderHook(() => useVoiceInput({ onTranscript }));

    act(() => {
      recognitionInstance.onresult?.({
        resultIndex: 0,
        results: [Object.assign([{ transcript: "oi" }], { isFinal: true })],
      });
    });

    expect(onTranscript).toHaveBeenCalledWith("oi");
  });

  it("erro 'not-allowed' mostra mensagem e não usa fallback de backend", () => {
    const { result } = renderHook(() =>
      useVoiceInput({ onTranscript: vi.fn() }),
    );
    act(() => {
      recognitionInstance.emitError("not-allowed");
    });
    expect(result.current.error).not.toBeNull();
    expect(result.current.isListening).toBe(false);
  });
});

describe("useVoiceInput — Web Speech API presente mas quebrada (Electron/Chromium)", () => {
  let recognitionInstance: FakeSpeechRecognition;

  beforeEach(() => {
    recognitionInstance = new FakeSpeechRecognition();
    // vi.fn() não é utilizável com `new` no Vitest (ao contrário do Jest) —
    // precisa de uma function declaration de verdade, que retorna o objeto
    // explicitamente (semântica padrão de JS: `new` usa o retorno quando é
    // um objeto, em vez do `this` recém-criado).
    vi.stubGlobal("webkitSpeechRecognition", function MockSpeechRecognition() {
      return recognitionInstance;
    });
    stubBackendRecordingSupport();
    mockedTranscribeAudio.mockReset();
  });

  it("erro 'network' cai automaticamente pro backend, sem mostrar erro genérico", async () => {
    mockedTranscribeAudio.mockResolvedValue({ text: "ditado via backend" });
    const onTranscript = vi.fn();
    const { result } = renderHook(() => useVoiceInput({ onTranscript }));

    act(() => {
      recognitionInstance.emitError("network");
    });

    // startBackendRecording foi disparado — getUserMedia + MediaRecorder.start
    await waitFor(() => {
      expect(FakeMediaRecorder.isTypeSupported).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(result.current.isListening).toBe(true);
    });
  });

  it("depois do erro 'network', toggleListening subsequente usa o backend direto", async () => {
    mockedTranscribeAudio.mockResolvedValue({ text: "" });
    const { result } = renderHook(() =>
      useVoiceInput({ onTranscript: vi.fn() }),
    );

    act(() => {
      recognitionInstance.emitError("network");
    });
    // A primeira tentativa (auto-retry) já deixou algo gravando; para via stop.
    await waitFor(() => expect(result.current.isListening).toBe(true));
    act(() => {
      result.current.stopListening();
    });
    await waitFor(() => expect(result.current.isListening).toBe(false));

    recognitionInstance.start.mockClear();

    act(() => {
      result.current.startListening();
    });
    await waitFor(() => expect(result.current.isListening).toBe(true));

    expect(recognitionInstance.start).not.toHaveBeenCalled();
  });
});
