// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ThreeColumnShell } from "@/components/layout/three-column-shell";
import { CenterCanvas } from "@/components/layout/center-canvas";

describe("ThreeColumnShell", () => {
  it("não expõe tabpanel quando o canvas só hospeda children", () => {
    render(
      <CenterCanvas>
        <div data-testid="editor" />
      </CenterCanvas>,
    );
    expect(screen.queryByRole("tabpanel")).not.toBeInTheDocument();
    expect(screen.getByTestId("editor")).toBeInTheDocument();
  });

  it("mantém o editor implícito visível enquanto documentos ficam abertos", () => {
    render(
      <CenterCanvas
        activeTab="editor"
        documents={[
          {
            id: "file:main.ts",
            kind: "file",
            workspaceId: "workspace",
            title: "main.ts",
            path: "main.ts",
          },
        ]}
        renderDocument={() => <div data-testid="document" />}
      >
        <div data-testid="editor" />
      </CenterCanvas>,
    );

    expect(screen.getByRole("tab", { name: "main.ts" })).toBeInTheDocument();
    expect(screen.getByTestId("editor")).toBeInTheDocument();
    expect(screen.queryByTestId("document")).not.toBeInTheDocument();
  });

  it("rejeita tabs com ids duplicados", () => {
    expect(() =>
      render(
        <CenterCanvas
          tabs={[
            { id: "duplicate", label: "A", content: <div>A</div> },
            { id: "duplicate", label: "B", content: <div>B</div> },
          ]}
        />,
      ),
    ).toThrowError(/unique ids/);
  });

  it("mantém o Header exclusivamente na coluna central", () => {
    render(
      <ThreeColumnShell
        centerHeader={<header data-testid="header" />}
        left={<div data-testid="left" />}
        center={<div data-testid="center" />}
        right={<div data-testid="right" />}
        columns={{
          left: { label: "Sessões" },
          center: { label: "Canvas" },
          right: { label: "Chat" },
        }}
      />,
    );
    const header = screen.getByTestId("header");
    const center = screen.getByRole("main");
    expect(screen.getByTestId("shell-header-slot")).toContainElement(header);
    expect(center).toContainElement(header);
    expect(
      screen.getByRole("complementary", { name: "Sessões" }),
    ).not.toContainElement(header);
    expect(
      screen.getByRole("complementary", { name: "Chat" }),
    ).not.toContainElement(header);
  });

  it("remove uma coluna hidden da geometria", () => {
    render(
      <ThreeColumnShell
        centerHeader={<header />}
        left={<div />}
        center={<div data-testid="center" />}
        right={<div data-testid="right" />}
        columns={{
          left: { label: "Sessões" },
          center: { label: "Canvas" },
          right: { label: "Chat", visibility: "hidden" },
        }}
      />,
    );
    expect(
      screen.queryByRole("complementary", { name: "Chat" }),
    ).not.toBeInTheDocument();
  });

  it("respeita a largura compacta da lista de sessões sem reservar o mínimo aberto", () => {
    render(
      <ThreeColumnShell
        centerHeader={<header />}
        left={<div />}
        center={<div />}
        right={null}
        columns={{
          left: { label: "Sessões", width: 64 },
          center: { label: "Chat" },
          right: { label: "Workbench", visibility: "hidden" },
        }}
      />,
    );

    const sessions = screen.getByRole("complementary", { name: "Sessões" });
    expect(sessions).toHaveStyle({ width: "64px" });
    expect(sessions).not.toHaveClass("min-w-60");
  });

  it("aplica o mesmo contrato de rail estreita a uma coluna visível", () => {
    render(
      <ThreeColumnShell
        centerHeader={<header />}
        left={<div />}
        center={<div />}
        right={null}
        columns={{
          left: { label: "Sessões", width: 64, minWidth: 64 },
          center: { label: "Chat" },
          right: { label: "Workbench", visibility: "hidden" },
        }}
      />,
    );

    expect(screen.getByRole("complementary", { name: "Sessões" })).toHaveStyle({
      width: "64px",
      minWidth: "64px",
    });
  });

  it("inverte a ordem física das rails sem mover o header do centro", () => {
    render(
      <ThreeColumnShell
        direction="rtl"
        centerHeader={<header data-testid="header" />}
        left={<div data-testid="left" />}
        center={<div data-testid="center" />}
        right={<div data-testid="right" />}
        columns={{
          left: { label: "Sessões" },
          center: { label: "Canvas" },
          right: { label: "Workbench" },
        }}
      />,
    );
    const shell = screen.getByRole("main").parentElement;
    expect(shell).toHaveClass("flex-row-reverse");
    // RTL changes the visual order through flexbox; the DOM order remains
    // stable for accessibility and keyboard navigation.
    const landmarks = shell?.children;
    const center = screen.getByRole("main");
    expect(landmarks?.[0]).toHaveAttribute("aria-label", "Sessões");
    expect(landmarks?.[1]).toHaveAttribute("aria-label", "Canvas");
    expect(landmarks?.[2]).toHaveAttribute("aria-label", "Workbench");
    expect(center).toContainElement(screen.getByTestId("header"));
  });

  it("oferece controle acessível para reabrir coluna colapsada", async () => {
    const onExpand = vi.fn();
    render(
      <ThreeColumnShell
        centerHeader={<header />}
        left={<div />}
        center={<div />}
        right={<div />}
        columns={{
          left: {
            label: "Sessões",
            visibility: "collapsed",
            onExpand,
            expandLabel: "Reabrir sessões",
          },
          center: { label: "Canvas" },
          right: { label: "Chat" },
        }}
      />,
    );
    const button = screen.getByRole("button", { name: "Reabrir sessões" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(button);
    expect(onExpand).toHaveBeenCalledOnce();
  });

  it.each(["ltr", "rtl"] as const)(
    "mantém somente a rail de 48px quando o Workbench está fechado (%s)",
    (direction) => {
      render(
        <ThreeColumnShell
          direction={direction}
          centerHeader={<header data-testid="header" />}
          left={<div data-testid="workbench-content" />}
          center={<div data-testid="center" />}
          right={<div data-testid="chat" />}
          columns={{
            left: {
              label: "Workbench",
              visibility: "collapsed",
              onExpand: () => undefined,
            },
            center: { label: "Canvas" },
            right: { label: "Chat" },
          }}
        />,
      );

      const workbench = screen.getByRole("complementary", {
        name: "Workbench",
      });
      expect(workbench).toHaveStyle({ width: "48px" });
      expect(screen.getByTestId("workbench-content")).toBeInTheDocument();
      expect(screen.getByTestId("workbench-content").parentElement).toHaveClass(
        "invisible",
      );
      expect(screen.getByRole("main")).toContainElement(
        screen.getByTestId("header"),
      );
    },
  );

  it("aplica à rail direita o mesmo fundo e altura integral da rail esquerda", () => {
    render(
      <ThreeColumnShell
        centerHeader={<header />}
        left={<div />}
        center={<div />}
        right={<div />}
        columns={{
          left: {
            label: "Sessões",
            visibility: "collapsed",
            onExpand: () => undefined,
          },
          center: { label: "Canvas" },
          right: {
            label: "Chat",
            visibility: "collapsed",
            onExpand: () => undefined,
          },
        }}
      />,
    );

    const rails = screen.getAllByRole("complementary");
    expect(rails).toHaveLength(2);
    for (const rail of rails) {
      expect(rail).toHaveClass("bg-sidebar");
      expect(rail.firstElementChild).toHaveClass("h-full");
    }
  });

  it.each([
    ["ltr", "left"],
    ["rtl", "right"],
  ] as const)(
    "mantém a largura do grupo Workbench no lado físico %s (%s)",
    (direction, side) => {
      render(
        <ThreeColumnShell
          direction={direction}
          centerHeader={<header />}
          left={<div />}
          center={<div />}
          right={<div />}
          columns={{
            left:
              side === "left"
                ? { label: "Workbench", width: 328 }
                : { label: "Sessões", width: 240 },
            center: { label: "Canvas" },
            right:
              side === "right"
                ? { label: "Workbench", width: 328 }
                : { label: "Chat", width: 300 },
          }}
        />,
      );

      expect(
        screen.getByRole("complementary", { name: "Workbench" }),
      ).toHaveStyle({ width: "328px" });
    },
  );

  it("mantém as colunas laterais abertas em pelo menos 240px e preserva a rail fechada", () => {
    const { rerender } = render(
      <ThreeColumnShell
        centerHeader={<header />}
        left={<div />}
        center={<div />}
        right={<div data-testid="chat" />}
        columns={{
          left: { label: "Workbench" },
          center: { label: "Canvas" },
          right: { label: "Chat", width: 180, minWidth: 0 },
        }}
      />,
    );

    expect(
      screen.getByRole("complementary", { name: "Workbench" }),
    ).toHaveStyle({
      minWidth: "240px",
    });
    expect(screen.getByRole("complementary", { name: "Chat" })).toHaveStyle({
      minWidth: "240px",
    });

    rerender(
      <ThreeColumnShell
        centerHeader={<header />}
        left={<div />}
        center={<div />}
        right={<div data-testid="chat" />}
        columns={{
          left: { label: "Workbench" },
          center: { label: "Canvas" },
          right: {
            label: "Chat",
            visibility: "collapsed",
            onExpand: () => undefined,
          },
        }}
      />,
    );

    expect(screen.getByRole("complementary", { name: "Chat" })).toHaveStyle({
      width: "48px",
    });
  });
});
