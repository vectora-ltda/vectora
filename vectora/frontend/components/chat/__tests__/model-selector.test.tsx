// @vitest-environment jsdom
/**
 * Tests do ModelSelector: mostra o modelo ativo, abre o dropdown e dispara
 * onChange com o id do modelo escolhido.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import {
  render,
  screen,
  cleanup,
  fireEvent,
  waitFor,
} from "@testing-library/react";
import { ModelSelector } from "../model-selector";
import {
  getAllowedModels,
  getModelDisplayName,
  getModelProvider,
} from "@/lib/config/deployment-config";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ModelSelector", () => {
  it("exibe o nome do modelo ativo e começa fechado", () => {
    const value = getAllowedModels()[0];
    render(<ModelSelector value={value} onChange={() => {}} />);
    const toggle = screen.getByRole("button", { expanded: false });
    expect(toggle).toHaveTextContent(getModelDisplayName(value));
    expect(toggle).toHaveClass("max-w-full");
    expect(toggle).toHaveClass("shrink");
  });

  it("abre o dropdown ao clicar", () => {
    const value = getAllowedModels()[0];
    render(<ModelSelector value={value} onChange={() => {}} />);
    const toggle = screen.getByRole("button", { expanded: false });
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
  });

  it("seleciona um modelo e dispara onChange com o id", () => {
    const onChange = vi.fn();
    const models = getAllowedModels();
    const value = models[0];
    const other = models[1];
    render(<ModelSelector value={value} onChange={onChange} />);

    fireEvent.click(screen.getByRole("button", { expanded: false }));

    const otherLabel = getModelDisplayName(other);
    const optionButton = screen
      .getAllByText(otherLabel)
      .map((el) => el.closest("button"))
      .find((b): b is HTMLButtonElement => b !== null);

    expect(optionButton).toBeTruthy();
    fireEvent.click(optionButton!);
    expect(onChange).toHaveBeenCalledWith(other);
  });

  it("esconde modelos de providers sem credencial (só os configurados)", async () => {
    const models = getAllowedModels();
    const gemini = models.find((m) => getModelProvider(m) === "google-genai");
    const openai = models.find((m) => getModelProvider(m) === "openai");
    expect(gemini && openai).toBeTruthy();

    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(JSON.stringify({ providers: ["google-genai"] }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        ),
      ),
    );

    render(<ModelSelector value={gemini!} onChange={() => {}} />);
    fireEvent.click(screen.getByRole("button", { expanded: false }));

    // Gemini (configurado) aparece; OpenAI (sem key) some após o fetch resolver.
    await waitFor(() => {
      expect(
        screen.queryByText(getModelDisplayName(openai!)),
      ).not.toBeInTheDocument();
    });
    expect(
      screen.getAllByText(getModelDisplayName(gemini!)).length,
    ).toBeGreaterThan(0);
  });

  it("mantém o modelo ativo visível mesmo se seu provider não tem key", async () => {
    const models = getAllowedModels();
    const openai = models.find((m) => getModelProvider(m) === "openai");
    expect(openai).toBeTruthy();

    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(JSON.stringify({ providers: ["google-genai"] }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        ),
      ),
    );

    // value = modelo OpenAI, mas só google-genai configurado → ainda aparece.
    render(<ModelSelector value={openai!} onChange={() => {}} />);
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    await waitFor(() => {
      expect(
        screen.getAllByText(getModelDisplayName(openai!)).length,
      ).toBeGreaterThan(0);
    });
  });

  it("mostra modelos dinâmicos (Ollama) mesmo sem provider configurado", async () => {
    const value = getAllowedModels()[0];

    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(
            JSON.stringify({
              providers: [],
              dynamic_models: [{ id: "ollama:qwen3:8b", label: "qwen3:8b" }],
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          ),
        ),
      ),
    );

    render(<ModelSelector value={value} onChange={() => {}} />);
    fireEvent.click(screen.getByRole("button", { expanded: false }));

    // Registro dinâmico é o próprio gate de acesso — não passa pelo filtro
    // de "provider configurado" que esconde modelos estáticos sem key.
    await waitFor(() => {
      expect(screen.getAllByText("qwen3:8b").length).toBeGreaterThan(0);
    });
  });

  it("mostra modelos dinâmicos (OpenRouter) quando o dropdown é reaberto", async () => {
    const value = getAllowedModels()[0];
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            providers: ["openrouter"],
            dynamic_models: [
              { id: "openrouter:openai/gpt-4o", label: "openai/gpt-4o" },
            ],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    vi.stubGlobal("fetch", fetchMock);

    render(<ModelSelector value={value} onChange={() => {}} />);

    fireEvent.click(screen.getByRole("button", { expanded: false }));
    await waitFor(() => {
      expect(screen.getAllByText("openai/gpt-4o").length).toBeGreaterThan(0);
    });
    fireEvent.click(screen.getByRole("button", { expanded: true }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled();
    });
  });

  it("oculta modelos dinâmicos indisponíveis e mantém o modelo ativo", async () => {
    const active = "nine_router:cx/gpt-5.6-luna";
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(
            JSON.stringify({
              providers: ["openai"],
              dynamic_models: [
                {
                  id: "openrouter:private/model",
                  label: "private/model",
                  available: false,
                },
                { id: active, label: active, available: false },
              ],
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          ),
        ),
      ),
    );

    render(<ModelSelector value={active} onChange={() => {}} />);
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    await waitFor(() => {
      expect(screen.queryByText("private/model")).not.toBeInTheDocument();
      expect(screen.getAllByText(active).length).toBeGreaterThan(0);
    });
  });

  it("esconde modelo incompatível com tool-calling no code mode, mas mantém no chat mode", async () => {
    const models = getAllowedModels();
    const cohere = models.find((m) => m === "cohere:command-a-plus-05-2026");
    const value = models.find((m) => m !== cohere)!;
    expect(cohere).toBeTruthy();

    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(
            JSON.stringify({
              providers: ["google-genai", "openai", "anthropic", "cohere"],
              tool_incompatible_models: [cohere],
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          ),
        ),
      ),
    );

    const { unmount } = render(
      <ModelSelector value={value} onChange={() => {}} codeMode />,
    );
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    await waitFor(() => {
      expect(
        screen.queryByText(getModelDisplayName(cohere!)),
      ).not.toBeInTheDocument();
    });
    unmount();

    // Mesmo fetch, mas codeMode=false — modelo incompatível continua na lista.
    render(<ModelSelector value={value} onChange={() => {}} />);
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    await waitFor(() => {
      expect(
        screen.getAllByText(getModelDisplayName(cohere!)).length,
      ).toBeGreaterThan(0);
    });
  });

  it("nunca esconde o modelo incompatível se ele for o ativo, mesmo em code mode", async () => {
    const models = getAllowedModels();
    const cohere = models.find((m) => m === "cohere:command-a-plus-05-2026");
    expect(cohere).toBeTruthy();

    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(
            JSON.stringify({
              providers: ["cohere"],
              tool_incompatible_models: [cohere],
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          ),
        ),
      ),
    );

    render(<ModelSelector value={cohere!} onChange={() => {}} codeMode />);
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    await waitFor(() => {
      expect(
        screen.getAllByText(getModelDisplayName(cohere!)).length,
      ).toBeGreaterThan(0);
    });
  });

  it("troca sozinho pro primeiro modelo compatível ao entrar em code mode com um incompatível já selecionado", async () => {
    const models = getAllowedModels();
    const cohere = models.find((m) => m === "cohere:command-a-plus-05-2026");
    expect(cohere).toBeTruthy();

    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(
            JSON.stringify({
              providers: ["google-genai", "openai", "anthropic", "cohere"],
              tool_incompatible_models: [cohere],
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          ),
        ),
      ),
    );

    const onChange = vi.fn();
    render(<ModelSelector value={cohere!} onChange={onChange} codeMode />);

    await waitFor(() => {
      expect(onChange).toHaveBeenCalledTimes(1);
    });
    expect(onChange.mock.calls[0]?.[0]).not.toBe(cohere);
  });

  it("não troca sozinho em chat mode, nem se o modelo já é compatível (edge)", async () => {
    const models = getAllowedModels();
    const cohere = models.find((m) => m === "cohere:command-a-plus-05-2026");
    const compatible = models.find((m) => m !== cohere)!;
    expect(cohere).toBeTruthy();

    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(
            JSON.stringify({
              providers: ["google-genai", "openai", "anthropic", "cohere"],
              tool_incompatible_models: [cohere],
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          ),
        ),
      ),
    );

    const onChangeChatMode = vi.fn();
    const { unmount } = render(
      <ModelSelector value={cohere!} onChange={onChangeChatMode} />,
    );
    await waitFor(() => {
      expect(
        screen.getAllByText(getModelDisplayName(cohere!)).length,
      ).toBeGreaterThan(0);
    });
    expect(onChangeChatMode).not.toHaveBeenCalled();
    unmount();

    const onChangeCompatible = vi.fn();
    render(
      <ModelSelector
        value={compatible}
        onChange={onChangeCompatible}
        codeMode
      />,
    );
    await waitFor(() => {
      expect(
        screen.getAllByText(getModelDisplayName(compatible)).length,
      ).toBeGreaterThan(0);
    });
    expect(onChangeCompatible).not.toHaveBeenCalled();
  });

  it("erro no fetch de /models/providers não quebra a lista (mantém só estáticos)", async () => {
    const value = getAllowedModels()[0];

    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new Error("network down"))),
    );

    render(<ModelSelector value={value} onChange={() => {}} />);
    fireEvent.click(screen.getByRole("button", { expanded: false }));

    await waitFor(() => {
      expect(
        screen.getAllByText(getModelDisplayName(value)).length,
      ).toBeGreaterThan(0);
    });
  });
});
