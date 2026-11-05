import * as messages from "@/lib/paraglide/messages";

type MsgFn = (inputs?: Record<string, unknown>) => string;

/**
 * Acesso a uma mensagem do Paraglide por chave dinâmica (resolvida em runtime).
 *
 * Aceita a chave no formato legado com ponto (`a.b.c`) ou com underscore
 * (`a_b_c`). Retorna a própria chave quando não há mensagem correspondente,
 * preservando o comportamento de fallback do i18n anterior.
 */
export function mDyn(key: string, params?: Record<string, unknown>): string {
  const fn = (messages as unknown as Record<string, MsgFn>)[
    key.replace(/\./g, "_")
  ];
  return typeof fn === "function" ? fn(params) : key;
}
