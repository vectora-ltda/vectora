import type * as Monaco from "monaco-editor";
import type { EngineLanguageContribution } from "./contract";

const registered = new WeakSet<object>();

export function registerEngineLanguages(
  monaco: typeof import("monaco-editor"),
  contributions: readonly EngineLanguageContribution[],
): void {
  if (registered.has(monaco)) return;
  const existing = new Set(
    monaco.languages.getLanguages().map((language) => language.id),
  );
  for (const contribution of contributions) {
    if (!existing.has(contribution.id))
      monaco.languages.register({
        id: contribution.id,
        aliases: contribution.aliases,
        extensions: contribution.extensions,
        filenames: contribution.filenames,
      });
    monaco.languages.setMonarchTokensProvider(
      contribution.id,
      contribution.tokenizer,
    );
    monaco.languages.setLanguageConfiguration(
      contribution.id,
      contribution.configuration,
    );
  }
  registered.add(monaco);
}
