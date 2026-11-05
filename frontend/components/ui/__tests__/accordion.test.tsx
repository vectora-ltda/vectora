// @vitest-environment jsdom
/**
 * Accordion — Header/Trigger precisam de `min-w-0` pra permitir que
 * conteúdo filho (ex: título truncado/quebrado) de fato encolha dentro do
 * flex; sem isso, o trigger sempre cresce pra caber o conteúdo inteiro,
 * estourando o container.
 */

import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import {
  Accordion,
  AccordionItem,
  AccordionTrigger,
  AccordionContent,
} from "../accordion";

afterEach(cleanup);

describe("Accordion — contenção de largura no Trigger", () => {
  it("Header e Trigger têm min-w-0 (permite que o conteúdo filho encolha)", () => {
    render(
      <Accordion type="multiple">
        <AccordionItem value="a">
          <AccordionTrigger>Título</AccordionTrigger>
          <AccordionContent>Conteúdo</AccordionContent>
        </AccordionItem>
      </Accordion>,
    );

    const trigger = screen.getByRole("button", { name: /título/i });
    expect(trigger.className).toContain("min-w-0");

    // Erro/borda: o Header (pai direto do Trigger) também precisa de
    // min-w-0 — sem isso o Trigger não encolhe mesmo tendo a classe.
    const header = trigger.parentElement;
    expect(header?.className).toContain("min-w-0");
  });
});
