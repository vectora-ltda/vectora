import { describe, expect, it } from "vitest";
import { runOutcomeOf } from "../run-outcome";

describe("runOutcomeOf", () => {
  it("conclusão normal, erro e backend antigo não mostram indicador", () => {
    expect(runOutcomeOf("stop")).toBeNull();
    expect(runOutcomeOf("error")).toBeNull();
    expect(runOutcomeOf("")).toBeNull();
    expect(runOutcomeOf(undefined)).toBeNull();
  });

  it("cancelamento e interrupção viram 'cancelled'", () => {
    expect(runOutcomeOf("cancelled")).toBe("cancelled");
    expect(runOutcomeOf("interrupted")).toBe("cancelled");
  });

  it("limites de passos viram 'limit'", () => {
    expect(runOutcomeOf("max_iterations")).toBe("limit");
    expect(runOutcomeOf("loop_cap_exceeded")).toBe("limit");
  });
});
