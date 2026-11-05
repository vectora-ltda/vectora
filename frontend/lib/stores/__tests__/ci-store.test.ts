/**
 * Tests para o ci-store: último CIRun recebido via webhook (volátil).
 */

import { describe, it, expect, beforeEach } from "vitest";
import { useCIStore, type CIRun } from "../ci-store";

function run(over: Partial<CIRun> = {}): CIRun {
  return {
    repo: "owner/repo",
    name: "CI",
    status: "completed",
    conclusion: "success",
    htmlUrl: "https://github.com/owner/repo/actions/runs/1",
    at: 1000,
    ...over,
  };
}

beforeEach(() => {
  useCIStore.setState({ lastRun: null });
});

describe("ci-store", () => {
  it("lastRun começa null", () => {
    expect(useCIStore.getState().lastRun).toBeNull();
  });

  it("setRun define o run", () => {
    useCIStore.getState().setRun(run());
    expect(useCIStore.getState().lastRun?.repo).toBe("owner/repo");
  });

  it("setRun preserva todos os campos", () => {
    const r = run({ name: "deploy", status: "in_progress", conclusion: null });
    useCIStore.getState().setRun(r);
    expect(useCIStore.getState().lastRun).toEqual(r);
  });

  it("setRun sobrescreve o run anterior", () => {
    const s = useCIStore.getState();
    s.setRun(run({ name: "a" }));
    s.setRun(run({ name: "b" }));
    expect(useCIStore.getState().lastRun?.name).toBe("b");
  });

  it("clear volta a null", () => {
    useCIStore.getState().setRun(run());
    useCIStore.getState().clear();
    expect(useCIStore.getState().lastRun).toBeNull();
  });

  it("clear quando já null é no-op", () => {
    useCIStore.getState().clear();
    expect(useCIStore.getState().lastRun).toBeNull();
  });

  it("aceita conclusion null (run em andamento)", () => {
    useCIStore
      .getState()
      .setRun(run({ status: "in_progress", conclusion: null }));
    expect(useCIStore.getState().lastRun?.conclusion).toBeNull();
  });

  it("aceita conclusion failure", () => {
    useCIStore.getState().setRun(run({ conclusion: "failure" }));
    expect(useCIStore.getState().lastRun?.conclusion).toBe("failure");
  });

  it("preserva o timestamp at", () => {
    useCIStore.getState().setRun(run({ at: 99999 }));
    expect(useCIStore.getState().lastRun?.at).toBe(99999);
  });

  it("preserva a htmlUrl para abrir no GitHub", () => {
    const url = "https://github.com/x/y/actions/runs/42";
    useCIStore.getState().setRun(run({ htmlUrl: url }));
    expect(useCIStore.getState().lastRun?.htmlUrl).toBe(url);
  });

  it("setRun após clear registra de novo", () => {
    const s = useCIStore.getState();
    s.setRun(run());
    s.clear();
    s.setRun(run({ name: "novo" }));
    expect(useCIStore.getState().lastRun?.name).toBe("novo");
  });

  it("status queued é preservado", () => {
    useCIStore.getState().setRun(run({ status: "queued", conclusion: null }));
    expect(useCIStore.getState().lastRun?.status).toBe("queued");
  });
});
