/**
 * Checagem de suporte a imagem por modelo OpenRouter, consultando o catálogo
 * público do backend (`GET /provider-routing/openrouter/models`, já cacheado
 * ~1h lá). Cache local em memória evita refetch a cada digitação/render.
 *
 * Falha aberto (assume suporte) em erro de rede ou modelo ausente do
 * catálogo — mesma política do backend em
 * `provider_routing.py::openrouter_model_supports_image`, pra não bloquear
 * envio por uma checagem que não deveria ser bloqueante por si só.
 */

const cache = new Map<string, boolean>();

export async function checkOpenRouterModelSupportsImage(
  modelId: string,
): Promise<boolean> {
  const cached = cache.get(modelId);
  if (cached !== undefined) return cached;

  try {
    const res = await fetch(
      `/provider-routing/openrouter/models?q=${encodeURIComponent(modelId)}`,
    );
    if (!res.ok) return true;
    const data = (await res.json()) as {
      models?: { id: string; input_modalities?: string[] }[];
    };
    const match = data.models?.find((m) => m.id === modelId);
    const supports = match
      ? (match.input_modalities?.includes("image") ?? true)
      : true;
    cache.set(modelId, supports);
    return supports;
  } catch {
    return true;
  }
}
