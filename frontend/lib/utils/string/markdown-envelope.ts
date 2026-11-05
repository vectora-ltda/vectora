/**
 * Markdown Envelope Stripping (defensivo)
 *
 * O orchestrator do Vectora **não instrui mais** o modelo a envelopar a
 * resposta em um bloco de código externo — a instrução original pedia 6
 * crases (`` ``````markdown ``) para blindar fences internos, mas LLMs
 * frequentemente ignoravam o "6" e usavam a convenção de mercado (3
 * crases). Quando o parser (remark) recebia um envelope de 3 crases que o
 * regex antigo não reconhecia (só aceitava 6), o fence vazava como texto
 * literal na tela e fragmentava o texto em parágrafos duplicados
 * palavra a palavra — daí a instrução ter sido removida do prompt
 * (ver backend/services/agent_factory.py).
 *
 * Esta função continua existindo como rede de segurança: se algum provider
 * (ou um usuário colando texto já envelopado) ainda produzir um bloco
 * ```markdown (ou `````` markdown, qualquer N≥3 crases) cobrindo a
 * resposta inteira, ela é desempacotada antes da renderização. Em uma
 * resposta normal (sem envelope) a função é um no-op — retorna o
 * conteúdo como veio.
 *
 * Aceita qualquer N≥3 crases, exigindo (via backreference) que abertura e
 * fechamento tenham exatamente o mesmo N — cobre tanto o 6 do design
 * original quanto o 3 mais comum na prática. O preço é que uma resposta
 * cujo ÚNICO conteúdo seja um bloco ```markdown intencional (ex.: "me
 * mostre um exemplo de arquivo .md") pode ser desempacotada por engano;
 * isso é raro e preferível ao bug de vazamento.
 *
 * Streaming-safe: remove o abre/fecha mesmo quando ainda parciais durante
 * a chegada dos tokens.
 */

// Captura um envelope `````markdown\n...\n````` (N≥3 crases, abre/fecha com
// o MESMO N via backreference \1), tolerante a CRLF/trailing space. Lazy
// quantifier no corpo + final opcional para suportar streaming parcial.
const FULL_ENVELOPE_RE =
  /^[\t ]*(`{3,})\s*markdown[\t ]*\r?\n([\s\S]*?)\r?\n[\t ]*\1[\t ]*\s*$/;

// Apenas a abertura — usado quando o stream ainda está chegando.
const OPEN_ENVELOPE_RE = /^[\t ]*(`{3,})\s*markdown[\t ]*\r?\n/;

/**
 * Remove o envelope `````markdown ... ````` (N≥3 crases) se presente.
 *
 * - Fechado completo → retorna o conteúdo interno
 * - Apenas aberto (streaming em andamento) → remove só o abre
 * - Sem envelope (resposta plain) → retorna como está
 * - Conteúdo vazio ou não-string → retorna inalterado
 */
export function stripMarkdownEnvelope(content: string): string {
  if (!content) return content;

  const trimmed = content.trimStart();

  // Caso 1: envelope completo (abre + corpo + fecha, mesmo N de crases)
  const full = trimmed.match(FULL_ENVELOPE_RE);
  if (full) return full[2] ?? "";

  // Caso 2: apenas a abertura chegou (token streaming parcial)
  const open = trimmed.match(OPEN_ENVELOPE_RE);
  if (open) {
    const fence = open[1];
    let body = trimmed.slice(open[0].length);
    // Se o fecho ainda não chegou mas o stream terminou com N crases no
    // final (mesmo N da abertura), remove o fechamento parcial também.
    const closeRe = new RegExp(`\\r?\\n[\\t ]*${fence}[\\t ]*\\s*$`);
    body = body.replace(closeRe, "");
    return body;
  }

  // Caso 3: sem envelope — retorna como veio
  return content;
}
