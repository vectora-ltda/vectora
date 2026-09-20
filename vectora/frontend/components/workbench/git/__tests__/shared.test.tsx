// @vitest-environment jsdom

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { HunkView } from "../shared";

describe("HunkView", () => {
  it("exibe as posições antigas e novas vindas do diff", () => {
    render(
      <HunkView
        hunk={{
          header: "@@ -18,4 +18,5 @@",
          lines: [
            {
              text: " contexto",
              type: "context",
              old_line_number: 18,
              new_line_number: 18,
            },
            {
              text: "-removida",
              type: "delete",
              old_line_number: 19,
              new_line_number: null,
            },
            {
              text: "+adicionada",
              type: "add",
              old_line_number: null,
              new_line_number: 19,
            },
          ],
        }}
      />,
    );

    expect(screen.getByText("@@ -18,4 +18,5 @@")).toBeInTheDocument();
    expect(screen.getAllByText("18")).toHaveLength(2);
    expect(screen.getAllByText("19")).toHaveLength(2);
    expect(screen.getByText("removida")).toBeInTheDocument();
    expect(screen.getByText("adicionada")).toBeInTheDocument();
  });

  it("renderiza o marcador quando o diff não termina com newline", () => {
    render(
      <HunkView
        hunk={{
          header: "@@ -1,1 +1,1 @@",
          lines: [
            {
              text: "+sem newline",
              type: "add",
              old_line_number: null,
              new_line_number: 1,
              no_trailing_newline: true,
            },
          ],
        }}
      />,
    );
    expect(screen.getByText(/No newline at end of file/)).toBeInTheDocument();
  });
});
