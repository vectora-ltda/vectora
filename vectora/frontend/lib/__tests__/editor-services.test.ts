import { describe, expect, it } from "vitest";
import { formatEditorText, lintEditorText } from "../editor-services";

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
});
