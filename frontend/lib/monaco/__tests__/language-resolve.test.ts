import { describe, expect, it } from "vitest";
import { languageFromFence, languageFromPath } from "../languages/resolve";
import { godotLanguages } from "../languages/godot";

const pathLanguage = (path: string) => languageFromPath(path, godotLanguages);
const fenceLanguage = (label: string) =>
  languageFromFence(label, godotLanguages);

describe("Godot language resolution", () => {
  it("resolves every supported file format and special project name", () => {
    expect(pathLanguage("scripts/player.GD")).toBe("gdscript");
    expect(pathLanguage("scenes/main.tscn")).toBe("godot-resource");
    expect(pathLanguage("resources/theme.tres")).toBe("godot-resource");
    expect(pathLanguage("project.godot")).toBe("godot-project");
    expect(pathLanguage("shaders/water.gdshaderinc")).toBe("gdshader");
    expect(pathLanguage("README.unknown")).toBe("plaintext");
  });

  it("resolves fences and keeps unknown labels safe", () => {
    expect(fenceLanguage("gdscript")).toBe("gdscript");
    expect(fenceLanguage("gd")).toBe("gdscript");
    expect(fenceLanguage("tscn")).toBe("godot-resource");
    expect(fenceLanguage("godot")).toBe("godot-project");
    expect(fenceLanguage("gdshader")).toBe("gdshader");
    expect(fenceLanguage("language-python")).toBe("python");
    expect(fenceLanguage("made-up-engine")).toBe("plaintext");
  });
});
