// @vitest-environment jsdom
/**
 * SessionSwitcher — dropdown de troca de sessão no modo IDE.
 *
 * Cobre: renderização do título correto, abertura/fechamento do dropdown,
 * listagem de threads, seleção de thread, nova sessão, estado ativo, lista vazia.
 */

import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";

vi.mock("@/lib/paraglide/messages", () => ({
  m: new Proxy({}, { get: (_t, prop) => () => String(prop) }),
}));

import { SessionSwitcher } from "../session-switcher";
import type { Thread } from "@/lib/hooks/threads";

function makeThread(id: string, title: string, wsId = "ws1"): Thread {
  return {
    thread_id: id,
    created_at: "2024-01-01T00:00:00Z",
    updated_at: "2024-01-01T00:00:00Z",
    metadata: { user_id: "u1", title },
    workspace_id: wsId,
  };
}

const THREADS: Thread[] = [
  makeThread("t1", "Primeiro chat"),
  makeThread("t2", "Segundo chat"),
  makeThread("t3", "Terceiro chat"),
];

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("SessionSwitcher — renderização base", () => {
  it("mostra o título da sessão atual", () => {
    render(
      <SessionSwitcher
        threads={THREADS}
        currentThreadId="t1"
        onSelectThread={vi.fn()}
        onNewSession={vi.fn()}
      />,
    );
    expect(screen.getByText("Primeiro chat")).toBeInTheDocument();
  });

  it("usa ide_session_untitled quando thread não tem título", () => {
    const noTitle: Thread = {
      thread_id: "t99",
      created_at: "",
      updated_at: "",
      metadata: { user_id: "u1", title: "" },
    };
    render(
      <SessionSwitcher
        threads={[noTitle]}
        currentThreadId="t99"
        onSelectThread={vi.fn()}
        onNewSession={vi.fn()}
      />,
    );
    expect(screen.getByText("ide_session_untitled")).toBeInTheDocument();
  });

  it("usa ide_session_untitled quando currentThreadId não está na lista", () => {
    render(
      <SessionSwitcher
        threads={THREADS}
        currentThreadId="nao-existe"
        onSelectThread={vi.fn()}
        onNewSession={vi.fn()}
      />,
    );
    expect(screen.getByText("ide_session_untitled")).toBeInTheDocument();
  });

  it("botão tem aria-label ide_session_switcher_label", () => {
    render(
      <SessionSwitcher
        threads={THREADS}
        currentThreadId="t1"
        onSelectThread={vi.fn()}
        onNewSession={vi.fn()}
      />,
    );
    expect(
      screen.getByLabelText("ide_session_switcher_label"),
    ).toBeInTheDocument();
  });

  it("dropdown fechado inicialmente (aria-expanded=false)", () => {
    render(
      <SessionSwitcher
        threads={THREADS}
        currentThreadId="t1"
        onSelectThread={vi.fn()}
        onNewSession={vi.fn()}
      />,
    );
    const btn = screen.getByLabelText("ide_session_switcher_label");
    expect(btn).toHaveAttribute("aria-expanded", "false");
  });
});

