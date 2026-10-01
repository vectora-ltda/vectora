import { describe, expect, it } from "vitest";
import {
  getBrowserCapabilityMatrix,
  getBrowserRuntime,
} from "../browser-capability-matrix";

describe("browser capability matrix", () => {
  it("exposes every native profile capability on desktop", () => {
    expect(
      getBrowserCapabilityMatrix("desktop").every(
        (capability) => capability.status === "available",
      ),
    ).toBe(true);
  });

  it("does not claim Electron capabilities in the web runtime", () => {
    expect(
      getBrowserCapabilityMatrix("web").every(
        (capability) => capability.status === "unavailable",
      ),
    ).toBe(true);
  });

  it("detects the runtime from the bridge", () => {
    expect(getBrowserRuntime(true)).toBe("desktop");
    expect(getBrowserRuntime(false)).toBe("web");
  });
});
