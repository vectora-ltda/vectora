// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  SystemOneConfiguration,
  TranscriptionSettings,
} from "../ai-models-tab";
import { useSettingsStore } from "@/lib/stores/settings-store";

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
    });
    await waitFor(() =>
      expect(screen.getByLabelText(/Credential/)).toHaveValue(""),
    );
    expect(screen.queryByText("temporary-secret")).not.toBeInTheDocument();
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

  it("reports when the local transcription model is ready from cache", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          dependency_available: true,
          model_cached: true,
          ready: true,
        }),
      }),
    );
    useSettingsStore.setState({ transcriptionMode: "local" });
    render(<TranscriptionSettings />);

    const readiness = await screen.findByRole("status");
    await waitFor(() =>
      expect(readiness.textContent).toMatch(
        /ai_models_transcription_local_ready|Local transcription is ready; the base model is cached\./,
      ),
    );
  });
});
