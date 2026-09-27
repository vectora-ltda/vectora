// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { CanvasFileDiff } from "../canvas-file-diff";
import { m } from "@/lib/paraglide/messages";

describe("CanvasFileDiff", () => {
  it("renderiza cabeçalho e linhas de um diff", () => {
    render(
      <CanvasFileDiff
        editedFile={{
          path: "src/app.ts",
          status: "modified",
          additions: 1,
          deletions: 1,
          hunks: [{ header: "@@ -1 +1 @@", lines: ["-old", "+new"] }],
        }}
      />,
    );

    expect(screen.getByText("@@ -1 +1 @@")).toBeInTheDocument();
    expect(screen.getByText("-old")).toBeInTheDocument();
    expect(screen.getByText("+new")).toBeInTheDocument();
  });

  it("mostra estado vazio quando não há hunks", () => {
    render(
      <CanvasFileDiff
        editedFile={{
          path: "empty.ts",
          status: "modified",
          additions: 0,
          deletions: 0,
          hunks: [],
        }}
      />,
    );
    expect(
      screen.getByText(m.chat_edited_files_diff_empty()),
    ).toBeInTheDocument();
  });
});
