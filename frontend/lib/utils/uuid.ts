/**
 * UUID seguro em qualquer contexto.
 *
 * `crypto.randomUUID()` exige Secure Context (HTTPS ou `localhost`). Em
 * cenários comuns do Vectora (acesso via IP da LAN ou Tailscale por HTTP),
 * Firefox/Zen disparam `TypeError: crypto.randomUUID is not a function`.
 *
 * O fallback usa `crypto.getRandomValues` (disponível em contextos
 * inseguros) para gerar 16 bytes aleatórios e formatá-los como UUID v4.
 * Como último recurso, `Math.random` — pior entropia mas não-criptográfico
 * é aceitável para IDs de UI/sessão.
 */
export function safeRandomUUID(): string {
  if (
    typeof crypto !== "undefined" &&
    typeof crypto.randomUUID === "function"
  ) {
    return crypto.randomUUID();
  }
  if (
    typeof crypto !== "undefined" &&
    typeof crypto.getRandomValues === "function"
  ) {
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    // Marca versão (4) e variante (10xx) conforme RFC 4122.
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0"));
    return (
      hex.slice(0, 4).join("") +
      "-" +
      hex.slice(4, 6).join("") +
      "-" +
      hex.slice(6, 8).join("") +
      "-" +
      hex.slice(8, 10).join("") +
      "-" +
      hex.slice(10, 16).join("")
    );
  }
  // Fallback final — não criptográfico, mas funcional para IDs locais.
  return `${rnd()}${rnd()}-${rnd()}-4${rnd().slice(1)}-${rnd()}-${rnd()}${rnd()}${rnd()}`;
}

function rnd(): string {
  return Math.floor((1 + Math.random()) * 0x10000)
    .toString(16)
    .slice(1);
}
