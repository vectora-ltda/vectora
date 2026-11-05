/**
 * FileIcon — ícones de arquivo por extensão/nome, via `@react-symbols/icons`
 * (tema Symbols, o mesmo do VS Code), com overrides pontuais pra Godot: a
 * lib não mapeia `gd`/`gdshader`/`gdignore` nem o arquivo `project.godot`
 * pros ícones `Godot*` que ela já embarca (confirmado lendo o bundle
 * publicado — essas chaves simplesmente não existem no mapa de extensão/
 * nome), e mapeia `tscn`/`tres` pra ícones sem relação (Dune/AngularService).
 */
import {
  FileIcon as ReactSymbolsFileIcon,
  type ExtensionType,
} from "@react-symbols/icons/utils";
import {
  GodotProject,
  GodotScript,
  GodotShader,
  GodotIgnore,
} from "@react-symbols/icons/files";

const GODOT_EXTENSION_OVERRIDES: ExtensionType = {
  gd: GodotScript,
  gdshader: GodotShader,
  gdignore: GodotIgnore,
  tscn: GodotProject,
  scn: GodotProject,
  tres: GodotProject,
};

const GODOT_FILENAME_OVERRIDES: ExtensionType = {
  "project.godot": GodotProject,
};

export function FileIcon({
  name,
  className = "w-3.5 h-3.5 shrink-0",
}: {
  name: string;
  className?: string;
}) {
  return (
    <ReactSymbolsFileIcon
      fileName={name.toLowerCase()}
      autoAssign
      editFileNameData={GODOT_FILENAME_OVERRIDES}
      editFileExtensionData={GODOT_EXTENSION_OVERRIDES}
      className={className}
    />
  );
}
