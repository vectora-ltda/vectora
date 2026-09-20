// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LibraryMcpPreview } from "../../library-mcp-preview";

const MCP = {
  id: "filesystem",
  name: "Filesystem",
  description: "Secure local filesystem access",
  installCommand: "npx -y @modelcontextprotocol/server-filesystem",
  envVars: ["ALLOWED_ROOT"],
  homepage: "https://example.com/filesystem",
  category: "filesystem",
  iconUrl: null,
};

describe("LibraryMcpPreview", () => {
  it("renderiza os detalhes do MCP no documento do canvas", () => {
    render(<LibraryMcpPreview mcp={MCP} />);

    expect(
      screen.getByRole("heading", { name: "Filesystem", level: 1 }),
    ).toBeInTheDocument();
    expect(screen.getByText(MCP.installCommand)).toBeInTheDocument();
    expect(screen.getByText("ALLOWED_ROOT")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Homepage/ })).toHaveAttribute(
      "href",
      "https://example.com/filesystem",
    );
  });

  it("não cria link para homepage com protocolo inseguro", () => {
    render(
      <LibraryMcpPreview mcp={{ ...MCP, homepage: "javascript:alert(1)" }} />,
    );

    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
});
