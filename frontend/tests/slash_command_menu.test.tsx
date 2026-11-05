// @vitest-environment jsdom
/**
 * Unit tests — SlashCommandMenu component
 *
 * Testa que o menu busca ferramentas via getTools() da API e renderiza
 * corretamente, sem mais depender de comandos hardcoded.
 *
 * Cobre:
 *  - Fetch das ferramentas ao montar (useEffect)
 *  - Filtragem correta por prefixo digitado
 *  - Menu esconde quando não há correspondência
 *  - Seleção de comando chama onSelect
 *  - Remoção dos comandos hardcoded (help, clear, model não aparecem)
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { SlashCommandMenu } from "@/components/chat/features/slash-command-menu";
import * as vectoraClient from "@/lib/api/vectora-client";

const MOCK_TOOLS = [
  {
    name: "remember",
    description: "Salva informação na memória persistente do agente",
    render_hint: "text",
    category: "memory",
    destructive: false,
    icon: "brain",
    args_schema_json: "{}",
  },
  {
    name: "schedule_task",
    description: "Agenda uma tarefa para execução futura",
    render_hint: "text",
    category: "scheduling",
    destructive: false,
    icon: "clock",
    args_schema_json: "{}",
  },
  {
    name: "create_background_task",
    description: "Delega uma sub-tarefa para um agente isolado",
    render_hint: "text",
    category: "delegate",
    destructive: false,
    icon: "cpu",
    args_schema_json: "{}",
  },
  {
    name: "web_search",
    description: "Faz uma busca na web",
    render_hint: "text",
    category: "search",
    destructive: false,
    icon: "search",
    args_schema_json: "{}",
  },
];

describe("SlashCommandMenu", () => {
  let getToolsSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    getToolsSpy = vi
      .spyOn(vectoraClient, "getTools")
      .mockResolvedValue({ tools: MOCK_TOOLS });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("chama getTools ao montar", async () => {
    render(<SlashCommandMenu input="/" onSelect={vi.fn()} />);
    await waitFor(() => {
      expect(getToolsSpy).toHaveBeenCalledOnce();
    });
  });

  it("renderiza todas as ferramentas com '/'", async () => {
    render(<SlashCommandMenu input="/" onSelect={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByText("/remember")).toBeInTheDocument();
      expect(screen.getByText("/schedule_task")).toBeInTheDocument();
      expect(screen.getByText("/create_background_task")).toBeInTheDocument();
      expect(screen.getByText("/web_search")).toBeInTheDocument();
    });
  });

  it("filtra por prefixo — '/re' só mostra 'remember'", async () => {
    render(<SlashCommandMenu input="/re" onSelect={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByText("/remember")).toBeInTheDocument();
    });
    expect(screen.queryByText("/schedule_task")).not.toBeInTheDocument();
  });

  it("filtra por prefixo — '/sch' só mostra 'schedule_task'", async () => {
    render(<SlashCommandMenu input="/sch" onSelect={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByText("/schedule_task")).toBeInTheDocument();
    });
    expect(screen.queryByText("/remember")).not.toBeInTheDocument();
  });

  it("não renderiza nada quando não há correspondência", async () => {
    render(<SlashCommandMenu input="/zzz" onSelect={vi.fn()} />);
    // Aguarda fetch terminar e depois confirma que menu está vazio
    await waitFor(() => expect(getToolsSpy).toHaveBeenCalled());
    expect(screen.queryByText(/^\/[a-z]/)).not.toBeInTheDocument();
  });

  it("não renderiza quando input não começa com '/'", async () => {
    const { container } = render(
      <SlashCommandMenu input="remember" onSelect={vi.fn()} />,
    );
    await waitFor(() => expect(getToolsSpy).toHaveBeenCalled());
    expect(container.firstChild).toBeNull();
  });

  it("não renderiza quando há espaço após o comando (usuário está digitando args)", async () => {
    const { container } = render(
      <SlashCommandMenu input="/remember texto" onSelect={vi.fn()} />,
    );
    await waitFor(() => expect(getToolsSpy).toHaveBeenCalled());
    expect(container.firstChild).toBeNull();
  });

  it("chama onSelect com o comando correto ao clicar", async () => {
    const onSelect = vi.fn();
    render(<SlashCommandMenu input="/re" onSelect={onSelect} />);
    await waitFor(() => screen.getByText("/remember"));

    fireEvent.mouseDown(screen.getByText("/remember").closest("button")!);
    expect(onSelect).toHaveBeenCalledWith(
      expect.objectContaining({ name: "remember", usage: "/remember" }),
    );
  });

  it("exibe descrição da ferramenta", async () => {
    render(<SlashCommandMenu input="/re" onSelect={vi.fn()} />);
    await waitFor(() => {
      expect(
        screen.getByText("Salva informação na memória persistente do agente"),
      ).toBeInTheDocument();
    });
  });

  it("NÃO exibe /help, /clear ou /model (removidos)", async () => {
    render(<SlashCommandMenu input="/" onSelect={vi.fn()} />);
    await waitFor(() => expect(getToolsSpy).toHaveBeenCalled());
    expect(screen.queryByText("/help")).not.toBeInTheDocument();
    expect(screen.queryByText("/clear")).not.toBeInTheDocument();
    expect(screen.queryByText("/model")).not.toBeInTheDocument();
  });

  it("trata falha do getTools graciosamente (menu vazio, sem crash)", async () => {
    getToolsSpy.mockRejectedValue(new Error("Network error"));
    // Não deve lançar nenhuma exceção
    render(<SlashCommandMenu input="/" onSelect={vi.fn()} />);
    // Aguarda fetch terminar
    await waitFor(() => expect(getToolsSpy).toHaveBeenCalled());
    // Menu fica vazio — nenhum comando disponível
    expect(screen.queryByText(/^\/[a-z]/)).not.toBeInTheDocument();
  });
});
