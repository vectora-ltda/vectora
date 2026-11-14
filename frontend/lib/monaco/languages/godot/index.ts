import { gdscriptLanguage } from "./gdscript";
import { godotResourceLanguage } from "./godot-resource";
import { godotProjectLanguage } from "./godot-project";
import { gdshaderLanguage } from "./gdshader";

export const godotLanguages = [
  gdscriptLanguage,
  godotResourceLanguage,
  godotProjectLanguage,
  gdshaderLanguage,
] as const;
