import { useEffect, useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { vscDarkPlus } from "react-syntax-highlighter/dist/esm/styles/prism";
import { AlertTriangle, Lock } from "lucide-react";
import { getSharedThread, type SharedThread } from "@/lib/api/vectora-client";
import { m } from "@/lib/paraglide/messages";

// Rota pública — não exige autenticação.
// O auth guard em __root.tsx já exclui o prefixo "/share/".
export const Route = createFileRoute("/share/$token")({
  component: SharePage,
});

function SharePage() {
  const { token } = Route.useParams() as { token: string };
  const [data, setData] = useState<SharedThread | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getSharedThread(token)
      .then(setData)
      .catch((err: unknown) => {
        setError(
          err instanceof Error ? err.message : "Erro ao carregar conversa.",
        );
      });
  }, [token]);

  return (
    <div className="min-h-full bg-background flex flex-col">
      {/* Header mínimo */}
      <header className="border-b border-border/60 h-14 flex items-center px-4 gap-2 shrink-0">
        <img src="/vectora.svg" alt={m.share_brand()} width={24} height={24} />
        <span className="text-sm font-semibold text-foreground">
          {m.share_brand()}
        </span>
        <span className="ml-auto flex items-center gap-1.5 text-xs text-muted-foreground">
          <Lock className="h-3 w-3" />
          {data?.permission === "comment"
            ? m.share_comment_permission()
            : m.share_read_permission()}
        </span>
      </header>

      <main className="flex-1 flex flex-col max-w-3xl w-full mx-auto px-4 py-8 gap-4">
        {/* Loading */}
        {data === undefined && !error && (
          <div className="flex-1 flex items-center justify-center">
            <img
              src="/vectora.svg"
              alt={m.share_loading()}
              width={40}
              height={40}
              className="animate-pulse opacity-40"
            />
          </div>
        )}

        {/* Não encontrado */}
        {data === null && (
          <div className="flex-1 flex flex-col items-center justify-center gap-3 text-center">
            <AlertTriangle className="h-10 w-10 text-muted-foreground/40" />
            <p className="text-sm font-medium text-foreground">
              {m.share_not_found()}
            </p>
            <p className="text-xs text-muted-foreground">{m.share_expired()}</p>
          </div>
        )}

        {/* Erro de rede */}
        {error && (
          <div className="flex-1 flex flex-col items-center justify-center gap-3 text-center">
            <AlertTriangle className="h-10 w-10 text-destructive/50" />
            <p className="text-sm font-medium text-foreground">
              {error || m.share_network_error()}
            </p>
          </div>
        )}

        {/* Conversa */}
        {data && (
          <>
            {data.title && (
              <h1 className="text-lg font-semibold text-foreground">
                {data.title}
              </h1>
            )}
            <div className="flex flex-col gap-6">
              {data.messages.map((msg, i) => (
                <SharedMessage key={i} role={msg.role} content={msg.content} />
              ))}
            </div>
          </>
        )}
      </main>
    </div>
  );
}

interface SharedMessageProps {
  role: "human" | "assistant";
  content: string;
}

function SharedMessage({ role, content }: SharedMessageProps) {
  const isHuman = role === "human";
  return (
    <div className={`flex gap-3 ${isHuman ? "justify-end" : "justify-start"}`}>
      {!isHuman && (
        <img
          src="/vectora.svg"
          alt={m.share_brand()}
          width={24}
          height={24}
          className="mt-1 shrink-0 opacity-70"
        />
      )}
      <div
        className={`max-w-[80%] rounded-2xl px-4 py-3 text-sm ${
          isHuman
            ? "bg-primary text-primary-foreground rounded-br-sm"
            : "bg-muted text-foreground rounded-bl-sm"
        }`}
      >
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            code({
              className,
              children,
              ...props
            }: React.HTMLAttributes<HTMLElement> & { inline?: boolean }) {
              const match = /language-(\w+)/.exec(className ?? "");
              const inline = !match;
              return !inline ? (
                <SyntaxHighlighter
                  style={vscDarkPlus}
                  language={match[1]}
                  PreTag="div"
                  className="rounded-lg text-xs my-2"
                >
                  {String(children).replace(/\n$/, "")}
                </SyntaxHighlighter>
              ) : (
                <code
                  className="bg-black/20 rounded px-1 py-0.5 text-xs font-mono"
                  {...props}
                >
                  {children}
                </code>
              );
            },
          }}
        >
          {content}
        </ReactMarkdown>
      </div>
      {isHuman && (
        <div className="mt-1 w-6 h-6 shrink-0 rounded-full bg-primary/20 flex items-center justify-center">
          <span className="text-[10px] font-medium text-primary">
            {m.share_user_avatar()}
          </span>
        </div>
      )}
    </div>
  );
}