describe("SessionSwitcher — abertura/fechamento", () => {
  it("clicar no botão abre o dropdown (aria-expanded=true)", () => {
    render(
      <SessionSwitcher
        threads={THREADS}
        currentThreadId="t1"
        onSelectThread={vi.fn()}
        onNewSession={vi.fn()}
      />,
    );
    const btn = screen.getByLabelText("ide_session_switcher_label");
    fireEvent.click(btn);
    expect(btn).toHaveAttribute("aria-expanded", "true");
  });

  it("dropdown mostra role=listbox ao abrir", () => {
    render(
      <SessionSwitcher
        threads={THREADS}
        currentThreadId="t1"
        onSelectThread={vi.fn()}
        onNewSession={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByLabelText("ide_session_switcher_label"));
    expect(screen.getByRole("listbox")).toBeInTheDocument();
  });

  it("clicar fora fecha o dropdown", () => {
    render(
      <SessionSwitcher
        threads={THREADS}
        currentThreadId="t1"
        onSelectThread={vi.fn()}
        onNewSession={vi.fn()}
      />,
    );
    const btn = screen.getByLabelText("ide_session_switcher_label");
    fireEvent.click(btn);
    expect(btn).toHaveAttribute("aria-expanded", "true");

    const overlay = document.querySelector(
      "[aria-hidden='true']",
    ) as HTMLElement;
    expect(overlay).not.toBeNull();
    fireEvent.click(overlay);
    expect(btn).toHaveAttribute("aria-expanded", "false");
  });

  it("clicar no botão novamente fecha o dropdown", () => {
    render(
      <SessionSwitcher
        threads={THREADS}
        currentThreadId="t1"
        onSelectThread={vi.fn()}
        onNewSession={vi.fn()}
      />,
    );
    const btn = screen.getByLabelText("ide_session_switcher_label");
    fireEvent.click(btn);
    fireEvent.click(btn);
    expect(btn).toHaveAttribute("aria-expanded", "false");
  });
});

describe("SessionSwitcher — listagem de threads", () => {
  beforeEach(() => {
    render(
      <SessionSwitcher
        threads={THREADS}
        currentThreadId="t2"
        onSelectThread={vi.fn()}
        onNewSession={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByLabelText("ide_session_switcher_label"));
  });

  it("mostra todos os threads no dropdown", () => {
    expect(screen.getAllByRole("option")).toHaveLength(3);
  });

  it("lista os títulos corretos", () => {
    expect(
      screen.getByRole("option", { name: "Primeiro chat" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: "Segundo chat" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: "Terceiro chat" }),
    ).toBeInTheDocument();
  });

  it("thread atual tem aria-selected=true", () => {
    const current = screen.getByRole("option", { name: "Segundo chat" });
    expect(current).toHaveAttribute("aria-selected", "true");
  });

  it("threads não-ativos têm aria-selected=false", () => {
    const other = screen.getByRole("option", { name: "Primeiro chat" });
    expect(other).toHaveAttribute("aria-selected", "false");
  });

  it("botão de nova sessão exibe ide_session_new", () => {
    expect(screen.getByText("ide_session_new")).toBeInTheDocument();
  });
});

describe("SessionSwitcher — lista vazia", () => {
  it("mostra sidebar_no_conversations quando não há threads", () => {
    render(
      <SessionSwitcher
        threads={[]}
        currentThreadId="t1"
        onSelectThread={vi.fn()}
        onNewSession={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByLabelText("ide_session_switcher_label"));
    expect(screen.getByText("sidebar_no_conversations")).toBeInTheDocument();
  });

  it("lista vazia: nenhum role=option renderizado", () => {
    render(
      <SessionSwitcher
        threads={[]}
        currentThreadId="t1"
        onSelectThread={vi.fn()}
        onNewSession={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByLabelText("ide_session_switcher_label"));
    expect(screen.queryAllByRole("option")).toHaveLength(0);
  });
});

describe("SessionSwitcher — interações", () => {
  it("clicar em thread chama onSelectThread com thread_id correto", () => {
    const onSelectThread = vi.fn();
    render(
      <SessionSwitcher
        threads={THREADS}
        currentThreadId="t1"
        onSelectThread={onSelectThread}
        onNewSession={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByLabelText("ide_session_switcher_label"));
    fireEvent.click(screen.getByRole("option", { name: "Segundo chat" }));
    expect(onSelectThread).toHaveBeenCalledOnce();
    expect(onSelectThread).toHaveBeenCalledWith("t2");
  });

  it("clicar em thread fecha o dropdown", () => {
    const onSelect = vi.fn();
    render(
      <SessionSwitcher
        threads={THREADS}
        currentThreadId="t1"
        onSelectThread={onSelect}
        onNewSession={vi.fn()}
      />,
    );
    const trigger = screen.getByLabelText("ide_session_switcher_label");
    fireEvent.click(trigger);
    fireEvent.click(screen.getByRole("option", { name: "Segundo chat" }));
    expect(trigger).toHaveAttribute("aria-expanded", "false");
  });

  it("clicar em nova sessão chama onNewSession", () => {
    const onNewSession = vi.fn();
    render(
      <SessionSwitcher
        threads={THREADS}
        currentThreadId="t1"
        onSelectThread={vi.fn()}
        onNewSession={onNewSession}
      />,
    );
    fireEvent.click(screen.getByLabelText("ide_session_switcher_label"));
    fireEvent.click(screen.getByText("ide_session_new"));
    expect(onNewSession).toHaveBeenCalledOnce();
  });

  it("clicar em nova sessão fecha o dropdown", () => {
    render(
      <SessionSwitcher
        threads={THREADS}
        currentThreadId="t1"
        onSelectThread={vi.fn()}
        onNewSession={vi.fn()}
      />,
    );
    const trigger = screen.getByLabelText("ide_session_switcher_label");
    fireEvent.click(trigger);
    fireEvent.click(screen.getByText("ide_session_new"));
    expect(trigger).toHaveAttribute("aria-expanded", "false");
  });

  it("onSelectThread NÃO é chamado quando se clica fora do dropdown", () => {
    const onSelectThread = vi.fn();
    render(
      <SessionSwitcher
        threads={THREADS}
        currentThreadId="t1"
        onSelectThread={onSelectThread}
        onNewSession={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByLabelText("ide_session_switcher_label"));
    const overlay = document.querySelector(
      "[aria-hidden='true']",
    ) as HTMLElement;
    fireEvent.click(overlay);
    expect(onSelectThread).not.toHaveBeenCalled();
  });
});

describe("SessionSwitcher — thread sem título no dropdown", () => {
  it("usa ide_session_untitled para threads com título vazio na lista", () => {
    const noTitle: Thread = {
      thread_id: "tx",
      created_at: "",
      updated_at: "",
      metadata: { user_id: "u1", title: "" },
    };
    render(
      <SessionSwitcher
        threads={[noTitle]}
        currentThreadId="t1"
        onSelectThread={vi.fn()}
        onNewSession={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByLabelText("ide_session_switcher_label"));
    const options = screen.getAllByRole("option");
    expect(options[0].textContent).toBe("ide_session_untitled");
  });
});
