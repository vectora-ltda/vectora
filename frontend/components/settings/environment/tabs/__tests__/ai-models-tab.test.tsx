// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ChatModelSettings,
  LocalSpeechSettings,
  SystemOneConfiguration,
  TranscriptionSettings,
} from "../ai-models-tab";
import { useSettingsStore } from "@/lib/stores/settings-store";

vi.mock("@/components/chat/model-selector", () => ({
  ModelSelector: ({
    value,
    onChange,
  }: {
    value: string;
    onChange: (value: string) => void;
  }) => (
    <select
      aria-label="Test model"
      value={value}
      onChange={(event) => onChange(event.target.value)}
    >
      <option value="openrouter:initial">Initial</option>
      <option value="ollama:qwen3">Qwen</option>
    </select>
  ),
}));

const status = {
  enabled: true,
  provider: "laya-local",
  local_base_url: "http://127.0.0.1:8080",
  local_model: "clef",
  laya_base_url: "http://127.0.0.1:11434",
  laya_model: "convaiinnovations/laya",
  laya_api_key_configured: true,
  clef_api_base_url: "https://api.cloudflare.com/client/v4/accounts",
  clef_account_id: "account-id",
  clef_api_model: "@cf/clef-flash",
  clef_api_key_configured: false,
  jev_api_base_url: "https://api.jev.ai",
  jev_api_model: "jev-fast",
  jev_api_key_configured: false,
  allow_remote_fallback: false,
  confidence_threshold_hitl: 0.7,
  risk_assessment_timeout_s: 10,
};

afterEach(() => vi.unstubAllGlobals());

describe("SystemOneConfiguration", () => {
  it("loads provider settings without exposing an existing credential", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => status }),
    );
    render(<SystemOneConfiguration />);

    await screen.findByDisplayValue("convaiinnovations/laya");
    expect(
      screen.getByText(/already configured; leave blank to keep it/),
    ).toBeInTheDocument();
    expect(screen.queryByDisplayValue("secret-value")).not.toBeInTheDocument();
  });

  it("sends only the replacement secret and clears it after success", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: async () => status })
      .mockResolvedValueOnce({ ok: true, json: async () => status });
    vi.stubGlobal("fetch", fetchMock);
    render(<SystemOneConfiguration />);

    const secret = await screen.findByLabelText(/Credential/);
    fireEvent.change(secret, { target: { value: "temporary-secret" } });
    fireEvent.click(
      screen.getByRole("button", { name: "Save provider configuration" }),
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const request = fetchMock.mock.calls[1][1] as RequestInit;
    expect(JSON.parse(String(request.body))).toMatchObject({
      laya_api_key: "temporary-secret",
      risk_assessment_timeout_s: 10,
    });
    await waitFor(() =>
      expect(screen.getByLabelText(/Credential/)).toHaveValue(""),
    );
    expect(screen.queryByText("temporary-secret")).not.toBeInTheDocument();
  });

  it("prevents saving a risk timeout outside the supported range", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => status }),
    );
    render(<SystemOneConfiguration />);

    const timeout = await screen.findByDisplayValue("10");
    fireEvent.change(timeout, { target: { value: "121" } });

    expect(
      screen.getByRole("button", { name: "Save provider configuration" }),
    ).toBeDisabled();
  });
});

describe("TranscriptionSettings", () => {
  it("persists local mode and offers explicit remote mode", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
      if (String(input).includes("TranscriptionReadiness")) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            dependency_available: true,
            model_cached: false,
            ready: false,
          }),
        });
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({ transcriptionMode: "local" }),
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    useSettingsStore.setState({ transcriptionMode: "remote" });
    render(<TranscriptionSettings />);

    const mode = screen.getByLabelText("Mode used for chat dictation");
    fireEvent.change(mode, { target: { value: "local" } });
    expect(mode).toHaveValue("local");
    const readiness = await screen.findByRole("status");
    await waitFor(() =>
      expect(readiness.textContent).toMatch(
        /ai_models_transcription_model_download_required|The local runtime is installed; the base model will download on first use\./,
      ),
    );
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([input, init]) =>
            !String(input).includes("TranscriptionReadiness") &&
            init?.method === "PATCH",
        ),
      ).toBe(true),
    );
    const updateCall = fetchMock.mock.calls.find(
      ([input]) => !String(input).includes("TranscriptionReadiness"),
    );
    expect(updateCall?.[1]).toMatchObject({
      method: "PATCH",
      body: JSON.stringify({ transcriptionMode: "local" }),
    });
    expect(
      screen.getByRole("option", { name: "Configured remote provider" }),
    ).toHaveValue("remote");
  });

  it("reports when the local transcription dependency is missing", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          dependency_available: false,
          model_cached: false,
          ready: false,
        }),
      }),
    );
    useSettingsStore.setState({ transcriptionMode: "local" });
    render(<TranscriptionSettings />);

    const readiness = await screen.findByRole("status");
    await waitFor(() =>
      expect(readiness.textContent).toMatch(
        /ai_models_transcription_dependency_missing|faster-whisper is not installed/,
      ),
    );
  });

  it("requires an explicit runtime probe before reporting local STT ready", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          dependency_available: true,
          model_cached: true,
          runtime_verified: false,
          probe_attempted: false,
          ready: false,
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          dependency_available: true,
          model_cached: true,
          runtime_verified: true,
          probe_attempted: true,
          ready: true,
        }),
      });
    vi.stubGlobal("fetch", fetchMock);
    useSettingsStore.setState({ transcriptionMode: "local" });
    render(<TranscriptionSettings />);

    const readiness = await screen.findByRole("status");
    await waitFor(() =>
      expect(readiness.textContent).toMatch(
        /ai_models_transcription_probe_required|The model is cached but has not been tested yet\./,
      ),
    );

    fireEvent.click(
      screen.getByRole("button", {
        name: /ai_models_transcription_probe_button|Test local runtime/,
      }),
    );

    await waitFor(() =>
      expect(fetchMock).toHaveBeenLastCalledWith(
        "/vectora.chat.v1.ChatService/TranscriptionReadiness?probe=true",
      ),
    );
    await waitFor(() =>
      expect(readiness.textContent).toMatch(
        /ai_models_transcription_local_ready|Local transcription is ready; the base model is cached\./,
      ),
    );
  });
});

