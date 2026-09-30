import type { EngineLanguageContribution } from "./contract";

const BUILTIN_NAMES: Record<string, string> = {
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

const BUILTIN_EXTENSIONS: Record<string, string> = {
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
const BUILTIN_FENCES: Record<string, string> = {
  typescript: "typescript",
  ts: "typescript",
  javascript: "javascript",
  js: "javascript",
  python: "python",
  py: "python",
  json: "json",
  css: "css",
  scss: "scss",
  less: "less",
  html: "html",
  xml: "xml",
  markdown: "markdown",
  md: "markdown",
  diff: "diff",
  rust: "rust",
  go: "go",
  java: "java",
  c: "c",
  cpp: "cpp",
  csharp: "csharp",
  cs: "csharp",
  ruby: "ruby",
  php: "php",
  shell: "shell",
  bash: "shell",
  yaml: "yaml",
  yml: "yaml",
  toml: "ini",
  ini: "ini",
  sql: "sql",
  graphql: "graphql",
  swift: "swift",
  kotlin: "kotlin",
  kt: "kotlin",
  scala: "scala",
  text: "plaintext",
  plaintext: "plaintext",
};

function basename(path: string): string {
  return (path.split(/[\\/]/).pop() ?? "").toLowerCase();
}

export function languageFromPath(
  path: string,
  contributions: readonly EngineLanguageContribution[] = [],
): string {
  const base = basename(path);
  const names = new Map<string, string>();
  for (const contribution of contributions) {
    for (const filename of contribution.filenames ?? [])
      names.set(filename.toLowerCase(), contribution.id);
  }
  const special = names.get(base) ?? BUILTIN_NAMES[base];
  if (special) return special;
  if (base.startsWith(".env")) return "ini";
  if (base.startsWith("dockerfile")) return "dockerfile";
  const ext = base.includes(".") ? `.${base.split(".").pop() ?? ""}` : "";
  for (const contribution of contributions) {
    if (
      (contribution.extensions ?? []).some(
        (candidate) => candidate.toLowerCase() === ext,
      )
    )
      return contribution.id;
  }
  return BUILTIN_EXTENSIONS[ext.slice(1)] ?? "plaintext";
}

export function languageFromFence(
  label: string,
  contributions: readonly EngineLanguageContribution[] = [],
): string {
  const normalized = label
    .trim()
    .toLowerCase()
    .replace(/^language-/, "");
  if (!normalized) return "plaintext";
  for (const contribution of contributions) {
    const aliases = [
      contribution.id,
      ...contribution.aliases,
      ...(contribution.fenceAliases ?? []),
    ];
    if (aliases.some((alias) => alias.toLowerCase() === normalized))
      return contribution.id;
  }
  return BUILTIN_FENCES[normalized] ?? "plaintext";
}
