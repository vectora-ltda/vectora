// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { ThemePicker } from "../theme-picker";
import {
  THEME_PRESETS,
  type BaseThemeColors,
  type ThemePresetDef,
} from "@/lib/theme/presets";

afterEach(() => {
  cleanup();
});

/** jsdom normaliza `style.background` de hex pra `rgb(r, g, b)` ao ler de
 * volta — compara pela forma computada, não pela string hex original. */
function hexToRgb(hex: string): string {
  const n = Number.parseInt(hex.slice(1), 16);
  return `rgb(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255})`;
}

/** O nome do tema agora é uma legenda fora do card (não mais texto dentro
 * do botão) — acha o botão pelo container que envolve os dois. */
function cardButtonFor(label: string): HTMLButtonElement {
  const container = screen.getByText(label).closest("div")!;
  return container.querySelector("button")!;
}

const customColors: BaseThemeColors = {
  background: "#111111",
  foreground: "#eeeeee",
  card: "#161616",
  border: "#222222",
  primary: "#00ff88",
  accent: "#ff8800",
  muted: "#333333",
  sidebar: "#0d0d0d",
  userBubble: "#004488",
};

function renderPicker(
  overrides: Partial<React.ComponentProps<typeof ThemePicker>> = {},
) {
  return render(
    <ThemePicker
      value={THEME_PRESETS[0]!.id}
      activeMode="dark"
      onChange={vi.fn()}
      presets={THEME_PRESETS}
      installedThemes={[]}
      customLabel="Personalizado"
      showMoreLabel="Exibir mais"
      showLessLabel="Mostrar menos"
      customColors={customColors}
      searchPlaceholder="Buscar..."
      marketplaceSupported={false}
      marketplaceErrorLabel="Erro"
      marketplaceInstallLabel="Instalar"
      marketplaceInstalledLabel="Instalado"
      marketplaceLoadingLabel="Buscando…"
      onThemeInstalled={vi.fn()}
      {...overrides}
    />,
  );
}

describe("ThemePicker", () => {
  it("renderiza seis paletas escuras inicialmente e o custom fixo", () => {
    renderPicker();
    const buttons = screen.getAllByRole("button");
    expect(buttons).toHaveLength(8);
    expect(screen.getByText("Personalizado")).toBeInTheDocument();
    for (const preset of THEME_PRESETS.filter((p) => p.mode === "dark").slice(
      0,
      6,
    )) {
      expect(screen.getByText(preset.label)).toBeInTheDocument();
    }
  });

  it("inclui temas instalados (marketplace) como cards adicionais", () => {
    const installed: ThemePresetDef = {
      id: "vscode-publisher.tema",
      label: "Tema Instalado",
      mode: "dark",
      family: "vscode:publisher:tema",
      colors: customColors,
    };
    renderPicker({ installedThemes: [installed] });
    fireEvent.change(screen.getByPlaceholderText("Buscar..."), {
      target: { value: "Tema Instalado" },
    });

    expect(screen.getByText("Tema Instalado")).toBeInTheDocument();
    expect(screen.getAllByRole("button")).toHaveLength(2);
  });

  it("clicar num preset chama onChange com o id do preset", () => {
    const onChange = vi.fn();
    renderPicker({ onChange });

    fireEvent.click(cardButtonFor("GitHub Dark"));

    expect(onChange).toHaveBeenCalledWith("github-dark");
  });

  it("opção ativa fica marcada com aria-pressed e as demais não", () => {
    renderPicker();

    expect(cardButtonFor("Default Dark")).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(cardButtonFor("GitHub Dark")).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("mantém visível o preset selecionado sem variante no modo ativo", () => {
    renderPicker({ value: "github-dark", activeMode: "light" });

    expect(screen.getByText("GitHub Dark")).toBeInTheDocument();
    expect(screen.getByText("GitHub Light")).toBeInTheDocument();
  });

  it("prioriza o preset selecionado quando ele está além da primeira página", () => {
    const presets: ThemePresetDef[] = Array.from({ length: 7 }, (_, index) => ({
      id: `dark-${index}`,
      label: `Dark ${index}`,
      mode: "dark",
      family: `test:dark-${index}`,
      colors: customColors,
    }));

    renderPicker({ presets, value: "dark-6" });

    expect(screen.getByText("Dark 6")).toBeInTheDocument();
    expect(screen.getAllByRole("button")).toHaveLength(8);
  });

  it("card de preview usa a cor real de background do preset (não um valor fixo) — erro/borda", () => {
    renderPicker();

    const firstPreset = THEME_PRESETS.find((p) => p.id === "default-dark")!;
    const button = cardButtonFor(firstPreset.label);
    expect(button.style.background).toBe(
      hexToRgb(firstPreset.colors.background),
    );
  });

  it("card do custom reflete customColors passado, não os presets — erro/borda", () => {
    renderPicker({ value: "custom" });

    const button = cardButtonFor("Personalizado");
    expect(button.style.background).toBe(hexToRgb(customColors.background));
  });

  it("busca filtra as opções por label", () => {
    renderPicker();

    const target = THEME_PRESETS.find((p) => p.label === "GitHub Dark")!;
    fireEvent.change(screen.getByPlaceholderText("Buscar..."), {
      target: { value: "GitHub Dark" },
    });

    expect(screen.getByText(target.label)).toBeInTheDocument();
    for (const preset of THEME_PRESETS.filter((p) => p.id !== target.id)) {
      expect(screen.queryByText(preset.label)).toBeNull();
    }
    expect(screen.getByText("Personalizado")).toBeInTheDocument();
  });

  it("erro de borda — busca sem nenhum resultado não quebra, só não renderiza cards", () => {
    renderPicker();

    fireEvent.change(screen.getByPlaceholderText("Buscar..."), {
      target: { value: "paleta-que-nao-existe-em-lugar-nenhum" },
    });

    expect(screen.queryAllByRole("button")).toHaveLength(1);
    expect(screen.getByText("Personalizado")).toBeInTheDocument();
  });

  it("seção de marketplace fica oculta quando marketplaceSupported é false", () => {
    renderPicker({ marketplaceSupported: false });

    fireEvent.change(screen.getByPlaceholderText("Buscar..."), {
      target: { value: "monokai" },
    });

    // Sem resultados locais (nenhum preset se chama "monokai") e sem seção
    // de marketplace — nenhum botão sobra na tela.
    expect(screen.queryAllByRole("button")).toHaveLength(1);
    expect(screen.getByText("Personalizado")).toBeInTheDocument();
  });
});
