// @vitest-environment jsdom
/**
 * GitRepoSelector — escolher qual repositório do workspace o painel Git mostra.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { GitRepoSelector } from "../git-repo-selector";

const api = vi.hoisted(() => ({
  fetchGitRepos: vi.fn(),
  selectGitRepo: vi.fn(),
}));
vi.mock("../api", () => api);

const toast = vi.hoisted(() => ({ error: vi.fn() }));
vi.mock("@/lib/stores/toast-store", () => ({
  useToastStore: { getState: () => toast },
}));

// O Select do Radix não é operável em jsdom: um <select> nativo expõe o mesmo contrato.
vi.mock("@/components/ui/select", () => ({
  Select: ({
    value,
    onValueChange,
    disabled,
    children,
  }: {
    value?: string;
    onValueChange: (value: string) => void;
    disabled?: boolean;
    children: React.ReactNode;
  }) => (
    <select
      aria-label="seletor"
      value={value ?? ""}
      disabled={disabled}
      onChange={(event) => onValueChange(event.target.value)}
    >
      <option value="" disabled />
      {children}
    </select>
  ),
  SelectTrigger: () => null,
  SelectValue: () => null,
  SelectContent: ({ children }: { children: React.ReactNode }) => (
    <>{children}</>
  ),
  SelectItem: ({
    value,
    children,
  }: {
    value: string;
    children: React.ReactNode;
  }) => <option value={value}>{children}</option>,
}));

vi.mock("@/lib/paraglide/messages", () => ({
  m: new Proxy({}, { get: (_target, key) => () => String(key) }),
}));

const root = { path: "", name: "monorepo", kind: "root" as const };
const api1 = { path: "services/api", name: "api", kind: "repo" as const };
const lib = { path: "vendor/lib", name: "lib", kind: "submodule" as const };

const select = () =>
  screen.queryByLabelText("seletor") as HTMLSelectElement | null;
const options = () =>
  Array.from(select()!.options)
    .filter((option) => option.value !== "")
    .map((option) => [option.value, option.textContent]);

beforeEach(() => {
  api.fetchGitRepos.mockReset();
  api.selectGitRepo.mockReset();
  toast.error.mockReset();
});
afterEach(cleanup);

describe("GitRepoSelector — quando aparece", () => {
  it("não aparece quando só há o repositório padrão", async () => {
    api.fetchGitRepos.mockResolvedValue({ repos: [root], selected: "" });
    render(<GitRepoSelector workspaceId="ws" />);

    await waitFor(() => expect(api.fetchGitRepos).toHaveBeenCalled());

    expect(screen.queryByTestId("git-repo-selector")).toBeNull();
  });

  it("não aparece sem repositório algum nem quando a busca falha", async () => {
    api.fetchGitRepos.mockResolvedValueOnce({ repos: [], selected: "" });
    const first = render(<GitRepoSelector workspaceId="ws" />);
    await waitFor(() => expect(api.fetchGitRepos).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId("git-repo-selector")).toBeNull();
    first.unmount();

    api.fetchGitRepos.mockResolvedValueOnce(null);
    render(<GitRepoSelector workspaceId="ws" />);
    await waitFor(() => expect(api.fetchGitRepos).toHaveBeenCalledTimes(2));
    expect(screen.queryByTestId("git-repo-selector")).toBeNull();
  });

  it("lista os repositórios com o tipo, e o padrão fica selecionado", async () => {
    api.fetchGitRepos.mockResolvedValue({
      repos: [root, api1, lib],
      selected: "",
    });
    render(<GitRepoSelector workspaceId="ws" />);

    await screen.findByTestId("git-repo-selector");

    expect(options()).toEqual([
      ["__default__", "monorepo · git_repo_kind_root"],
      ["services/api", "services/api"],
      ["vendor/lib", "vendor/lib · git_repo_kind_submodule"],
    ]);
    expect(select()!.value).toBe("__default__");
  });

  it("mostra o repositório em uso", async () => {
    api.fetchGitRepos.mockResolvedValue({
      repos: [root, api1],
      selected: "services/api",
    });
    render(<GitRepoSelector workspaceId="ws" />);

    await screen.findByTestId("git-repo-selector");

    expect(select()!.value).toBe("services/api");
  });

  it("sem repositório na raiz, pede para escolher um", async () => {
    api.fetchGitRepos.mockResolvedValue({ repos: [api1, lib], selected: "" });
    render(<GitRepoSelector workspaceId="ws" />);

    await screen.findByTestId("git-repo-selector");

    expect(screen.getByText("git_repo_choose_hint")).toBeTruthy();
    expect(select()!.value).toBe("");
  });

  it("com um repositório padrão, não mostra a dica de escolha", async () => {
    api.fetchGitRepos.mockResolvedValue({ repos: [root, api1], selected: "" });
    render(<GitRepoSelector workspaceId="ws" />);

    await screen.findByTestId("git-repo-selector");

    expect(screen.queryByText("git_repo_choose_hint")).toBeNull();
  });

  it("busca de novo ao trocar de workspace", async () => {
    api.fetchGitRepos.mockResolvedValue({ repos: [root, api1], selected: "" });
    const { rerender } = render(<GitRepoSelector workspaceId="a" />);
    await screen.findByTestId("git-repo-selector");

    rerender(<GitRepoSelector workspaceId="b" />);

    await waitFor(() =>
      expect(api.fetchGitRepos).toHaveBeenLastCalledWith("b"),
    );
  });
});

describe("GitRepoSelector — escolher", () => {
  beforeEach(() => {
    api.fetchGitRepos.mockResolvedValue({
      repos: [root, api1, lib],
      selected: "",
    });
  });

  it("grava a escolha e avisa quem usa o painel", async () => {
    api.selectGitRepo.mockResolvedValue({
      repos: [root, api1, lib],
      selected: "services/api",
    });
    const onSelected = vi.fn();
    render(<GitRepoSelector workspaceId="ws" onSelected={onSelected} />);
    await screen.findByTestId("git-repo-selector");

    fireEvent.change(select()!, { target: { value: "services/api" } });

    await waitFor(() =>
      expect(onSelected).toHaveBeenCalledWith("services/api"),
    );
    expect(api.selectGitRepo).toHaveBeenCalledWith("ws", "services/api");
    expect(select()!.value).toBe("services/api");
  });

  it("voltar ao padrão envia o caminho vazio", async () => {
    api.fetchGitRepos.mockResolvedValue({
      repos: [root, api1],
      selected: "services/api",
    });
    api.selectGitRepo.mockResolvedValue({ repos: [root, api1], selected: "" });
    const onSelected = vi.fn();
    render(<GitRepoSelector workspaceId="ws" onSelected={onSelected} />);
    await screen.findByTestId("git-repo-selector");

    fireEvent.change(select()!, { target: { value: "__default__" } });

    await waitFor(() =>
      expect(api.selectGitRepo).toHaveBeenCalledWith("ws", ""),
    );
    await waitFor(() => expect(onSelected).toHaveBeenCalledWith(""));
  });

  it("quando o servidor recusa, mostra o motivo e não avisa nem troca", async () => {
    api.selectGitRepo.mockRejectedValue(new Error("Confie nesta pasta."));
    const onSelected = vi.fn();
    render(<GitRepoSelector workspaceId="ws" onSelected={onSelected} />);
    await screen.findByTestId("git-repo-selector");

    fireEvent.change(select()!, { target: { value: "vendor/lib" } });

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith("git_repo_error", {
        description: "Confie nesta pasta.",
      }),
    );
    expect(onSelected).not.toHaveBeenCalled();
    expect(select()!.value).toBe("__default__");
  });

  it("fica desabilitado enquanto grava", async () => {
    let finish!: (value: unknown) => void;
    api.selectGitRepo.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    render(<GitRepoSelector workspaceId="ws" />);
    await screen.findByTestId("git-repo-selector");

    fireEvent.change(select()!, { target: { value: "services/api" } });
    await waitFor(() => expect(select()!.disabled).toBe(true));
    finish({ repos: [root, api1, lib], selected: "services/api" });

    await waitFor(() => expect(select()!.disabled).toBe(false));
  });
});
