import { describe, expect, it } from "vitest";
import {
  formatEditorText,
  hasBlockingDiagnostics,
  lintEditorText,
} from "../editor-services";

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

  it("formats common text files and validates language-specific syntax", () => {
    expect(formatEditorText("README.md", "# Title  \nbody")).toBe(
      "# Title\nbody\n",
    );
    expect(lintEditorText("script.ts", "const value = {\n")).toEqual(
      expect.arrayContaining([expect.objectContaining({ severity: "error" })]),
    );
  });

  it("can promote warnings to blocking errors", () => {
    const warnings = lintEditorText("README.md", "#Title");
    expect(warnings[0]?.severity).toBe("warning");
    const errors = lintEditorText("README.md", "#Title", {
      warningsAsErrors: true,
    });
    expect(errors[0]?.severity).toBe("error");
    expect(hasBlockingDiagnostics(warnings, true)).toBe(true);
  });
});
