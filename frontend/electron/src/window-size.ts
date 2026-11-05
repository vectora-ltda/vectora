/**
 * Tamanho default da janela principal — extraído de main.ts pra ser
 * testável sem importar `electron` (só existe dentro do processo main
 * real). Recebe a área útil da tela como parâmetro explícito em vez de
 * chamar `screen.getPrimaryDisplay()` diretamente.
 */

export interface WindowSize {
  width: number;
  height: number;
}

/**
 * ~62% da área útil da tela, capado entre 960x600 (mínimo usável) e
 * 1280x720 (não cresce além disso só porque a tela é grande — o objetivo
 * aqui é a janela nascer visivelmente menor que a tela em telas 1080p+,
 * não preencher o espaço disponível).
 */
export function computeDefaultWindowSize(screenSize: WindowSize): WindowSize {
  return {
    width: Math.round(Math.min(Math.max(screenSize.width * 0.62, 960), 1280)),
    height: Math.round(Math.min(Math.max(screenSize.height * 0.62, 600), 720)),
  };
}
