// @vitest-environment jsdom
/**
 * Tests para TitleBar: invisível fora do desktop (sem window.vectora),
 * botões voltar/recarregar/minimizar/maximizar/fechar quando presente, e
 * sincronização do ícone maximizar/restaurar com o estado da janela nativa.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import {
  render,
  screen,
  cleanup,
  act,
  fireEvent,
} from "@testing-library/react";
import { TitleBar } from "../title-bar";

const mockBack = vi.fn();
const mockCanGoBack = vi.fn(() => true);

vi.mock("@tanstack/react-router", () => ({
  useRouter: () => ({
    history: { back: mockBack, canGoBack: mockCanGoBack },
  }),
}));

vi.mock("@/lib/paraglide/messages", () => ({
  m: new Proxy(
    {},
    {
      get:
        (_t, prop) =>
        (..._args: unknown[]) =>
          String(prop),
    },
  ),
}));

vi.mock("next/image", () => ({
  default: (props: Record<string, unknown>) => {
    // eslint-disable-next-line @next/next/no-img-element
    return <img alt={props.alt as string} />;
  },
}));

function installVectoraBridge(
  overrides: Partial<NonNullable<Window["vectora"]>["windowControls"]> = {},
) {
  const onStateChange = vi.fn(
    (_handler: (s: { maximized: boolean }) => void) => {
      return () => undefined;
    },
  );
  window.vectora = {
    windowControls: {
      minimize: vi.fn(),
      maximizeToggle: vi.fn(),
      close: vi.fn(),
      isMaximized: vi.fn().mockResolvedValue(false),
      onStateChange,
      ...overrides,
    },
  } as unknown as Window["vectora"];
  return window.vectora!.windowControls!;
}

beforeEach(() => {
  vi.clearAllMocks();
  mockCanGoBack.mockReturnValue(true);
});

afterEach(() => {
  cleanup();
  delete (window as { vectora?: unknown }).vectora;
});

describe("TitleBar", () => {
  it("não renderiza nada fora do app desktop (sem window.vectora)", () => {
    const { container } = render(<TitleBar />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renderiza os 5 controles quando window.vectora.windowControls existe", async () => {
    installVectoraBridge();
    render(<TitleBar />);

    expect(await screen.findByLabelText("titlebar_back")).toBeInTheDocument();
    expect(screen.getByLabelText("titlebar_reload")).toBeInTheDocument();
    expect(screen.getByLabelText("titlebar_minimize")).toBeInTheDocument();
    expect(screen.getByLabelText("titlebar_maximize")).toBeInTheDocument();
    expect(screen.getByLabelText("titlebar_close")).toBeInTheDocument();
  });

  it("desabilita o botão voltar quando não há histórico (edge)", async () => {
    mockCanGoBack.mockReturnValue(false);
    installVectoraBridge();
    render(<TitleBar />);

    expect(await screen.findByLabelText("titlebar_back")).toBeDisabled();
  });

  it("clicar em voltar chama router.history.back()", async () => {
    installVectoraBridge();
    render(<TitleBar />);

    fireEvent.click(await screen.findByLabelText("titlebar_back"));
    expect(mockBack).toHaveBeenCalledOnce();
  });

  it("clicar em minimizar/maximizar/fechar chama os IPC correspondentes", async () => {
    const controls = installVectoraBridge();
    render(<TitleBar />);

    fireEvent.click(await screen.findByLabelText("titlebar_minimize"));
    expect(controls.minimize).toHaveBeenCalledOnce();

    fireEvent.click(screen.getByLabelText("titlebar_maximize"));
    expect(controls.maximizeToggle).toHaveBeenCalledOnce();

    fireEvent.click(screen.getByLabelText("titlebar_close"));
    expect(controls.close).toHaveBeenCalledOnce();
  });

  it("troca o ícone/label para 'restaurar' quando a janela já está maximizada", async () => {
    installVectoraBridge({ isMaximized: vi.fn().mockResolvedValue(true) });
    render(<TitleBar />);

    expect(
      await screen.findByLabelText("titlebar_restore"),
    ).toBeInTheDocument();
    expect(
      screen.queryByLabelText("titlebar_maximize"),
    ).not.toBeInTheDocument();
  });

  it("mostra o ícone e o nome Vectora ao lado dos botões de voltar/recarregar", async () => {
    installVectoraBridge();
    render(<TitleBar />);

    await screen.findByLabelText("titlebar_back");
    expect(screen.getByText("Vectora")).toBeInTheDocument();
  });

  it("reage a onStateChange (duplo-clique na titlebar nativa) sem precisar de novo isMaximized()", async () => {
    let pushState: ((s: { maximized: boolean }) => void) | undefined;
    installVectoraBridge({
      onStateChange: vi.fn((handler) => {
        pushState = handler;
        return () => undefined;
      }),
    });
    render(<TitleBar />);
    await screen.findByLabelText("titlebar_maximize");

    act(() => pushState?.({ maximized: true }));

    expect(
      await screen.findByLabelText("titlebar_restore"),
    ).toBeInTheDocument();
  });
});
