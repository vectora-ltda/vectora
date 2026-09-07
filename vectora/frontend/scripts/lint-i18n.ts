import { readFile, readdir } from "node:fs/promises";
import { extname, join, relative, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { promisify } from "node:util";
import { execFile } from "node:child_process";
import { parse } from "@babel/parser";

export interface I18nViolation {
  column: number;
  line: number;
  message: string;
  file: string;
}

const TEXT_ATTRIBUTES = new Set(["alt", "aria-label", "placeholder", "title"]);
const IGNORED_PARTS = new Set([
  "e2e",
  "tests",
  "__tests__",
  "paraglide",
  "node_modules",
  "dist",
  "build",
  "coverage",
  "vendor",
  "generated",
  "public",
]);
const execFileAsync = promisify(execFile);

export function isIgnoredPath(filePath: string): boolean {
  const normalized = filePath.replaceAll("\\", "/");
  const fileName = normalized.split("/").at(-1) ?? normalized;
  return (
    /\.(?:test|spec)\.[^.]+$/i.test(fileName) ||
    normalized
      .split("/")
      .some((part) =>
        [
          "e2e",
          "tests",
          "__tests__",
          "paraglide",
          "node_modules",
          "dist",
          "build",
          "coverage",
          "vendor",
          "generated",
          "public",
        ].includes(part),
      )
  );
}

/** Returns whether text contains user-visible letters. */
function hasLetters(value: string): boolean {
  return /[\p{L}]/u.test(value);
}

/** Adds a formatted violation for a source node. */
function addViolation(
  file: string,
  node: { loc?: { start: { line: number; column: number } } },
  kind: string,
  violations: I18nViolation[],
): void {
  violations.push({
    file,
    line: node.loc?.start.line ?? 1,
    column: (node.loc?.start.column ?? 0) + 1,
    message: `${kind} must use a Paraglide message (m.* or mDyn()).`,
  });
}

function staticLiteralText(node: unknown): string | undefined {
  if (!node || typeof node !== "object") return undefined;
  const value = node as {
    type?: string;
    value?: unknown;
    expressions?: unknown[];
    quasis?: Array<{ value?: { cooked?: unknown } }>;
  };
  if (value.type === "StringLiteral" && typeof value.value === "string")
    return value.value;
  if (value.type === "TemplateLiteral" && value.expressions?.length === 0) {
    const cooked = value.quasis?.[0]?.value?.cooked;
    return typeof cooked === "string" ? cooked : undefined;
  }
  return undefined;
}

/** Finds hardcoded visible strings in one TypeScript or TSX source file. */
export function lintSource(sourceText: string, file: string): I18nViolation[] {
  const source = parse(sourceText, {
    sourceType: "module",
    plugins: ["typescript", "jsx"],
  });
  const violations: I18nViolation[] = [];

  function visit(node: unknown, parentType?: string): void {
    if (!node || typeof node !== "object") return;
    const candidate = node as {
      type?: string;
      value?: unknown;
      name?: { name?: string };
      loc?: { start: { line: number; column: number } };
    };
    if (
      candidate.type === "JSXExpressionContainer" &&
      parentType !== "JSXAttribute"
    ) {
      const expression = (candidate as { expression?: unknown }).expression;
      const text = staticLiteralText(expression);
      if (text !== undefined && hasLetters(text)) {
        addViolation(file, candidate, "Visible JSX text", violations);
      }
    } else if (
      candidate.type === "JSXText" &&
      typeof candidate.value === "string"
    ) {
      const text = candidate.value.replace(/\s+/g, " ").trim();
      if (hasLetters(text))
        addViolation(file, candidate, "Visible JSX text", violations);
    } else if (
      candidate.type === "JSXAttribute" &&
      candidate.name?.name &&
      TEXT_ATTRIBUTES.has(candidate.name.name)
    ) {
      const initializer = candidate.value as
        | {
            type?: string;
            value?: unknown;
            expression?: { type?: string; value?: unknown };
          }
        | undefined;
      const literal =
        initializer?.type === "JSXExpressionContainer"
          ? (initializer as { expression?: unknown }).expression
          : initializer;
      const text = staticLiteralText(literal);
      if (text !== undefined && hasLetters(text)) {
        addViolation(
          file,
          candidate,
          `${candidate.name.name} attribute`,
          violations,
        );
      }
    }
    for (const value of Object.values(candidate)) {
      if (Array.isArray(value))
        value.forEach((child) => visit(child, candidate.type));
      else if (value && typeof value === "object") visit(value, candidate.type);
    }
  }

  visit(source);
  return violations;
}

/** Recursively lists eligible frontend source files while skipping generated/test trees. */
async function filesUnder(root: string): Promise<string[]> {
  const entries = await readdir(root, { withFileTypes: true });
  const files: string[] = [];
  for (const entry of entries) {
    if (entry.name.startsWith(".") || IGNORED_PARTS.has(entry.name)) continue;
    const path = join(root, entry.name);
    if (entry.isDirectory()) files.push(...(await filesUnder(path)));
    else if ([".ts", ".tsx"].includes(extname(entry.name))) files.push(path);
  }
  return files;
}

/** Runs the command-line checker and reports violations to stderr. */
export async function runCli(args = process.argv.slice(2)): Promise<number> {
  const root = resolve(process.cwd());
  let paths: string[];
  if (args.includes("--changed")) {
    const repoRoot = resolve(root, "..", "..");
    let stdout: string;
    try {
      ({ stdout } = await execFileAsync(
        "git",
        [
          "diff",
          "--name-only",
          "--diff-filter=AM",
          "HEAD^",
          "--",
          "vectora/frontend",
        ],
        { cwd: repoRoot },
      ));
    } catch {
      ({ stdout } = await execFileAsync(
        "git",
        [
          "diff-tree",
          "--no-commit-id",
          "--name-only",
          "-r",
          "-m",
          "HEAD",
          "--",
          "vectora/frontend",
        ],
        { cwd: repoRoot },
      ));
    }
    paths = stdout
      .split(/\r?\n/)
      .filter((path) => /\.(ts|tsx)$/.test(path) && !isIgnoredPath(path))
      .map((path) => resolve(repoRoot, path));
  } else {
    const fileArgs = args.filter((arg) => arg !== "--changed" && arg !== "--");
    const repoRoot = resolve(root, "..", "..");
    paths =
      fileArgs.length > 0
        ? fileArgs
            .map((path) =>
              path.startsWith("vectora/")
                ? resolve(repoRoot, path)
                : resolve(path),
            )
            .filter((path) => /\.(ts|tsx)$/.test(path) && !isIgnoredPath(path))
        : await filesUnder(root);
  }
  const violations: I18nViolation[] = [];
  for (const path of paths) {
    const source = await readFile(path, "utf8");
    const displayPath = relative(process.cwd(), path) || path;
    violations.push(...lintSource(source, displayPath));
  }
  for (const violation of violations) {
    console.error(
      `${violation.file}:${violation.line}:${violation.column} ${violation.message}`,
    );
  }
  return violations.length > 0 ? 1 : 0;
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(resolve(process.argv[1])).href
) {
  void runCli()
    .then((code) => {
      process.exitCode = code;
    })
    .catch((error: unknown) => {
      console.error(error);
      process.exitCode = 2;
    });
}
