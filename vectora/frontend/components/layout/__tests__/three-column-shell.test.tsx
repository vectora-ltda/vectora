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
    expect(center).not.toContainElement(header);
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
    expect(landmarks?.[0]).toHaveAttribute("aria-label", "Sessões");
    expect(landmarks?.[1]).toHaveAttribute("aria-label", "Canvas");
    expect(landmarks?.[2]).toHaveAttribute("aria-label", "Workbench");
    expect(screen.getByTestId("shell-header-slot")).toContainElement(
      screen.getByTestId("header"),
    );
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
      expect(screen.queryByTestId("workbench-content")).not.toBeInTheDocument();
      expect(screen.getByTestId("shell-header-slot")).toContainElement(
        screen.getByTestId("header"),
      );
    },
  );

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
});
