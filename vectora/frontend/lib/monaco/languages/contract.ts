import type * as Monaco from "monaco-editor";

export interface EngineLanguageSourceContract {
  engine: string;
  version: string;
  files: readonly string[];
  dynamicSymbols?: readonly string[];
}

/** Contrato estável para contribuições de linguagens do Vectora. */
export interface EngineLanguageContribution {
  id: string;
  aliases: string[];
  extensions?: string[];
  filenames?: string[];
  fenceAliases?: string[];
  configuration: Monaco.languages.LanguageConfiguration;
  tokenizer: Monaco.languages.IMonarchLanguage;
  source?: EngineLanguageSourceContract;
}
