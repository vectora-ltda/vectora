// @vitest-environment jsdom
/**
 * FileIcon — wrapper fino sobre `@react-symbols/icons/utils`. A resolução
 * extensão/nome→ícone em si é responsabilidade da lib (confiável, testada
 * upstream); o que este arquivo cobre é a parte que É nossa: passar
 * `fileName` normalizado e injetar os overrides de Godot que a lib não
 * mapeia nativamente (confirmado lendo o bundle publicado — `gd`,
 * `gdshader`, `gdignore` e o arquivo `project.godot` não existem no mapa
 * de extensão/nome da lib).
 */
import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";

const receivedProps: Record<string, unknown>[] = [];

vi.mock("@react-symbols/icons/utils", () => ({
  FileIcon: (props: Record<string, unknown>) => {
    receivedProps.push(props);
    return <span data-testid="rs-file-icon" />;
  },
}));

vi.mock("@react-symbols/icons/files", () => ({
  GodotProject: () => <span>GodotProject</span>,
  GodotScript: () => <span>GodotScript</span>,
  GodotShader: () => <span>GodotShader</span>,
  GodotIgnore: () => <span>GodotIgnore</span>,
}));

import { FileIcon } from "@/components/icons/file-icon";
import {
  GodotProject,
  GodotScript,
  GodotShader,
  GodotIgnore,
} from "@react-symbols/icons/files";

describe("FileIcon", () => {
  it("delega pro FileIcon da lib com fileName normalizado e autoAssign ligado", () => {
    receivedProps.length = 0;
    render(<FileIcon name="Example.TS" />);
    expect(receivedProps).toHaveLength(1);
    expect(receivedProps[0].fileName).toBe("example.ts");
    expect(receivedProps[0].autoAssign).toBe(true);
  });

  it("injeta overrides de extensão Godot (gd/gdshader/gdignore/tscn/scn/tres) que a lib não mapeia", () => {
    receivedProps.length = 0;
    render(<FileIcon name="player.gd" />);
    const ext = receivedProps[0].editFileExtensionData as Record<
      string,
      unknown
    >;
    expect(ext.gd).toBe(GodotScript);
    expect(ext.gdshader).toBe(GodotShader);
    expect(ext.gdignore).toBe(GodotIgnore);
    expect(ext.tscn).toBe(GodotProject);
    expect(ext.scn).toBe(GodotProject);
    expect(ext.tres).toBe(GodotProject);
  });

  it("injeta override de nome exato pra project.godot (raiz de projeto Godot)", () => {
    receivedProps.length = 0;
    render(<FileIcon name="project.godot" />);
    const names = receivedProps[0].editFileNameData as Record<string, unknown>;
    expect(names["project.godot"]).toBe(GodotProject);
  });

  it("normaliza case (PROJECT.GODOT == project.godot) antes de passar pra lib", () => {
    receivedProps.length = 0;
    render(<FileIcon name="PROJECT.GODOT" />);
    expect(receivedProps[0].fileName).toBe("project.godot");
  });

  it("repassa className customizado, com default sensato quando omitido", () => {
    receivedProps.length = 0;
    render(<FileIcon name="a.ts" className="w-5 h-5" />);
    expect(receivedProps[0].className).toBe("w-5 h-5");

    receivedProps.length = 0;
    render(<FileIcon name="b.ts" />);
    expect(receivedProps[0].className).toBe("w-3.5 h-3.5 shrink-0");
  });

  it("erro/borda: nome de arquivo sem extensão não lança exceção (delega fallback pra lib)", () => {
    receivedProps.length = 0;
    expect(() => render(<FileIcon name="Makefile" />)).not.toThrow();
    expect(receivedProps[0].fileName).toBe("makefile");
  });
});
