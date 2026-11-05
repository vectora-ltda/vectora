/**
 * Aceita os atributos booleanos `jsx` e `global` em `<style>`. Sem
 * runtime equivalente — os atributos são inertes no DOM, servem
 * apenas para silenciar o type-check em arquivos que usam a sintaxe.
 */
import "react";

declare module "react" {
  interface StyleHTMLAttributes<T> {
    jsx?: boolean;
    global?: boolean;
  }
}
