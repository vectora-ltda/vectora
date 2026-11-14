// @vitest-environment jsdom
/**
 * ShareThreadDialog — cria, copia e revoga o link de leitura de uma conversa.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { ShareThreadDialog } from "../share-thread-dialog";

const api = vi.hoisted(() => ({
  createShare: vi.fn(),
  revokeShare: vi.fn(),
}));
vi.mock("@/lib/api/vectora-client", () => api);

vi.mock("@/lib/paraglide/messages", () => ({
  m: new Proxy(
    {},
    {
      get: (_target, key) => (params?: Record<string, string>) =>
        params
          ? `${String(key)}:${Object.values(params).join(",")}`
          : String(key),
    },
  ),
}));

const created = {
  token: "tok-1",
  url: "http://localhost:8080/share/tok-1",
  expires_at: "2030-01-01T00:00:00+00:00",
};

function renderDialog() {
  return render(
    <ShareThreadDialog threadId="t1" open onOpenChange={() => {}} />,
  );
}

beforeEach(() => {
  api.createShare.mockReset();
  api.revokeShare.mockReset();
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
  });
});
afterEach(cleanup);

describe("ShareThreadDialog", () => {
  it("cria o link com a validade escolhida e mostra o endereço", async () => {
    api.createShare.mockResolvedValue(created);
    renderDialog();

    fireEvent.change(screen.getByRole("combobox"), {
      target: { value: "168" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "share_dialog_create" }),
    );

    await waitFor(() =>
      expect(screen.getByDisplayValue(created.url)).toBeTruthy(),
    );
    expect(api.createShare).toHaveBeenCalledWith("t1", 168);
  });

  it("copia o link para a área de transferência", async () => {
    api.createShare.mockResolvedValue(created);
    renderDialog();
    fireEvent.click(
      screen.getByRole("button", { name: "share_dialog_create" }),
    );
    await screen.findByDisplayValue(created.url);

    fireEvent.click(screen.getByRole("button", { name: "share_dialog_copy" }));

    await screen.findByRole("button", { name: "share_dialog_copied" });
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(created.url);
  });

  it("revoga o link criado e volta a oferecer a criação", async () => {
    api.createShare.mockResolvedValue(created);
    api.revokeShare.mockResolvedValue(undefined);
    renderDialog();
    fireEvent.click(
      screen.getByRole("button", { name: "share_dialog_create" }),
    );
    await screen.findByDisplayValue(created.url);

    fireEvent.click(
      screen.getByRole("button", { name: "share_dialog_revoke" }),
    );

    await screen.findByRole("button", { name: "share_dialog_create" });
    expect(api.revokeShare).toHaveBeenCalledWith("tok-1");
  });

  it("falha ao criar mostra o erro e não exibe link", async () => {
    api.createShare.mockRejectedValue(new Error("500"));
    renderDialog();

    fireEvent.click(
      screen.getByRole("button", { name: "share_dialog_create" }),
    );

    expect((await screen.findByRole("alert")).textContent).toBe(
      "share_dialog_error",
    );
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("falha ao revogar mantém o link e mostra o erro", async () => {
    api.createShare.mockResolvedValue(created);
    api.revokeShare.mockRejectedValue(new Error("500"));
    renderDialog();
    fireEvent.click(
      screen.getByRole("button", { name: "share_dialog_create" }),
    );
    await screen.findByDisplayValue(created.url);

    fireEvent.click(
      screen.getByRole("button", { name: "share_dialog_revoke" }),
    );

    expect((await screen.findByRole("alert")).textContent).toBe(
      "share_dialog_error",
    );
    expect(screen.getByDisplayValue(created.url)).toBeTruthy();
  });
});
