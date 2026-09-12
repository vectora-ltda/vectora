// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";

vi.mock("@/lib/paraglide/messages", () => ({
  m: {
    chat_url_preview_loading: () => "Carregando preview",
    chat_url_preview_unavailable: () => "Preview indisponível",
    chat_url_preview_remove: () => "Remover preview",
  },
}));

import { UrlPreviewCard } from "../url-preview-card";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

beforeEach(() => {
  vi.useFakeTimers();
});

function response(payload: object, ok = true): Response {
  return { ok, json: async () => payload } as Response;
}

describe("UrlPreviewCard", () => {
  it("debounces the request and renders sanitized structured metadata", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      response({
        title: "Vectora docs",
        description: "Secure preview",
        origin: "example.com",
        image: null,
        url: "https://example.com/docs",
      }),
    );

    render(
      <UrlPreviewCard url="https://example.com/docs" onDismiss={vi.fn()} />,
    );
    expect(screen.getByText("Carregando preview")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(349);
    });
    expect(fetchMock).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
      await Promise.resolve();
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/url-preview?url=https%3A%2F%2Fexample.com%2Fdocs",
      expect.objectContaining({
        credentials: "include",
        signal: expect.any(AbortSignal),
      }),
    );
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByText("Vectora docs")).toBeInTheDocument();
    expect(screen.getByText("Secure preview")).toBeInTheDocument();
    expect(screen.getByText("example.com")).toBeInTheDocument();
  });

  it("shows an unavailable state when the preview request fails", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(response({}, false));
    render(<UrlPreviewCard url="https://example.com" onDismiss={vi.fn()} />);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(350);
      await Promise.resolve();
    });

    expect(screen.getByText("Preview indisponível")).toBeInTheDocument();
  });

  it("aborts the previous request when the URL changes and on unmount", async () => {
    const signals: AbortSignal[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) => {
      signals.push((init as RequestInit).signal as AbortSignal);
      return await new Promise<Response>(() => {});
    });
    const view = render(
      <UrlPreviewCard url="https://example.com/one" onDismiss={vi.fn()} />,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(350);
    });
    expect(signals).toHaveLength(1);

    view.rerender(
      <UrlPreviewCard url="https://example.com/two" onDismiss={vi.fn()} />,
    );
    expect(signals[0].aborted).toBe(true);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(350);
    });
    expect(signals).toHaveLength(2);
    view.unmount();
    expect(signals[1].aborted).toBe(true);
  });

  it("dismisses the card while leaving the caller's URL unchanged", () => {
    const onDismiss = vi.fn();
    render(<UrlPreviewCard url="https://example.com" onDismiss={onDismiss} />);

    fireEvent.click(screen.getByRole("button", { name: "Remover preview" }));

    expect(onDismiss).toHaveBeenCalledOnce();
    expect(
      screen.getByRole("button", { name: "Remover preview" }),
    ).toBeInTheDocument();
  });
});
