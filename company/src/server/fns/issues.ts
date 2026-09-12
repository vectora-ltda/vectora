import { createServerFn } from "@tanstack/react-start";
import { z } from "zod";
import { SERVICES_URL, servicesFetch } from "#/lib/services/client";

const IssueSchema = z.object({
  title: z.string().min(3).max(200),
  category: z.enum(["bug", "feedback", "feature"]),
  description: z.string().max(5000).optional(),
  email: z.string().email().optional().or(z.literal("")),
  turnstileToken: z.string(),
});

export const submitIssue = createServerFn({ method: "POST" })
  .validator(IssueSchema)
  .handler(async ({ data: input }) => {
    return servicesFetch<{ ok: true }>("/issues", {
      method: "POST",
      body: JSON.stringify(input),
    });
  });

// Variante multipart — anexos (prints/vídeos) não passam por JSON. Os campos
// escalares são validados com o MESMO IssueSchema; o FormData original segue
// intacto pro worker (que valida tipo/tamanho dos arquivos com autoridade).
const IssueFormSchema = z.instanceof(FormData).superRefine((form, ctx) => {
  const parsed = IssueSchema.safeParse({
    title: form.get("title"),
    category: form.get("category"),
    description: form.get("description") || undefined,
    email: form.get("email") ?? "",
    turnstileToken: form.get("turnstileToken"),
  });
  if (!parsed.success) {
    ctx.addIssue({
      code: "custom",
      message: parsed.error.issues
        .map((issue) => `${issue.path.join(".")}: ${issue.message}`)
        .join("; "),
    });
  }
});

export const submitIssueWithFiles = createServerFn({ method: "POST" })
  .validator(IssueFormSchema)
  .handler(async ({ data }) => {
    // fetch direto (não servicesFetch): o Content-Type multipart com boundary
    // é gerado pelo fetch a partir do FormData — forçar application/json
    // corromperia o corpo.
    const res = await fetch(`${SERVICES_URL}/issues`, {
      method: "POST",
      body: data,
    });
    const body = (await res.json().catch(() => ({}))) as {
      ok?: true;
      error?: string;
    };
    if (!res.ok) throw new Error(body.error ?? `services_error_${res.status}`);
    return body as { ok: true };
  });

const ISSUE_CATEGORIES = ["bug", "feedback", "feature"] as const;
type IssueCategory = (typeof ISSUE_CATEGORIES)[number];

export type IssueListItem = {
  id: string;
  title: string;
  category: IssueCategory;
  description: string | null;
  files: string[];
  created_at: string;
};

export const listOpenIssues = createServerFn({ method: "GET" }).handler(
  async (): Promise<IssueListItem[]> => {
    // `files` chega opcional do worker (issues antigas gravadas antes da
    // migration 0005 não têm a coluna preenchida).
    const items =
      await servicesFetch<
        Array<Omit<IssueListItem, "files"> & { files?: string[] }>
      >("/issues");
    // Keys R2 → URLs absolutas servidas pelo worker (GET /issues/files/*).
    return items.map((item) => ({
      ...item,
      files: (item.files ?? []).map(
        (key) => `${SERVICES_URL}/issues/files/${key}`,
      ),
    }));
  },
);

export type IssueDetail = IssueListItem & {
  status: "open" | "resolved";
  github_url?: string | null;
  github_sync_state?: string;
  core_url?: string | null;
  comments?: Array<{
    github_comment_id: number;
    author: string;
    body: string;
    html_url: string | null;
    created_at: string;
  }>;
};

// servicesFetch lança em qualquer resposta não-2xx (incl. 404) — null aqui
// significa "não existe", tratado pela rota como not-found.
export const getIssue = createServerFn({ method: "GET" })
  .validator(z.object({ id: z.string().min(1) }))
  .handler(async ({ data }): Promise<IssueDetail | null> => {
    const item = await servicesFetch<
      Omit<IssueDetail, "files"> & { files?: string[] }
    >(`/issues/${encodeURIComponent(data.id)}`).catch(() => null);
    if (!item) return null;
    return {
      ...item,
      files: (item.files ?? []).map(
        (key) => `${SERVICES_URL}/issues/files/${key}`,
      ),
    };
  });

export const joinWaitlist = createServerFn({ method: "POST" })
  .validator(
    z.object({
      email: z.string().email(),
      turnstileToken: z.string(),
      source: z.string().optional(),
    }),
  )
  .handler(async ({ data: input }) => {
    return servicesFetch<{ ok: true }>("/issues/waitlist", {
      method: "POST",
      body: JSON.stringify(input),
    });
  });
