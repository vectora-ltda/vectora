import { describe, expect, it } from "vitest";
import * as path from "path";
import {
  resolveRuntimeProfile,
  runtimeHome,
  runtimeUserDataName,
} from "../runtime-profile.js";

describe("runtime profiles", () => {
  it("separates the installed app from the development app", () => {
    expect(resolveRuntimeProfile({}, true)).toBe("stable");
    expect(resolveRuntimeProfile({}, false)).toBe("dev");
    expect(runtimeUserDataName("stable")).toBe("vectora");
    expect(runtimeUserDataName("dev")).toBe("vectora-dev");
  });

  it("sanitizes an explicit profile without changing the stable default", () => {
    expect(
      resolveRuntimeProfile({ VECTORA_RUNTIME_PROFILE: "dev/0.2.x" }, false),
    ).toBe("dev-0-2-x");
    expect(runtimeHome({}, "dev", "/home/user")).toBe(
      path.join("/home/user", ".vectora-dev"),
    );
  });

  it("honors an explicitly isolated home", () => {
    expect(
      runtimeHome(
        { VECTORA_HOME: "/tmp/vectora-profile" },
        "dev",
        "/home/user",
      ),
    ).toBe("/tmp/vectora-profile");
  });
});
