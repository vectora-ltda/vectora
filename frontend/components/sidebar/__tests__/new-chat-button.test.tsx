// @vitest-environment jsdom
/**
 * Testes do NewChatButton — estado ativo quando a sessão atual é nova/vazia.
 */

import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";

import { NewChatButton } from "../new-chat-button";

afterEach(cleanup);

describe("NewChatButton", () => {
  it("destaca como ativo quando active=true", () => {
    render(<NewChatButton onClick={() => {}} active />);
    const btn = screen.getByRole("button");
    expect(btn.getAttribute("aria-current")).toBe("page");
    expect(btn.className).toContain("bg-muted border-border");
  });

  it("sem active, não fica destacado (erro/borda)", () => {
    render(<NewChatButton onClick={() => {}} />);
    const btn = screen.getByRole("button");
    expect(btn.getAttribute("aria-current")).toBeNull();
    expect(btn.className).toContain("bg-muted/30");
  });
});
