"use client";

/**
 * XtermView — wrapper client-only do xterm.js. Conecta direto ao WebSocket do
 * backend (uvicorn); o token de autenticação é obtido via /auth/ws-token
 * (cookies httpOnly não trafegam em WS cross-origin) e passa na query string.
 */

import { useEffect, useRef } from "react";

import { VECTORA_API_URL } from "@/lib/constants/api";
import { m } from "@/lib/paraglide/messages";
/** Lê os tokens de cor ativos (`.dark`/`.light`) e monta o tema do xterm.
 * `background` usa `--sidebar` (o mesmo tom mais escuro da sidebar), não
 * `--background` — consistente com o resto do chrome do workbench. */
function readXtermTheme(): Record<string, string> {
  if (typeof document === "undefined") {
    return { background: "#181818", foreground: "#e4e4e7" };
  }
  const styles = getComputedStyle(document.documentElement);
  const get = (name: string) => styles.getPropertyValue(name).trim();
  const background = get("--sidebar") || get("--background") || "#181818";
  const foreground = get("--foreground") || "#e4e4e7";
  const primary = get("--primary") || "#7FC8FF";
  return {
    background,
    foreground,
    cursor: primary,
    selectionBackground: `color-mix(in srgb, ${primary} 30%, transparent)`,
  };
}

interface XtermViewProps {
  terminalId: string;
  threadId: string;
  workspaceId: string;
  onClosed?: () => void;
  fontSize?: number;
  scrollback?: number;
  cursorBlink?: boolean;
}

