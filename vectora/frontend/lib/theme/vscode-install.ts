import {
  parse as parseJsonc,
  printParseErrorCode,
  type ParseError,
} from "jsonc-parser";
import { useSettingsStore } from "@/lib/stores/settings-store";
import type { ThemePresetDef } from "@/lib/theme/presets";
import {
  convertVscodeColorTheme,
  isLightTheme,
} from "@/lib/theme/vscode-convert";

/** Temas do VS Code são arquivos JSONC (comentários `//`/`/* *\/` e vírgula
 * final permitidos pelo próprio schema oficial do editor) — `JSON.parse`
 * lançaria `SyntaxError` num tema legítimo que usa qualquer um dos dois. */
export function parseVscodeThemeJson(contents: string): unknown {
  const errors: ParseError[] = [];
  const value = parseJsonc(contents, errors, { allowTrailingComma: true });
  if (errors.length > 0) {
    throw new Error(
      `Tema VS Code com JSON inválido: ${printParseErrorCode(errors[0]!.error)}.`,
    );
  }
  return value;
}

const MARKETPLACE_ID_RE = /^[\w-]+\.[\w-]+$/;

export interface VscodeMarketplaceSearchItem {
  extensionId: string;
  displayName: string;
  publisher: string;
}

/** Busca no VS Code Marketplace via IPC do Electron. Devolve `[]` em modo
 * navegador/servidor (`window.vectora?.themes` ausente) — o caller decide
 * se esconde a UI de busca por completo (ver `theme-picker.tsx`). */
export async function searchVscodeMarketplaceThemes(
  query: string,
  limit = 10,
): Promise<VscodeMarketplaceSearchItem[]> {
  const api = window.vectora?.themes;
  if (!api) return [];
  const results = await api.searchMarketplace(query, limit);
  return results.map((r) => ({
    extensionId: r.extensionId,
    displayName: r.displayName,
    publisher: r.publisher,
  }));
}

/**
 * Baixa (via IPC do Electron) e instala um tema do VS Code Marketplace pelo
 * id da extensão (`publisher.name`). Lança erro claro — nunca falha
 * silenciosamente — quando: o id é inválido, a ponte desktop não existe
 * (modo navegador) ou a extensão não declara nenhum tema.
 */
export async function installVscodeThemeFromMarketplace(
  extensionId: string,
): Promise<ThemePresetDef> {
  if (!MARKETPLACE_ID_RE.test(extensionId)) {
    throw new Error(`Id de extensão inválido: ${extensionId}`);
  }
  const api = window.vectora?.themes;
  if (!api) {
    throw new Error(
      "Instalação de temas do VS Code Marketplace só está disponível no app desktop.",
    );
  }
  const file = await api.fetchMarketplace(extensionId);
  const first = file.themes[0];
  if (!first) {
    throw new Error(`A extensão ${extensionId} não declara nenhum tema.`);
  }
  const colors = convertVscodeColorTheme(parseVscodeThemeJson(first.contents));
  const themeId =
    String(
      (first as { id?: unknown; path?: unknown }).id ??
        (first as { path?: unknown }).path ??
        file.displayName,
    )
      .replace(/[^a-zA-Z0-9._-]+/g, "-")
      .replace(/^-+|-+$/g, "") || "theme";
  const mode = isLightTheme(colors) ? "light" : "dark";
  const theme: ThemePresetDef = {
    id: `vscode-${extensionId}-${themeId}`,
    label: file.displayName,
    mode,
    family: `vscode:${extensionId}:${themeId}`,
    colors,
  };
  useSettingsStore.getState().addInstalledTheme(theme);
  return theme;
}
