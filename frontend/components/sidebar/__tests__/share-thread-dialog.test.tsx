// @vitest-environment jsdom
/**
 * ShareThreadDialog — lista, cria, copia e revoga os links de leitura de uma
 * conversa.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { ShareThreadDialog } from "../share-thread-dialog";

const api = vi.hoisted(() => ({
  createShare: vi.fn(),
  listShares: vi.fn(),
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

const link = (token: string) => ({
  token,
  url: `http://localhost:8080/share/${token}`,
  expires_at: "2030-01-01T00:00:00+00:00",
});

function renderDialog() {
  return render(
    <ShareThreadDialog threadId="t1" open onOpenChange={() => {}} />,
  );
}

const itemOf = (token: string) =>
  screen
    .getByDisplayValue(`http://localhost:8080/share/${token}`)
    .closest("li")!;

beforeEach(() => {
  api.createShare.mockReset();
  api.listShares.mockReset();
  api.revokeShare.mockReset();
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
  });
});
afterEach(cleanup);

describe("ShareThreadDialog — links ativos", () => {
  it("carrega e mostra os links ativos da conversa", async () => {
    api.listShares.mockResolvedValue({ shares: [link("a"), link("b")] });
    renderDialog();

    expect(screen.getByText("share_dialog_loading")).toBeTruthy();
    await screen.findByDisplayValue(link("a").url);

    expect(screen.getByDisplayValue(link("b").url)).toBeTruthy();
    expect(api.listShares).toHaveBeenCalledWith("t1");
  });

  it("sem links ativos, avisa que não há nenhum", async () => {
    api.listShares.mockResolvedValue({ shares: [] });
    renderDialog();

    expect(await screen.findByText("share_dialog_none")).toBeTruthy();
  });

  it("falha ao listar mostra o erro e não trava a criação", async () => {
    api.listShares.mockRejectedValue(new Error("500"));
    renderDialog();

    expect((await screen.findByRole("alert")).textContent).toBe(
      "share_dialog_error",
    );
    expect(
      (
        screen.getByRole("button", {
          name: "share_dialog_create",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false);
  });
});

describe("ShareThreadDialog — criar", () => {
  it("cria com a validade escolhida e põe o link novo no topo da lista", async () => {
    api.listShares.mockResolvedValue({ shares: [link("velho")] });
    api.createShare.mockResolvedValue(link("novo"));
    renderDialog();
    await screen.findByDisplayValue(link("velho").url);

    fireEvent.change(screen.getByRole("combobox"), {
      target: { value: "168" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "share_dialog_create" }),
    );

    await screen.findByDisplayValue(link("novo").url);
    expect(api.createShare).toHaveBeenCalledWith("t1", 168);
    const urls = screen
      .getAllByLabelText("share_dialog_link_label")
      .map((input) => (input as HTMLInputElement).value);
    expect(urls).toEqual([link("novo").url, link("velho").url]);
  });

  it("falha ao criar mostra o erro e mantém a lista", async () => {
    api.listShares.mockResolvedValue({ shares: [link("a")] });
    api.createShare.mockRejectedValue(new Error("500"));
    renderDialog();
    await screen.findByDisplayValue(link("a").url);

    fireEvent.click(
      screen.getByRole("button", { name: "share_dialog_create" }),
    );

    expect((await screen.findByRole("alert")).textContent).toBe(
      "share_dialog_error",
    );
    expect(screen.getAllByLabelText("share_dialog_link_label")).toHaveLength(1);
  });
});

describe("ShareThreadDialog — copiar e revogar", () => {
  it("copia o endereço do link escolhido", async () => {
    api.listShares.mockResolvedValue({ shares: [link("a"), link("b")] });
    renderDialog();
    await screen.findByDisplayValue(link("a").url);

    fireEvent.click(
      within(itemOf("b")).getByRole("button", { name: "share_dialog_copy" }),
    );

    await within(itemOf("b")).findByRole("button", {
      name: "share_dialog_copied",
    });
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(link("b").url);
    expect(
      within(itemOf("a")).getByRole("button", { name: "share_dialog_copy" }),
    ).toBeTruthy();
  });

  it("revoga só o link escolhido", async () => {
    api.listShares.mockResolvedValue({ shares: [link("a"), link("b")] });
    api.revokeShare.mockResolvedValue(undefined);
    renderDialog();
    await screen.findByDisplayValue(link("a").url);

    fireEvent.click(
      within(itemOf("a")).getByRole("button", { name: "share_dialog_revoke" }),
    );

    await waitFor(() =>
      expect(screen.queryByDisplayValue(link("a").url)).toBeNull(),
    );
    expect(api.revokeShare).toHaveBeenCalledWith("a");
    expect(screen.getByDisplayValue(link("b").url)).toBeTruthy();
  });

  it("falha ao revogar mantém o link e mostra o erro", async () => {
    api.listShares.mockResolvedValue({ shares: [link("a")] });
    api.revokeShare.mockRejectedValue(new Error("500"));
    renderDialog();
    await screen.findByDisplayValue(link("a").url);

    fireEvent.click(
      within(itemOf("a")).getByRole("button", { name: "share_dialog_revoke" }),
    );

    expect((await screen.findByRole("alert")).textContent).toBe(
      "share_dialog_error",
    );
    expect(screen.getByDisplayValue(link("a").url)).toBeTruthy();
  });
});
