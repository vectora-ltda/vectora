// @vitest-environment jsdom

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { WorkbenchHost } from "@/components/layout/workbench-host";

describe("WorkbenchHost", () => {
  it.each([
    ["left", ["rail", "panel"]],
    ["right", ["panel", "rail"]],
  ] as const)(
    "mantém rail e conteúdo compostos quando o Workbench fica à %s",
    (side, order) => {
      const { container } = render(
        <WorkbenchHost
          side={side}
          rail={<div data-testid="rail" />}
          panel={<div data-testid="panel" />}
        />,
      );

      const host = container.querySelector("section");
      expect(host).toBeInTheDocument();
      expect(
        Array.from(host?.children ?? []).map((node) =>
          node.getAttribute("data-testid"),
        ),
      ).toEqual(order);
      expect(
        screen.getByRole("region", { name: /workbench/i }),
      ).toContainElement(screen.getByTestId("rail"));
      expect(
        screen.getByRole("region", { name: /workbench/i }),
      ).toContainElement(screen.getByTestId("panel"));
    },
  );
});
