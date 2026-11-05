// @vitest-environment jsdom

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/paraglide/messages", () => ({
  m: new Proxy(
    {},
    {
      get: (_target, key) => (args?: { count?: number; path?: string }) =>
        `${String(key)}${args?.count ?? args?.path ?? ""}`,
    },
  ),
}));

import { EditedFilesCard } from "../edited-files-card";

const files = [1, 2, 3, 4].map((index) => ({
  path: `src/file-${index}.ts`,
  status: "M",
  additions: index,
  deletions: 0,
  hunks: [{ header: "@@ -1 +1 @@", lines: ["+changed"] }],
}));

describe("EditedFilesCard", () => {
  it("renderiza três caminhos, expande a lista e abre o diff por ativação", () => {
    const onOpenFile = vi.fn();
    render(<EditedFilesCard files={files} onOpenFile={onOpenFile} />);

    expect(screen.getByTestId("edited-files-card")).toBeTruthy();
    expect(screen.getByText("src/file-1.ts")).toBeTruthy();
    expect(screen.queryByText("src/file-4.ts")).toBeNull();

    fireEvent.click(
      screen.getByRole("button", {
        name: /show_more|chat_edited_files_show_more/i,
      }),
    );
    expect(screen.getByText("src/file-4.ts")).toBeTruthy();

    fireEvent.click(screen.getByText("src/file-2.ts"));
    expect(onOpenFile).toHaveBeenCalledWith(files[1]);
  });

  it("não renderiza cartão sem arquivos", () => {
    const { container } = render(
      <EditedFilesCard files={[]} onOpenFile={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
