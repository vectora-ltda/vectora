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
    expect(runtimeHome({}, "preview", "/home/user")).toBe(
      path.join("/home/user", ".vectora-preview"),
    );
    expect(
      resolveRuntimeProfile({ VECTORA_RUNTIME_PROFILE: "   " }, false),
    ).toBe("dev");
    expect(
      resolveRuntimeProfile({ VECTORA_RUNTIME_PROFILE: "prévia🚀" }, false),
    ).toBe("pr-via-");
  });

  it("honors an explicitly isolated home", () => {
    expect(
      runtimeHome(
        { VECTORA_HOME: "/tmp/vectora-profile" },
        "dev",
        "/home/user",
      ),
    ).toBe(path.normalize("/tmp/vectora-profile"));
  });

  it("normalizes tilde and relative explicit homes", () => {
    expect(
      runtimeHome({ VECTORA_HOME: "~/vectora-profile" }, "dev", "/home/user"),
    ).toBe(path.resolve("/home/user", "vectora-profile"));
    expect(
      runtimeHome({ VECTORA_HOME: "profiles/vectora" }, "dev", "/home/user"),
    ).toBe(path.resolve("/home/user", "profiles", "vectora"));
  });
});