describe("ChatModelSettings", () => {
  it("persists the primary chat selection through the shared store", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue({ ok: true, json: async () => ({}) });
    vi.stubGlobal("fetch", fetchMock);
    useSettingsStore.setState({ selectedModel: "openrouter:initial" });
    render(<ChatModelSettings />);
    fireEvent.change(screen.getByLabelText("Test model"), {
      target: { value: "ollama:qwen3" },
    });
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/settings/prefs",
        expect.objectContaining({ method: "PATCH" }),
      ),
    );
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      selectedModel: "ollama:qwen3",
    });
    expect(useSettingsStore.getState().selectedModel).toBe("ollama:qwen3");
  });
});

describe("LocalSpeechSettings", () => {
  const payload = {
    model: "base",
    language: "",
    system_command: "",
    system_model: "",
    models: [
      { id: "tiny", cached: false },
      { id: "base", cached: true },
      { id: "small", cached: false },
    ],
    languages: ["pt", "en"],
    engines: {
      embedded: true,
      apple_speech: true,
      system: false,
      system_kind: "",
    },
  };

  const stubFetch = (patchResponse?: { ok: boolean; body: unknown }) => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (
        String(input) === "/admin/transcription" &&
        init?.method === "PATCH"
      ) {
        const sent = JSON.parse(String(init.body)) as Record<string, string>;
        return Promise.resolve({
          ok: patchResponse?.ok ?? true,
          status: patchResponse?.ok === false ? 422 : 200,
          json: async () => patchResponse?.body ?? { ...payload, ...sent },
        });
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => payload,
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  };

  it("shows detected engines and models with their download state", async () => {
    stubFetch();
    render(<LocalSpeechSettings />);

    expect(
      await screen.findByTestId("stt-engine-embedded"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("stt-engine-apple")).toBeInTheDocument();
    expect(screen.getByTestId("stt-engine-system")).toBeInTheDocument();
    const model = screen.getByLabelText(
      /Bundled whisper model|ai_models_stt_model_label/,
    );
    expect(model).toHaveValue("base");
    expect(
      screen.getAllByRole("option").map((option) => option.textContent),
    ).toEqual(expect.arrayContaining([expect.stringContaining("small")]));
  });

  it("hides the macOS engine when the helper is not present", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            ...payload,
            engines: { ...payload.engines, apple_speech: false },
          }),
        }),
      ),
    );
    render(<LocalSpeechSettings />);
    await screen.findByTestId("stt-engine-embedded");
    expect(screen.queryByTestId("stt-engine-apple")).not.toBeInTheDocument();
  });

  it("saves the model and the language when they change", async () => {
    const fetchMock = stubFetch();
    render(<LocalSpeechSettings />);

    const model = await screen.findByLabelText(
      /Bundled whisper model|ai_models_stt_model_label/,
    );
    fireEvent.change(model, { target: { value: "small" } });
    await waitFor(() => expect(model).toHaveValue("small"));

    const language = screen.getByLabelText(
      /Transcription language|ai_models_stt_language_label/,
    );
    fireEvent.change(language, { target: { value: "pt" } });
    await waitFor(() => expect(language).toHaveValue("pt"));

    const bodies = fetchMock.mock.calls
      .filter(([, init]) => init?.method === "PATCH")
      .map(([, init]) => JSON.parse(String(init?.body)));
    expect(bodies).toEqual([{ model: "small" }, { language: "pt" }]);
    expect(await screen.findByRole("status")).toBeInTheDocument();
  });

  it("shows the server reason when a value is rejected", async () => {
    stubFetch({ ok: false, body: { detail: "modelo inválido" } });
    render(<LocalSpeechSettings />);

    const model = await screen.findByLabelText(
      /Bundled whisper model|ai_models_stt_model_label/,
    );
    fireEvent.change(model, { target: { value: "tiny" } });
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("modelo inválido");
  });

  it("explains that the settings need an administrator when loading fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({ ok: false, status: 403, json: async () => ({}) }),
      ),
    );
    render(<LocalSpeechSettings />);
    expect(await screen.findByRole("status")).toBeInTheDocument();
  });
});
