// @vitest-environment jsdom
/**
 * SkillsSection — seção Skills da Library. Cobre a sub-área "Catálogo":
 * vem expandida por padrão (não escondida atrás de um toggle fechado —
 * regressão de descoberta), lista GET /skills/catalog, instalar chama
 * POST /skills {skill_id}, toggle ainda permite recolher/reabrir; erro/borda:
 * catálogo vazio mostra estado específico, não quebra a lista de instaladas
 * ao lado (SkillsTab, mockado).
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import {
  render,
  screen,
  cleanup,
  waitFor,
  fireEvent,
  act,
} from "@testing-library/react";

vi.mock("@/components/settings/environment/tabs/skills-tab", () => ({
  SkillsTab: () => <div>stub-skills-tab</div>,
}));

import { SkillsSection } from "../library-skills-section";
import { useLibraryStore, type CatalogSkill } from "@/lib/stores/library-store";

afterEach(cleanup);

beforeEach(() => {
  useLibraryStore.setState({
    skillsItems: [],
    skillsLoading: false,
    skillsFetchedAt: null,
    skillsQuery: "",
    skillsError: null,
  });
});

const CATALOG: CatalogSkill[] = [
  {
    id: "pdf-extract",
    name: "PDF Extract",
    description: "Extrai texto de PDFs",
    source: "https://github.com/example/pdf-extract-skill",
  },
];

function mockFetch({
  entries = CATALOG as typeof CATALOG,
  installOk = true,
} = {}) {
  global.fetch = vi
    .fn()
    .mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/skills/catalog") {
        return Promise.resolve({
          ok: true,
          json: async () => ({ entries, total: entries.length }),
        } as Response);
      }
      if (url === "/skills" && init?.method === "POST") {
        return Promise.resolve({
          ok: installOk,
          json: async () => ({}),
        } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => ({}) } as Response);
    });
}

describe("SkillsSection — Catálogo", () => {
  it("vem expandido por padrão e lista as skills curadas do registry remoto sem precisar de clique", async () => {
    mockFetch();
    render(<SkillsSection query="" />);

    await waitFor(() => {
      expect(screen.getByText("PDF Extract")).toBeTruthy();
    });
  });

  it("toggle ainda permite recolher e reabrir o catálogo", async () => {
    mockFetch();
    render(<SkillsSection query="" />);
    await waitFor(() => screen.getByText("PDF Extract"));

    fireEvent.click(screen.getByText("Browse catalog"));
    expect(screen.queryByText("PDF Extract")).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("Browse catalog"));
    await waitFor(() => {
      expect(screen.getByText("PDF Extract")).toBeTruthy();
    });
  });

  it("instalar chama POST /skills com o id do catálogo", async () => {
    mockFetch();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<SkillsSection query="" />);
    await waitFor(() => screen.getByText("PDF Extract"));

    fireEvent.click(screen.getByText("Install"));

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        "/skills",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({
            skill_id: CATALOG[0].id,
            confirm_unverified: true,
          }),
        }),
      );
    });
  });

  it("digitar na busca dispara /skills/catalog?q= com debounce de 350ms; erro de rede na busca seguinte mantém o último resultado bom e mostra erro discreto", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetchMock = vi.fn(async (url: string) => {
      if (url === "/license/status") {
        return {
          ok: true,
          json: async () => ({ configured: false }),
        } as Response;
      }
      if (url === "/skills/catalog") {
        return {
          ok: true,
          json: async () => ({ entries: CATALOG }),
        } as Response;
      }
      if (url === "/skills/catalog?q=pdf") {
        return {
          ok: true,
          json: async () => ({ entries: CATALOG }),
        } as Response;
      }
      return { ok: true, json: async () => ({}) } as Response;
    });
    global.fetch = fetchMock as unknown as typeof fetch;

    const { rerender } = render(<SkillsSection query="" />);
    await vi.waitFor(() =>
      expect(screen.getByText("PDF Extract")).toBeTruthy(),
    );

    rerender(<SkillsSection query="pdf" />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(350);
    });
    await vi.waitFor(() => {
      expect(
        fetchMock.mock.calls.some((c) => c[0] === "/skills/catalog?q=pdf"),
      ).toBe(true);
    });

    fetchMock.mockImplementation(async (url: string) => {
      if (url === "/skills/catalog?q=falha") throw new Error("offline");
      return { ok: true, json: async () => ({}) } as Response;
    });
    rerender(<SkillsSection query="falha" />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(350);
    });

    await vi.waitFor(() => {
      expect(screen.getByText("PDF Extract")).toBeTruthy();
      expect(screen.getByText("Error searching skills")).toBeTruthy();
    });

    vi.useRealTimers();
  });

  it("catálogo vazio mostra estado específico, não erro e não quebra SkillsTab", async () => {
    mockFetch({ entries: [] });
    render(<SkillsSection query="" />);
    expect(screen.getByText("stub-skills-tab")).toBeTruthy();

    await waitFor(() => {
      expect(screen.getByText("No curated skills available yet.")).toBeTruthy();
    });
  });

  it("exibe confiança verificada sem badge redundante de comunidade", async () => {
    mockFetch({
      entries: [
        {
          ...CATALOG[0],
          id: "a",
          name: "Skill Oficial",
          vectora_verified: true,
        },
        { ...CATALOG[0], id: "b", name: "Skill Verificada", verified: true },
        { ...CATALOG[0], id: "c", name: "Skill Comunidade" },
      ],
    });
    render(<SkillsSection query="" />);

    await waitFor(() => screen.getByText("Skill Oficial"));
    expect(screen.getByText("Official")).toBeTruthy();
    expect(screen.getByText("Verified")).toBeTruthy();
    expect(screen.queryByText("Community", { exact: true })).toBeNull();
  });
});
