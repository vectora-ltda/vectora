import { describe, expect, it } from "vitest";
import { getModeComposition } from "@/components/layout/mode-composition";

describe("mode composition", () => {
  it.each([
    ["assistant", "sessions", "chat", "workbench"],
    ["ide", "workbench", "canvas", "chat"],
    ["kanban", "sessions", "kanban", null],
  ] as const)(
    "%s maps slots without moving the central header",
    (mode, left, center, right) => {
      expect(getModeComposition(mode)).toEqual({ left, center, right });
    },
  );
});
