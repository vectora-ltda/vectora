"use client";

/**
 * FallbacksTab — ordem de fallback de modelos LLM.
 *
 * Extraído de `preferencias-tab.tsx` (aba "Geral") pra aba própria — é uma
 * preferência cross-provider (a ordem vale pra qualquer provider ativo), não
 * config de um gateway específico, então não se encaixa em Provider Routing
 * (Ambiente) nem faz sentido misturado com tema/idioma/font-scale em "Geral".
 */

import { ArrowDown, ArrowUp, Plus, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import {
  getAllowedModels,
  getModelDisplayName,
  getModelProvider,
  isProviderVisionCapable,
  isModelImageCapable,
  type ProviderModelInfo,
  type ModelOption,
} from "@/lib/config/deployment-config";
import { ProviderIcon } from "@/components/icons/provider-icons";
import { m } from "@/lib/paraglide/messages";

async function fetchFallbackOrder(): Promise<string[]> {
  const res = await fetch("/admin/model/fallback-order");
  if (!res.ok) return [];
  const data = (await res.json()) as { fallback_order: string[] };
  return data.fallback_order ?? [];
}

async function saveFallbackOrder(order: string[]): Promise<string[]> {
  const res = await fetch("/admin/model/fallback-order", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ order }),
  });
  if (!res.ok) return order;
  const data = (await res.json()) as { fallback_order?: string[] };
  return data.fallback_order ?? order;
}

const IMAGE_FALLBACK_NONE = "__none__";

async function fetchImageFallbackModel(): Promise<string> {
  const res = await fetch("/admin/model/image-fallback");
  if (!res.ok) return "";
  const data = (await res.json()) as { model: string };
  return data.model ?? "";
}

async function saveImageFallbackModel(model: string): Promise<void> {
  await fetch("/admin/model/image-fallback", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model: model === IMAGE_FALLBACK_NONE ? "" : model }),
  });
}

