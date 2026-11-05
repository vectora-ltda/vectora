import { execFile } from "node:child_process";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";
import { describe, expect, it } from "vitest";
import { filesUnder, isIgnoredPath, lintSource, runCli } from "./lint-i18n";

const execFileAsync = promisify(execFile);

function git(cwd: string, ...args: string[]) {
  return execFileAsync("git", args, { cwd }).then(({ stdout }) =>
    stdout.trim(),
  );
}

function runChanged(cwd: string, env: Record<string, string>) {
  return execFileAsync(
    process.execPath,
    [
      join(process.cwd(), "node_modules", "tsx", "dist", "cli.mjs"),
      join(process.cwd(), "scripts", "lint-i18n.ts"),
      "--changed",
    ],
    { cwd, env: { ...process.env, GITHUB_BASE_REF: "stale", ...env } },
  );
}

async function repoWithHead(headFile: string, headSource: string) {
  const root = await mkdtemp(join(tmpdir(), "lint-i18n-changed-"));
  const frontend = join(root, "frontend");
  await mkdir(frontend);
  await git(root, "init", "-q");
  await git(root, "config", "user.email", "t@example.com");
  await git(root, "config", "user.name", "t");
  await writeFile(join(root, "README.md"), "raiz");
  await git(root, "add", "-A");
  await git(root, "commit", "-q", "-m", "raiz");
  // Simula o runner de uma PR empilhada: `origin/<base>` aponta para um
  // ponto antes de qualquer arquivo do frontend, então a diff contra ele
  // trataria os arquivos antigos como alterados.
  await git(root, "update-ref", "refs/remotes/origin/stale", "HEAD");
  await writeFile(join(frontend, "old.tsx"), "<button>Antigo</button>");
  await git(root, "add", "-A");
  await git(root, "commit", "-q", "-m", "base");
  const base = await git(root, "rev-parse", "HEAD");
  await writeFile(join(frontend, headFile), headSource);
  await git(root, "add", "-A");
  await git(root, "commit", "-q", "-m", "head");
  const head = await git(root, "rev-parse", "HEAD");
  return { root, frontend, base, head };
}

async function repoWithMovedFrontend(source: string, editedSource?: string) {
  const root = await mkdtemp(join(tmpdir(), "lint-i18n-moved-"));
  await git(root, "init", "-q");
  await git(root, "config", "user.email", "t@example.com");
  await git(root, "config", "user.name", "t");
  await mkdir(join(root, "app", "frontend"), { recursive: true });
  await writeFile(join(root, "app", "frontend", "old.tsx"), source);
  await git(root, "add", "-A");
  await git(root, "commit", "-q", "-m", "base");
  const base = await git(root, "rev-parse", "HEAD");
  await git(root, "mv", "app/frontend", "frontend");
  if (editedSource !== undefined) {
    await writeFile(join(root, "frontend", "old.tsx"), editedSource);
  }
  await git(root, "add", "-A");
  await git(root, "commit", "-q", "-m", "move");
  const head = await git(root, "rev-parse", "HEAD");
  return { root, frontend: join(root, "frontend"), base, head };
}

