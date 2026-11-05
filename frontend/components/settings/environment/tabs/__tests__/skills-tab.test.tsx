// @vitest-environment jsdom
/**
 * SkillsTab — cobre o callback opcional onSkillsChange, disparado a cada
 * refresh com a contagem atual de skills instaladas.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";

import { SkillsTab } from "../skills-tab";

afterEach(cleanup);

function mockFetch(skills: object[]) {
  global.fetch = vi.fn().mockImplementation((url: string) => {
    if (url === "/skills") {
      return Promise.resolve({
        ok: true,
        json: async () => ({ skills }),
      } as Response);
    }
    return Promise.resolve({ ok: true, json: async () => ({}) } as Response);
  });
}

describe("SkillsTab", () => {
  beforeEach(() => {
    mockFetch([
      {
        id: "s1",
        name: "Skill 1",
        description: "d",
        source: "https://example.com/s1",
        path: "/p",
        installed_at: "",
        installed_by: "",
      },
    ]);
  });

  it("renderiza sem props (uso atual em Settings → Skills)", async () => {
    render(<SkillsTab />);
    await waitFor(() => {
      expect(screen.getByText("Skill 1")).toBeTruthy();
    });
    expect(
      screen.queryByPlaceholderText(/github\.com|local path|caminho/i),
    ).toBeNull();
  });

  it("chama onSkillsChange com a contagem após carregar", async () => {
    const onSkillsChange = vi.fn();
    render(<SkillsTab onSkillsChange={onSkillsChange} />);
    await waitFor(() => {
      expect(onSkillsChange).toHaveBeenCalledWith(1);
    });
  });

  it("erro/borda: lista vazia chama onSkillsChange(0), sem quebrar", async () => {
    mockFetch([]);
    const onSkillsChange = vi.fn();
    render(<SkillsTab onSkillsChange={onSkillsChange} />);
    await waitFor(() => {
      expect(onSkillsChange).toHaveBeenCalledWith(0);
    });
  });
});
