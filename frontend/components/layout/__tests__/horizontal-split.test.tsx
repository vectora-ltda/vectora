// @vitest-environment jsdom
/**
 * HorizontalSplit — regressão: o painel esquerdo (`left`, onde o Header vive)
 * usava `min-w-0` puro, sem piso — arrastar o workbench bem largo numa janela
 * estreita podia encolher o Header até sumir ícones inteiros (ajuda,
 * configurações, mode-switch) em vez de só truncar texto.
 *
 * Correção seguinte (achado real, 2026-08-30): o piso de `minLeft` (360px,
 * valor fixo) não considerava o espaço que a faixa colapsada do workbench
 * (48px, sempre visível independente de `showRight`) já ocupa — num
 * viewport de celular (largura < 360+48=408px), o painel esquerdo estourava
 * por cima da faixa em vez de encolher (reproduzido ao vivo em 375px:
 * mensagens de chat renderizando atrás dos ícones do workbench). O piso
 * agora é `min(minLeft, 100% - espaço do painel direito)`.
 */
import { describe, expect, it, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";

import { HorizontalSplit } from "../horizontal-split";

afterEach(cleanup);

describe("HorizontalSplit — piso de largura do painel esquerdo", () => {
  it("aplica min-width de 360px por padrão no painel esquerdo, quando o painel direito está fechado", () => {
    render(
      <HorizontalSplit
        left={<div data-testid="left-content">Header + chat</div>}
        right={null}
        showRight={false}
        rightSize={280}
        onResize={() => {}}
      />,
    );
    const wrapper = screen.getByTestId("left-content").parentElement;
    expect(wrapper?.style.minWidth).toBe("min(360px, 100% - 0px)");
  });

  it("aceita um minLeft customizado (edge — 0 desativa o piso)", () => {
    render(
      <HorizontalSplit
        left={<div data-testid="left-content">Header + chat</div>}
        right={null}
        showRight={false}
        rightSize={280}
        onResize={() => {}}
        minLeft={0}
      />,
    );
    const wrapper = screen.getByTestId("left-content").parentElement;
    expect(wrapper?.style.minWidth).toBe("min(0px, 100% - 0px)");
  });

  it("desconta a faixa colapsada do workbench (48px) do piso — o painel esquerdo nunca deve estourar por cima dela", () => {
    // Achado real: com showRight=true + rightCollapsed=true, a faixa de
    // 48px fica sempre visível independente do painel esquerdo. Sem
    // descontar esse espaço, minLeft=360 sozinho já excede a largura
    // total de qualquer celular (viewport < 408px), forçando overlap.
    render(
      <HorizontalSplit
        left={<div data-testid="left-content">Header + chat</div>}
        right={<div>Workbench</div>}
        showRight
        rightCollapsed
        collapsedWidth={48}
        rightSize={280}
        onResize={() => {}}
      />,
    );
    const wrapper = screen.getByTestId("left-content").parentElement;
    expect(wrapper?.style.minWidth).toBe("min(360px, 100% - 48px)");
  });

  it("desconta o painel direito aberto (não-colapsado) + o handle de resize (4px) do piso", () => {
    render(
      <HorizontalSplit
        left={<div data-testid="left-content">Header + chat</div>}
        right={<div>Workbench</div>}
        showRight
        rightSize={300}
        onResize={() => {}}
      />,
    );
    const wrapper = screen.getByTestId("left-content").parentElement;
    // rightWidth clamped por [minRight=180, maxRight=720] -> 300 fica como está; + handle 4px
    expect(wrapper?.style.minWidth).toBe("min(360px, 100% - 304px)");
  });
});
