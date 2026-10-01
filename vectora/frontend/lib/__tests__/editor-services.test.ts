import { describe, expect, it } from "vitest";
import { formatEditorText, lintEditorText } from "../editor-services";

describe("editor services", () => {
  it("formats JSON and normalizes line endings", () => {
    expect(formatEditorText("settings.json", '{"enabled":true}\r\n')).toBe(
      '{\n  "enabled": true\n}\n',
    );
  });

  it("reports invalid JSON and trailing whitespace", () => {
    const diagnostics = lintEditorText(
      "settings.json",
      '{\n  "enabled": true,  \n',
    );
    expect(diagnostics.some((item) => item.severity === "error")).toBe(true);
    expect(diagnostics.some((item) => item.message.includes("Espaços"))).toBe(
      true,
    );
  });
});
