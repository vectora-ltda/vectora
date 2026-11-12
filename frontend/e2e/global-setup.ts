import { chromium, type FullConfig } from "@playwright/test";

const CDP_URL =
  process.env.PLAYWRIGHT_CDP_URL ??
  `http://127.0.0.1:${process.env.VECTORA_ELECTRON_CDP_PORT ?? "9222"}`;
const APP_URL = process.env.PLAYWRIGHT_BASE_URL ?? "vectora-app://app/";
const EMAIL = process.env.E2E_EMAIL ?? "e2e@vectora.local";
const PASSWORD = process.env.E2E_PASSWORD ?? "Vectora-e2e-2026!";

/** Warns when E2E authentication could write into the developer's real app data. */
function warnIfHomeNotIsolated(): void {
  if (process.env.VECTORA_HOME) return;
  console.warn(
    "\n⚠️  VECTORA_HOME não está setado neste shell.\n" +
      "   Confirme que o processo Electron e o backend conectado também usam um VECTORA_HOME isolado;\n" +
      "   esta suíte cria usuários e dados reais no armazenamento do Vectora.\n",
  );
}

/** Prepara a sessão no backend conectado à janela Electron. */
async function globalSetup(_config: FullConfig): Promise<void> {
  const appUrl = new URL(APP_URL);
  if (
    appUrl.protocol !== "vectora-app:" ||
    appUrl.hostname !== "app" ||
    appUrl.username ||
    appUrl.password ||
    appUrl.port
  ) {
    throw new Error("E2E: a autenticação exige a origem vectora-app://app/.");
  }
  warnIfHomeNotIsolated();

  const browser = await chromium.connectOverCDP(CDP_URL);
  try {
    const context = browser.contexts()[0];
    const page = context?.pages()[0];
    if (!page)
      throw new Error(`Nenhuma janela Electron disponível em ${CDP_URL}`);
    await page.goto(APP_URL);
    const result = await page.evaluate(
      async ({ email, password }) => {
        const users = await fetch("/auth/has-users");
        if (!users.ok) return { step: "has-users", status: users.status };
        const { exists } = (await users.json()) as { exists: boolean };
        const response = await fetch(exists ? "/auth/signin" : "/auth/signup", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(
            exists ? { email, password } : { name: "E2E", email, password },
          ),
        });
        if (!response.ok)
          return {
            step: exists ? "signin" : "signup",
            status: response.status,
          };
        const me = await fetch("/auth/me");
        return { step: "me", status: me.status };
      },
      { email: EMAIL, password: PASSWORD },
    );
    if (result.status !== 200) {
      throw new Error(
        `E2E: autenticação Electron falhou em ${result.step} (${result.status}).`,
      );
    }
  } finally {
    await browser.close();
  }
}

export default globalSetup;