describe("lint-i18n", () => {
  it("reports visible JSX text and text attributes", () => {
    const violations = lintSource(
      '<button aria-label="Salvar">Salvar</button>',
      "fixture.tsx",
    );
    expect(violations).toHaveLength(2);
    expect(violations.map(({ message }) => message)).toEqual([
      expect.stringContaining("aria-label"),
      expect.stringContaining("Visible JSX text"),
    ]);
  });

  it("catches literal strings inside JSX expressions", () => {
    expect(
      lintSource('<button title={"Salvar"}>Continuar</button>', "fixture.tsx"),
    ).toHaveLength(2);
  });

  it("detects a static JSX expression child", () => {
    expect(
      lintSource('<button>{"Salvar"}</button>', "fixture.tsx"),
    ).toHaveLength(1);
  });

  it("detects static template literals", () => {
    expect(
      lintSource("<button>{`Salvar`}</button>", "fixture.tsx"),
    ).toHaveLength(1);
    expect(lintSource("<img alt={`Logotipo`} />", "fixture.tsx")).toHaveLength(
      1,
    );
  });

  it("returns a failing exit code only when files violate the rule", async () => {
    const directory = await mkdtemp(join(tmpdir(), "lint-i18n-"));
    try {
      const valid = join(directory, "valid.tsx");
      const invalid = join(directory, "invalid.tsx");
      await writeFile(valid, "<button>{m.save()}</button>");
      await writeFile(invalid, "<button>Salvar</button>");
      expect(await runCli([valid])).toBe(0);
      expect(await runCli([invalid])).toBe(1);
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });

  it("reports exit status and diagnostics through the package CLI entrypoint", async () => {
    const directory = await mkdtemp(join(tmpdir(), "lint-i18n-command-"));
    try {
      const valid = join(directory, "valid.tsx");
      const invalid = join(directory, "invalid.tsx");
      await writeFile(valid, "<button>{m.save()}</button>");
      await writeFile(invalid, "<button>Salvar</button>");

      const runPackageLint = (file: string) =>
        execFileAsync(
          process.execPath,
          [
            join(process.cwd(), "node_modules", "tsx", "dist", "cli.mjs"),
            join("scripts", "lint-i18n.ts"),
            "--",
            file,
          ],
          { cwd: process.cwd() },
        );

      const result = await runPackageLint(valid);
      expect(`${result.stdout}\n${result.stderr}`).not.toContain(
        "Visible JSX text must use a Paraglide message",
      );

      await expect(runPackageLint(invalid)).rejects.toMatchObject({
        code: 1,
        stderr: expect.stringContaining("invalid.tsx:1:9"),
      });
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });

  describe("--changed with the pull request commits", () => {
    it("lints only what the pull request changed, not older violations", async () => {
      const { root, frontend, base, head } = await repoWithHead(
        "novo.tsx",
        "<button>{m.save()}</button>",
      );
      try {
        const result = await runChanged(frontend, {
          PR_BASE_SHA: base,
          PR_HEAD_SHA: head,
        });
        expect(`${result.stdout}\n${result.stderr}`).not.toContain("old.tsx");
      } finally {
        await rm(root, { recursive: true, force: true });
      }
    });

    it("fails for a violation introduced by the pull request", async () => {
      const { root, frontend, base, head } = await repoWithHead(
        "novo.tsx",
        "<button>Salvar</button>",
      );
      try {
        await expect(
          runChanged(frontend, { PR_BASE_SHA: base, PR_HEAD_SHA: head }),
        ).rejects.toMatchObject({
          code: 1,
          stderr: expect.stringContaining("novo.tsx"),
        });
      } finally {
        await rm(root, { recursive: true, force: true });
      }
    });

    it("does not treat files moved into frontend as new", async () => {
      const { root, frontend, base, head } = await repoWithMovedFrontend(
        "<button>Antigo</button>",
      );
      try {
        const result = await runChanged(frontend, {
          PR_BASE_SHA: base,
          PR_HEAD_SHA: head,
        });
        expect(`${result.stdout}
${result.stderr}`).not.toContain("old.tsx");
      } finally {
        await rm(root, { recursive: true, force: true });
      }
    });

    it("lints only the lines edited in a file moved into frontend", async () => {
      const { root, frontend, base, head } = await repoWithMovedFrontend(
        "<button>Antigo</button>",
        "<>\n<button>Antigo</button>\n<p>Novo texto</p>\n</>",
      );
      try {
        await expect(
          runChanged(frontend, { PR_BASE_SHA: base, PR_HEAD_SHA: head }),
        ).rejects.toMatchObject({
          code: 1,
          stderr: expect.stringMatching(/old\.tsx:3:/),
        });
      } finally {
        await rm(root, { recursive: true, force: true });
      }
    });

    it("ignores malformed commit ids and falls back to the base branch", async () => {
      const { root, frontend } = await repoWithHead(
        "novo.tsx",
        "<button>{m.save()}</button>",
      );
      try {
        await expect(
          runChanged(frontend, {
            PR_BASE_SHA: "--output=/tmp/x",
            PR_HEAD_SHA: "not-a-sha",
          }),
        ).rejects.toMatchObject({
          code: 1,
          stderr: expect.stringContaining("old.tsx"),
        });
      } finally {
        await rm(root, { recursive: true, force: true });
      }
    });
  });

  it("applies exclusions to every path mode", () => {
    expect(isIgnoredPath("components/__tests__/fixture.tsx")).toBe(true);
    expect(isIgnoredPath("components\\__tests__\\fixture.tsx")).toBe(true);
    expect(isIgnoredPath("components/e2e/fixture.tsx")).toBe(true);
    expect(isIgnoredPath("lib/paraglide/messages.ts")).toBe(true);
    expect(isIgnoredPath("lib\\paraglide\\messages.ts")).toBe(true);
    expect(isIgnoredPath("node_modules/pkg/index.tsx")).toBe(true);
    expect(isIgnoredPath("scripts/lint-i18n.test.ts")).toBe(true);
    expect(isIgnoredPath("src/Button.spec.tsx")).toBe(true);
    expect(isIgnoredPath("src\\Button.spec.tsx")).toBe(true);
    expect(isIgnoredPath("generated/messages.ts")).toBe(true);
    expect(isIgnoredPath("components/settings/panel.tsx")).toBe(false);
  });

  it("filters test files during a full directory scan", async () => {
    const directory = await mkdtemp(join(tmpdir(), "lint-i18n-scan-"));
    try {
      await mkdir(join(directory, "src"));
      await writeFile(join(directory, "src", "Button.test.tsx"), "");
      await writeFile(join(directory, "src", "Button.spec.tsx"), "");
      await writeFile(join(directory, "src", "Button.tsx"), "");
      await expect(filesUnder(directory)).resolves.toEqual([
        join(directory, "src", "Button.tsx"),
      ]);
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });

  it("accepts message calls and ignores technical attributes", () => {
    const violations = lintSource(
      'const label = m.common_save(); const dynamic = mDyn("common.save");\n' +
        '<button className="SaveButton" data-testid="save" title={label}>{label}</button>',
      "fixture.tsx",
    );
    expect(violations).toEqual([]);
  });
});
