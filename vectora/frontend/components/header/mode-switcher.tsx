"use client";

import { useLayoutEffect, useRef, useState } from "react";
import { Bot, Code2, KanbanSquare } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useSettingsStore } from "@/lib/stores/settings-store";
import { m } from "@/lib/paraglide/messages";
import type { UiMode } from "@/lib/stores/settings-store";

interface ModeSwitchProps {
  show?: boolean;
  //: Largura do Header inteiro (medida por ele, não por este componente) —
  //: o switcher vive dentro da mesma linha que o título e os ícones de
  //: ajuda/configurações, então mede a linha TODA, não o espaço que sobra
  //: pra ele entre os outros dois grupos (que sempre convergiria pro
  //: tamanho mínimo, colapsando o texto cedo demais).
  width: number;
}

const TRUNCATE_BELOW = 900;
const ICON_ONLY_BELOW = 640;

type LabelSize = "full" | "truncated" | "icon";

//: Cor do indicador deslizante e do texto/fundo ativo — só o modo ativo
//: ganha destaque, pra não brigar visualmente com o resto do header.
const MODE_ACCENT: Record<UiMode, { text: string; bg: string; bar: string }> = {
  assistant: {
    text: "text-blue-400",
    bg: "bg-blue-500/10",
    bar: "bg-blue-400",
  },
  ide: {
    text: "text-violet-400",
    bg: "bg-violet-500/10",
    bar: "bg-violet-400",
  },
  kanban: {
    text: "text-git-success",
    bg: "bg-git-success/10",
    bar: "bg-git-success",
  },
};

const MODES: { mode: UiMode; Icon: LucideIcon }[] = [
  { mode: "assistant", Icon: Bot },
  { mode: "ide", Icon: Code2 },
  { mode: "kanban", Icon: KanbanSquare },
];

//: Aba plana — cada modo é seu próprio
//: retângulo; o destaque de "ativo" vem do indicador deslizante do pai
//: (`ModeSwitch`), não de uma borda própria — permite a barra animar
//: de posição/largura ao trocar de modo em vez de saltar.
function ModeButton({
  mode,
  active,
  onClick,
  Icon,
  label,
  labelSize,
  buttonRef,
}: {
  mode: UiMode;
  active: boolean;
  onClick: () => void;
  Icon: LucideIcon;
  label: string;
  labelSize: LabelSize;
  buttonRef: (el: HTMLButtonElement | null) => void;
}) {
  const accent = MODE_ACCENT[mode];
  return (
    <button
      ref={buttonRef}
      type="button"
      onClick={onClick}
      aria-pressed={active}
      title={label}
      className={`flex h-full min-h-11 items-center gap-1.5 px-2.5 text-xs transition-colors min-w-0 ${
        active
          ? `${accent.bg} ${accent.text}`
          : "text-muted-foreground hover:text-foreground hover:bg-muted/40"
      }`}
    >
      <Icon className="w-3.5 h-3.5 shrink-0" />
      {/* `grid-template-columns` anima de "0fr" (colapsado) pra "1fr"
          (largura natural do conteúdo) — jeito CSS-only de animar uma
          largura que vai até "auto", que `width`/`max-width` não fazem
          nativamente. O `overflow-hidden` do wrapper corta o texto sem
          reflow abrupto enquanto a coluna encolhe/cresce. `sr-only` no
          modo ícone garante nome acessível pra leitor de tela mesmo com
          a coluna zerada visualmente — sem ele, texto num container
          `overflow:hidden` de largura 0 ainda pode ficar sujeito a
          heurísticas de "elemento invisível" de alguns leitores de tela. */}
      <span
        className="grid overflow-hidden transition-[grid-template-columns] duration-200 ease-out"
        style={{ gridTemplateColumns: labelSize === "icon" ? "0fr" : "1fr" }}
      >
        <span
          data-slot="label"
          className={`min-w-0 whitespace-nowrap ${
            labelSize === "icon"
              ? "sr-only"
              : labelSize === "truncated"
                ? "truncate max-w-[3.5rem]"
                : ""
          }`}
        >
          {label}
        </span>
      </span>
    </button>
  );
}

export function ModeSwitch({ show = false, width }: ModeSwitchProps) {
  const uiMode = useSettingsStore((s) => s.uiMode);
  const setUiMode = useSettingsStore((s) => s.setUiMode);
  const groupRef = useRef<HTMLDivElement>(null);
  const buttonRefs = useRef<Partial<Record<UiMode, HTMLButtonElement>>>({});
  const [indicator, setIndicator] = useState<{ left: number; width: number }>({
    left: 0,
    width: 0,
  });

  const labelSize: LabelSize =
    width >= TRUNCATE_BELOW
      ? "full"
      : width >= ICON_ONLY_BELOW
        ? "truncated"
        : "icon";

  // Recalcula a posição/largura do indicador deslizante sempre que o modo
  // ativo muda ou o layout dos botões muda (ex.: labelSize colapsa/expande
  // o texto, mudando a largura de cada botão). `useLayoutEffect` mede DEPOIS
  // do DOM aplicar o layout novo, mas ANTES do browser pintar — evita o
  // indicador "piscar" na posição antiga por um frame. Um ResizeObserver no
  // grupo cobre mudanças de largura dos botões (ex.: labelSize) sem precisar
  // desse valor no array de dependências, já que ele nunca é lido no corpo.
  useLayoutEffect(() => {
    const group = groupRef.current;
    if (!group) return;

    const measure = () => {
      const btn = buttonRefs.current[uiMode];
      if (!btn) return;
      const groupRect = group.getBoundingClientRect();
      const btnRect = btn.getBoundingClientRect();
      setIndicator({
        left: btnRect.left - groupRect.left,
        width: btnRect.width,
      });
    };

    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(group);
    return () => observer.disconnect();
  }, [uiMode]);

  if (!show) return null;

  return (
    <div
      ref={groupRef}
      role="group"
      aria-label={m.ide_mode_switcher_label()}
      className="relative flex h-full min-h-11 items-stretch min-w-0"
    >
      {MODES.map(({ mode, Icon }) => (
        <ModeButton
          key={mode}
          mode={mode}
          active={uiMode === mode}
          onClick={() => {
            if (uiMode !== mode) setUiMode(mode);
          }}
          Icon={Icon}
          label={
            mode === "assistant"
              ? m.ide_mode_assistente()
              : mode === "ide"
                ? m.ide_mode_ide()
                : m.ide_mode_kanban()
          }
          labelSize={labelSize}
          buttonRef={(el) => {
            if (el) buttonRefs.current[mode] = el;
          }}
        />
      ))}
      {/* Indicador deslizante — uma única barra que anima posição/largura
          entre os botões (`transition-all`) em vez de cada botão ter sua
          própria borda estática, que só trocava de lugar sem transição. */}
      <span
        aria-hidden="true"
        className={`pointer-events-none absolute bottom-0 h-0.5 rounded-full transition-all duration-200 ease-out ${MODE_ACCENT[uiMode].bar}`}
        style={{ left: indicator.left, width: indicator.width }}
      />
    </div>
  );
}
