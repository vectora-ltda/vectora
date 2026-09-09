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

type ChangedLines = Map<string, Set<number>>;

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

function changedLinesFromDiff(diff: string): ChangedLines {
  const result: ChangedLines = new Map();
  let file: string | undefined;
  for (const line of diff.split(/\r?\n/)) {
    if (line.startsWith("+++ b/")) {
      file = line.slice(6);
      result.set(file, new Set());
      continue;
    }
    const match = line.match(/^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@/);
    if (!match || !file) continue;
    const start = Number(match[1]);
    const count = Number(match[2] ?? "1");
    const lines = result.get(file) ?? new Set<number>();
    for (let offset = 0; offset < count; offset += 1) lines.add(start + offset);
    result.set(file, lines);
  }
  return result;
}

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
      parentType !== "JSXAttribute" &&
      parentType !== "JSXStyleElement"
    ) {
      const expression = (candidate as { expression?: unknown }).expression;
      const text = staticLiteralText(expression);
      if (text !== undefined && hasLetters(text)) {
        addViolation(file, candidate, "Visible JSX text", violations);
      }
    } else if (
      candidate.type === "JSXText" &&
      typeof candidate.value === "string" &&
      parentType !== "JSXStyleElement"
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
    const childParentType =
      candidate.type === "JSXElement" &&
      (candidate as { openingElement?: { name?: { name?: string } } })
        .openingElement?.name?.name === "style"
        ? "JSXStyleElement"
        : candidate.type;
    for (const value of Object.values(candidate)) {
      if (Array.isArray(value))
        value.forEach((child) => visit(child, childParentType));
      else if (value && typeof value === "object")
        visit(value, childParentType);
    }
  }

  visit(source);
  return violations;
}

/** Recursively lists eligible frontend source files while skipping generated/test trees. */
export async function filesUnder(root: string): Promise<string[]> {
  const entries = await readdir(root, { withFileTypes: true });
  const files: string[] = [];
  for (const entry of entries) {
    if (entry.name.startsWith(".") || IGNORED_PARTS.has(entry.name)) continue;
    const path = join(root, entry.name);
    if (entry.isDirectory()) files.push(...(await filesUnder(path)));
    else if (
      [".ts", ".tsx"].includes(extname(entry.name)) &&
      !isIgnoredPath(path)
    ) {
      files.push(path);
    }
  }
  return files;
}

/** Runs the command-line checker and reports violations to stderr. */
export async function runCli(args = process.argv.slice(2)): Promise<number> {
  const root = resolve(process.cwd());
  const repoRoot = resolve(root, "..", "..");
  let paths: string[];
  let changedLines: ChangedLines | undefined;
  if (args.includes("--changed")) {
    let stdout: string;
    const baseRef = process.env.GITHUB_BASE_REF;
    const diffRef =
      baseRef && /^[A-Za-z0-9._/-]+$/.test(baseRef)
        ? `origin/${baseRef}...HEAD`
        : "HEAD";
    ({ stdout } = await execFileAsync(
      "git",
      [
        "diff",
        "--name-only",
        "--diff-filter=AM",
        diffRef,
        "--",
        "vectora/frontend",
      ],
      { cwd: repoRoot },
    ));
    const diff = await execFileAsync(
      "git",
      ["diff", "--unified=0", diffRef, "--", "vectora/frontend"],
      { cwd: repoRoot },
    );
    changedLines = changedLinesFromDiff(diff.stdout);
    paths = stdout
      .split(/\r?\n/)
      .filter((path) => /\.(ts|tsx)$/.test(path) && !isIgnoredPath(path))
      .map((path) => resolve(repoRoot, path));
  } else {
    const fileArgs = args.filter((arg) => arg !== "--changed" && arg !== "--");
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
    const fileViolations = lintSource(source, displayPath);
    const changed = changedLines?.get(relative(repoRoot, path));
    violations.push(
      ...(changed
        ? fileViolations.filter((violation) => changed.has(violation.line))
        : fileViolations),
    );
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
