/**
 * Parser de slash commands (lib/constants/slash-commands.ts).
 */

import { describe, it, expect } from "vitest";
import {
  isSlashQuery,
  parseSlashCommand,
  filterCommands,
  type SlashCommand,
} from "../lib/constants/slash-commands";

const SAMPLE_COMMANDS: SlashCommand[] = [
  { name: "help", description: "Mostra ajuda", usage: "/help" },
  {
    name: "model",
    description: "Muda o modelo",
    usage: "/model <nome>",
    takesArg: true,
  },
  {
    name: "remember",
    description: "Salva na memória",
    usage: "/remember",
    takesArg: true,
  },
];

describe("isSlashQuery", () => {
  it("true para '/' e '/mod' (ainda escolhendo comando)", () => {
    expect(isSlashQuery("/")).toBe(true);
    expect(isSlashQuery("/mod")).toBe(true);
  });

  it("false quando já há espaço (comando + arg)", () => {
    expect(isSlashQuery("/model gpt")).toBe(false);
  });

  it("false para texto normal", () => {
    expect(isSlashQuery("ola")).toBe(false);
    expect(isSlashQuery("")).toBe(false);
  });
});

describe("parseSlashCommand", () => {
  it("retorna null para texto sem barra", () => {
    expect(parseSlashCommand("ola mundo")).toBeNull();
  });

  it("extrai nome sem argumento", () => {
    expect(parseSlashCommand("/help")).toEqual({ name: "help", arg: "" });
  });

  it("extrai nome e argumento", () => {
    expect(parseSlashCommand("/model gemini-2.5-flash")).toEqual({
      name: "model",
      arg: "gemini-2.5-flash",
    });
  });

  it("normaliza o nome para minúsculas e apara espaços", () => {
    expect(parseSlashCommand("  /HELP  ")).toEqual({ name: "help", arg: "" });
  });

  it("suporta underscores no nome do comando", () => {
    expect(parseSlashCommand("/learn_from_session")).toEqual({
      name: "learn_from_session",
      arg: "",
    });
  });
});

describe("filterCommands", () => {
  it("lista todos quando só há a barra", () => {
    expect(filterCommands("/", SAMPLE_COMMANDS).length).toBeGreaterThanOrEqual(
      3,
    );
  });

  it("filtra por prefixo", () => {
    const r = filterCommands("/mo", SAMPLE_COMMANDS);
    expect(r).toHaveLength(1);
    expect(r[0].name).toBe("model");
  });

  it("vazio quando não começa com barra", () => {
    expect(filterCommands("mo", SAMPLE_COMMANDS)).toEqual([]);
  });
});