export function XtermView({
  terminalId,
  threadId,
  workspaceId,
  onClosed,
  cursorBlink = true,
  fontSize = 13,
  scrollback = 5000,
}: XtermViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<any | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const fitRef = useRef<any | null>(null);
  const resizeObsRef = useRef<ResizeObserver | null>(null);
  const themeObsRef = useRef<MutationObserver | null>(null);

  // O callback e o tradutor mudam de identidade a cada render do pai; mantê-los
  // fora das dependências do efeito evita reconectar (e derrubar) o WebSocket a
  // cada re-render. Refs entregam sempre a versão atual sem re-executar o efeito.
  const onClosedRef = useRef(onClosed);
  useEffect(() => {
    onClosedRef.current = onClosed;
  }, [onClosed]);

  useEffect(() => {
    if (!containerRef.current) return;
    let cancelled = false;
    const host = containerRef.current;

    (async () => {
      // CSS + libs carregados só no cliente
      await import("@xterm/xterm/css/xterm.css");
      const [{ Terminal }, { FitAddon }, { WebLinksAddon }] = await Promise.all(
        [
          import("@xterm/xterm"),
          import("@xterm/addon-fit"),
          import("@xterm/addon-web-links"),
        ],
      );
      if (cancelled) return;

      const term = new Terminal({
        fontFamily:
          getComputedStyle(document.documentElement).getPropertyValue(
            "--font-family-mono",
          ) || '"JetBrains Mono", ui-monospace, monospace',
        fontSize,
        cursorBlink,
        theme: readXtermTheme(),
        scrollback,
        convertEol: true,
      });
      const fit = new FitAddon();
      term.loadAddon(fit);
      term.loadAddon(new WebLinksAddon());
      term.open(host);
      try {
        fit.fit();
      } catch {
        // ignora se o container não tiver dimensões ainda
      }
      // xterm.js só foca a textarea interna automaticamente no clique —
      // sem isso, abrir a aba não deixa o usuário digitar até clicar
      // manualmente dentro do terminal.
      term.focus();
      termRef.current = term;
      fitRef.current = fit;

      // Token para o WS
      let token = "";
      try {
        const r = await fetch("/auth/ws-token");
        if (r.ok) {
          const data = await r.json();
          token = String(data?.token ?? "");
        }
      } catch {
        // sem token: o backend recusa
      }

      // No desktop Electron, a página carrega de `vectora-app://app/` (scheme
      // custom, não http/https) — uma URL relativa de WebSocket não resolve
      // pra `ws://` contra essa origem (só fetch/HTTP passa pelo proxy de
      // protocolo do main process) e o construtor lançaria SyntaxError.
      // window.vectora expõe a origem real do backend nesse caso; fora do
      // Electron (browser/dev server), VECTORA_API_URL já resolve certo.
      const bridgeOrigin = await window.vectora?.getBackendWsOrigin?.();
      const wsBase = bridgeOrigin ?? VECTORA_API_URL.replace(/^http/i, "ws");
      const url = `${wsBase}/vectora.terminal.v1/ws?terminal_id=${encodeURIComponent(terminalId)}&thread_id=${encodeURIComponent(threadId)}&workspace_id=${encodeURIComponent(workspaceId)}&token=${encodeURIComponent(token)}`;
      if (cancelled) return;

      let ws: WebSocket;
      try {
        ws = new WebSocket(url);
      } catch {
        term.write(`\r\n\x1b[31m[${m.terminal_conn_error()}]\x1b[0m\r\n`);
        return;
      }
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;

      const sendResize = () => {
        try {
          fit.fit();
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(
              JSON.stringify({
                type: "resize",
                cols: term.cols,
                rows: term.rows,
              }),
            );
          }
        } catch {
          // ignora
        }
      };

      ws.addEventListener("open", () => {
        sendResize();
      });

      ws.addEventListener("message", (ev: MessageEvent) => {
        if (typeof ev.data === "string") {
          try {
            const j = JSON.parse(ev.data);
            if (j.type === "error") {
              term.write(`\r\n\x1b[31m${j.message}\x1b[0m\r\n`);
            } else if (j.type === "closed") {
              term.write(`\r\n\x1b[33m[${m.terminal_ended()}]\x1b[0m\r\n`);
              onClosedRef.current?.();
            }
          } catch {
            // mensagens de texto não-JSON também viram saída crua
            term.write(ev.data);
          }
        } else {
          term.write(new Uint8Array(ev.data));
        }
      });

      ws.addEventListener("error", () => {
        term.write(`\r\n\x1b[31m[${m.terminal_conn_error()}]\x1b[0m\r\n`);
      });

      ws.addEventListener("close", () => {
        // Desmontagem (troca de aba) não encerra o terminal: o PTY sobrevive no
        // backend e reconecta com o mesmo terminal_id. Só propaga o fechamento
        // quando o socket cai durante uso real.
        if (!cancelled) onClosedRef.current?.();
      });

      // stdin do user → WS
      term.onData((data: string) => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(new TextEncoder().encode(data));
        }
      });

      // Resize observer mantém o terminal alinhado com o container
      const obs = new ResizeObserver(() => sendResize());
      obs.observe(host);
      resizeObsRef.current = obs;

      // Observa troca de tema (.dark/.light) e atualiza as cores do terminal
      const themeObs = new MutationObserver(() => {
        term.options.theme = readXtermTheme();
      });
      themeObs.observe(document.documentElement, {
        attributes: true,
        attributeFilter: ["class"],
      });
      themeObsRef.current = themeObs;
    })();

    return () => {
      cancelled = true;
      try {
        themeObsRef.current?.disconnect();
      } catch {
        // ignora
      }
      try {
        resizeObsRef.current?.disconnect();
      } catch {
        // ignora
      }
      try {
        wsRef.current?.close();
      } catch {
        // ignora
      }
      try {
        termRef.current?.dispose();
      } catch {
        // ignora
      }
      wsRef.current = null;
      termRef.current = null;
      fitRef.current = null;
      resizeObsRef.current = null;
      themeObsRef.current = null;
    };
  }, [cursorBlink, fontSize, scrollback, terminalId, threadId, workspaceId]);

  return (
    <div
      ref={containerRef}
      className="h-full w-full bg-sidebar"
      onClick={() => termRef.current?.focus()}
    />
  );
}