function ImageFallbackModelSection() {
  const [model, setModel] = useState<string>(IMAGE_FALLBACK_NONE);
  const [models, setModels] = useState<ProviderModelInfo[]>([]);

  useEffect(() => {
    fetchImageFallbackModel()
      .then((fetched) => setModel(fetched || IMAGE_FALLBACK_NONE))
      .catch(() => setModel(IMAGE_FALLBACK_NONE));
  }, []);

  useEffect(() => {
    fetch("/models/providers")
      .then((res) =>
        res.ok ? res.json() : Promise.reject(new Error("catalog")),
      )
      .then(
        (data: {
          models?: ProviderModelInfo[];
          dynamic_models?: ProviderModelInfo[];
        }) =>
          setModels([...(data.models ?? []), ...(data.dynamic_models ?? [])]),
      )
      .catch(() => setModels([]));
  }, []);

  const onChange = (value: string) => {
    setModel(value);
    void saveImageFallbackModel(value);
  };

  return (
    <div className="space-y-2">
      <Label>{m.prefs_image_fallback_title()}</Label>
      <p className="text-xs text-muted-foreground">
        {m.prefs_image_fallback_help()}
      </p>
      <Select value={model} onValueChange={onChange}>
        <SelectTrigger className="h-8 text-xs">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={IMAGE_FALLBACK_NONE} className="text-xs">
            {m.prefs_image_fallback_none()}
          </SelectItem>
          {(models.length > 0
            ? models.filter(isModelImageCapable).map((item) => item.id)
            : getAllowedModels().filter((mid) =>
                isProviderVisionCapable(getModelProvider(mid)),
              )
          ).map((mid) => (
            <SelectItem key={mid} value={mid} className="text-xs">
              {getModelDisplayName(mid as ModelOption) || mid}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

export function FallbacksTab() {
  const [order, setOrder] = useState<string[]>([]);
  const [addModel, setAddModel] = useState<string>("");
  const [catalogModels, setCatalogModels] = useState<ProviderModelInfo[]>([]);

  useEffect(() => {
    fetchFallbackOrder()
      .then(setOrder)
      .catch(() => setOrder([]));
  }, []);

  useEffect(() => {
    fetch("/models/providers")
      .then((res) =>
        res.ok ? res.json() : Promise.reject(new Error("catalog")),
      )
      .then(
        (data: {
          models?: ProviderModelInfo[];
          dynamic_models?: ProviderModelInfo[];
        }) =>
          setCatalogModels([
            ...(data.models ?? []),
            ...(data.dynamic_models ?? []),
          ]),
      )
      .catch(() => setCatalogModels([]));
  }, []);

  const persist = useCallback(async (next: string[]) => {
    setOrder(next);
    setOrder(await saveFallbackOrder(next));
  }, []);

  const moveUp = (i: number) => {
    if (i === 0) return;
    const next = [...order];
    [next[i - 1], next[i]] = [next[i], next[i - 1]];
    persist(next);
  };

  const moveDown = (i: number) => {
    if (i === order.length - 1) return;
    const next = [...order];
    [next[i], next[i + 1]] = [next[i + 1], next[i]];
    persist(next);
  };

  const remove = (i: number) => {
    persist(order.filter((_, idx) => idx !== i));
  };

  const add = () => {
    if (!addModel || order.includes(addModel)) return;
    persist([...order, addModel]);
    setAddModel("");
  };

  const available = (
    catalogModels.length > 0
      ? catalogModels
          // Local runtimes remain valid fallback targets while their daemon
          // is booting. Persisting the choice lets the next request retry it
          // after readiness without forcing the user to reconfigure the UI.
          .filter(
            (item) =>
              item.available ||
              ["ollama", "llamacpp", "dmr"].includes(item.provider),
          )
          .map((item) => item.id)
      : getAllowedModels()
  ).filter((mid) => !order.includes(mid));

  return (
    <div className="space-y-6">
      <ImageFallbackModelSection />
      <div className="space-y-2">
        <Label>{m.prefs_fallback_order_title()}</Label>
        <p className="text-xs text-muted-foreground">
          {m.prefs_fallback_order_help()}
        </p>

        {order.length === 0 ? (
          <p className="text-xs text-muted-foreground italic py-1">
            {m.prefs_fallback_order_empty()}
          </p>
        ) : (
          <ul className="space-y-1">
            {order.map((mid, i) => (
              <li
                key={mid}
                className="flex items-center gap-2 rounded-md border border-border px-2 py-1.5 bg-muted/30 text-sm"
              >
                <ProviderIcon
                  provider={getModelProvider(mid as ModelOption)}
                  className="w-3.5 h-3.5 shrink-0 text-muted-foreground"
                />
                <span className="flex-1 truncate">
                  {getModelDisplayName(mid as ModelOption) || mid}
                </span>
                <button
                  onClick={() => moveUp(i)}
                  disabled={i === 0}
                  title={m.prefs_fallback_order_move_up()}
                  aria-label={m.prefs_fallback_order_move_up()}
                  className="p-1 rounded hover:bg-muted disabled:opacity-30"
                >
                  <ArrowUp className="w-3.5 h-3.5" />
                </button>
                <button
                  onClick={() => moveDown(i)}
                  disabled={i === order.length - 1}
                  title={m.prefs_fallback_order_move_down()}
                  aria-label={m.prefs_fallback_order_move_down()}
                  className="p-1 rounded hover:bg-muted disabled:opacity-30"
                >
                  <ArrowDown className="w-3.5 h-3.5" />
                </button>
                <button
                  onClick={() => remove(i)}
                  title={m.prefs_fallback_order_remove()}
                  aria-label={m.prefs_fallback_order_remove()}
                  className="p-1 rounded hover:bg-muted text-muted-foreground hover:text-destructive"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </li>
            ))}
          </ul>
        )}

        {available.length > 0 && (
          <div className="flex gap-2 pt-1">
            <Select value={addModel} onValueChange={setAddModel}>
              <SelectTrigger className="flex-1 h-8 text-xs">
                <SelectValue
                  placeholder={m.prefs_fallback_order_add_placeholder()}
                />
              </SelectTrigger>
              <SelectContent>
                {available.map((mid) => (
                  <SelectItem key={mid} value={mid} className="text-xs">
                    {getModelDisplayName(mid as ModelOption) || mid}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button
              size="sm"
              variant="outline"
              className="h-8 px-3 text-xs shrink-0"
              onClick={add}
              disabled={!addModel}
            >
              <Plus className="w-3.5 h-3.5 mr-1" />
              {m.prefs_fallback_order_add()}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
