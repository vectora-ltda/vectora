// @vitest-environment jsdom
/**
 * Testes do StashModal — lista/push/pop/drop de stashes.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  cleanup,
} from "@testing-library/react";
import { StashModal } from "../stash-modal";
import * as api from "../api";

vi.mock("@/lib/paraglide/messages", () => ({
  m: new Proxy(
    {},
    {
      get:
        (_target, prop) =>
        (..._args: unknown[]) =>
          String(prop),
    },
  ),
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("StashModal", () => {
  it("carrega a lista de stashes ao abrir e mostra estado vazio quando não há entradas", async () => {
    const spy = vi
      .spyOn(api, "apiStash")
      .mockResolvedValue({ entries: [], message: "" });

    render(
      <StashModal
        workspaceId="ws1"
        open={true}
        onOpenChange={() => {}}
        onChanged={() => {}}
      />,
    );

    await waitFor(() => expect(spy).toHaveBeenCalledWith("ws1", "list"));
    expect(
      await screen.findByText("workbench_diff_stash_empty"),
    ).toBeInTheDocument();
  });

  it("não carrega a lista quando o modal está fechado", () => {
    const spy = vi.spyOn(api, "apiStash");
    render(
      <StashModal
        workspaceId="ws1"
        open={false}
        onOpenChange={() => {}}
        onChanged={() => {}}
      />,
    );
    expect(spy).not.toHaveBeenCalled();
  });

  it("renderiza as entradas de stash retornadas pela API", async () => {
    vi.spyOn(api, "apiStash").mockResolvedValue({
      entries: [{ index: 0, label: "WIP on main: abc" }],
      message: "",
    });

    render(
      <StashModal
        workspaceId="ws1"
        open={true}
        onOpenChange={() => {}}
        onChanged={() => {}}
      />,
    );

    expect(await screen.findByText("WIP on main: abc")).toBeInTheDocument();
  });

  it("digitar um nome e apertar Enter faz push do stash com o nome informado", async () => {
    const spy = vi
      .spyOn(api, "apiStash")
      .mockResolvedValue({ entries: [], message: "" });
    const onChanged = vi.fn();

    render(
      <StashModal
        workspaceId="ws1"
        open={true}
        onOpenChange={() => {}}
        onChanged={onChanged}
      />,
    );
    await waitFor(() => expect(spy).toHaveBeenCalledWith("ws1", "list"));

    const input = screen.getByPlaceholderText(
      "workbench_diff_stash_name_placeholder",
    );
    fireEvent.change(input, { target: { value: "meu-stash" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith("ws1", "push", { name: "meu-stash" }),
    );
    expect(onChanged).toHaveBeenCalled();
  });

  it("clicar em pop chama a API com action=pop e recarrega a lista", async () => {
    const spy = vi
      .spyOn(api, "apiStash")
      .mockResolvedValueOnce({
        entries: [{ index: 0, label: "stash@{0}" }],
        message: "",
      })
      .mockResolvedValueOnce({ entries: [], message: "" })
      .mockResolvedValueOnce({ entries: [], message: "" });

    render(
      <StashModal
        workspaceId="ws1"
        open={true}
        onOpenChange={() => {}}
        onChanged={() => {}}
      />,
    );

    fireEvent.click(await screen.findByText("workbench_diff_stash_pop"));

    await waitFor(() => expect(spy).toHaveBeenCalledWith("ws1", "pop", {}));
  });

  it("clicar no botão de drop chama a API com index e drop", async () => {
    const spy = vi
      .spyOn(api, "apiStash")
      .mockResolvedValueOnce({
        entries: [{ index: 2, label: "stash@{2}" }],
        message: "",
      })
      .mockResolvedValueOnce({ entries: [], message: "" })
      .mockResolvedValueOnce({ entries: [], message: "" });

    render(
      <StashModal
        workspaceId="ws1"
        open={true}
        onOpenChange={() => {}}
        onChanged={() => {}}
      />,
    );

    await screen.findByText("stash@{2}");
    const dropBtn = screen.getByTitle("workbench_diff_stash_drop");
    fireEvent.click(dropBtn);

    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith("ws1", "drop", { index: 2 }),
    );
  });
});
