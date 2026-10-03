import { describe, expect, it } from "vitest";
import {
  formatEditorText,
  hasBlockingDiagnostics,
  lintEditorText,
} from "../editor-services";

describe("editor services", () => {
  it("preserves JSONC comments and trailing commas while formatting", () => {
    const input = '{\n// user comment\n"enabled":true,\n}';
    expect(lintEditorText("settings.jsonc", input)).toEqual([]);
    const formatted = formatEditorText("settings.jsonc", input);
    expect(formatted).toContain("// user comment");
    expect(formatted).toContain('"enabled": true,');
    expect(
      lintEditorText("settings.json", input).some(
        (d) => d.severity === "error",
      ),
    ).toBe(true);
  });

  it("reports the actual error line independently of diagnostic message length", () => {
    const text = '{\n"padding": "' + "x".repeat(300) + '",\n"invalid": !\n}';
    expect(lintEditorText("settings.json", text)[0].line).toBe(3);
    expect(formatEditorText("settings.json", text)).toBe(text);
  });
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
