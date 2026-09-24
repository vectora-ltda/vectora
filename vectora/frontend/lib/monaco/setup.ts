/**
 * Configuração local do Monaco — sem CDN.
 *
 * O `@monaco-editor/react`, por padrão, baixa o Monaco de um CDN em runtime.
 * Isso quebra no Electron e em ambientes offline (a proposta self-hosted do
 * Vectora). Aqui apontamos o loader para o pacote `monaco-editor` empacotado
 * pelo Vite e registramos os web workers via imports `?worker`.
 *
 * Os workers são importados por caminho relativo a `node_modules`, não pelo
 * specifier nu do pacote (`monaco-editor/esm/...`): o resolver do Rolldown
 * (bundler do Vite 8) falha ao resolver o primeiro import `?worker` com
 * specifier de pacote de cada build — não importa qual worker é o primeiro,
 * sempre quebra; caminho relativo evita essa resolução via `exports` do
 * `package.json` e não depende de ordem.
 *
 * Importar este módulo (efeito colateral) antes de montar qualquer `<Editor>`.
 */

// Os imports `?worker` são módulos virtuais do Vite (cada um exporta um
// construtor Worker como default); o resolver do oxlint não os entende.
/* oxlint-disable import/default */
import * as monaco from "monaco-editor";
import editorWorker from "../../node_modules/monaco-editor/esm/vs/editor/editor.worker.js?worker";
import jsonWorker from "../../node_modules/monaco-editor/esm/vs/language/json/json.worker.js?worker";
import cssWorker from "../../node_modules/monaco-editor/esm/vs/language/css/css.worker.js?worker";
import htmlWorker from "../../node_modules/monaco-editor/esm/vs/language/html/html.worker.js?worker";
import tsWorker from "../../node_modules/monaco-editor/esm/vs/language/typescript/ts.worker.js?worker";
import { loader } from "@monaco-editor/react";

declare global {
  interface Window {
    MonacoEnvironment?: monaco.Environment;
  }
}

self.MonacoEnvironment = {
  getWorker(_workerId: string, label: string) {
    if (label === "json") return new jsonWorker();
    if (label === "css" || label === "scss" || label === "less") {
      return new cssWorker();
    }
    if (label === "html" || label === "handlebars" || label === "razor") {
      return new htmlWorker();
    }
    if (label === "typescript" || label === "javascript") {
      return new tsWorker();
    }
    return new editorWorker();
  },
};

loader.config({ monaco });

/** Linguagem do Monaco a partir do caminho do arquivo.
 *
 * Trata primeiro nomes especiais e dotfiles (sem extensão "real"), depois
 * cai na extensão. Desconhecido → "plaintext" (ainda editável no Monaco). */
export function languageFromPath(path: string): string {
  const base = (path.split(/[/\\]/).pop() ?? "").toLowerCase();

  // Nomes especiais / dotfiles cujo realce não vem da extensão.
  const byName: Record<string, string> = {
    ".gitignore": "ignore",
    ".dockerignore": "ignore",
    ".npmignore": "ignore",
    ".gitattributes": "ini",
    ".editorconfig": "ini",
    dockerfile: "dockerfile",
    makefile: "makefile",
    procfile: "yaml",
    ".bashrc": "shell",
    ".zshrc": "shell",
    ".profile": "shell",
  };
  if (base in byName) return byName[base];
  if (base.startsWith(".env")) return "ini"; // .env, .env.local, .env.production
  if (base.startsWith("dockerfile")) return "dockerfile"; // Dockerfile.dev

  const ext = base.includes(".") ? (base.split(".").pop() ?? "") : "";
  const map: Record<string, string> = {
    ts: "typescript",
    tsx: "typescript",
    js: "javascript",
    jsx: "javascript",
    mjs: "javascript",
    cjs: "javascript",
    json: "json",
    css: "css",
    scss: "scss",
    less: "less",
    html: "html",
    htm: "html",
    xml: "xml",
    md: "markdown",
    markdown: "markdown",
    diff: "diff",
    py: "python",
    rs: "rust",
    go: "go",
    java: "java",
    c: "c",
    h: "c",
    cpp: "cpp",
    hpp: "cpp",
    cs: "csharp",
    rb: "ruby",
    php: "php",
    sh: "shell",
    bash: "shell",
    yaml: "yaml",
    yml: "yaml",
    toml: "ini",
    ini: "ini",
    sql: "sql",
    graphql: "graphql",
    swift: "swift",
    kt: "kotlin",
    scala: "scala",
    r: "r",
  };
  return map[ext] ?? "plaintext";
}
